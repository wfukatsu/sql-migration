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
    _merge_source(tree, found)
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
    count, with_ties = row_cap(select)
    if isinstance(count, exp.Literal) and count.name == "1" and not with_ties:
        return True
    items = select.expressions or []
    if not items or select.args.get("group"):
        return False
    return all(isinstance(item.unalias() if hasattr(item, "unalias") else item, exp.AggFunc)
               for item in items)


def row_cap(tree: exp.Expression) -> tuple[exp.Expression | None, bool]:
    """The row count a query caps itself at, and whether the cap is `WITH TIES`.

    `LIMIT 1` and `FETCH FIRST 1 ROWS ONLY` both land on `limit`, as an `exp.Limit` or an `exp.Fetch`, and
    the count sits on a different argument of each. `WITH TIES` is not a cap of its own: it can return
    several rows at the boundary. Callers use this both to tell that a query cannot return a second row and
    to tell that it must not be rewritten into something the cap no longer applies to (`COUNT(*)`).
    """
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    limit = select.args.get("limit") if select is not None else None
    if limit is None:
        return (None, False)
    count = limit.args.get("count") if isinstance(limit, exp.Fetch) else \
        (limit.expression if isinstance(limit, exp.Limit) else None)
    options = limit.args.get("limit_options")
    return (count, bool(options and options.args.get("with_ties")))


def _merge_source(tree: exp.Expression, found: dict[str, str]) -> None:
    """MERGE の値は `USING (SELECT :b AS c ...) s` を通って `s.c` として書かれる。

    その 1 段を辿らないと、bind に列が付かない——生成コードは値を**型変換せずに**ドライバへ渡し、
    `BigDecimal` が `DB-SQL-10016` で拒まれる。辿るのは「別名 -> その別名が選んでいる bind」の
    1 対 1 対応だけで、式を選んでいる別名は対応が作れないので飛ばす。
    """
    if not isinstance(tree, exp.Merge):
        return
    using = tree.args.get("using")
    inner = using.this if isinstance(using, exp.Subquery) else using
    select = inner if isinstance(inner, exp.Select) else None
    if select is None:
        return
    alias = using.alias_or_name if using is not None else ""
    by_alias: dict[str, str] = {}
    for item in select.expressions or []:
        value = item.this if isinstance(item, exp.Alias) else item
        name = item.alias if isinstance(item, exp.Alias) else getattr(item, "name", "")
        if isinstance(value, exp.Placeholder) and name:
            by_alias[name.lower()] = str(value.this)
    if not by_alias:
        return
    for column in tree.find_all(exp.Column):
        if alias and (column.table or "").lower() != alias.lower():
            continue
        placeholder = by_alias.get(column.name.lower())
        if placeholder:
            # `s.customer_id` が立っている位置の列名は、その別名と同じ名前である
            found.setdefault(placeholder, column.name.lower())


def _insert_values(tree: exp.Expression, found: dict[str, str]) -> None:
    """INSERT names its columns in one list and its values in another; the pairing is positional.

    MERGE の `WHEN NOT MATCHED THEN INSERT` も同じ形で、そこも見る。見ないと bind に列が付かず、
    生成コードは値を**型変換せずに**ドライバへ渡す（`BigDecimal` が `DB-SQL-10016` で拒まれる）。
    """
    for insert in ([tree] if isinstance(tree, exp.Insert) else list(tree.find_all(exp.Insert))):
        # 素の INSERT は列並びを `Schema` として持つが、MERGE の枝の中では `Tuple` である。
        # 片方だけ見ていると、そちらの bind にだけ列が付かない
        if not isinstance(insert.this, (exp.Schema, exp.Tuple)):
            continue
        columns = [c.name for c in insert.this.expressions
                   if isinstance(c, (exp.Column, exp.Identifier))]
        values = insert.expression
        tuples = values.expressions if isinstance(values, exp.Values) else \
            [values] if isinstance(values, exp.Tuple) else []
        if not columns:
            continue
        for tuple_ in tuples:
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
