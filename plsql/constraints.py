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

import re

import sqlglot
from sqlglot import exp

from .dynamic import TRUNCATE_AS_DELETE
from .identity import RETURNING_INTO
from .ir import model as M
from .limits import Constraints
from .source import strip_ansi
from .symbols import ForeignKey, OracleSchema, SymbolTable
from .triggers import _declaration, _declare

PREFIX = "v_fk_"


def rewrite(program: M.Program, constraints: Constraints | None, schema: OracleSchema | None,
            symbols: SymbolTable | None = None) -> None:
    if schema is None or not (schema.checks or schema.foreign_keys or schema.uniques or schema.not_null):
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
    if statement.kind == "DynamicSql":
        _dynamic(statement, decided, schema)
        return []
    if statement.kind != "SqlOperation":
        return []
    kind = (statement.sql_kind or "").upper()
    if kind == "DELETE":
        return _children(statement, decided, schema, routine, counter)
    if kind not in ("INSERT", "UPDATE"):
        return []
    original = statement.original_sql or ""
    returning = RETURNING_INTO.search(original)
    try:
        tree = sqlglot.parse_one(original[:returning.start()] if returning else original, dialect="oracle")
    except Exception as error:
        _unread(statement, kind, error, decided, schema)
        return []
    table = _table(tree)
    if table is None:
        return []
    checks = schema.checks.get(table, [])
    keys = schema.foreign_keys.get(table, [])
    key = set(schema.primary_key(table))
    # the key is the target's too: ScalarDB refuses a NULL or a second row with it. Anything else it does not keep
    uniques = [(n, c) for n, c in schema.uniques.get(table, []) if not set(c) <= key]
    not_null = sorted(schema.not_null.get(table, set()) - key)
    written = _written(tree, kind, table, schema)
    if kind == "UPDATE":
        # a constraint on columns the UPDATE leaves alone holds as it did: the stored row already met it (#148 M1)
        touched = set(written or {})
        checks = [(n, c) for n, c in checks if _check_columns(c) is None or _check_columns(c) & touched]
        keys = [k for k in keys if set(k.columns) & touched]
        uniques = [(n, c) for n, c in uniques if set(c) & touched]
        not_null = [c for c in not_null if c in touched]
    constrained = [n for n, _ in checks] + [k.name for k in keys] + [n for n, _ in uniques]
    if not constrained and not not_null:
        return []
    if not decided.decided(table):
        # NOT NULL alone does not make the table undecided: nearly every table has one, and guarding it is part of
        # deciding the table (#148 M1). CHECK, FOREIGN KEY and UNIQUE do
        if constrained:
            statement.add("INFO", "CONSTRAINT_UNDECIDED",
                          f"{table} には CHECK / FOREIGN KEY / UNIQUE（{', '.join(constrained)}）があるが移行先には無い。"
                          f"書く側で guard するかは決定である（limits.yaml constraints.enforce）")
        return []
    if written is None:
        statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                      f"{table} の制約を guard できない: 書く値がこの文から読めない"
                      f"（INSERT … SELECT、または列の並びが DDL と合わない）")
        return []
    names = _plsql_names(routine)
    stored = set(schema.columns(table) or {})
    out: list[M.Statement] = []
    guarded: list[str] = []
    # NOT NULL first: Oracle reports ORA-01400 for a row that also breaks a CHECK (#148 M1)
    for column in not_null:
        if kind == "UPDATE" and column not in written:
            continue   # an UPDATE that does not set the column leaves it as it is
        value = written.get(column, exp.Null())   # a column the INSERT leaves out (and has no DEFAULT) is NULL
        if isinstance(value, exp.Literal) and (not value.is_string or value.this != ""):
            continue   # a number or a non-empty string ('' is NULL in Oracle)
        if _reads_stored(value, stored, names):
            statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                          f"NOT NULL {column} の値が表の値を読む式である。書く前に評価できない")
            continue
        counter[0] += 1
        code, verb = (-1400, "insert") if kind == "INSERT" else (-1407, "update")
        out.append(_refusal(statement, f"nn{counter[0]}", f"{value.sql(dialect='oracle', normalize_functions=False)} IS NULL",
                            code, f'ORA-{abs(code):05d}: cannot {verb} NULL into ("{table.upper()}"."{column.upper()}")'))
        guarded.append(f"NOT NULL {column}")
    for name, columns in uniques:
        if kind == "UPDATE" and not set(columns) & set(written):
            continue
        statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                      f"UNIQUE {name}（{', '.join(columns)}）は守らない: 重複を知るには書く前に同じ値の行を読む必要がある"
                      f"（索引か走査）。Oracle は ORA-00001 で断る（#148）")
    for name, condition in checks:
        try:
            check = sqlglot.parse_one(condition, dialect="oracle")
        except Exception as error:
            # skipping it silently left the table unguarded with nobody told (#161, maintainability L3)
            statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                          f"CHECK {name}（{condition}）を読めない（{_reason(error)}）。書く前に評価していない")
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


