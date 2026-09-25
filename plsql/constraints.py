"""#50: CHECK / FOREIGN KEY guards for tables the project decided to enforce (`constraints.enforce`).

ScalarDB has neither constraint. A CHECK is a condition on the row being written, and the writer has every value
in hand, so it is evaluated before the write:

    INSERT INTO bulk_target VALUES (v_ids(i), v_names(i), v_sals(i));       -- CHECK (salary < 15000)

    -> IF NOT (v_sals(i) < 15000) THEN RAISE_APPLICATION_ERROR(-2290, ...); END IF;
       INSERT INTO bulk_target VALUES (v_ids(i), v_names(i), v_sals(i));

A FOREIGN KEY is a row that has to exist, so it is a keyed read of the parent before the write:

    INSERT INTO employees (..., job_id, ...) VALUES (..., p_job, ...);       -- REFERENCES jobs

    -> SELECT job_id INTO v_fk_1 FROM jobs WHERE job_id = p_job;             -- at most one; NULL when absent
       IF p_job IS NOT NULL AND fk1%NOTFOUND THEN RAISE_APPLICATION_ERROR(-2291, ...); END IF;
       INSERT ...

The error codes are Oracle's (ORA-02290 / ORA-02291), so a handler bound with `PRAGMA EXCEPTION_INIT` still
matches. What this does not do: a CHECK an UPDATE cannot evaluate because it reads a column the statement does
not write, a value that reads the stored row (an RMW nobody split), and `INSERT ... SELECT` are reported
(`CONSTRAINT_NOT_GUARDED`) rather than guessed. NULL passes a CHECK and skips a FOREIGN KEY, as in Oracle.

The guard is a different transaction shape from the constraint: the parent read joins the transaction's read
set, so a concurrent delete of the parent surfaces as a commit conflict instead of being blocked. That is the
optimistic-control trade the project already made (`rowLocks.optimistic`).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .identity import RETURNING_INTO
from .ir import model as M
from .limits import Constraints
from .symbols import ForeignKey, OracleSchema, SymbolTable
from .triggers import _declaration, _declare

PREFIX = "v_fk_"


def rewrite(program: M.Program, constraints: Constraints | None, schema: OracleSchema | None,
            symbols: SymbolTable | None = None) -> None:
    if schema is None or not (schema.checks or schema.foreign_keys):
        return
    decided = constraints or Constraints()
    for module in program.modules:
        for routine in module.routines:
            before = len(routine.declarations)
            counter = [0]
            routine.body = _sequence(routine.body, routine, decided, schema, counter)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, routine, decided, schema, counter)
            _declare(symbols, routine, routine.declarations[before:])


def _sequence(statements: list[M.Statement], routine: M.Routine, decided: Constraints,
              schema: OracleSchema, counter: list[int]) -> list[M.Statement]:
    out: list[M.Statement] = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, decided, schema, counter))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, decided, schema, counter)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, routine, decided, schema, counter)
        out.extend(_guards(statement, routine, decided, schema, counter))
        out.append(statement)
    return out


def _guards(statement: M.Statement, routine: M.Routine, decided: Constraints, schema: OracleSchema,
            counter: list[int]) -> list[M.Statement]:
    if statement.kind != "SqlOperation":
        return []
    kind = (statement.sql_kind or "").upper()
    if kind not in ("INSERT", "UPDATE"):
        return []
    original = statement.original_sql or ""
    returning = RETURNING_INTO.search(original)
    try:
        tree = sqlglot.parse_one(original[:returning.start()] if returning else original, dialect="oracle")
    except Exception:
        return []
    table = _table(tree)
    if table is None:
        return []
    checks = schema.checks.get(table, [])
    keys = schema.foreign_keys.get(table, [])
    if not checks and not keys:
        return []
    if not decided.decided(table):
        statement.add("INFO", "CONSTRAINT_UNDECIDED",
                      f"{table} には CHECK / FOREIGN KEY（{', '.join([n for n, _ in checks] + [k.name for k in keys])}）"
                      f"があるが移行先には無い。書く側で guard するかは決定である（limits.yaml constraints.enforce）")
        return []
    written = _written(tree, kind, table, schema)
    if written is None:
        statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                      f"{table} の CHECK / FOREIGN KEY を guard できない: 書く値がこの文から読めない"
                      f"（INSERT … SELECT、または列の並びが DDL と合わない）")
        return []
    names = _plsql_names(routine)
    stored = set(schema.columns(table) or {})
    out: list[M.Statement] = []
    guarded: list[str] = []
    for name, condition in checks:
        try:
            check = sqlglot.parse_one(condition, dialect="oracle")
        except Exception:
            continue
        columns = {c.name.lower() for c in check.find_all(exp.Column)}
        if kind == "UPDATE" and not columns <= set(written):
            statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                          f"CHECK {name} はこの UPDATE が書かない列（{', '.join(sorted(columns - set(written)))}）を読む。"
                          f"書く前に評価できない")
            continue
        if any(_reads_stored(written.get(c), stored, names) for c in columns if c in written):
            statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                          f"CHECK {name} の値が表の値を読む式である（RMW を割っていない）。書く前に評価できない")
            continue
        for column in list(check.find_all(exp.Column)):
            column.replace(written.get(column.name.lower(), exp.Null()).copy())   # INSERT で省いた列は NULL
        counter[0] += 1
        out.append(_refusal(statement, f"ck{counter[0]}", f"NOT ({check.sql(dialect='oracle', normalize_functions=False)})", -2290,
                            f"ORA-02290: check constraint ({name.upper()}) violated"))
        guarded.append(f"CHECK {name}")
    for key in keys:
        if not set(key.columns) <= set(written) or not key.parent_columns:
            continue   # UPDATE が触らない列は変わらないので有効なまま。INSERT で省いた列は NULL で、FK は見ない
        values = [written[c] for c in key.columns]
        if all(isinstance(v, exp.Null) for v in values):
            continue
        if any(_reads_stored(v, stored, names) for v in values):
            statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                          f"FOREIGN KEY {key.name} の値が表の値を読む式である。書く前に親を読めない")
            continue
        counter[0] += 1
        flag = f"fk{counter[0]}"
        variable = _free_name(routine)
        where = " AND ".join(f"{parent} = {value.sql(dialect='oracle', normalize_functions=False)}"
                             for parent, value in zip(key.parent_columns, values))
        read = M.SqlOperation(id=f"{statement.id}{flag}", kind="SqlOperation", source_range=statement.source_range,
                              sql_kind="SELECT", cardinality="AT_MOST_ONE", not_found_flag=flag,
                              original_sql=f"SELECT {key.parent_columns[0]} FROM {key.parent} WHERE {where}",
                              into_targets=[variable])
        read.add("INFO", "CONSTRAINT_GUARD",
                 f"FOREIGN KEY {key.name} の親（{key.parent}）を書く前に読む。無ければ ORA-02291 を投げる"
                 f"（limits.yaml constraints.enforce: {decided.why(table)}）")
        routine.declarations.append(_declaration(routine, variable, key.parent, key.parent_columns[0], schema,
                                                 statement))
        given = " AND ".join(f"{v.sql(dialect='oracle', normalize_functions=False)} IS NOT NULL" for v in values if not isinstance(v, exp.Null))
        out.append(read)
        out.append(_refusal(statement, flag, f"{given} AND {flag}%NOTFOUND", -2291,
                            f"ORA-02291: integrity constraint ({key.name.upper()}) violated - parent key not found"))
        guarded.append(f"FOREIGN KEY {key.name}")
    if guarded:
        statement.add("INFO", "CONSTRAINT_GUARD",
                      f"{table} の {', '.join(guarded)} を書く前に評価する。移行先に制約は無く、違反は Oracle と同じ番号の"
                      f"例外になる（limits.yaml constraints.enforce: {decided.why(table)}）")
    return out


def _refusal(statement: M.Statement, tag: str, condition: str, code: int, message: str) -> M.If:
    raise_ = M.Raise(id=f"{statement.id}{tag}raise", kind="Raise", source_range=statement.source_range,
                     error_code=code, message=f"'{message}'")
    return M.If(id=f"{statement.id}{tag}if", kind="If", source_range=statement.source_range,
                branches=[M.Branch(condition=condition, body=[raise_])])


def _table(tree: exp.Expression) -> str | None:
    if isinstance(tree, exp.Insert):
        target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        return target.name.lower() if isinstance(target, exp.Table) else None
    if isinstance(tree, exp.Update):
        return tree.this.name.lower() if isinstance(tree.this, exp.Table) else None
    return None


def _written(tree: exp.Expression, kind: str, table: str, schema: OracleSchema) -> dict[str, exp.Expression] | None:
    """The value each column gets, as the statement spells it. None when the statement does not say."""
    if kind == "UPDATE":
        out: dict[str, exp.Expression] = {}
        for assignment in tree.expressions or []:
            if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Column):
                out[assignment.this.name.lower()] = assignment.expression
        return out
    values = tree.expression
    tuples = values.expressions if isinstance(values, exp.Values) else []
    if len(tuples) != 1 or not isinstance(tuples[0], exp.Tuple):
        return None
    if isinstance(tree.this, exp.Schema):
        columns = [c.name.lower() for c in tree.this.expressions]
    else:
        columns = list(schema.columns(table) or {})   # no column list: the DDL's order
    row = tuples[0].expressions
    if len(columns) != len(row):
        return None
    return dict(zip(columns, row))


def _plsql_names(routine: M.Routine) -> set[str]:
    return {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}


def _reads_stored(value: exp.Expression | None, stored: set[str], names: set[str]) -> bool:
    """A bare name in a VALUES / SET expression is a column of the table unless the routine declares it."""
    if value is None:
        return False
    return any(isinstance(n, exp.Column) and not n.table and n.name.lower() in stored
               and n.name.lower() not in names for n in value.walk())


def _free_name(routine: M.Routine) -> str:
    used = {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}
    index = 1
    while f"{PREFIX}{index}" in used:
        index += 1
    return f"{PREFIX}{index}"
