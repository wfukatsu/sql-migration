"""P1-6: hand the SQL inside PL/SQL to the existing converter, and bring the answer back.

`scalardb_migrate` already knows how to turn a SQL statement into ScalarDB SQL, decide its access path, and
decompose what ScalarDB SQL cannot run into a fetch + residual plan. None of that is re-implemented here. This
module is the adapter: it takes the SQL out of a PL/SQL statement, makes it into something the converter can read,
and puts the answer back on the IR node.

    result = analyse(operation, scope="pkg_order.create_order", symbols=table, registry=registry)
    operation.target_status   # OK | WARN | PLANNED | ERROR
    operation.plan_id         # set when the statement needs a run-time plan

## Three things the adapter has to get right

* **`SELECT INTO` is PL/SQL, not SQL.** sqlglot keeps it on the statement, and its shape differs between one
  target (`Into.this`) and several (`Into.expressions`); both are stripped here and recorded as `into_targets`,
  because the converter must see a plain SELECT.
* **PL/SQL variables are not columns.** `WHERE order_id = p_id` reads as two columns to a SQL parser. The symbol
  table decides: a bare identifier that resolves to a variable or parameter in scope becomes a bind.
* **Binds are named, never `?`.** The decomposer collapses an anonymous placeholder to `{"param": "?"}` and the
  Java runtime resolves it by that string, so two `?` in one fetch would resolve to the same value (P2-9). A
  unique name per variable keeps the existing path correct.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

from scalardb_migrate.converter import StatementConverter
from scalardb_migrate.schema import SchemaRegistry

from .columns import at_most_one_row, bind_columns, select_columns, selects_star
from .ir.model import BindVariable, SqlOperation
from .source import Issue, SourceRange
from .symbols import SymbolTable

BIND_KINDS = {"variable", "constant", "parameter"}
CARDINALITY_BY_KIND = {"SELECT": "UNKNOWN", "INSERT": "NONE", "UPDATE": "NONE", "DELETE": "NONE", "MERGE": "NONE"}


@dataclass
class SqlAnalysisResult:
    """The contract of §4.1. Serialisable on its own, so the bridge can become a process boundary later."""

    sql_id: str
    source_dialect: str = "oracle"
    text: str = ""
    into_targets: list[dict] = field(default_factory=list)
    binds: list[dict] = field(default_factory=list)
    cardinality: str = "UNKNOWN"
    source_range: dict | None = None
    status: str = "ERROR"
    converted: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    read_set: list[str] = field(default_factory=list)
    write_set: list[str] = field(default_factory=list)
    access_path: str | None = None
    plan: dict | None = None

    def to_json(self, indent: int = 1) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent) + "\n"


def analyse(operation: SqlOperation, scope: str, symbols: SymbolTable | None = None,
            registry: SchemaRegistry | None = None, storage: str = "jdbc",
            plan_dir: str | Path | None = None, lift: bool = True,
            loop_variables: dict[str, dict[str, str | None]] | None = None) -> SqlAnalysisResult:
    """Run one SQL statement through the converter and write the answer onto the IR node.

    `lift` may be turned off for a routine whose read carried a row lock: see `capability.check`.

    `loop_variables` names the cursor FOR loop variables in scope at this statement, each with the Oracle type
    of its fields: see `bind_variables`.
    """
    registry = registry if registry is not None else SchemaRegistry()
    result = SqlAnalysisResult(sql_id=operation.id, text=operation.original_sql,
                               source_range=_range(operation.source_range))
    try:
        tree = sqlglot.parse_one(operation.original_sql, dialect="oracle")
    except Exception as e:  # noqa: BLE001 - a parse failure is a diagnostic, not a crash
        issue = Issue("ERROR", "SQL_PARSE", f"{type(e).__name__}: {e}", operation.source_range)
        operation.diagnostics.append(issue)
        operation.target_status = "ERROR"
        result.issues.append(_issue(issue))
        return result

    targets = strip_into(tree)
    # after strip_into, not before: `INTO v_row` parses as a table, and a star is only expandable when the
    # statement reads exactly one table
    expand_star(tree, symbols)
    if targets:
        operation.into_targets = list(targets)
        operation.cardinality = "EXACTLY_ONE" if len(targets) >= 1 and not _is_bulk(tree) else "MANY"
    # no INTO in the SQL does not mean no assignment targets: a cursor rewritten to the query it was (#11)
    # carries them on the node, because the INTO was never part of its text
    result.into_targets = [{"name": t} for t in operation.into_targets]
    result.cardinality = operation.cardinality
    operation.at_most_one_row = at_most_one_row(tree)

    binds = bind_variables(tree, scope, symbols, loop_variables)
    if lift:
        lift_expressions(tree, binds, scope, symbols)
    attribute_columns(tree, binds, operation, registry, symbols)
    operation.binds = binds
    result.binds = [asdict(b) for b in binds]

    converter = StatementConverter("oracle", registry, {}, decompose=True, storage=storage)
    converted = converter.convert(tree.sql(dialect="oracle"))

    operation.target_status = converted.status
    operation.target_sql = list(converted.converted)
    operation.sql_kind = converted.kind.upper() if converted.kind else operation.sql_kind
    for issue in converted.issues:
        mapped = Issue(issue.severity, issue.code, issue.message, operation.source_range)
        operation.diagnostics.append(mapped)
        result.issues.append(_issue(mapped))
    result.status = converted.status
    result.converted = list(converted.converted)
    result.access_path = next((i.message for i in converted.issues if i.code == "ACCESS"), None)

    operation.read_set, operation.write_set = read_write_sets(tree)
    result.read_set, result.write_set = operation.read_set, operation.write_set

    if converted.plan is not None:
        result.plan = converted.plan
        operation.plan_id = f"{operation.id}.plan.json"
        if plan_dir is not None:
            path = Path(plan_dir)
            path.mkdir(parents=True, exist_ok=True)
            (path / _plan_filename(operation.id)).write_text(
                json.dumps(converted.plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def strip_into(tree: exp.Expression) -> list[str]:
    """Remove `INTO` from a SELECT and return the assignment targets, in order.

    sqlglot stores one target on `Into.this` and several on `Into.expressions`; a caller that only handles one of
    those silently loses the other (this is why both shapes have a test).

    The target is written whole, qualifier included. `INTO v_rec.name` assigns a field of a record, and taking
    only `name` loses the one thing that says so -- after which the generator cannot tell it apart from a local
    called `name`, and quietly assigns nothing.
    """
    into = tree.args.get("into") if isinstance(tree, exp.Select) else None
    if into is None:
        return []
    targets: list[str] = []
    if into.expressions:
        targets = [_target_name(e) for e in into.expressions]
    elif into.this is not None:
        targets = [_target_name(into.this)]
    tree.set("into", None)
    return targets


# P4-4: functions the Java runtime can evaluate, so an expression built only from these (plus variables and
# literals) can be computed in the application and bound as a value. Anything else is left for the converter to
# refuse -- a function nobody has implemented would otherwise be lifted out and then fail to compile.
EVALUABLE = {"NVL", "ROUND", "TRUNC", "SYSDATE", "SYSTIMESTAMP", "MOD", "ABS", "GREATEST", "LEAST",
             "UPPER", "LOWER", "RTRIM", "LTRIM", "TRIM", "LENGTH", "SUBSTR", "TO_CHAR", "COALESCE"}

LIFTABLE_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Neg, exp.Paren, exp.Concat)

# `seq_audit_id.NEXTVAL` は sqlglot には修飾された列に見える。採番は移行先で決めた方式に置き換わるので
# （計画 §9）、式として持ち上げて Java 側で採る。CURRVAL は持ち上げない——直前の NEXTVAL が同じ
# セッションで何を返したかに依存しており、その依存は移行後には存在しない
NEXTVAL = re.compile(r"^(?P<sequence>[\w$#]+)\.NEXTVAL$", re.IGNORECASE)


def _sequence_of(node: exp.Expression) -> str | None:
    if not isinstance(node, exp.Column) or not node.table:
        return None
    match = NEXTVAL.match(f"{node.table}.{node.name}")
    return match.group("sequence") if match else None


def lift_expressions(tree: exp.Expression, binds: list[BindVariable], scope: str,
                     symbols: SymbolTable | None) -> None:
    """Replace a SET / VALUES expression ScalarDB cannot evaluate with a bind the application computes.

    ScalarDB SQL takes literals and bind markers and nothing else, so `SET total_amount = ROUND(:v * :r, 2)` is
    refused outright. The value does not need the database to compute it: every operand is already a PL/SQL
    variable, and the generated Java has the same functions (`Plsql.round`, `Plsql.nvl`, ...). So the expression
    is lifted out, bound as one value, and evaluated where it can be.

    Two limits keep this from turning a refusal into a wrong answer:

    * **an expression that mentions a column is not lifted.** `SET stock_qty = stock_qty + :n` needs the stored
      value, and reading it first is a different transaction shape with a different race -- that is a redesign
      (LOCK-001), not a rewrite.
    * **only functions the runtime implements are lifted.** Lifting an unimplemented one would replace a clear
      conversion error with Java that does not compile, which is worse.
    """
    for parent, expressions in _value_positions(tree):
        for i, value in enumerate(list(expressions)):
            if not _liftable(value):
                continue
            placeholder = _unique(f"expr{len(binds) + 1}", {b.name: b for b in binds})
            binds.append(BindVariable(name=placeholder, direction="IN",
                                      expression=_as_plsql(value, binds)))
            expressions[i].replace(exp.Placeholder(this=placeholder))


def _as_plsql(value: exp.Expression, binds: list[BindVariable]) -> str:
    """The expression as PL/SQL again, with the placeholders put back to the variable names they came from.

    `bind_variables` has already turned every variable reference into `:name`, so rendering the tree gives
    `ROUND(:v_total * :p_rate, 2)`. What is wanted is the PL/SQL the routine wrote, because that is what the
    expression translator reads and what a reviewer recognises.
    """
    rendered = value.sql(dialect="oracle")
    for bind in binds:
        if bind.plsql_variable:
            rendered = re.sub(rf":{re.escape(bind.name)}\b", bind.plsql_variable, rendered)
    return rendered


def _value_positions(tree: exp.Expression) -> list[tuple[exp.Expression, list]]:
    """Where a value may legally be lifted from: an INSERT's VALUES tuple and an UPDATE's SET right-hand sides.

    MERGE の枝の中にも同じ位置がある。外側だけを見ていると、`WHEN NOT MATCHED THEN INSERT ...
    VALUES (..., SYSDATE)` の `SYSDATE` が持ち上がらず、**ScalarDB が評価できない式として拒否
    される**——同じ形が UPDATE / INSERT なら通っているのに、である。
    """
    out: list[tuple[exp.Expression, list]] = []
    for node in [tree] + [w for w in tree.find_all(exp.When)] if isinstance(tree, exp.Merge) else [tree]:
        out.extend(_positions_of(node))
    return out


def _positions_of(node: exp.Expression) -> list[tuple[exp.Expression, list]]:
    out: list[tuple[exp.Expression, list]] = []
    for insert in ([node] if isinstance(node, exp.Insert) else list(node.find_all(exp.Insert))):
        values = insert.expression
        # 素の INSERT は `VALUES (...)` を `Values`（タプルの並び）として持つが、MERGE の枝の中では
        # `Tuple` 1 つである。見る形を 1 つに決め打つと、片方が黙って持ち上がらない
        tuples = values.expressions if isinstance(values, exp.Values) else \
            [values] if isinstance(values, exp.Tuple) else []
        for tuple_ in tuples:
            out.append((tuple_, tuple_.expressions))
    for update in ([node] if isinstance(node, exp.Update) else list(node.find_all(exp.Update))):
        for assignment in update.args.get("expressions") or []:
            if isinstance(assignment, exp.EQ):
                out.append((assignment, [assignment.args["expression"]]))
    # WHERE の比較の、**列を含まない側**。`changed_at < SYSTIMESTAMP - p_keep_days` の右辺は、
    # データベースが持っている値を 1 つも読まない——アプリで計算して 1 つの値として渡せる。
    # 持ち上げないと ScalarDB は式を受け付けず、計画に回っても H2 が日時の算術で止まった
    # （`prc_purge_audit` / 2026-09-19）。列を読む側は `_liftable` が拒む
    for where in node.find_all(exp.Where):
        for comparison in where.find_all(exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE):
            if comparison.find_ancestor(exp.Select) is not where.find_ancestor(exp.Select):
                continue   # 副問い合わせの中。外側の文の値ではない
            out.append((comparison, [comparison.args["this"], comparison.args["expression"]]))
    return out


def _unparen(value: exp.Expression) -> exp.Expression:
    while isinstance(value, exp.Paren):
        value = value.this
    return value


def _liftable(value: exp.Expression) -> bool:
    inner = _unparen(value)
    value = inner
    if _sequence_of(value):
        return True   # 採番。移行先の方式へ置き換わる
    if isinstance(value, exp.Column) and _is_pseudo_column(value):
        return True   # a clock or session read on its own, not a stored value
    if isinstance(value, (exp.Literal, exp.Placeholder, exp.Null, exp.Column)):
        return False  # already a value, or a column this must not touch
    if any(isinstance(node, exp.Column) and not _is_pseudo_column(node) for node in value.walk()):
        return False  # mentions a column: the database holds the operand, not the application
    if isinstance(value, exp.Select) or value.find(exp.Select) is not None:
        return False  # a subquery is not an expression the application can evaluate
    if _mentions_correlation(value):
        # `:NEW.status` / `:OLD.status` are the trigger's row, and the target has no trigger: where those
        # values come from is #12, not a rewrite. Lifting one would replace a clear conversion error with
        # Java that does not compile, which is the one outcome this function exists to avoid.
        return False
    for node in value.walk():
        if isinstance(node, (exp.Func, exp.Anonymous)) and _function_name(node) not in EVALUABLE:
            return False
    return isinstance(value, LIFTABLE_ARITHMETIC) or isinstance(value, (exp.Func, exp.Anonymous))


# what the session is, rather than what a row holds. `USER` parses as a bare column and the converter refuses
# it for that reason; it is the value the caller supplies (#1), so it is lifted like a clock read.
PSEUDO_COLUMNS = {"SYSDATE", "SYSTIMESTAMP", "CURRENT_DATE", "CURRENT_TIMESTAMP", "USER"}


CORRELATION = {"NEW", "OLD"}


def _mentions_correlation(value: exp.Expression) -> bool:
    """Whether the expression reads a trigger correlation name. `:NEW.x` parses as a placeholder plus a dot."""
    return any(isinstance(node, exp.Placeholder) and str(node.this or "").upper() in CORRELATION
               for node in value.walk())


def _is_pseudo_column(node: exp.Column) -> bool:
    """`SYSDATE` and friends parse as bare columns; they read the session, not a stored value."""
    return node.name.upper() in PSEUDO_COLUMNS and not node.table


def _function_name(node: exp.Expression) -> str:
    name = getattr(node, "name", "") or type(node).__name__
    if isinstance(node, exp.Anonymous):
        return str(node.this).upper()
    return {"Nvl": "NVL", "Coalesce": "COALESCE", "Round": "ROUND", "Trunc": "TRUNC", "Mod": "MOD",
            "Abs": "ABS", "Upper": "UPPER", "Lower": "LOWER", "Length": "LENGTH", "Substring": "SUBSTR",
            "ToChar": "TO_CHAR", "Greatest": "GREATEST", "Least": "LEAST", "Trim": "TRIM",
            "CurrentDate": "SYSDATE", "CurrentTimestamp": "SYSTIMESTAMP"}.get(
        type(node).__name__, str(name).upper())


def _target_name(node: exp.Expression) -> str:
    """`v_rec.name` stays `v_rec.name`; a bare identifier stays itself."""
    if isinstance(node, exp.Column) and node.table:
        return f"{node.table}.{node.name}"
    if isinstance(node, exp.Dot):
        return node.sql(dialect="oracle")
    return node.name or node.sql(dialect="oracle")


def expand_star(tree: exp.Expression, symbols: SymbolTable | None) -> None:
    """Replace `SELECT *` with the table's columns, in DDL order, when the DDL is known.

    A star does not name what it returns, so nothing downstream can line the result up with anything: a
    `%ROWTYPE` target cannot be built, and the capture's column order would be whatever the target database
    chose. Naming the columns here fixes both, and it is the same list Oracle would have expanded.

    Left alone when there is not exactly one known table -- expanding a join's star would require deciding an
    order across tables, which the DDL does not settle.
    """
    schema = symbols.oracle_schema if symbols else None
    if schema is None:
        return
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None or not any(isinstance(e, exp.Star) for e in select.expressions):
        return
    tables = [t.name.lower() for t in tree.find_all(exp.Table) if t.name]
    if len(tables) != 1:
        return
    columns = schema.columns(tables[0])
    if not columns:
        return
    select.set("expressions", [exp.column(name) for name in columns])


def bind_variables(tree: exp.Expression, scope: str, symbols: SymbolTable | None,
                   loop_variables: dict[str, dict[str, str | None]] | None = None) -> list[BindVariable]:
    """Replace PL/SQL variable references with named placeholders, in place, and describe them.

    Without a symbol table nothing is replaced: guessing which bare identifier is a variable would rewrite real
    column references into binds, which fails at run time rather than at analysis time.

    A qualified name is a column of that table -- with one exception. Inside `FOR r IN (SELECT ...) LOOP`,
    `r.order_id` is the loop's row, and the generated Java already holds it as a value before the statement
    runs. Treating it as a column is what makes `WHERE order_id = r.order_id` a column-to-column comparison
    ScalarDB refuses, and what keeps `TO_CHAR(r.order_id)` from being lifted (#10). `loop_variables` says which
    qualifiers are loop variables, and what the query declared each field as; anything not named there is left
    alone, so a real qualified column is untouched.
    """
    loop_fields = {name.lower(): fields for name, fields in (loop_variables or {}).items()}
    if symbols is None and not loop_fields:
        return []
    _correlation_as_qualified(tree, loop_fields)
    found: dict[str, BindVariable] = {}
    _collection_elements(tree, scope, symbols, found)
    for column in list(tree.find_all(exp.Column)):
        if column.table:
            fields = loop_fields.get(column.table.lower())
            if fields is None:
                continue  # qualified: it is a column of that table, not a variable
            variable = f"{column.table}.{column.name}"
            placeholder = _unique(f"{column.table}_{column.name}", found, variable)
            found[placeholder] = BindVariable(
                name=placeholder, direction="IN", oracle_type=fields.get(column.name.lower()),
                plsql_variable=variable)
            column.replace(exp.Placeholder(this=placeholder))
            continue
        if symbols is None:
            continue
        name = column.name
        symbol = symbols.resolve(scope, name)
        if symbol is None or symbol.kind not in BIND_KINDS:
            continue
        placeholder = _unique(name, found)
        found[placeholder] = BindVariable(
            name=placeholder, direction=symbol.direction or "IN",
            oracle_type=symbol.type.oracle if symbol.type else None, plsql_variable=name)
        column.replace(exp.Placeholder(this=placeholder))
    return list(found.values())


def _collection_elements(tree: exp.Expression, scope: str, symbols: SymbolTable | None,
                         found: dict[str, BindVariable]) -> None:
    """`p_ids(i)` を bind にする。`FORALL i IN 1 .. p_ids.COUNT` が回しているコレクションの要素で、
    生成コードは文が走る前から値として持っている——cursor FOR ループの行（#10）と同じ立場である。

    コレクションかどうかは**symbol table が答える**。素の `f(x)` を一律に要素参照とみなすと、
    本物の関数呼び出しを壊す。
    """
    if symbols is None:
        return
    for node in list(tree.find_all(exp.Anonymous)):
        name = str(node.this or "")
        symbol = symbols.resolve(scope, name)
        if symbol is None or symbol.type is None or symbol.type.origin != "collection":
            continue
        arguments = node.expressions or []
        if len(arguments) != 1 or not isinstance(arguments[0], exp.Column) or arguments[0].table:
            continue   # 添字が式である。どの要素かが決まらない
        index = arguments[0].name
        variable = f"{name}({index})"
        placeholder = _unique(f"{name}_{index}", found, variable)
        element = (symbol.type.resolved or "").partition("TABLE OF ")[2] or None
        found[placeholder] = BindVariable(name=placeholder, direction="IN", oracle_type=element,
                                          plsql_variable=variable)
        node.replace(exp.Placeholder(this=placeholder))


def _correlation_as_qualified(tree: exp.Expression, loop_fields: dict) -> None:
    """`:NEW.status` を `NEW.status` の形にする。呼び出し側が `NEW` / `OLD` を渡したときだけ。

    Oracle の相関名は placeholder + ドットに構文解析されるので、そのままでは「修飾された参照」を
    見る道に乗らない。乗せてしまえば、cursor FOR ループの行（#10）と同じ扱いになる——どちらも
    「文が走る前から Java が値として持っているもの」である。

    `NEW` / `OLD` を渡していない routine では何もしない。渡していないのに書き換えると、`NEW` という
    別名の表を持つ問い合わせを壊す。
    """
    if not ({"new", "old"} & set(loop_fields)):
        return
    for dot in list(tree.find_all(exp.Dot)):
        placeholder = dot.this
        if not isinstance(placeholder, exp.Placeholder):
            continue
        qualifier = str(placeholder.this or "").upper()
        if qualifier.lower() not in loop_fields or not dot.expression:
            continue
        dot.replace(exp.column(dot.expression.name, table=qualifier))


def attribute_columns(tree: exp.Expression, binds: list[BindVariable], operation: SqlOperation,
                      registry: SchemaRegistry | None, symbols: SymbolTable | None = None) -> None:
    """Record the ScalarDB column behind each bind and each select item, where there is exactly one.

    Without a registry there is nothing to look a type up in, so nothing is recorded -- the same rule as
    bind_variables: say nothing rather than guess, because a wrong type here is a wrong value at run time.
    """
    if registry is None:
        return
    tables = [t.name.lower() for t in tree.find_all(exp.Table) if t.name]
    oracle = symbols.oracle_schema if symbols else None
    by_bind = bind_columns(tree)
    for bind in binds:
        column = by_bind.get(bind.name)
        if column:
            bind.column = column
            bind.scalardb_type = _column_type(registry, tables, column)
            bind.column_oracle_type = _oracle_type(oracle, tables, column)
    operation.selects_star = selects_star(tree)
    operation.into_columns = select_columns(tree)
    operation.into_types = [_column_type(registry, tables, c) if c else None for c in operation.into_columns]
    operation.into_oracle_types = [_oracle_type(oracle, tables, c) if c else None
                                   for c in operation.into_columns]


def _oracle_type(schema, tables: list[str], column: str) -> str | None:
    """What the Oracle DDL declares the column as, when the statement's tables agree about it."""
    if schema is None:
        return None
    found = {t for table in tables if (t := schema.column(table, column))}
    return found.pop() if len(found) == 1 else None


def _column_type(registry: SchemaRegistry, tables: list[str], column: str) -> str | None:
    """The column's ScalarDB type, or None when the statement's tables disagree about it."""
    found = set()
    for table in tables:
        meta = registry.get(table)
        if meta is None:
            continue
        for name, kind in meta.columns.items():
            if name.lower() == column:
                found.add(kind)
    return found.pop() if len(found) == 1 else None


def read_write_sets(tree: exp.Expression) -> tuple[list[str], list[str]]:
    """Tables read and tables written, lower-cased and de-duplicated, keeping source order."""
    written: list[str] = []
    if isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        target = tree.this if not isinstance(tree, exp.Update) else tree.this
        table = target if isinstance(target, exp.Table) else (target.find(exp.Table) if target else None)
        if table is not None:
            written.append(table.name.lower())
    read = [t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in written]
    return _unique_list(read), _unique_list(written)


def _is_bulk(tree: exp.Expression) -> bool:
    into = tree.args.get("into")
    return bool(into is not None and into.args.get("bulk_collect"))


def _unique(name: str, taken: dict, variable: str | None = None) -> str:
    """One placeholder per variable, unique inside the statement (P2-9: two anonymous binds collide).

    `variable` is the PL/SQL name the placeholder stands for, when it differs from the placeholder itself --
    `r.order_id` cannot be a placeholder name, but two references to it must still share one bind.
    """
    variable = name if variable is None else variable
    candidate = name
    suffix = 2
    while candidate in taken and taken[candidate].plsql_variable != variable:
        candidate = f"{name}_{suffix}"
        suffix += 1
    return candidate


def _unique_list(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def _plan_filename(sql_id: str) -> str:
    return sql_id.replace("#", "_").replace(".", "_") + ".plan.json"


def _range(source_range: SourceRange | None) -> dict | None:
    if source_range is None:
        return None
    return {"file": source_range.file, "startLine": source_range.start_line, "endLine": source_range.end_line}


def _issue(issue: Issue) -> dict:
    return {"severity": issue.severity, "code": issue.code, "message": issue.message}
