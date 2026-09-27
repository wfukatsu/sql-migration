"""#14: `SELECT ... BULK COLLECT INTO` と、その配列を回す `FORALL` を、1 つの走査ループにする。

Oracle のこの形は「表から N 行を配列へ読み、その配列で N 回 DML する」である。移行先に配列束縛は
無いので、**読んだ行をそのまま回す** cursor FOR ループが同じことをする:

    SELECT product_id, qty BULK COLLECT INTO v_products, v_qtys FROM order_lines WHERE ...;
    FORALL i IN 1 .. v_products.COUNT
      INSERT INTO inventory_tx (...) VALUES (..., v_products(i), -v_qtys(i), ...);

    -> FOR r IN (SELECT product_id, qty FROM order_lines WHERE ...) LOOP
         INSERT INTO inventory_tx (...) VALUES (..., r.product_id, -r.qty, ...);
       END LOOP;

書き換えないと、`BULK COLLECT INTO` を持つ SELECT は **1 行の SELECT INTO** として扱われ、
0 件と複数件が元に無い例外になる（実際 `-1422 TOO_MANY_ROWS` が出ていた）。cursor FOR ループに
寄せれば、行数上限（`CUR-002` / #19）も、書いた表を読み直さない検査（P2-4）も、そのまま効く。

**FORALL が 1 往復なのに対し、ループは N 回**である。これは性能の差であって答えの差ではないが、
隠さずに `BULK_CHUNKED` として残す。`FORALL ... SAVE EXCEPTIONS`（部分失敗を許す原子性）は
別の話なので、ここでは扱わない——`BULK-002` が REDESIGN として捕まえ続ける。
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from .ir import model as M

BULK_INTO = re.compile(r"\bBULK\s+COLLECT\s+INTO\s+(?P<targets>[\w$#]+(?:\s*,\s*[\w$#]+)*)\s*",
                       re.IGNORECASE)
COUNT_BOUND = re.compile(r"^\s*1\s*\.\.\s*(?P<collection>[\w$#]+)\s*\.\s*COUNT\s*$", re.IGNORECASE)
ROW = "r"


def rewrite(routine: M.Routine) -> None:
    """Replace every `BULK COLLECT` + `FORALL` pair in `routine` with the loop it is, in place."""
    mappings: list[dict[str, str]] = []
    context = _Context(routine)
    routine.body = _sequence(routine.body, mappings, context, ())
    for handler in routine.exception_handlers:
        handler.body = _sequence(handler.body, mappings, context, ())
    for of_column in mappings:
        # `v_ids(SQL%BULK_EXCEPTIONS(j).ERROR_INDEX)` in a SAVE EXCEPTIONS handler is the failed element's
        # value. The collections are gone (the rows are streamed), and the failed element is the row the
        # per-element part receives (gen_java.split), so the reference becomes the row's column
        for handler in routine.exception_handlers:
            for statement in _walk(handler.body):
                _handler_references(statement, of_column)


def _handler_references(statement: M.Statement, of_column: dict[str, str]) -> None:
    import dataclasses

    def rewrite_text(text: str) -> str:
        for collection, column in of_column.items():
            text = re.sub(rf"(?<![\w$#.]){re.escape(collection)}\s*\(\s*SQL%BULK_EXCEPTIONS\s*\(\s*[\w$#]+\s*\)"
                          rf"\s*\.\s*ERROR_INDEX\s*\)", f"{ROW}.{column}", text, flags=re.IGNORECASE)
        return text

    for field in dataclasses.fields(statement):
        value = getattr(statement, field.name, None)
        if isinstance(value, str) and field.name not in ("id", "kind"):
            setattr(statement, field.name, rewrite_text(value))
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            setattr(statement, field.name, [rewrite_text(v) for v in value])
    for branch in getattr(statement, "branches", []) or []:
        branch.condition = rewrite_text(branch.condition or "")


def _walk(statements: list[M.Statement]) -> list[M.Statement]:
    from .lower import _walk as walk

    return walk(statements)


class _Context:
    """What `_pair` needs to know about the rest of the routine: the fused loop leaves the collections empty and
    SQL%ROWCOUNT at what the loop made of it, so whatever reads either after the pair decides whether it may (#109)."""

    def __init__(self, routine: M.Routine):
        self.routine = routine
        self.locals = {d.name.lower() for d in routine.declarations}
        self.blocks: list[M.Block] = []   # the nested blocks around the statement being looked at


def _sequence(statements: list[M.Statement], mappings: list[dict[str, str]], context: _Context,
              loops: tuple[M.Loop, ...]) -> list[M.Statement]:
    for statement in statements:
        inner = loops + (statement,) if statement.kind == "Loop" else loops
        if statement.kind == "Block":
            context.blocks.append(statement)
        try:
            for attribute in ("body", "else_body"):
                nested = getattr(statement, attribute, None)
                if nested:
                    setattr(statement, attribute, _sequence(nested, mappings, context, inner))
            for branch in getattr(statement, "branches", []) or []:
                branch.body = _sequence(branch.body, mappings, context, inner)
            for handler in getattr(statement, "exception_handlers", []) or []:
                handler.body = _sequence(handler.body, mappings, context, loops)
        finally:
            if statement.kind == "Block":
                context.blocks.pop()

    out: list[M.Statement] = []
    index = 0
    while index < len(statements):
        paired = _pair(statements, index, context, loops)
        if paired is None:
            out.append(statements[index])
            index += 1
            continue
        loop, of_column = paired
        mappings.append(of_column)
        out.append(loop)
        index += 2
    return out


def _pair(statements: list[M.Statement], index: int, context: _Context,
          loops: tuple[M.Loop, ...]) -> "tuple[M.Loop, dict[str, str]] | None":
    """`SELECT ... BULK COLLECT INTO a, b` の直後に `FORALL i IN 1 .. a.COUNT` が来る並び。"""
    run = statements[index:index + 2]
    if len(run) < 2 or run[0].kind != "SqlOperation" or run[1].kind != "Loop":
        return None
    select, forall = run
    if (select.sql_kind or "").upper() != "SELECT" or (forall.loop_kind or "") != "forall":
        return None
    match = BULK_INTO.search(select.original_sql or "")
    if match is None:
        return None
    targets = [t.strip() for t in match.group("targets").split(",")]
    bound = COUNT_BOUND.match(forall.cursor or "")
    if bound is None or bound.group("collection").lower() not in {t.lower() for t in targets}:
        return None
    query = _without_bulk_into(select.original_sql or "", match)
    columns = _select_names(query)
    if columns is None or len(columns) != len(targets):
        return None   # 位置で対応させられない。射影が式で名前を持たないときなど
    of_column = {t.lower(): c for t, c in zip(targets, columns)}
    refused = _read_after(context, select, forall, loops, of_column)
    if refused:
        # the pair stays as written: BULK-001 keeps the routine out of AUTO, and the generator reads every row into
        # the collection, which is what the later reads need (#109). Decided before `_body` rewrites the FORALL
        select.add("INFO", "BULK_NOT_FUSED", f"BULK COLLECT と FORALL を 1 つの走査ループにしなかった: {refused}")
        return None
    body = _body(forall.body, of_column)
    if body is None:
        return None
    for statement in _walk(body):
        # SQL%ROWCOUNT after a FORALL is the rows of every element, not the last one's. `_read_after` refuses the
        # pair when something reads it, and the loop still counts as FORALL does, for whatever it cannot see (#109)
        if statement.kind == "SqlOperation" and (statement.sql_kind or "").upper() in ("INSERT", "UPDATE", "DELETE",
                                                                                         "MERGE"):
            statement.accumulates_rowcount = True
    operation = M.SqlOperation(id=f"{select.id}#query", kind="SqlOperation",
                               source_range=select.source_range, sql_kind="SELECT",
                               original_sql=query, cardinality="MANY")
    loop = M.Loop(id=select.id, kind="Loop", source_range=select.source_range,
                  loop_kind="cursor-for", variable=ROW, query=operation, body=body,
                  cursor=f"{ROW} IN ({query})")
    loop.add("INFO", "BULK_CHUNKED",
             f"BULK COLLECT into {', '.join(targets)} と、それを回す FORALL を 1 つの走査ループに "
             f"した。FORALL は 1 往復、ループは行ごとに 1 回で、性能は変わるが答えは変わらない。"
             f"行数上限は cursor FOR ループとして決める（CUR-002）")
    return loop, of_column


IMPLICIT_CURSOR = re.compile(r"\bSQL\s*%\s*(?:ROWCOUNT|BULK_ROWCOUNT|FOUND|NOTFOUND)\b", re.IGNORECASE)
SAVE_EXCEPTIONS_ELEMENT = r"\s*\(\s*SQL%BULK_EXCEPTIONS\s*\(\s*[\w$#]+\s*\)\s*\.\s*ERROR_INDEX\s*\)"


def _read_after(context: _Context, select: M.Statement, forall: M.Loop, loops: tuple[M.Loop, ...],
                of_column: dict[str, str]) -> str | None:
    """Why the pair may not become one loop, or None (#109).

    The loop streams the rows: the collections are never filled, and SQL%ROWCOUNT is what the loop leaves. So the
    pair is kept when anything else reads one of the collections -- `v_ids.COUNT` after it was COLLECTION_IS_NULL --
    or when the implicit cursor's attributes are read after it. A collection that is not a local of the routine or
    of a block around the pair (a parameter, a package variable) is read by someone this cannot see.
    """
    locals_ = set(context.locals)
    for block in context.blocks:
        locals_ |= {d.name.lower() for d in block.declarations}
    foreign = sorted(t for t in of_column if t not in locals_)
    if foreign:
        return f"{', '.join(foreign)} は routine の局所変数ではない（呼び出し側や他の routine が読む）"
    pair = {id(select), id(forall)} | {id(s) for s in _walk(forall.body)}
    names = re.compile(r"(?<![\w$#.])(?:" + "|".join(re.escape(t) for t in of_column) + r")(?![\w$#])",
                       re.IGNORECASE)
    after = forall.source_range.end_line if forall.source_range else None
    others = _walk(context.routine.body) + [s for h in context.routine.exception_handlers for s in _walk(h.body)]
    # the next iteration of a loop around the pair runs the statements above the pair after it
    in_loop = {id(s) for loop in loops for s in _walk(loop.body)}
    readers, counted = [], False
    for statement in others:
        if id(statement) in pair:
            continue
        text = _text(statement)
        for declaration in getattr(statement, "declarations", None) or []:
            text += "\n" + (declaration.initial or "")   # `DECLARE n NUMBER := v_ids.COUNT;` in a later block
        # `v_ids(SQL%BULK_EXCEPTIONS(j).ERROR_INDEX)` is the failed element, which `rewrite` turns into the row
        text = re.sub(r"(?<![\w$#.])[\w$#]+" + SAVE_EXCEPTIONS_ELEMENT, "", text, flags=re.IGNORECASE)
        if names.search(text):
            readers.append(statement)
        start = statement.source_range.start_line if statement.source_range else None
        if IMPLICIT_CURSOR.search(text) and (after is None or start is None or start >= after
                                             or id(statement) in in_loop):
            counted = True
    if readers:
        return (f"{', '.join(of_column)} を組の外でも読んでいる（走査ループにすると配列は埋まらない。"
                f"{readers[0].source_range or readers[0].id}）")
    if counted:
        return "組の後ろで SQL%ROWCOUNT / SQL%BULK_ROWCOUNT / SQL%FOUND / SQL%NOTFOUND を読んでいる"
    return None


def _text(node) -> str:
    """Every string a statement (or declaration) holds, its own fields only: nested bodies are walked separately."""
    import dataclasses

    parts: list[str] = []

    def collect(value) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, M.BindVariable):
            parts.extend(v for v in (value.plsql_variable, value.expression) if v)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (str, M.BindVariable)):
                    collect(item)

    for field in dataclasses.fields(node):
        if field.name in ("id", "kind", "diagnostics", "body", "else_body", "query", "exception_handlers",
                          "declarations", "source_range", "type", "variant_statements"):
            continue
        collect(getattr(node, field.name, None))
    for branch in getattr(node, "branches", []) or []:
        parts.append(branch.condition or "")
    if getattr(node, "selector", None):
        parts.append(node.selector)
    return "\n".join(parts)


