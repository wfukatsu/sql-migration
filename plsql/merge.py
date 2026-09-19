"""#26 の続き: `MERGE` を「読んでから UPDATE か INSERT を選ぶ」へ割る。

`MERGE` を `UPSERT` にすると、**既存行に当たったときに `WHEN MATCHED` が設定していない列まで
上書きする**（`pkg_customer_import.import` で実害を測った: tier が GOLD→BRONZE、registered_on が
ずれる）。#26 の決定は converter を **WARN のまま**にすることで、それが許容できない移行では
「読んでから UPDATE / INSERT を選ぶ形にすること」としている。PL/SQL の移行はまさにそれである
——目的は Oracle と同じ行が残ることだからである。

    MERGE INTO customers c USING (SELECT :id AS customer_id, :name AS name FROM dual) s
      ON (c.customer_id = s.customer_id)
      WHEN MATCHED THEN UPDATE SET c.name = s.name
      WHEN NOT MATCHED THEN INSERT (customer_id, name, tier) VALUES (s.customer_id, s.name, 'BRONZE');

    -> SELECT COUNT(*) INTO v_merge_1 FROM customers WHERE customer_id = :id;
       IF v_merge_1 > 0 THEN
         UPDATE customers SET name = :name WHERE customer_id = :id;        -- WHEN MATCHED だけ
       ELSE
         INSERT INTO customers (customer_id, name, tier) VALUES (:id, :name, 'BRONZE');
       END IF;

**converter は変えない。** 単体ツール `sql-transpile` の挙動（I10 とベンチマークの数字）を守るのが
#26 で WARN を選んだ理由であり、ここは PL/SQL の移行だけの話である。

**安全なのは同じトランザクションの中で読んで書くから**で、2 つのトランザクションが同時に
「無い」と読んで両方 INSERT しても、片方は commit で弾かれる（Consensus Commit / P3-4 で実測）。
弾かれたものを再試行するのは呼び出し側である。だから #9 の RMW と同じく、**記録された routine
だけ**を割る（`limits.yaml` の `rowLocks.optimistic`）。

割れない形（`USING` が表や複数行の問い合わせ、`ON` が等値の AND でない、`DELETE` 句がある）は
触らない。UPSERT と、上書きされる列を名指しする警告がそのまま残る。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .ir import model as M
from .limits import RowLocks
from .symbols import OracleSchema, Symbol, SymbolTable

PREFIX = "v_merge_"


def rewrite(program: M.Program, row_locks: RowLocks | None, schema: OracleSchema | None,
            symbols: SymbolTable | None = None) -> None:
    locks = row_locks or RowLocks()
    for module in program.modules:
        for routine in module.routines:
            if not locks.decided(routine.id):
                continue   # 読んでから書く形は、決めた routine だけ（#9 と同じ）
            before = len(routine.declarations)
            routine.body = _sequence(routine.body, routine, schema)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, routine, schema)
            scope = symbols.scopes.get(routine.id) if symbols else None
            for declaration in routine.declarations[before:]:
                if scope is not None:
                    # 登録しないと、SQL の側がこの変数を**列として**読む（#9 の RMW で同じことが起きた）
                    scope.declare(Symbol(name=declaration.name, kind="variable", scope=routine.id,
                                         type=declaration.type, source_range=declaration.source_range))


def _sequence(statements: list[M.Statement], routine: M.Routine,
              schema: OracleSchema | None) -> list[M.Statement]:
    out: list[M.Statement] = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, schema))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, schema)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, routine, schema)
        out.extend(_split(statement, routine) or [statement])
    return out


def _split(statement: M.Statement, routine: M.Routine) -> list[M.Statement] | None:
    if statement.kind != "SqlOperation" or (statement.sql_kind or "").upper() != "MERGE":
        return None
    try:
        tree = sqlglot.parse_one(statement.original_sql or "", dialect="oracle")
    except Exception:
        return None
    if not isinstance(tree, exp.Merge) or not isinstance(tree.this, exp.Table):
        return None
    table = tree.this.name
    target = tree.this.alias_or_name
    source = _source(tree.args.get("using"))
    if source is None:
        return None   # USING が表か、1 行の値の組でない
    alias, values = source
    keys = _keys(tree.args.get("on"), target, alias, values)
    if keys is None:
        return None
    matched, inserted = None, None
    for when in (tree.args.get("whens").expressions if tree.args.get("whens") else []):
        then = when.args.get("then")
        if when.args.get("matched") and isinstance(then, exp.Update) and matched is None:
            matched = then
        elif not when.args.get("matched") and isinstance(then, exp.Insert) and inserted is None:
            inserted = then
        else:
            return None   # DELETE 句、条件つきの枝、同じ枝が 2 つ。どれを選ぶかを推測しない
    if matched is None or inserted is None:
        return None
    if any(when.args.get("condition") for when in tree.args["whens"].expressions):
        return None   # `WHEN MATCHED THEN UPDATE ... WHERE ...`。条件の意味を持ち込まない

    where = " AND ".join(f"{column} = {value}" for column, value in keys)
    sets = []
    for assignment in matched.expressions or []:
        if not isinstance(assignment, exp.EQ) or not isinstance(assignment.this, exp.Column):
            return None
        value = _substituted(assignment.expression, target, alias, values)
        if value is None:
            return None
        sets.append(f"{assignment.this.name} = {value}")
    columns = [c.name for c in (inserted.this.expressions if isinstance(inserted.this, exp.Tuple)
                                else inserted.this.expressions if inserted.this else [])]
    row = inserted.expression.expressions if isinstance(inserted.expression, exp.Tuple) else None
    if not columns or row is None or len(row) != len(columns):
        return None
    rendered = [_substituted(value, target, alias, values) for value in row]
    if any(v is None for v in rendered):
        return None

    variable = _free_name(routine)
    routine.declarations.append(M.Declaration(
        id=f"{routine.id}#decl-{variable}", kind="Declaration", name=variable,
        source_range=statement.source_range,
        type=M.TypeRef(oracle="NUMBER", resolved="NUMBER", origin="inferred")))
    exists = M.SqlOperation(
        id=f"{statement.id}exists", kind="SqlOperation", source_range=statement.source_range,
        sql_kind="SELECT", cardinality="EXACTLY_ONE", into_targets=[variable],
        original_sql=f"SELECT COUNT(*) FROM {table} WHERE {where}", read_set=[table.lower()])
    update = M.SqlOperation(
        id=f"{statement.id}update", kind="SqlOperation", source_range=statement.source_range,
        sql_kind="UPDATE", write_set=[table.lower()],
        original_sql=f"UPDATE {table} SET {', '.join(sets)} WHERE {where}")
    insert = M.SqlOperation(
        id=f"{statement.id}insert", kind="SqlOperation", source_range=statement.source_range,
        sql_kind="INSERT", write_set=[table.lower()],
        original_sql=f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(rendered)})")
    for written in (update, insert):
        written.add("INFO", "MERGE_SPLIT",
                    f"MERGE を「読んでから UPDATE か INSERT を選ぶ」へ割った。UPSERT にすると既存行で "
                    f"WHEN MATCHED が設定していない列まで上書きする（#26）。**同じトランザクションの中で"
                    f"読んで書く**ので、同時に「無い」と読んだ 2 つは commit で片方が弾かれる"
                    f"（再試行は呼び出し側の責務・#9）")
    choose = M.If(id=f"{statement.id}choose", kind="If", source_range=statement.source_range,
                  branches=[M.Branch(condition=f"{variable} > 0", body=[update])], else_body=[insert])
    return [exists, choose]


def _source(using) -> tuple[str, dict[str, str]] | None:
    """`USING (SELECT <式> AS a, ... FROM dual) s` の (別名, 列 -> 式)。1 行の値の組だけを扱う。"""
    if not isinstance(using, exp.Subquery) or not isinstance(using.this, exp.Select):
        return None
    select = using.this
    source = select.args.get("from_") or select.args.get("from")
    table = source.this if source is not None else None
    if not isinstance(table, exp.Table) or table.name.lower() != "dual":
        return None   # 表を読む USING は複数行になりうる。1 回の選択では同じにならない
    if select.args.get("where") or select.args.get("joins"):
        return None
    values: dict[str, str] = {}
    for item in select.expressions:
        if not isinstance(item, exp.Alias):
            return None
        values[item.alias.lower()] = item.this.sql(dialect="oracle", normalize_functions=False)
    alias = using.alias_or_name
    return (alias, values) if alias else None


def _keys(on, target: str, alias: str, values: dict[str, str]) -> list[tuple[str, str]] | None:
    """`ON (c.k = s.k AND ...)` の (列, 値)。等値の AND 以外は扱わない。"""
    if on is None:
        return None
    out = []
    conditions = [on]
    while conditions:
        condition = conditions.pop()
        if isinstance(condition, exp.Paren):
            conditions.append(condition.this)
        elif isinstance(condition, exp.And):
            conditions.extend([condition.this, condition.expression])
        elif isinstance(condition, exp.EQ):
            left, right = condition.this, condition.expression
            if _is(right, target) and not _is(left, target):
                left, right = right, left
            if not (isinstance(left, exp.Column) and _is(left, target)):
                return None
            value = _substituted(right, target, alias, values)
            if value is None:
                return None
            out.append((left.name, value))
        else:
            return None
    return sorted(out) or None


def _is(node, qualifier: str) -> bool:
    return isinstance(node, exp.Column) and (node.table or "").lower() == qualifier.lower()


def _substituted(node: exp.Expression, target: str, alias: str, values: dict[str, str]) -> str | None:
    """`s.name` を USING が与えた式に置き換える。**書き込む先の表の列を読む式は割らない**——
    `SET c.qty = c.qty + s.qty` は読んだ値に依存する別の話（#9 の RMW）である。"""
    # 根そのものを置き換えても変数は元の節を指したままなので、括弧で包んでから置き換える
    # （`s.name` 単体の値がそのまま残った——置き換えたつもりで何も起きていなかった）
    holder = exp.Paren(this=node.copy())
    for column in list(holder.find_all(exp.Column)):
        qualifier = (column.table or "").lower()
        if qualifier == alias.lower():
            value = values.get(column.name.lower())
            if value is None:
                return None
            column.replace(sqlglot.parse_one(value, dialect="oracle"))
        elif qualifier == target.lower():
            return None
    copied = holder.this
    if isinstance(copied, exp.Column) and not copied.table and copied.name.lower() in values:
        return None   # 修飾の無い名前。USING の列か表の列か、ここでは決められない
    return copied.sql(dialect="oracle", normalize_functions=False)


def _free_name(routine: M.Routine) -> str:
    used = {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}
    index = 1
    while f"{PREFIX}{index}" in used:
        index += 1
    return f"{PREFIX}{index}"
