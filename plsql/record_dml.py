"""A whole record written to a table: `INSERT INTO t VALUES rec` and `UPDATE t SET ROW = rec` (#92).

Oracle takes the record's fields in order as the table's columns in order. Left as it stands, the record reached the
converter as one bind -- a value of the record class, which ScalarDB refused as a type (samples/oracle-plsql-docs
5-52, 5-53). Written out column by column it is an ordinary INSERT or UPDATE, and every field is a bind the
generated Java already holds (`sqlbridge.bind_variables`).

`SET ROW` writes the key columns too, and ScalarDB cannot move a row to another key. When the statement's WHERE
pins the key to one value (`WHERE week = i`), writing the record's key is either a no-op -- the record carries the
same key -- or a move. `key_guards` takes the key out of the SET and leaves a check the generated code runs before
the UPDATE: the same key goes ahead, another one is refused at run time rather than updated elsewhere in silence.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def expand(sql: str, symbols, scope: str | None, schema) -> str | None:
    """The statement with the record written out column by column, or None when it is not that shape."""
    if symbols is None or schema is None or scope is None or not ("VALUES" in sql.upper() or "ROW" in sql.upper()):
        return None
    try:
        tree = sqlglot.parse_one(sql, dialect="oracle")
    except Exception:  # noqa: BLE001 - not ours to report; sqlbridge says why it cannot be parsed
        return None
    if isinstance(tree, exp.Insert) and isinstance(tree.this, exp.Table):
        values = tree.expression
        rows = values.expressions if isinstance(values, exp.Values) else []
        if len(rows) != 1 or len(rows[0].expressions) != 1 or not isinstance(rows[0].expressions[0], exp.Column) \
                or rows[0].expressions[0].table:
            return None
        pairs = _pairs(symbols, scope, schema, tree.this.name, rows[0].expressions[0].name)
        if pairs is None:
            return None
        tree.set("this", exp.Schema(this=tree.this.copy(), expressions=[exp.to_identifier(c) for c, _ in pairs]))
        tree.set("expression", exp.Values(expressions=[exp.Tuple(expressions=[
            exp.column(field, table=record) for _, (record, field) in pairs])]))
        return tree.sql(dialect="oracle")
    if isinstance(tree, exp.Update) and isinstance(tree.this, exp.Table):
        sets = tree.expressions
        if len(sets) != 1 or not isinstance(sets[0], exp.EQ) or not isinstance(sets[0].this, exp.Column) \
                or sets[0].this.name.upper() != "ROW" or sets[0].this.table \
                or not isinstance(sets[0].expression, exp.Column) or sets[0].expression.table:
            return None
        pairs = _pairs(symbols, scope, schema, tree.this.name, sets[0].expression.name)
        if pairs is None:
            return None
        tree.set("expressions", [exp.EQ(this=exp.column(column), expression=exp.column(field, table=record))
                                 for column, (record, field) in pairs])
        return tree.sql(dialect="oracle")
    return None


def _pairs(symbols, scope: str, schema, table: str, record: str) -> list[tuple[str, tuple[str, str]]] | None:
    """Each column of `table` with the record field that goes into it, by position; None unless `record` is a
    record whose fields are as many as the table's columns."""
    from .gen_java.types import record_columns

    symbol = symbols.resolve(scope, record)
    resolved = (symbol.type.resolved or "") if symbol is not None and symbol.type is not None else ""
    columns = list((schema.columns(table) or {}).keys())
    if symbol is None or symbol.kind not in ("variable", "parameter") or not resolved.upper().startswith("RECORD("):
        return None
    fields = [name for name, _ in record_columns(resolved)]
    if not columns or len(fields) != len(columns):
        return None   # Oracle refuses the statement; so does the converter, with its own reason
    return [(column, (record, field)) for column, field in zip(columns, fields)]


def key_guards(tree: exp.Expression, registry) -> list[dict[str, str]]:
    """Take out of an UPDATE's SET each key column its WHERE pins to one value, and say what to check instead.

    Only `key = <value>` conjuncts at the top of the WHERE pin a key, and only a value that names no column of the
    table is one value. A SET left with nothing but key columns is left alone: the converter refuses it, as before.
    """
    if not isinstance(tree, exp.Update) or not isinstance(tree.this, exp.Table) or registry is None:
        return []
    meta = registry.get(tree.this.name)
    if meta is None:
        return []
    keys = {c.lower() for c in meta.primary_key}
    columns = {c.lower() for c in meta.columns} if getattr(meta, "columns", None) else set()
    where = tree.args.get("where")
    pinned: dict[str, exp.Expression] = {}
    for conjunct in (where.this.flatten() if where is not None and isinstance(where.this, exp.And)
                     else [where.this] if where is not None else []):
        if isinstance(conjunct, exp.EQ) and isinstance(conjunct.this, exp.Column) \
                and conjunct.this.name.lower() in keys \
                and not any(c.name.lower() in columns and not c.table for c in conjunct.expression.find_all(exp.Column)):
            pinned.setdefault(conjunct.this.name.lower(), conjunct.expression)
    moves = [s for s in tree.expressions if isinstance(s, exp.EQ) and isinstance(s.this, exp.Column)
             and s.this.name.lower() in keys]
    if not moves or len(moves) == len(tree.expressions) or any(m.this.name.lower() not in pinned for m in moves):
        return []
    guards = []
    for move in moves:
        guards.append({"column": move.this.name, "value": move.expression.sql(dialect="oracle"),
                       "where": pinned[move.this.name.lower()].sql(dialect="oracle")})
        move.pop()
    return guards