def _children(statement: M.SqlOperation, decided: Constraints, schema: OracleSchema,
              routine: M.Routine | None = None, counter: list[int] | None = None) -> list[M.Statement]:
    """A DELETE of a table other tables' FOREIGN KEYs point at: Oracle refuses it while a child row exists (ORA-02292).

    For a table the project decided to enforce, the children are counted before the delete when the DELETE names
    the parent's key the child refers to (`DELETE FROM customers WHERE customer_id = p_id`), and the delete is
    refused with ORA-02292 as Oracle does (#154):

        SELECT COUNT(*) INTO v_fk_1 FROM orders WHERE customer_id = p_id;
        IF v_fk_1 > 0 THEN RAISE_APPLICATION_ERROR(-2292, ...); END IF;

    Any other DELETE (a range, a key the child does not refer to), a dynamic one, and a key with ON DELETE CASCADE /
    SET NULL -- what Oracle does to the children, not a refusal -- are reported, not guarded (#148 M1)."""
    try:
        tree = sqlglot.parse_one(statement.original_sql or "", dialect="oracle")
    except Exception as error:
        _unread(statement, "DELETE", error, decided, schema)
        return []
    target = tree.this if isinstance(tree, exp.Delete) else None
    if not isinstance(target, exp.Table):
        return []
    table = target.name.lower()
    children = [(child, key) for child, keys in schema.foreign_keys.items() for key in keys if key.parent == table]
    if not children:
        return []
    named = ", ".join(f"{child}.{key.name}" for child, key in children)
    if not (decided.decided(table) or any(decided.decided(child) for child, _ in children)):
        statement.add("INFO", "CONSTRAINT_UNDECIDED",
                      f"{table} を指す FOREIGN KEY（{named}）が移行先には無い。子のある行の DELETE を Oracle は断る"
                      f"（ORA-02292）。書く側で守るかは決定である（limits.yaml constraints.enforce）")
        return []
    stored = set(schema.columns(table) or {})
    equalities = _key_equalities(tree, routine, stored) if routine is not None and counter is not None else None
    out: list[M.Statement] = []
    guarded, unguarded = [], []
    for child, key in children:
        parent_columns = key.parent_columns or tuple(schema.primary_key(table))
        if key.on_delete:
            unguarded.append(f"{child}.{key.name}（ON DELETE {key.on_delete}: Oracle は子の行を"
                             f"{'消す' if key.on_delete == 'CASCADE' else 'NULL にする'}が、移行先では何もしない）")
            continue
        if equalities is None or not parent_columns or not set(parent_columns) <= set(equalities) \
                or len(parent_columns) != len(key.columns):
            unguarded.append(f"{child}.{key.name}（" + ("動的 SQL の文の前には guard を置かない" if routine is None
                                                    else "消す行をこの文のキーの等号から読めない") + "）")
            continue
        counter[0] += 1
        flag = f"fk{counter[0]}"
        variable = _free_name(routine)
        where = " AND ".join(f"{column} = {equalities[parent].sql(dialect='oracle', normalize_functions=False)}"
                             for column, parent in zip(key.columns, parent_columns))
        count = M.SqlOperation(id=f"{statement.id}{flag}", kind="SqlOperation", source_range=statement.source_range,
                               sql_kind="SELECT", cardinality="EXACTLY_ONE",
                               original_sql=f"SELECT COUNT(*) FROM {child} WHERE {where}", into_targets=[variable])
        count.add("INFO", "CONSTRAINT_GUARD",
                  f"FOREIGN KEY {key.name} の子（{child}）を消す前に数える。あれば ORA-02292 を投げる"
                  f"（limits.yaml constraints.enforce: {decided.why(table) or decided.why(child)}）")
        routine.declarations.append(M.Declaration(id=f"{routine.id}#decl-{variable}", kind="Declaration",
                                                  name=variable, source_range=statement.source_range,
                                                  type=M.TypeRef(oracle="NUMBER", resolved="NUMBER",
                                                                 origin="column-type")))
        out.append(count)
        out.append(_refusal(statement, flag, f"{variable} > 0", -2292,
                            f"ORA-02292: integrity constraint ({key.name.upper()}) violated - child record found"))
        guarded.append(f"{child}.{key.name}")
    if guarded:
        statement.add("INFO", "CONSTRAINT_GUARD",
                      f"{table} を指す FOREIGN KEY（{', '.join(guarded)}）: 消す前に子の行を数え、あれば Oracle と同じ"
                      f" ORA-02292 で断る（#154）")
    if unguarded:
        statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                      f"{table} を指す FOREIGN KEY（{', '.join(unguarded)}）: 子のある行の DELETE を断らない。"
                      f"Oracle は ORA-02292 で断る（#148）")
    return out


