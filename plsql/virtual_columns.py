"""A write to a virtual column, raised as Oracle raises it (#172, the language reference's examples 5-42 / 5-43).

    CREATE TABLE plch_departure (..., expected DATE GENERATED ALWAYS AS (departure_time + delay/24/60/60) VIRTUAL);

A virtual column is computed on every read and stored nowhere. The target has no such column (the converter leaves
it out of the ScalarDB schema), so there are two things to get right:

* **A write to it is refused by Oracle, whatever the value**, and the generated code refuses it the same way rather
  than storing anything. Measured on Oracle 23.26 Free (2026-09-30):

      INSERT INTO t (..., v) VALUES (..., <any value, NULL too>)    ORA-54013  (also INSERT ... SELECT)
      INSERT INTO t VALUES (<a value for every column>)             ORA-54013  (and `VALUES rec` of t%ROWTYPE)
      UPDATE t SET v = <any value, NULL and DEFAULT too>            ORA-54017  (also SET ROW = rec, MERGE's UPDATE)
      INSERT ... (..., v) VALUES (..., DEFAULT)                      accepted: the column is computed as usual

  The statement becomes `RAISE_APPLICATION_ERROR(-54013, 'ORA-54013: ...')`, so a handler bound with
  `PRAGMA EXCEPTION_INIT` or `WHEN OTHERS` sees what it saw. Oracle refuses before it looks at a row, so the
  refusal does not depend on the data. A `DEFAULT` for the column is taken out of the statement (the target
  computes nothing, and has no column to take it).
* **A read of it cannot be answered from the target.** The ScalarDB registry the capability check uses is told
  which columns are virtual (`mark_registry`), and the converter refuses a statement that names one (or `SELECT *`
  over the table) with VIRTUAL_COLUMN: computing the expression is left to a person, not guessed.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .identity import RETURNING_INTO
from .ir import model as M
from .symbols import OracleSchema

INSERT_REFUSED = (-54013, "ORA-54013: INSERT operation disallowed on virtual columns")
UPDATE_REFUSED = (-54017, "ORA-54017: UPDATE operation disallowed on virtual columns")


def rewrite(program: M.Program, schema: OracleSchema | None) -> None:
    if schema is None or not schema.virtual:
        return
    for module in program.modules:
        for routine in module.routines:
            routine.body = _sequence(routine.body, schema)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, schema)


def mark_registry(registry, schema: OracleSchema | None) -> None:
    """Tell the ScalarDB registry which columns of its tables are virtual in Oracle, so the converter refuses a
    statement that reads one. A Schema Loader file cannot say it; a column it lists anyway is not read either."""
    if registry is None or schema is None:
        return
    for table, columns in schema.virtual.items():
        meta = registry.get(table)
        if meta is not None:
            meta.virtual_columns = dict(columns)


def _sequence(statements: list[M.Statement], schema: OracleSchema) -> list[M.Statement]:
    out: list[M.Statement] = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, schema))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, schema)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, schema)
        out.append(_refused(statement, schema) if statement.kind == "SqlOperation" else statement)
    return out


def _refused(statement: M.SqlOperation, schema: OracleSchema) -> M.Statement:
    kind = (statement.sql_kind or "").upper()
    if kind not in ("INSERT", "UPDATE", "MERGE"):
        return statement
    original = statement.original_sql or ""
    returning = RETURNING_INTO.search(original)
    try:
        tree = sqlglot.parse_one(original[:returning.start()] if returning else original, dialect="oracle")
    except Exception:  # noqa: BLE001 - not ours to report; sqlbridge says why it cannot be parsed
        return statement
    refusal = _refusal(tree, schema)
    if refusal is None:
        if _drop_defaults(tree, schema):
            statement.original_sql = tree.sql(dialect="oracle") + (original[returning.start():] if returning else "")
            statement.add("INFO", "VIRTUAL_COLUMN",
                          "仮想列への DEFAULT を文から外した。Oracle は式で計算し、移行先にはその列が無い（#172）")
        return statement
    code, message = refusal
    raise_ = M.Raise(id=f"{statement.id}virtual", kind="Raise", source_range=statement.source_range,
                     error_code=code, message=f"'{message}'", labels=list(statement.labels))
    raise_.add("INFO", "VIRTUAL_COLUMN",
               f"仮想列に書く文なので、Oracle と同じく {message.split(':')[0]} を上げる（文は実行しない。"
               f"Oracle は値によらず断る。#172）: {' '.join(original.split())[:120]}")
    return raise_


def _virtual(tree: exp.Expression, schema: OracleSchema) -> tuple[str, dict[str, str]] | None:
    target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
    if not isinstance(target, exp.Table):
        return None
    columns = schema.virtual.get(target.name.lower())
    return (target.name.lower(), columns) if columns else None


def _refusal(tree: exp.Expression, schema: OracleSchema) -> tuple[int, str] | None:
    found = _virtual(tree, schema)
    if found is None:
        return None
    table, virtual = found
    if isinstance(tree, exp.Update):
        written = [a.this.name.lower() for a in tree.expressions or []
                   if isinstance(a, exp.EQ) and isinstance(a.this, exp.Column)]
        return UPDATE_REFUSED if set(written) & set(virtual) else None
    if isinstance(tree, exp.Merge):
        for when in (tree.args.get("whens").expressions if tree.args.get("whens") else []):
            then = when.args.get("then")
            if isinstance(then, exp.Update) and any(
                    isinstance(a, exp.EQ) and isinstance(a.this, exp.Column) and a.this.name.lower() in virtual
                    for a in then.expressions or []):
                return UPDATE_REFUSED
            if isinstance(then, exp.Insert) and isinstance(then.this, exp.Tuple) and any(
                    c.name.lower() in virtual for c in then.this.expressions):
                return INSERT_REFUSED
        return None
    if not isinstance(tree, exp.Insert):
        return None
    for column, value in _pairs(tree, table, schema):
        if column in virtual and not _is_default(value):
            return INSERT_REFUSED
    return None


def _pairs(tree: exp.Insert, table: str, schema: OracleSchema) -> list[tuple[str, exp.Expression | None]]:
    """Each column the INSERT writes, with its value when the statement has one row of VALUES (None otherwise:
    `INSERT ... SELECT`, whose value for a named column is never DEFAULT)."""
    if isinstance(tree.this, exp.Schema):
        columns = [c.name.lower() for c in tree.this.expressions]
    else:
        columns = list(schema.tables.get(table, {}))   # no column list: every column of the table, in order
    values = tree.expression
    rows = values.expressions if isinstance(values, exp.Values) else []
    row = rows[0].expressions if len(rows) == 1 and isinstance(rows[0], exp.Tuple) else None
    if row is None or len(row) != len(columns):
        return [(c, None) for c in columns]
    return list(zip(columns, row))


def _is_default(value: exp.Expression | None) -> bool:
    return isinstance(value, exp.Var) and value.name.upper() == "DEFAULT" or \
        isinstance(value, exp.Column) and not value.table and value.name.upper() == "DEFAULT" and not value.this.quoted


def _drop_defaults(tree: exp.Expression, schema: OracleSchema) -> bool:
    """`DEFAULT` given to a virtual column, taken out -- with a column list written out when there was none."""
    if not isinstance(tree, exp.Insert):
        return False
    found = _virtual(tree, schema)
    values = tree.expression
    if found is None or not isinstance(values, exp.Values) or len(values.expressions) != 1:
        return False
    table, virtual = found
    pairs = _pairs(tree, table, schema)
    if not any(c in virtual for c, _ in pairs) or any(v is None for _, v in pairs):
        return False
    kept = [(c, v) for c, v in pairs if c not in virtual]
    target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
    tree.set("this", exp.Schema(this=target.copy(), expressions=[exp.to_identifier(c) for c, _ in kept]))
    tree.set("expression", exp.Values(expressions=[exp.Tuple(expressions=[v.copy() for _, v in kept])]))
    return True
