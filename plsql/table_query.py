"""#135: a query whose one source is `TABLE(v)`, v a PL/SQL collection, is run by the application.

The rows of `TABLE(v)` are the application's own collection: ScalarDB has no table of that name, and binding the
collection sent a List where a value goes (DB-SQL-10016 at run time, samples/oracle-plsql-docs 12-18, 12-19, 12-20,
6-30). The generator walks the collection instead -- filter, sort, project -- and hands the rows to the same code a
query's rows go to (SELECT INTO, BULK COLLECT, an explicit cursor's OPEN, a cursor FOR loop).

What Oracle does, measured on 26ai (2026-09-28):

* the rows come in index order, a nested table's deleted elements skipped, and `TABLE(NULL)` has no rows;
* `ORDER BY` is ascending with NULLs last and descending with NULLs first, strings by their binary value;
* a scalar collection's column is `COLUMN_VALUE`; a collection of records has the record's fields as columns.

Taken: `SELECT <items> [BULK COLLECT] INTO ... FROM TABLE(v) [alias] [WHERE ...] [ORDER BY ...] [FETCH FIRST n ROWS
ONLY]`, where each item is `*`, a column or an expression over the row and PL/SQL values, or the whole select list is
`COUNT(*)`. Anything else that reads `TABLE(...)` -- a join, GROUP BY, DISTINCT, a subquery, other aggregates, a
function's result (`TABLE(f(x))`) -- is refused with its reason when the SQL is analysed, rather than handed to ScalarDB
to fail when it runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# `FROM TABLE(`: cheap test before parsing
TABLE_SOURCE = re.compile(r"\bTABLE\s*\(", re.IGNORECASE)
# the name the rewritten expressions give the row; the generator binds it to the Java variable holding the element
ROW = "table_row_"
# `TABLE(v)`: the collection a query reads, before anything is known of its type
COLLECTION = re.compile(r"\bTABLE\s*\(\s*(?P<collection>[A-Za-z][\w$#]*)\s*\)", re.IGNORECASE)


class Refused(Exception):
    """A `TABLE(...)` query the application does not run, with the reason."""


@dataclass
class TableQuery:
    collection: str                 # the PL/SQL collection `TABLE(...)` reads
    alias: str | None
    # select items as PL/SQL expression text over ROW (a scalar row is ROW itself, a record's field ROW.f);
    # None for `*`, which is every column of the element
    items: list[str] | None
    names: list[str] = field(default_factory=list)   # each item's column name, for a cursor FOR loop's row
    where: str | None = None
    order: list[tuple[str | int, bool, bool]] = field(default_factory=list)   # (expression | position, desc, nulls first)
    count: bool = False             # `SELECT COUNT(*)`
    limit: int | None = None        # FETCH FIRST n ROWS ONLY


def reads_table(sql: str | None) -> bool:
    return bool(sql and TABLE_SOURCE.search(sql))


def collection_of(sql: str | None) -> str | None:
    match = COLLECTION.search(sql or "")
    return match.group("collection") if match else None


def parse(sql: str | None, routine_name: str | None = None, fields: list[str] | None = None,
          check_columns: bool = True) -> TableQuery | None:
    """The query as the application runs it; None when it does not read `TABLE(...)`. Raises Refused.

    `fields`: the element's fields when the collection holds records (None for scalars). A column of the row is
    one of them (or `COLUMN_VALUE` for scalars) -- in Oracle the row's column wins over a PL/SQL variable of the
    same name, and so it does here. Any other name is a PL/SQL value; `p.i` qualified by the routine is `i`.
    `check_columns` off: only the shape is checked (the analysis does not know the element's fields)."""
    if not reads_table(sql):
        return None
    try:
        tree = sqlglot.parse_one(sql, dialect="oracle")
    except Exception as e:  # noqa: BLE001
        raise Refused(f"TABLE() query that does not parse: {type(e).__name__}") from e
    if not isinstance(tree, exp.Select):
        raise Refused("TABLE() outside a plain SELECT")
    source = tree.args.get("from_") or tree.args.get("from")
    table = source.this if source is not None else None
    function = table.this if isinstance(table, exp.Table) else None
    if not (isinstance(function, exp.Anonymous) and str(function.this).upper() == "TABLE"):
        if any(isinstance(n, exp.Anonymous) and str(n.this).upper() == "TABLE" for n in tree.walk()):
            raise Refused("TABLE() that is not the query's only source")
        return None
    if tree.args.get("joins"):
        raise Refused("TABLE() joined with another source")
    for key in ("group", "having", "distinct", "connect", "offset", "windows", "qualify"):
        if tree.args.get(key):
            raise Refused(f"TABLE() query with {key.upper()}")
    if any(isinstance(n, (exp.Subquery, exp.Window)) or (isinstance(n, exp.Select) and n is not tree)
           for n in tree.walk()):
        raise Refused("TABLE() query with a subquery")
    arguments = function.expressions
    if len(arguments) != 1 or not isinstance(arguments[0], exp.Column) or arguments[0].table:
        raise Refused("TABLE() of something other than a PL/SQL collection variable (a function's result, a column)")
    collection = arguments[0].name
    alias = table.alias or None
    columns = {f.lower() for f in fields} if fields is not None else {"column_value"}

    def rewrite(node):
        if isinstance(node, exp.Column):
            qualifier = node.table.lower() if node.table else ""
            name = node.name.lower()
            if name in columns and (not qualifier or qualifier == (alias or "").lower()):
                if fields is None:
                    return exp.column(ROW)
                return exp.column(node.name, table=ROW)
            if qualifier and alias and qualifier == alias.lower() and check_columns:
                raise Refused(f"TABLE() row has no column {node.name}")
            if qualifier and routine_name and qualifier == routine_name.lower():
                return exp.column(node.name)   # `p.i`: the routine's own parameter
        return node

    def text(node) -> str:
        return node.transform(rewrite).sql(dialect="oracle")

    query = TableQuery(collection=collection, alias=alias, items=[])
    projections = tree.expressions
    aggregates = [n for p in projections for n in p.walk() if isinstance(n, exp.AggFunc)]
    if aggregates:
        if len(projections) == 1 and isinstance(projections[0].unalias(), exp.Count) \
                and isinstance(projections[0].unalias().this, exp.Star):
            query.count = True
            query.items = None
        else:
            raise Refused("TABLE() query with an aggregate other than COUNT(*)")
    elif len(projections) == 1 and isinstance(projections[0], exp.Star):
        query.items = None
    else:
        for projection in projections:
            if isinstance(projection, exp.Star) or (isinstance(projection, exp.Column)
                                                    and isinstance(projection.this, exp.Star)):
                raise Refused("TABLE() query with * next to other items")
            query.items.append(text(projection.unalias()))
            query.names.append(projection.alias_or_name.lower())
    where = tree.args.get("where")
    if where is not None:
        query.where = text(where.this)
    order = tree.args.get("order")
    for ordered in (order.expressions if order is not None else []):
        key = ordered.this
        position = int(key.name) if isinstance(key, exp.Literal) and not key.is_string and key.name.isdigit() else None
        if position is not None and (query.count or not 1 <= position <= len(query.items or fields or [None])):
            raise Refused(f"ORDER BY {position} outside the select list")
        query.order.append((position if position is not None else text(key), bool(ordered.args.get("desc")),
                            bool(ordered.args.get("nulls_first"))))
    limit = tree.args.get("limit")
    if limit is not None:
        count = limit.args.get("count") if isinstance(limit, exp.Fetch) else limit.expression
        if not (isinstance(count, exp.Literal) and count.name.isdigit()) or \
                (isinstance(limit, exp.Fetch) and (limit.args.get("limit_options") and
                                                   (limit.args["limit_options"].args.get("percent") or
                                                    limit.args["limit_options"].args.get("with_ties")))):
            raise Refused("TABLE() query with a row limit other than FETCH FIRST n ROWS ONLY")
        query.limit = int(count.name)
    return query
