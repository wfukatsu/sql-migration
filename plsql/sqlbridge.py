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
from dataclasses import asdict, dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

from scalardb_migrate.converter import StatementConverter
from scalardb_migrate.schema import SchemaRegistry

from .columns import bind_columns, select_columns, selects_star
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
            plan_dir: str | Path | None = None) -> SqlAnalysisResult:
    """Run one SQL statement through the converter and write the answer onto the IR node."""
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
    operation.into_targets = [t for t in targets]
    result.into_targets = [{"name": t} for t in targets]
    if targets:
        operation.cardinality = "EXACTLY_ONE" if len(targets) >= 1 and not _is_bulk(tree) else "MANY"
        result.cardinality = operation.cardinality

    binds = bind_variables(tree, scope, symbols)
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


def bind_variables(tree: exp.Expression, scope: str, symbols: SymbolTable | None) -> list[BindVariable]:
    """Replace PL/SQL variable references with named placeholders, in place, and describe them.

    Without a symbol table nothing is replaced: guessing which bare identifier is a variable would rewrite real
    column references into binds, which fails at run time rather than at analysis time.
    """
    if symbols is None:
        return []
    found: dict[str, BindVariable] = {}
    for column in list(tree.find_all(exp.Column)):
        if column.table:
            continue  # qualified: it is a column of that table, not a variable
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


def _unique(name: str, taken: dict) -> str:
    """One placeholder per variable, unique inside the statement (P2-9: two anonymous binds collide)."""
    candidate = name
    suffix = 2
    while candidate in taken and taken[candidate].plsql_variable != name:
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
