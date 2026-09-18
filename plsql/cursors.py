"""#11: the two explicit-cursor shapes that are not a scan -- "the first row" and "how many rows".

`docs/plsql-cursor-patterns.md` sorts the corpus's cursors into six forms. A (a scan that only reads) and D
(a scan that writes its own table, refused) are handled. B and C are handled here, and neither is a loop:

* **B. the first row, with a default.** `OPEN c; FETCH c INTO v; IF c%NOTFOUND THEN v := 0; END IF; CLOSE c;`
  is a query ordered by something, its first row, and a value for when there is none. The value is written in
  the original -- in the `%NOTFOUND` branch -- and losing it is the one way this rewrite can be wrong.
* **C. counting the rows.** `OPEN c; LOOP FETCH c; EXIT WHEN c%NOTFOUND; n := n + 1; END LOOP; CLOSE c;`
  is `SELECT COUNT(*)`. `COUNT` returns a row even when nothing matched (`SEM-004`), which is what the
  original did too -- the counter was initialised before the loop -- so no `NO_DATA_FOUND` is introduced.

Both are recognised on the **statement sequence**, not on the cursor: the same cursor used a different way is
a different shape, and a sequence that does anything else is left alone rather than guessed at. What is not
recognised stays as the OPEN / FETCH / CLOSE nodes it was, which the generator refuses -- visibly.
"""

from __future__ import annotations

import dataclasses
import re

import sqlglot
from sqlglot import exp

from .columns import row_cap
from .ir import model as M
from .symbols import OracleSchema, SymbolTable

NOTFOUND = re.compile(r"^\s*(?P<cursor>[\w$#]+)\s*%\s*NOTFOUND\s*$", re.IGNORECASE)
ISOPEN = re.compile(r"^\s*(?P<cursor>[\w$#]+)\s*%\s*ISOPEN\s*$", re.IGNORECASE)


def rewrite(routine: M.Routine, symbols: SymbolTable | None, module: str | None = None,
            schema: OracleSchema | None = None) -> None:
    """Replace the B and C shapes in `routine` with the query each one is, in place.

    `schema` is what decides whether a cursor parameter may be substituted at all: Oracle resolves a name
    inside the cursor's query to a column before it resolves it to the parameter, and only the DDL says
    which names are columns.
    """
    if symbols is None:
        return
    _named_cursor_loops(routine, symbols, module, schema)
    rewritten: set[str] = set()
    routine.body = _sequence(routine.body, routine, symbols, module, schema, rewritten)
    for handler in routine.exception_handlers:
        handler.body = _sequence(handler.body, routine, symbols, module, schema, rewritten)
    if rewritten:
        # `IF c%ISOPEN THEN CLOSE c; END IF;` guarded a cursor that no longer exists. Left in place it is a
        # reference to nothing, and the generator would refuse the handler it sits in.
        routine.body = _drop_isopen(routine.body, rewritten)
        for handler in routine.exception_handlers:
            handler.body = _drop_isopen(handler.body, rewritten)


# --- `FOR r IN c LOOP` --------------------------------------------------------------------------------

NAMED_LOOP = re.compile(r"^\s*[\w$#]+\s+IN\s+(?P<cursor>[\w$#.]+)\s*(?:\((?P<arguments>.*)\))?\s*$",
                        re.IGNORECASE | re.DOTALL)


def _named_cursor_loops(routine: M.Routine, symbols: SymbolTable, module: str | None,
                        schema: OracleSchema | None) -> None:
    """Give `FOR r IN c LOOP` the query the cursor was declared as.

    An inline `FOR r IN (SELECT ...)` already carries its query as a statement (P4-5), and everything
    downstream -- the converter, the capability check, the generator -- works from that node. A named cursor's
    query lives in its declaration instead, which for a package-level cursor is not in this file's IR at all,
    so the loop arrived with nothing to convert and was refused for the shape of its header rather than for
    anything about what it does. Resolving it here puts the two shapes on the same path.

    `FOR UPDATE` in the declaration comes with it. The lock is the reason several of these routines are a
    redesign (`LOCK-001` / `LOCK-002`), and a query that arrived without it would look safe.
    """
    for loop in _loops(routine.body):
        if (loop.loop_kind or "") != "cursor-for" or loop.query is not None or not loop.cursor:
            continue
        match = NAMED_LOOP.match(loop.cursor)
        if match is None:
            continue
        query = _query(match.group("cursor").lower(), _arguments(match.group("arguments")),
                       routine, symbols, module, schema)
        if query is None:
            continue
        loop.query = M.SqlOperation(id=f"{loop.id}#query", kind="SqlOperation",
                                    source_range=loop.source_range, sql_kind="SELECT",
                                    original_sql=query, cardinality="MANY")
        _mark_row_lock(loop.query, query)


