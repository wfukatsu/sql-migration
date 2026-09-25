"""#11: the two explicit-cursor shapes that are not a scan -- "the first row" and "how many rows".

`docs/plsql-migration/plsql-cursor-patterns.md` sorts the corpus's cursors into six forms. A (a scan that only reads) and D
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
    _drop_order_nobody_reads(routine, schema)
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
        _current_of(loop, match.group("cursor").lower(), query, schema)


CURRENT_OF = re.compile(r"\bWHERE\s+CURRENT\s+OF\s+(?P<cursor>[\w$#]+)\s*$", re.IGNORECASE)


def _current_of(loop: M.Loop, cursor: str, query: str, schema: OracleSchema | None) -> None:
    """`UPDATE t ... WHERE CURRENT OF c` -> `... WHERE <主キー> = r.<主キー>`（2026-09-19）。

    `CURRENT OF` は「いま FETCH した行」である。cursor が表の主キーを読んでいれば、その値で同じ行を
    指せる——行を指す手段が変わるだけで、指している行は変わらない。ScalarDB SQL に `CURRENT OF` は
    無い（構文エラーになる）。

    書き換えるのは **cursor が 1 つの表だけを読み、主キーを全部選んでいて、書く表がその表**のとき
    だけである。行ロック（`FOR UPDATE`）を落とすかどうかは別の話で、そちらは記録された routine
    だけが通る（#9）。ここは行の指し方しか変えない。
    """
    try:
        tree = sqlglot.parse_one(query, dialect="oracle")
    except Exception:
        return
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    source = (select.args.get("from_") or select.args.get("from")) if select is not None else None
    if select is None or source is None or not isinstance(source.this, exp.Table) or select.args.get("joins"):
        return
    table = source.this.name.lower()
    key = schema.primary_key(table) if schema is not None else []
    selected = {item.alias_or_name.lower() for item in select.expressions}
    if not key or not all(column in selected for column in key):
        return
    row = loop.variable or "r"
    for statement in loop.body:
        found = CURRENT_OF.search(statement.original_sql or "") if statement.kind == "SqlOperation" else None
        if found is None or found.group("cursor").lower() != cursor:
            continue
        written = re.match(r"^\s*(?:UPDATE|DELETE\s+FROM)\s+(?P<table>[\w$#]+)", statement.original_sql,
                           re.IGNORECASE)
        if written is None or written.group("table").lower() != table:
            continue
        predicate = " AND ".join(f"{column} = {row}.{column}" for column in key)
        statement.original_sql = statement.original_sql[:found.start()] + f"WHERE {predicate}"
        statement.add("INFO", "CURRENT_OF",
                      f"WHERE CURRENT OF {cursor} を主キー（{', '.join(key)}）で指す形にした。指している行は"
                      f"変わらない。行ロックを落とすかどうかは別に決める（#9）")


def _drop_order_nobody_reads(routine: M.Routine, schema: OracleSchema | None) -> None:
    """結果に効かない `ORDER BY` を落とす（2026-09-19 / #20 の決定 A: `mark_reviewed`）。

    `FOR r IN (... ORDER BY ordered_at) LOOP UPDATE t SET note = 'reviewed' WHERE <主キー> = r.<主キー>`
    は全行に同じことをするだけで、どの順で回しても残る行は同じである。Oracle で順序が効くのは
    **ロックを取る順**だけで、移行先に行ロックは無い。落とすと、パーティションをまたぐ並べ替えが
    要らなくなる（`status` の索引で読める）。

    落としてよいと言えるのは、次が**全部**成り立つときだけである——1 つでも欠けたら順序は残す:

    * 件数の上限（`FETCH FIRST` / `ROWNUM` / `LIMIT`）が無い——あればどの行が選ばれるかが順序で決まる
    * 本体が、読んだ行を**主キーで指す** UPDATE / DELETE だけである——他の行に触れない
    * 書く値が、定数・その行の値・routine の引数だけである——前の反復が残した値を読まない
    * 途中で抜けない（EXIT / RETURN が無い）
    """
    for loop in _loops(routine.body):
        query = loop.query
        if (loop.loop_kind or "") != "cursor-for" or query is None or loop.chunk:
            continue
        try:
            tree = sqlglot.parse_one(query.original_sql or "", dialect="oracle")
        except Exception:
            continue
        select = tree if isinstance(tree, exp.Select) else None
        if select is None or not select.args.get("order") or row_cap(select)[0] is not None \
                or select.args.get("limit") or query.locking_mode:
            continue
        source = select.args.get("from_") or select.args.get("from")
        if source is None or not isinstance(source.this, exp.Table) or select.args.get("joins"):
            continue
        table = source.this.name.lower()
        key = schema.primary_key(table) if schema is not None else []
        if not key or not _order_free(loop, table, key, routine):
            continue
        select.set("order", None)
        query.original_sql = select.sql(dialect="oracle")
        loop.cursor = f"{loop.variable or 'r'} IN ({query.original_sql})"
        query.add("INFO", "ORDER_DROPPED",
                  "ORDER BY は結果に効かない（各行を主キーで指して同じことをするだけ）ので落とした。"
                  "Oracle で順序が効くのはロックを取る順だけで、移行先に行ロックは無い（#20）")


def _order_free(loop: M.Loop, table: str, key: list[str], routine: M.Routine) -> bool:
    row = (loop.variable or "r").lower()
    parameters = {p.name.lower() for p in routine.parameters}
    for statement in loop.body:
        if statement.kind != "SqlOperation" or (statement.sql_kind or "").upper() not in ("UPDATE", "DELETE"):
            return False
        try:
            tree = sqlglot.parse_one(statement.original_sql or "", dialect="oracle")
        except Exception:
            return False
        target = tree.this if isinstance(tree, (exp.Update, exp.Delete)) else None
        if not isinstance(target, exp.Table) or target.name.lower() != table:
            return False
        where = tree.args.get("where")
        pinned = {}
        for condition in (list(_conjuncts(where.this)) if where is not None else []):
            if not isinstance(condition, exp.EQ) or not isinstance(condition.this, exp.Column):
                return False
            value = condition.expression
            if isinstance(value, exp.Column) and (value.table or "").lower() == row:
                pinned[condition.this.name.lower()] = value.name.lower()
            else:
                return False
        if any(pinned.get(column) != column for column in key):
            return False   # 読んだ行を主キーで指していない
        for assignment in (tree.expressions or []) if isinstance(tree, exp.Update) else []:
            for column in assignment.expression.find_all(exp.Column):
                qualifier = (column.table or "").lower()
                if qualifier != row and column.name.lower() not in parameters:
                    return False   # 表の列か、前の反復が残した局所変数を読んでいる
    return True


def _conjuncts(condition):
    while isinstance(condition, exp.Paren):
        condition = condition.this
    if isinstance(condition, exp.And):
        yield from _conjuncts(condition.this)
        yield from _conjuncts(condition.expression)
    else:
        yield condition


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
            _chunks(statements, index, routine, symbols, module, schema) or \
            _scan(statements, index, routine, symbols, module, schema)
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


# --- A. 走査（`OPEN c; LOOP FETCH c INTO v; EXIT WHEN c%NOTFOUND; ... END LOOP; CLOSE c;`）--------

def _scan(statements: list[M.Statement], index: int, routine: M.Routine, symbols: SymbolTable,
          module: str | None,
          schema: OracleSchema | None) -> tuple[list[M.Statement], int, str] | None:
    """明示 cursor で回すだけのループ。**cursor FOR ループと同じもの**である。

    B（先頭 1 件）や C（件数）と違い、本体が何をしていてもよい——読んだ行で何かする、という形
    そのものだからである。だから本体には触らず、**行の値を元の変数へ代入する文を先頭に足す**:

        OPEN c; LOOP FETCH c INTO v_order_id; EXIT WHEN c%NOTFOUND; <本体> END LOOP; CLOSE c;

        -> FOR r IN (<cursor の問い合わせ>) LOOP
             v_order_id := r.order_id;   -- 足すのはこれだけ
             <本体>
           END LOOP;

    変数を残すのは、**ループの後でも Oracle と同じ値が入っている**ためでもある。Oracle は最後の
    FETCH が空振りしたとき INTO の対象を変えない——つまり最後の行の値が残る。参照を書き換えて
    しまうと、ループの外で読んでいる routine で意味が変わる。
    """
    run = statements[index:]
    if len(run) < 3 or run[0].kind != "OpenCursor" or run[1].kind != "Loop" or run[2].kind != "CloseCursor":
        return None
    cursor = _cursor_name(run[0].cursor)
    if _cursor_name(run[2].cursor) != cursor or (run[1].loop_kind or "basic") != "basic":
        return None
    body = list(run[1].body)
    if len(body) < 2 or body[0].kind != "Fetch" or _cursor_name(body[0].cursor) != cursor:
        return None
    if getattr(body[0], "bulk_limit", None):
        return None   # 分割読み。E が扱う
    if body[1].kind != "Exit" or not _names(body[1].condition, cursor, NOTFOUND):
        return None   # 抜ける条件が `%NOTFOUND` でない。何行読むのかがこの形では決まらない
    rest = body[2:]
    if any(s.kind in ("Fetch", "OpenCursor", "CloseCursor") for s in _walk_all(rest)):
        return None   # 1 反復に 2 回読む、あるいは中で開き直す。行を配るループでは同じにならない
    if any(s.kind == "Exit" and _names(s.condition, cursor, NOTFOUND) for s in _walk_all(rest)):
        return None
    targets = list(body[0].into_targets or [])
    query = _query(cursor, list(run[0].arguments), routine, symbols, module, schema)
    if query is None or not targets:
        return None
    columns = _output_names(query)
    whole_row = len(targets) == 1 and _rowtype_of(routine, targets[0]) == cursor
    if not whole_row and (columns is None or len(columns) != len(targets)):
        return None   # 位置で対応させられない（射影が式で名前を持たないときなど）
    operation = _operation(run[1], routine, query, [])
    if operation is None:
        return None
    operation.cardinality = "MANY"
    if whole_row:
        # `FETCH c INTO r` with `r c%ROWTYPE`: the record itself is the loop variable (#39). The generator then
        # emits no separate local for it, so a read of `r` after the loop does not compile -- visible, not wrong
        row, assignments = targets[0], []
    else:
        row = _row_name(routine)
        assignments = [
            M.Assignment(id=f"{body[0].id}row{position}", kind="Assignment",
                         source_range=body[0].source_range, target=target,
                         expression=f"{row}.{column}")
            for position, (target, column) in enumerate(zip(targets, columns), start=1)]
    # `c%ROWCOUNT` inside the body: the rows read so far, counted in a local of its own (#39)
    rowcount = re.compile(rf"\b{re.escape(cursor)}\s*%\s*ROWCOUNT\b", re.IGNORECASE)
    if any(rowcount.search(text) for statement in _walk_all(rest) if dataclasses.is_dataclass(statement)
           for text in _texts(statement)):
        counter = _free_name(routine, f"{cursor}_rowcount")
        routine.declarations.append(M.Declaration(
            id=f"{run[0].id}rowcount", kind="Declaration", source_range=run[0].source_range, name=counter,
            type=M.TypeRef(oracle="PLS_INTEGER", resolved="PLS_INTEGER"), initial="0"))
        _replace_in(rest, rowcount, counter)
        assignments.append(M.Assignment(id=f"{run[0].id}count", kind="Assignment",
                                        source_range=run[0].source_range, target=counter,
                                        expression=f"{counter} + 1"))
    loop = M.Loop(id=run[1].id, kind="Loop", source_range=run[1].source_range,
                  loop_kind="cursor-for", variable=row, query=operation,
                  body=assignments + rest, cursor=f"{row} IN ({query})")
    loop.add("INFO", "CUR_SCAN",
             f"cursor {cursor} は読むだけのループだった。行を先に読んで回す形にした——移行先に"
             f"跨トランザクションの cursor は無いので、動く行数はメモリで決まる（上限は --limits）。"
             + ("FETCH の代入先はそのまま残してあるので、ループの後でも Oracle と同じ値が入っている"
                if not whole_row else f"%ROWTYPE の {row} がループ変数になる（ループの後の {row} は読めない）"))
    return ([loop], 3, cursor)


def _rowtype_of(routine: M.Routine, name: str) -> str | None:
    """`r c_emp%ROWTYPE` -> `c_emp`: the cursor (or table) whose row the declaration holds."""
    for declaration in routine.declarations:
        if declaration.name.lower() == name.lower() and declaration.type is not None:
            written = (declaration.type.oracle or "").strip()
            if written.upper().endswith("%ROWTYPE"):
                return written.split("%")[0].strip().lower()
    return None


def _free_name(routine: M.Routine, wanted: str) -> str:
    used = {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}
    name, index = wanted, 1
    while name.lower() in used:
        index += 1
        name = f"{wanted}_{index}"
    return name


def _replace_in(statements: list[M.Statement], pattern: re.Pattern, replacement: str) -> None:
    for statement in _walk_all(statements):
        if not dataclasses.is_dataclass(statement):
            continue
        for field in dataclasses.fields(statement):
            value = getattr(statement, field.name, None)
            if isinstance(value, str) and field.name not in ("id", "kind"):
                setattr(statement, field.name, pattern.sub(replacement, value))
            elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
                setattr(statement, field.name, [pattern.sub(replacement, v) for v in value])
        for branch in getattr(statement, "branches", []) or []:
            if isinstance(getattr(branch, "condition", None), str):
                branch.condition = pattern.sub(replacement, branch.condition)


def _walk_all(statements: list[M.Statement]) -> list[M.Statement]:
    from .lower import _walk

    return _walk(statements)


def _output_names(sql: str) -> list[str] | None:
    """射影ごとの出力名。1 つでも決まらなければ None。"""
    from .bulk import _select_names

    return _select_names(sql)


def _row_name(routine: M.Routine) -> str:
    """ループ変数の名前。元のコードが持っている名前を踏まない。"""
    used = {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}
    if "r" not in used:
        return "r"
    index = 1
    while f"r{index}" in used:
        index += 1
    return f"r{index}"


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
    guards = _limit_guards(run[1], fetch.bulk_limit)
    if guards is None:
        return None   # LIMIT が 0 以下の literal。Oracle は必ず ORA-06502 を投げる——書き換えずに残す
    counted = _counts_only(body[1:], collection)
    if counted is not None:
        # 本体が塊の件数を足しているだけなら、行を 1 行も持たずに COUNT(*) で同じ答えが出る
        # （#19 の決定、2026-09-19）。上限の決定そのものが要らなくなる
        variable = _free_count_name(routine)
        routine.declarations.append(M.Declaration(
            id=f"{routine.id}#decl-{variable}", kind="Declaration", name=variable,
            source_range=run[1].source_range,
            type=M.TypeRef(oracle="NUMBER", resolved="NUMBER", origin="inferred")))
        if symbols is not None and symbols.scopes.get(routine.id) is not None:
            from .symbols import Symbol

            symbols.scopes[routine.id].declare(Symbol(name=variable, kind="variable", scope=routine.id,
                                                      type=routine.declarations[-1].type))
        count = _operation(run[1], routine, _count_sql(query), [variable])
        count.cardinality = "EXACTLY_ONE"
        count.add("INFO", "CUR_COUNT",
                  f"cursor {cursor} は {fetch.bulk_limit} 件ずつ読んで件数を足すだけだった。COUNT(*) で同じ"
                  f"答えが出るので、行を持たない（#19 / 2026-09-19）")
        total = M.Assignment(id=f"{run[1].id}sum", kind="Assignment", source_range=run[1].source_range,
                             target=counted, expression=f"{counted} + {variable}")
        return (guards + [count, total], 3, cursor)
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
    return (guards + [loop], 3, cursor)


def _limit_guards(where: M.Statement, limit: str) -> list[M.Statement]:
    """`LIMIT n` の n が正でないときに Oracle が投げる誤りを、そのまま投げる（2026-09-19 に実測）。

        LIMIT 0 / 負の値 -> ORA-06502（VALUE_ERROR）
        LIMIT NULL       -> ORA-06500（STORAGE_ERROR）

    以前は「0 件で抜けるのと同じ」として何もしていなかった——実測せずに書いた説明で、誤りだった。
    数値の literal なら確かめるまでもないので、変数のときだけ置く。
    """
    if re.fullmatch(r"\s*\d+\s*", limit):
        return [] if int(limit) > 0 else None
    guards = []
    for index, (condition, code, message) in enumerate((
            (f"{limit} IS NULL", -6500, "'PL/SQL: storage error'"),
            (f"{limit} < 1", -6502, "'PL/SQL: value or conversion error'")), start=1):
        raised = M.Raise(id=f"{where.id}limit{index}raise", kind="Raise", source_range=where.source_range,
                         error_code=code, message=message)
        guards.append(M.If(id=f"{where.id}limit{index}", kind="If", source_range=where.source_range,
                           branches=[M.Branch(condition=condition, body=[raised])]))
    return guards


def _counts_only(body: list[M.Statement], collection: str) -> str | None:
    """本体が `EXIT WHEN v.COUNT = 0; x := x + v.COUNT;` だけなら、足している変数 x。"""
    if len(body) != 2 or body[0].kind != "Exit" or body[1].kind != "Assignment":
        return None
    count = rf"{re.escape(collection)}\s*\.\s*COUNT"
    if not re.fullmatch(rf"\s*{count}\s*=\s*0\s*", body[0].condition or "", re.IGNORECASE):
        return None
    target = body[1].target or ""
    if not re.fullmatch(rf"\s*{re.escape(target)}\s*\+\s*{count}\s*", body[1].expression or "", re.IGNORECASE):
        return None
    return target


def _free_count_name(routine: M.Routine) -> str:
    used = {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}
    index = 1
    while f"v_count_{index}" in used:
        index += 1
    return f"v_count_{index}"


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