def _without_bulk_into(sql: str, match: re.Match) -> str:
    """`BULK COLLECT INTO a, b` を落とす。残りはそのままの問い合わせである。"""
    return (sql[:match.start()] + sql[match.end():]).strip()


def _select_names(sql: str) -> list[str] | None:
    """射影ごとの出力名。1 つでも名前が決まらなければ None（位置対応が作れない）。"""
    try:
        tree = sqlglot.parse_one(sql, dialect="oracle")
    except Exception:
        return None
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None or not select.expressions:
        return None
    names = []
    for item in select.expressions:
        if isinstance(item, exp.Alias):
            names.append(item.alias)
        elif isinstance(item, exp.Column):
            names.append(item.name)
        else:
            return None
    return names if all(names) else None


def _body(statements: list[M.Statement], of_column: dict[str, str]) -> list[M.Statement] | None:
    """FORALL の本体を、ループ変数の行を読む形に書き換える。`a(i)` -> `r.<列>`。

    添字が 1 つの素の識別子でない参照、`a.COUNT` のように配列そのものを使う参照が 1 つでもあれば
    諦める。配列の使い方が「i 番目の値を読む」以外なら、行を回す形は同じことをしない。
    """
    out: list[M.Statement] = []
    for statement in statements:
        if statement.kind != "SqlOperation" or not statement.original_sql:
            return None
        sql = _row_references(statement.original_sql, of_column)
        if sql is None:
            return None
        statement.original_sql = sql
        out.append(statement)
    return out or None