def _arguments(written: str | None) -> list[str]:
    """`c(p_status, v_limit)` -> the actuals, in order. Nothing nested here needs a real parser."""
    return [a.strip() for a in (written or "").split(",") if a.strip()]


def _mark_row_lock(node: M.SqlOperation, text: str) -> None:
    from .lower import mark_row_lock   # deferred: `lower` imports this module

    mark_row_lock(node, text)


def _loops(statements: list[M.Statement]):
    for statement in statements:
        if statement.kind == "Loop":
            yield statement
        yield from _loops(getattr(statement, "body", []) or [])
        yield from _loops(getattr(statement, "else_body", []) or [])
        for branch in getattr(statement, "branches", []) or []:
            yield from _loops(branch.body)
        for handler in getattr(statement, "exception_handlers", []) or []:
            yield from _loops(handler.body)


def _sequence(statements: list[M.Statement], routine: M.Routine, symbols: SymbolTable,
              module: str | None, schema: OracleSchema | None,
              rewritten: set[str]) -> list[M.Statement]:
    """One pass over a statement list, replacing any run that matches B or C. Nested bodies go first."""
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, symbols, module, schema, rewritten))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, symbols, module, schema, rewritten)
        # a nested block's handler is a statement sequence like any other (#18)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, routine, symbols, module, schema, rewritten)

    out: list[M.Statement] = []
    index = 0
    while index < len(statements):
        match = _first_row(statements, index, routine, symbols, module, schema) or \
            _count(statements, index, routine, symbols, module, schema) or \
            _chunks(statements, index, routine, symbols, module, schema)
        if match is None:
            out.append(statements[index])
            index += 1
            continue
        replacement, consumed, cursor = match
        out.extend(replacement)
        rewritten.add(cursor.lower())
        index += consumed
    return out


# --- B. the first row ---------------------------------------------------------------------------------

def _first_row(statements: list[M.Statement], index: int, routine: M.Routine, symbols: SymbolTable,
               module: str | None,
               schema: OracleSchema | None) -> tuple[list[M.Statement], int, str] | None:
    """`OPEN c; FETCH c INTO v; [IF c%NOTFOUND THEN ... END IF;] CLOSE c;`

    The `%NOTFOUND` branch is kept as it is written. It is the part that says what the routine does when
    nothing matched, and the generator answers it from a flag the read sets rather than from the value -- a
    row whose column is NULL is not the same as no row.
    """
    run = statements[index:]
    if len(run) < 3 or run[0].kind != "OpenCursor" or run[1].kind != "Fetch":
        return None
    cursor = _cursor_name(run[0].cursor)
    if _cursor_name(run[1].cursor) != cursor or not run[1].into_targets:
        return None
    consumed = 2
    branch: M.Statement | None = None
    if run[consumed].kind == "If" and _guards(run[consumed], cursor, NOTFOUND):
        branch = run[consumed]
        consumed += 1
    if consumed >= len(run) or run[consumed].kind != "CloseCursor" or \
            _cursor_name(run[consumed].cursor) != cursor:
        return None
    consumed += 1
    query = _query(cursor, list(run[0].arguments), routine, symbols, module, schema)
    if query is None:
        return None
    operation = _operation(run[1], routine, _limit_one(query), run[1].into_targets)
    if operation is None:
        return None
    operation.cardinality = "AT_MOST_ONE"
    operation.not_found_flag = cursor
    return ([operation] + ([branch] if branch is not None else []), consumed, cursor)


def _limit_one(sql: str) -> str:
    """The same query, first row only. The order is the cursor's; only the row count is added."""
    tree = sqlglot.parse_one(sql, dialect="oracle")
    if tree.args.get("limit") is None:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(1)))
    return tree.sql(dialect="oracle")


# --- C. counting --------------------------------------------------------------------------------------