def _key_equalities(tree: exp.Delete, routine: M.Routine, stored: set[str]) -> dict[str, exp.Expression] | None:
    """`WHERE a = p_a AND b = 3`: each column's value, when the WHERE is nothing but such equalities on PL/SQL values
    and literals. None otherwise -- which rows go is then not something a count by key can say."""
    where = tree.args.get("where")
    if where is None:
        return None
    names = _plsql_names(routine)
    out: dict[str, exp.Expression] = {}
    for term in _conjuncts(where.this):
        if not isinstance(term, exp.EQ):
            return None
        column, value = term.this, term.expression
        if not isinstance(column, exp.Column):
            column, value = value, column
        if not isinstance(column, exp.Column) or not (
                isinstance(value, exp.Literal) or
                # a name that is also a column of the table is the column in Oracle, not the variable
                (isinstance(value, exp.Column) and not value.table and value.name.lower() in names
                 and value.name.lower() not in stored) or
                isinstance(value, (exp.Placeholder, exp.Parameter))):
            return None
        out[column.name.lower()] = value
    return out


def _conjuncts(e: exp.Expression) -> list[exp.Expression]:
    while isinstance(e, exp.Paren):
        e = e.this
    if isinstance(e, exp.And):
        return _conjuncts(e.this) + _conjuncts(e.expression)
    return [e]


def _dynamic(statement: M.DynamicSql, decided: Constraints, schema: OracleSchema) -> None:
    """#148 H1: a folded `EXECUTE IMMEDIATE 'INSERT ...'` writes the table as the static INSERT does. No guard is put
    in front of it (the generator writes the variant where the EXECUTE IMMEDIATE is, chosen at run time), so a
    decided table is said to be unguarded, and an undecided one undecided -- as for a static write."""
    for variant in statement.variant_statements or []:
        kind = (variant.sql_kind or "").upper()
        if kind == "DELETE" and any(d.code == TRUNCATE_AS_DELETE for d in variant.diagnostics):
            _truncated_parent(variant, schema)
            continue
        if kind == "DELETE":
            _children(variant, decided, schema)
            continue
        if kind not in ("INSERT", "UPDATE"):
            continue
        try:
            tree = sqlglot.parse_one(variant.original_sql or "", dialect="oracle")
        except Exception as error:
            _unread(variant, kind, error, decided, schema)
            continue
        table = _table(tree)
        if table is None:
            continue
        key = set(schema.primary_key(table))
        names = [n for n, _ in schema.checks.get(table, [])] + [k.name for k in schema.foreign_keys.get(table, [])] \
            + [n for n, c in schema.uniques.get(table, []) if not set(c) <= key]
        if decided.decided(table) and schema.not_null.get(table, set()) - key:
            names.append("NOT NULL")
        if not names:
            continue
        if not decided.decided(table):
            variant.add("INFO", "CONSTRAINT_UNDECIDED",
                        f"{table} には CHECK / FOREIGN KEY / UNIQUE（{', '.join(names)}）があるが移行先には無い。書く側で guard "
                        f"するかは決定である（limits.yaml constraints.enforce）")
        else:
            variant.add("WARN", "CONSTRAINT_NOT_GUARDED",
                        f"{table} の制約（{', '.join(names)}）を guard していない: 動的 SQL の文の前には"
                        f" guard を置かない（#148）。静的な文に書き直せば置く")


