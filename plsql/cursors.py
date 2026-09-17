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

import re

import sqlglot
from sqlglot import exp

from .ir import model as M
from .symbols import SymbolTable

NOTFOUND = re.compile(r"^\s*(?P<cursor>[\w$#]+)\s*%\s*NOTFOUND\s*$", re.IGNORECASE)
ISOPEN = re.compile(r"^\s*(?P<cursor>[\w$#]+)\s*%\s*ISOPEN\s*$", re.IGNORECASE)


def rewrite(routine: M.Routine, symbols: SymbolTable | None, module: str | None = None) -> None:
    """Replace the B and C shapes in `routine` with the query each one is, in place."""
    if symbols is None:
        return
    rewritten: set[str] = set()
    routine.body = _sequence(routine.body, routine, symbols, module, rewritten)
    for handler in routine.exception_handlers:
        handler.body = _sequence(handler.body, routine, symbols, module, rewritten)
    if rewritten:
        # `IF c%ISOPEN THEN CLOSE c; END IF;` guarded a cursor that no longer exists. Left in place it is a
        # reference to nothing, and the generator would refuse the handler it sits in.
        routine.body = _drop_isopen(routine.body, rewritten)
        for handler in routine.exception_handlers:
            handler.body = _drop_isopen(handler.body, rewritten)


def _sequence(statements: list[M.Statement], routine: M.Routine, symbols: SymbolTable,
              module: str | None, rewritten: set[str]) -> list[M.Statement]:
    """One pass over a statement list, replacing any run that matches B or C. Nested bodies go first."""
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, symbols, module, rewritten))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, symbols, module, rewritten)

    out: list[M.Statement] = []
    index = 0
    while index < len(statements):
        match = _first_row(statements, index, routine, symbols, module) or \
            _count(statements, index, routine, symbols, module)
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
               module: str | None) -> tuple[list[M.Statement], int, str] | None:
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
    query = _query(cursor, run[0], routine, symbols, module)
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
           module: str | None) -> tuple[list[M.Statement], int, str] | None:
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
    query = _query(cursor, run[0], routine, symbols, module)
    if query is None:
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


# --- shared -------------------------------------------------------------------------------------------

def _query(cursor: str, opened: M.CursorStatement, routine: M.Routine, symbols: SymbolTable,
           module: str | None) -> str | None:
    """The cursor's query, with its own parameters substituted by what `OPEN` passed.

    A cursor declared in a package specification is not in the IR at all -- the lowering reads the body -- so
    the symbol table is what answers, and it is also what makes `OPEN c(p_status)` resolvable: the cursor's
    parameter names are its own, not the caller's.
    """
    symbol = symbols.resolve(routine.id, cursor) or (symbols.resolve(module, cursor) if module else None)
    if symbol is None or symbol.kind != "cursor" or not symbol.query:
        return None
    arguments = list(opened.arguments)
    if len(symbol.parameters) != len(arguments):
        return None   # positional only; a named actual (`c(p_status => v)`) is not read here
    query = symbol.query
    for name, actual in zip(symbol.parameters, arguments):
        query = re.sub(rf"(?<![\w$#.]){re.escape(name)}(?![\w$#])", actual, query, flags=re.IGNORECASE)
    return query


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
        if statement.kind == "If" and len(statement.branches) == 1 and not statement.else_body:
            match = ISOPEN.match(statement.branches[0].condition or "")
            body = statement.branches[0].body
            if match and match.group("cursor").lower() in rewritten and \
                    all(s.kind == "CloseCursor" for s in body):
                continue
        out.append(statement)
    return out