def _count(statements: list[M.Statement], index: int, routine: M.Routine, symbols: SymbolTable,
           module: str | None,
           schema: OracleSchema | None) -> tuple[list[M.Statement], int, str] | None:
    """`OPEN c; LOOP FETCH c INTO ...; EXIT WHEN c%NOTFOUND; n := n + 1; END LOOP; CLOSE c;`

    The loop body has to be exactly those three things. A body that also read the fetched values is doing
    something the count does not do, and rewriting it would lose that.
    """
    run = statements[index:]
    if len(run) < 3 or run[0].kind != "OpenCursor" or run[1].kind != "Loop" or run[2].kind != "CloseCursor":
        return None
    cursor = _cursor_name(run[0].cursor)
    if _cursor_name(run[2].cursor) != cursor or (run[1].loop_kind or "basic") != "basic":
        return None
    counter = _counted(run[1], cursor)
    if counter is None:
        return None
    # the loop counts, but the FETCH also assigns: after the loop the INTO targets hold the last row Oracle
    # read. The rewrite drops those assignments, so it is only the same routine if nobody reads them again.
    fetched = list(run[1].body[0].into_targets or [])
    if fetched and _reads_outside(routine, run[:3], fetched):
        return None
    query = _query(cursor, list(run[0].arguments), routine, symbols, module, schema)
    if query is None:
        return None
    if row_cap(sqlglot.parse_one(query, dialect="oracle"))[0] is not None:
        # the cursor counts at most n rows; COUNT(*) returns one row, so the cap would no longer apply to
        # anything and the count would be of every matching row instead
        return None
    operation = _operation(run[1], routine, _count_sql(query), [counter])
    if operation is None:
        return None
    operation.cardinality = "EXACTLY_ONE"
    operation.add("INFO", "CUR_COUNT",
                  f"cursor {cursor} counted its rows; replaced by COUNT(*). COUNT returns a row even when "
                  f"nothing matched (SEM-004), which is what the loop did -- {counter} was initialised before "
                  f"it -- so no NO_DATA_FOUND is introduced")
    return ([operation], 3, cursor)


def _counted(loop: M.Loop, cursor: str) -> str | None:
    """The variable the loop counts into, when the loop does nothing else."""
    body = list(loop.body)
    if len(body) != 3 or body[0].kind != "Fetch" or _cursor_name(body[0].cursor) != cursor:
        return None
    if body[1].kind != "Exit" or not _names(body[1].condition, cursor, NOTFOUND):
        return None
    if body[2].kind != "Assignment" or not body[2].target:
        return None
    increment = re.fullmatch(rf"\s*{re.escape(body[2].target)}\s*\+\s*1\s*", body[2].expression or "",
                             re.IGNORECASE)
    return body[2].target if increment else None


def _count_sql(sql: str) -> str:
    """`SELECT COUNT(*)` over the same rows. The cursor's ORDER BY does not survive: it counts the same."""
    tree = sqlglot.parse_one(sql, dialect="oracle")
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return sql
    select.set("expressions", [exp.Count(this=exp.Star())])
    select.set("order", None)
    return select.sql(dialect="oracle")


# --- E. 分割読み（`FETCH ... BULK COLLECT INTO v LIMIT n`）-----------------------------------------