def _truncated_parent(variant: M.SqlOperation, schema: OracleSchema) -> None:
    """Oracle refuses to TRUNCATE a table an enabled FOREIGN KEY points at (ORA-02266), child rows or not; the
    DELETE it was made (#154) is not refused. Said, whether the project decided the table or not."""
    try:
        tree = sqlglot.parse_one(variant.original_sql or "", dialect="oracle")
    except Exception:
        return
    table = tree.this.name.lower() if isinstance(tree, exp.Delete) and isinstance(tree.this, exp.Table) else None
    children = [f"{child}.{key.name}" for child, keys in schema.foreign_keys.items() for key in keys
                if key.parent == table]
    if children:
        variant.add("WARN", "CONSTRAINT_NOT_GUARDED",
                    f"{table} を指す FOREIGN KEY（{', '.join(children)}）がある。Oracle はこの表の TRUNCATE を"
                    f" ORA-02266 で断るが、移行先の DELETE は断らない（#154）")


# the table a write names, read from the text when sqlglot cannot read the statement
_TARGET = re.compile(r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE(?:\s+FROM)?)\s+(?:"?\w+"?\s*\.\s*)?"?(\w+)', re.IGNORECASE)


def _reason(error: Exception) -> str:
    text = strip_ansi(str(error)).strip()
    return (text.splitlines()[0] if text else type(error).__name__)[:160]


def _unread(statement: M.SqlOperation, kind: str, error: Exception, decided: Constraints,
            schema: OracleSchema) -> None:
    """A write the guards could not parse. It used to be skipped without a word, so a table the project decided to
    guard lost its guard and nobody knew (#161, maintainability L3). Said as a guard not written (decided table) or
    a decision not made (undecided), naming the constraints the table has."""
    match = _TARGET.match(statement.original_sql or "")
    table = match.group(1).lower() if match else None
    reason = _reason(error)
    if table is None:
        if decided.enforce:
            statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                          f"この文を読めず（{reason}）、書く表が分からない。制約の guard を置いていない")
        return
    if kind == "DELETE":
        children = [(child, key) for child, keys in schema.foreign_keys.items() for key in keys if key.parent == table]
        names = [f"{child}.{key.name}" for child, key in children]
        undecided = not (decided.decided(table) or any(decided.decided(child) for child, _ in children))
    else:
        key = set(schema.primary_key(table))
        names = [n for n, _ in schema.checks.get(table, [])] + [k.name for k in schema.foreign_keys.get(table, [])] \
            + [n for n, c in schema.uniques.get(table, []) if not set(c) <= key]
        undecided = not decided.decided(table)
        if not undecided and schema.not_null.get(table, set()) - key:
            names.append("NOT NULL")
    if not names:
        return
    if undecided:
        statement.add("INFO", "CONSTRAINT_UNDECIDED",
                      f"{table} {'を指す FOREIGN KEY' if kind == 'DELETE' else 'には CHECK / FOREIGN KEY / UNIQUE'}"
                      f"（{', '.join(names)}）があるが移行先には無い。書く側で guard するかは決定である"
                      f"（limits.yaml constraints.enforce）")
    else:
        statement.add("WARN", "CONSTRAINT_NOT_GUARDED",
                      f"{table} の制約（{', '.join(names)}）を guard していない: この文を読めない（{reason}）")


def _check_columns(condition: str) -> set[str] | None:
    """The columns a CHECK reads; None when it does not parse (then it counts as touched, and is not waved through)."""
    try:
        return {c.name.lower() for c in sqlglot.parse_one(condition, dialect="oracle").find_all(exp.Column)}
    except Exception:
        return None


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