def _row_references(sql: str, of_column: dict[str, str]) -> str | None:
    """`v_products(i)` を `r.product_id` にする。構文木の上で行うので、文字列の一致では動かない。"""
    try:
        tree = sqlglot.parse_one(sql, dialect="oracle")
    except Exception:
        return None
    subscript = None
    for node in list(tree.walk()):
        name = getattr(node, "name", "") or ""
        if isinstance(node, exp.Anonymous) and name.lower() in of_column:
            arguments = node.expressions or []
            if len(arguments) != 1 or not isinstance(arguments[0], exp.Column) or arguments[0].table:
                return None   # 添字が式である。行を回す形には移せない
            if subscript is not None and arguments[0].name.lower() != subscript:
                return None   # 添字が 2 種類ある。同じ行を指しているとは限らない
            subscript = arguments[0].name.lower()
            node.replace(exp.column(of_column[name.lower()], table=ROW))
    for node in tree.walk():
        # 配列そのものへの参照（`a.COUNT` など）が残っていれば、書き換えは同じことをしない
        if isinstance(node, exp.Column) and (node.table or "").lower() in of_column:
            return None
        if isinstance(node, exp.Anonymous) and (getattr(node, "name", "") or "").lower() in of_column:
            return None
    return tree.sql(dialect="oracle")