def _chunks(statements: list[M.Statement], index: int, routine: M.Routine, symbols: SymbolTable,
            module: str | None,
            schema: OracleSchema | None) -> tuple[list[M.Statement], int, str] | None:
    """`OPEN c; LOOP FETCH c BULK COLLECT INTO v LIMIT n; ... END LOOP; CLOSE c;`

    Oracle のこの形は**メモリを守るための分割読み**である——n 件ずつ取り、取れなくなったら抜ける。
    移行先に跨トランザクションの cursor は無いので、行は**先にまとめて読む**しかない（走査行数の
    上限が守るのはそこで、`--limits` が決める）。残るのは「n 件ずつ配る」というループの形である。

    **だから `n` の意味は変わる。** 元は「1 回に読み込む件数」、移した後は「1 回に配る件数」である。
    読む量を決めていたものが、配る量しか決めなくなる——**隠さずに `BULK_CHUNKED` として残す**。

    本体はそのまま残す。`EXIT WHEN v.COUNT = 0` も残る: 配る側は空の塊を渡さないので発火しないが、
    元に書いてあるものを落とす理由が無い。ループの後で `v` を読んでいたら書き換えない——Oracle は
    最後に取った（空の）塊を残すので、そこまで同じにはできない。
    """
    run = statements[index:]
    if len(run) < 3 or run[0].kind != "OpenCursor" or run[1].kind != "Loop" or run[2].kind != "CloseCursor":
        return None
    cursor = _cursor_name(run[0].cursor)
    if _cursor_name(run[2].cursor) != cursor or (run[1].loop_kind or "basic") != "basic":
        return None
    body = list(run[1].body)
    if not body or body[0].kind != "Fetch" or _cursor_name(body[0].cursor) != cursor:
        return None
    fetch = body[0]
    if not fetch.bulk_limit or len(fetch.into_targets or []) != 1:
        return None   # 分割読みでない、または 2 つ以上の配列へ取る形。位置で対応させられない
    collection = fetch.into_targets[0]
    if _reads_outside(routine, run[:3], [collection]):
        return None
    query = _query(cursor, list(run[0].arguments), routine, symbols, module, schema)
    if query is None:
        return None
    operation = _operation(run[1], routine, query, [])
    if operation is None:
        return None
    operation.cardinality = "MANY"
    loop = M.Loop(id=run[1].id, kind="Loop", source_range=run[1].source_range,
                  loop_kind="cursor-for", variable=collection, chunk=fetch.bulk_limit,
                  query=operation, body=body[1:],
                  cursor=f"{collection} IN chunks({query}, {fetch.bulk_limit})")
    loop.add("INFO", "BULK_CHUNKED",
             f"cursor {cursor} は {fetch.bulk_limit} 件ずつ読んでいた。移行先に跨トランザクションの "
             f"cursor は無いので行は先にまとめて読み、{fetch.bulk_limit} 件ずつ**配る**ループにした。"
             f"読み込む量を決めていた値が、配る量しか決めなくなる——メモリを守るのは走査行数の上限"
             f"（--limits）である")
    return ([loop], 3, cursor)


# --- shared -------------------------------------------------------------------------------------------

def _reads_outside(routine: M.Routine, run: list[M.Statement], names: list[str]) -> bool:
    """Whether any of `names` is mentioned by a statement of `routine` that is not part of `run`.

    Deliberately blunt: every string a statement carries is searched, so a name that is only written to, or
    that a nested declaration happens to reuse, counts as a read. The answer decides whether a rewrite is
    made at all, and the cost of the two answers is not the same -- declining leaves the cursor visible for
    a person to look at, while rewriting on a wrong answer silently drops an assignment.
    """
    skip: set[int] = set()
    _ids(run, skip)
    return _mentions(routine.body, names, skip) or \
        any(_mentions(handler.body, names, skip) for handler in routine.exception_handlers)


def _ids(statements: list[M.Statement], out: set[int]) -> None:
    for statement in statements:
        out.add(id(statement))
        for nested in _nested(statement):
            _ids(nested, out)


def _nested(statement: M.Statement):
    for attribute in ("body", "else_body"):
        yield getattr(statement, attribute, None) or []
    for branch in getattr(statement, "branches", []) or []:
        yield branch.body
    for handler in getattr(statement, "exception_handlers", []) or []:
        yield handler.body


def _mentions(statements: list[M.Statement], names: list[str], skip: set[int]) -> bool:
    for statement in statements:
        if id(statement) in skip:
            continue
        if any(_word(name).search(text) for text in _texts(statement) for name in names):
            return True
        if any(_mentions(nested, names, skip) for nested in _nested(statement)):
            return True
    return False


def _texts(statement: M.Statement):
    """Every piece of PL/SQL text a statement carries, without naming the attributes one by one.

    The IR grows nodes, and a list of attribute names would silently stop covering them.
    """
    for field in dataclasses.fields(statement):
        if field.name in _NOT_TEXT:
            continue
        value = getattr(statement, field.name, None)
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (item for item in value if isinstance(item, str))


# ids and classifications, not PL/SQL the routine could read a variable from
_NOT_TEXT = {"id", "kind", "sql_kind", "loop_kind", "cardinality", "label", "direction"}


def _word(name: str) -> re.Pattern:
    return re.compile(rf"(?<![\w$#.]){re.escape(name)}(?![\w$#])", re.IGNORECASE)


