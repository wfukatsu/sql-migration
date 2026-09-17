"""P3-1: work out which ScalarDB column each bind and each select item of a SQL statement belongs to.

The generated repository hands PL/SQL values to JDBC and reads them back. Both directions need the column's
ScalarDB type: a `NUMBER` arrives as a `BigDecimal`, which ScalarDB's driver refuses outright (DB-SQL-10016),
and a money column stored as a scaled `BIGINT` has to be unscaled on the way out. Neither can be decided from
the PL/SQL type alone, because the answer depends on what the column was mapped to.

The mapping is static. It is computed here, once, at generation time, rather than guessed at run time from
result-set metadata -- which ScalarDB is not obliged to populate, and which would make a wrong answer a run-time
surprise instead of a generation-time refusal. Anything this cannot map stays unmapped, and the generator then
binds the value unchanged, exactly as it did before.

The shapes handled are the ones the corpus produces: a bind compared to a column, a bind assigned to a column in
`SET`, a bind in a `VALUES` position, and a select item that is a column or a plain aggregate over one. A bind
used inside an expression (`WHERE price * :rate > 10`) is deliberately not mapped: the value no longer belongs
to a single column, and pretending otherwise is how a silently wrong conversion happens.
"""

from __future__ import annotations

from sqlglot import exp

# an aggregate whose value has the same type as its argument; COUNT and AVG do not, and are listed separately
SAME_TYPE_AGGREGATES = (exp.Sum, exp.Min, exp.Max)

COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def bind_columns(tree: exp.Expression) -> dict[str, str]:
    """{placeholder name: column name} for every bind this can attribute to exactly one column."""
    found: dict[str, str] = {}
    _insert_values(tree, found)
    for node in tree.walk():
        if isinstance(node, COMPARISONS):
            _pair(node.this, node.expression, found)
            _pair(node.expression, node.this, found)
        elif isinstance(node, exp.In):
            for item in node.expressions or []:
                _pair(node.this, item, found)
        elif isinstance(node, exp.Between):
            _pair(node.this, node.args.get("low"), found)
            _pair(node.this, node.args.get("high"), found)
    return found


def selects_star(tree: exp.Expression) -> bool:
    """`SELECT *` asks for every column, so the result's width is the table's, not the query's."""
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    return bool(select is not None and any(isinstance(e, exp.Star) or
                                           (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
                                           for e in select.expressions))


def select_columns(tree: exp.Expression) -> list[str | None]:
    """The column behind each select item, or None where there is not exactly one."""
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return []
    return [_behind(item) for item in select.expressions]


# --------------------------------------------------------------------------------------------------

def at_most_one_row(tree: exp.Expression) -> bool:
    """Whether this query cannot return a second row, by its own shape.

    Two shapes say so: `LIMIT 1`, and a select list that is nothing but aggregates with no `GROUP BY` -- an
    aggregate over no rows still returns its one row (`SEM-004`). It matters because `SELECT INTO` is warned
    about (`MULTI_ROW_INTO`) when it cannot reach its row by key, and neither of these can raise TOO_MANY_ROWS
    however it is reached.
    """
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return False
    # `LIMIT 1` and `FETCH FIRST 1 ROWS ONLY` both land on `limit`, as an `exp.Limit` or an `exp.Fetch`, and
    # the count sits on a different argument of each. `WITH TIES` is neither: it can return several.
    limit = select.args.get("limit")
    count = limit.args.get("count") if isinstance(limit, exp.Fetch) else \
        (limit.expression if isinstance(limit, exp.Limit) else None)
    options = limit.args.get("limit_options") if limit is not None else None
    if isinstance(count, exp.Literal) and count.name == "1" and not (options and options.args.get("with_ties")):
        return True
    items = select.expressions or []
    if not items or select.args.get("group"):
        return False
    return all(isinstance(item.unalias() if hasattr(item, "unalias") else item, exp.AggFunc)
               for item in items)


def _insert_values(tree: exp.Expression, found: dict[str, str]) -> None:
    """INSERT names its columns in one list and its values in another; the pairing is positional."""
    if not isinstance(tree, exp.Insert) or not isinstance(tree.this, exp.Schema):
        return
    columns = [c.name for c in tree.this.expressions if isinstance(c, (exp.Column, exp.Identifier))]
    values = tree.expression
    if not columns or not isinstance(values, exp.Values):
        return
    for tuple_ in values.expressions:
        for i, value in enumerate(tuple_.expressions):
            if isinstance(value, exp.Placeholder) and i < len(columns):
                found.setdefault(str(value.this), columns[i].lower())


def _pair(column: exp.Expression | None, value: exp.Expression | None, found: dict[str, str]) -> None:
    if isinstance(column, exp.Column) and isinstance(value, exp.Placeholder):
        found.setdefault(str(value.this), column.name.lower())


def _behind(item: exp.Expression) -> str | None:
    item = item.this if isinstance(item, exp.Alias) else item
    if isinstance(item, exp.Column):
        return item.name.lower()
    if isinstance(item, SAME_TYPE_AGGREGATES) and isinstance(item.this, exp.Column):
        return item.this.name.lower()
    return None