def _query(cursor: str, arguments: list[str], routine: M.Routine, symbols: SymbolTable,
           module: str | None, schema: OracleSchema | None) -> str | None:
    """The cursor's query, with its own parameters substituted by what `OPEN` passed.

    A cursor declared in a package specification is not in the IR at all -- the lowering reads the body -- so
    the symbol table is what answers, and it is also what makes `OPEN c(p_status)` resolvable: the cursor's
    parameter names are its own, not the caller's.
    """
    symbol = symbols.resolve(routine.id, cursor) or (symbols.resolve(module, cursor) if module else None)
    if symbol is None or symbol.kind != "cursor" or not symbol.query:
        return None
    if len(symbol.parameters) != len(arguments):
        return None   # positional only; a named actual (`c(p_status => v)`) is not read here
    return _substituted(symbol.query, symbol.parameters, arguments, schema)


def _substituted(query: str, parameters: list[str], arguments: list[str],
                 schema: OracleSchema | None) -> str | None:
    """`query` with each cursor parameter replaced by the actual `OPEN` passed, or None to give up.

    Oracle resolves a name inside a cursor's query to a **column** of the queried tables first, and to the
    cursor's parameter only when no column has that name. A textual replacement cannot tell the two apart:
    `CURSOR c(status VARCHAR2) IS SELECT ... WHERE status = status` compares the column with itself in
    Oracle, and replacing both halves turned it into `:b = :b` -- true for every row, with no diagnostic.
    So the names are resolved on the tree, where a qualified `o.status` is not a name of its own, and a
    parameter whose name the schema also knows as a column of one of the tables is not resolved at all.

    Giving up is the safe answer: the OPEN / FETCH / CLOSE stay as they were and the generator refuses them
    visibly, which is what the schema being unknown deserves too -- without the DDL, nothing here can tell
    whether a collision exists.
    """
    if not parameters:
        return query
    try:
        tree = sqlglot.parse_one(query, dialect="oracle")
    except Exception:
        return None
    tables = [t.name for t in tree.find_all(exp.Table) if t.name]
    wanted = {name.lower() for name in parameters}
    for table in tables:
        columns = schema.columns(table) if schema else None
        if columns is None:
            return None   # an unknown table cannot be cleared of the collision
        if wanted & set(columns):
            return None   # the column wins in Oracle; the substitution would ask a different question
    actual_of: dict[str, exp.Expression] = {}
    for name, actual in zip(parameters, arguments):
        try:
            actual_of[name.lower()] = sqlglot.parse_one(actual, dialect="oracle")
        except Exception:
            return None
    for column in list(tree.find_all(exp.Column)):
        if not column.table and column.name.lower() in actual_of:
            column.replace(actual_of[column.name.lower()].copy())
    return tree.sql(dialect="oracle")


def _operation(source: M.Statement, routine: M.Routine, sql: str,
               targets: list[str]) -> M.SqlOperation | None:
    # the id of the statement it replaces, which is free now and keeps the generated method's name in the
    # `<routine>Stmt<n>` form everything else uses
    return M.SqlOperation(id=source.id, kind="SqlOperation", source_range=source.source_range,
                          sql_kind="SELECT", original_sql=sql, into_targets=list(targets))


def _guards(statement: M.If, cursor: str, pattern: re.Pattern) -> bool:
    """A single-branch IF whose condition is this cursor's attribute, and nothing else."""
    return len(statement.branches) == 1 and not statement.else_body \
        and _names(statement.branches[0].condition, cursor, pattern)


def _names(condition: str | None, cursor: str, pattern: re.Pattern) -> bool:
    match = pattern.match(condition or "")
    return bool(match) and match.group("cursor").lower() == cursor.lower()


def _cursor_name(written: str | None) -> str:
    """`c_open_orders(p_status)` names `c_open_orders`; the arguments are kept on the node."""
    return (written or "").split("(")[0].strip().lower()


def _drop_isopen(statements: list[M.Statement], rewritten: set[str]) -> list[M.Statement]:
    out: list[M.Statement] = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _drop_isopen(nested, rewritten))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _drop_isopen(branch.body, rewritten)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _drop_isopen(handler.body, rewritten)
        if statement.kind == "If" and len(statement.branches) == 1 and not statement.else_body:
            match = ISOPEN.match(statement.branches[0].condition or "")
            body = statement.branches[0].body
            if match and match.group("cursor").lower() in rewritten and \
                    all(s.kind == "CloseCursor" for s in body):
                continue
        out.append(statement)
    return out
