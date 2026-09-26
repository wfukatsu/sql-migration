"""#53: DBMS_SQL whose statement is a constant query, taken to a static cursor FOR loop.

    c := DBMS_SQL.OPEN_CURSOR;
    DBMS_SQL.PARSE(c, 'SELECT * FROM departments WHERE ROWNUM <= 2', DBMS_SQL.NATIVE);
    DBMS_SQL.DESCRIBE_COLUMNS2(c, ncols, cols);           -> ncols := 4
    FOR i IN 1 .. ncols LOOP DBMS_SQL.DEFINE_COLUMN(...)   -> (nothing: the columns are known)
    n := DBMS_SQL.EXECUTE(c);                              -> n := 0 (a query's EXECUTE returns nothing useful)
    WHILE DBMS_SQL.FETCH_ROWS(c) > 0 LOOP                  -> FOR r_sql_1 IN (SELECT <columns> FROM ...) LOOP
      DBMS_SQL.COLUMN_VALUE(c, i, v_val);                  ->   v_val := CASE i WHEN 1 THEN TO_CHAR(r_sql_1.a) ... END
      ... cols(i).col_name ...                             ->   CASE i WHEN 1 THEN 'A' ... END
    DBMS_SQL.CLOSE_CURSOR(c);                              -> (nothing)

The column list is the query's own, or the table's from the DDL for `SELECT *`. The rewrite is all or nothing:
a PARSE whose statement is not a constant query, a second cursor, a call this does not know (BIND_VARIABLE,
VARIABLE_VALUE, ...), a use of the cursor outside these calls -- any of them leaves the routine as it was, and the
generator refuses it where it did. The routine then carries DBMS_SQL_DYNAMIC, which says why.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from .ir import model as M
from .symbols import OracleSchema

CALL = re.compile(r"^DBMS_SQL\.(?P<member>\w+)$", re.IGNORECASE)
FETCH = re.compile(r"^\s*DBMS_SQL\s*\.\s*FETCH_ROWS\s*\(\s*(?P<cursor>[\w$#]+)\s*\)\s*>\s*0\s*$", re.IGNORECASE)
EXECUTE = re.compile(r"^\s*DBMS_SQL\s*\.\s*EXECUTE\s*\(\s*(?P<cursor>[\w$#]+)\s*\)\s*$", re.IGNORECASE)
OPEN = re.compile(r"^\s*DBMS_SQL\s*\.\s*OPEN_CURSOR\s*(?:\(\s*\))?\s*$", re.IGNORECASE)
KNOWN = {"PARSE", "DESCRIBE_COLUMNS", "DESCRIBE_COLUMNS2", "DESCRIBE_COLUMNS3", "DEFINE_COLUMN", "COLUMN_VALUE",
         "CLOSE_CURSOR"}
CHAR = re.compile(r"^\s*(N?VARCHAR2?|N?CHAR|CLOB)\b", re.IGNORECASE)


class _Refused(Exception):
    pass


def rewrite(program: M.Program, schema: OracleSchema | None) -> None:
    for module in program.modules:
        for routine in module.routines:
            if "DBMS_SQL" not in repr(routine).upper():
                continue
            try:
                _routine(routine, schema)
            except _Refused as reason:
                routine.add("WARN", "DBMS_SQL_DYNAMIC",
                            f"DBMS_SQL を静的な cursor に下ろせない: {reason}。PARSE の文字列が実行時に決まるなら、"
                            f"実行ログから流れた文を集めて専用の Repository にする（#53）")


def _routine(routine: M.Routine, schema: OracleSchema | None) -> None:
    from .lower import _walk

    opened = [d for d in routine.declarations if d.initial and OPEN.match(d.initial)]
    opened += [s for s in _walk(routine.body) if s.kind == "Assignment" and OPEN.match(s.expression or "")]
    if len(opened) != 1:
        raise _Refused("OPEN_CURSOR が 1 つでない")
    cursor = (opened[0].name if isinstance(opened[0], M.Declaration) else opened[0].target).lower()
    parses = [s for s in _walk(routine.body) if s.kind == "Call" and (s.callee or "").upper() == "DBMS_SQL.PARSE"]
    if len(parses) != 1 or len(parses[0].arguments) < 2:
        raise _Refused("PARSE が 1 つでない")
    literal = re.fullmatch(r"\s*'((?:[^']|'')*)'\s*", parses[0].arguments[1])
    if literal is None:
        raise _Refused("PARSE に渡す文字列が定数でない（実行時に決まる）")
    query, columns = _columns(literal.group(1).replace("''", "'"), schema)
    for statement in _walk(routine.body):
        member = CALL.match(statement.callee or "") if statement.kind == "Call" else None
        if member and member.group("member").upper() not in KNOWN:
            raise _Refused(f"DBMS_SQL.{member.group('member')} は模していない")
    describe = [s for s in _walk(routine.body) if s.kind == "Call" and
                (s.callee or "").upper().startswith("DBMS_SQL.DESCRIBE_COLUMNS")]
    descriptions = {s.arguments[2].strip().lower(): s.arguments[1].strip() for s in describe if len(s.arguments) == 3}
    loops = [s for s in _walk(routine.body) if s.kind == "Loop" and s.loop_kind == "while"
             and (FETCH.match(s.condition or "") or re.search(r"DBMS_SQL", s.condition or "", re.IGNORECASE))]
    if len(loops) != 1 or not FETCH.match(loops[0].condition or "") or \
            FETCH.match(loops[0].condition).group("cursor").lower() != cursor:
        raise _Refused("WHILE DBMS_SQL.FETCH_ROWS(c) > 0 の形の読み取りループが 1 つでない")

    row = "r_sql_1"
    types = {name: _type_of(holder) for holder in list(routine.declarations) + list(routine.parameters)
             for name in [holder.name.lower()]}

    def column_value(index: str, target: str) -> str:
        wants_text = bool(CHAR.match(types.get(target.lower(), "") or ""))
        whens = " ".join(f"WHEN {i} THEN {'TO_CHAR(' if wants_text and not is_char else ''}{row}.{name}"
                         f"{')' if wants_text and not is_char else ''}"
                         for i, (name, is_char) in enumerate(columns, start=1))
        return f"CASE {index} {whens} END"

    def names_of(text: str) -> str:
        for holder in descriptions:
            pattern = re.compile(rf"\b{re.escape(holder)}\s*\(\s*(?P<index>[\w$#]+)\s*\)\s*\.\s*col_name\b", re.IGNORECASE)
            text = pattern.sub(lambda m: "CASE " + m.group("index") + " " +
                               " ".join(f"WHEN {i} THEN '{name.upper()}'" for i, (name, _) in enumerate(columns, 1)) +
                               " END", text)
        return text

    def rewritten(statements: list[M.Statement]) -> list[M.Statement]:
        out: list[M.Statement] = []
        for statement in statements:
            for attribute in ("body", "else_body"):
                nested = getattr(statement, attribute, None)
                if nested:
                    setattr(statement, attribute, rewritten(nested))
            for branch in getattr(statement, "branches", []) or []:
                branch.body = rewritten(branch.body)
                branch.condition = names_of(branch.condition)
            member = CALL.match(statement.callee or "") if statement.kind == "Call" else None
            where = statement.source_range
            if member:
                name = member.group("member").upper()
                if name in ("PARSE", "DEFINE_COLUMN", "CLOSE_CURSOR"):
                    continue
                if name.startswith("DESCRIBE_COLUMNS"):
                    out.append(M.Assignment(id=f"{statement.id}ncols", kind="Assignment", source_range=where,
                                            target=statement.arguments[1].strip(), expression=str(len(columns))))
                    continue
                if name == "COLUMN_VALUE":
                    index, target = statement.arguments[1].strip(), statement.arguments[2].strip()
                    out.append(M.Assignment(id=f"{statement.id}value", kind="Assignment", source_range=where,
                                            target=target, expression=column_value(index, target)))
                    continue
            if statement.kind == "Assignment" and EXECUTE.match(statement.expression or ""):
                statement.expression = "0"
            if statement.kind == "Assignment" and OPEN.match(statement.expression or ""):
                continue
            if statement.kind == "Call":
                statement.arguments = [names_of(a) for a in statement.arguments]
            if statement.kind == "Assignment":
                statement.expression = names_of(statement.expression or "")
            if statement.kind == "Loop" and statement.loop_kind == "for" and not statement.body:
                continue   # the DEFINE_COLUMN loop, now empty
            if statement is loops[0]:
                operation = M.SqlOperation(id=f"{statement.id}query", kind="SqlOperation", source_range=where,
                                           sql_kind="SELECT", original_sql=query, cardinality="MANY")
                loop = M.Loop(id=statement.id, kind="Loop", source_range=where, loop_kind="cursor-for",
                              variable=row, query=operation, body=statement.body, cursor=f"{row} IN ({query})")
                loop.add("INFO", "DBMS_SQL_STATIC", "DBMS_SQL の一連（OPEN_CURSOR〜CLOSE_CURSOR）を、PARSE の文字列が定数の"
                                                    "問合せなので静的な cursor FOR ループにした（#53）")
                out.append(loop)
                continue
            out.append(statement)
        return out

    routine.body = rewritten(routine.body)
    kept = []
    for declaration in routine.declarations:
        if declaration.name.lower() == cursor:
            declaration.initial = None
        if declaration.name.lower() in descriptions:
            continue   # the DESC_TAB is read only for its column names, which are now constants
        kept.append(declaration)
    routine.declarations = kept


def _type_of(holder) -> str:
    return (holder.type.resolved or holder.type.oracle) if holder.type is not None else ""


def _columns(query: str, schema: OracleSchema | None) -> tuple[str, list[tuple[str, bool]]]:
    """The query with `*` spelled out, and its columns as (name, is text)."""
    try:
        tree = sqlglot.parse_one(query, dialect="oracle")
    except Exception as e:  # noqa: BLE001
        raise _Refused(f"PARSE の文字列を読めない（{e}）")
    if not isinstance(tree, exp.Select):
        raise _Refused("PARSE の文字列が問合せでない（DML / DDL の DBMS_SQL は模していない）")
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1:
        raise _Refused("PARSE の問合せが 1 つの表を読むのでない")
    declared = schema.columns(tables[0].name) if schema is not None else None
    if len(tree.expressions) == 1 and isinstance(tree.expressions[0], exp.Star):
        if not declared:
            raise _Refused(f"{tables[0].name} の列が DDL に無い（SELECT * を展開できない）")
        tree.set("expressions", [exp.column(c) for c in declared])
    columns = []
    for projection in tree.expressions:
        if not isinstance(projection, (exp.Column, exp.Alias)) or not projection.alias_or_name:
            raise _Refused("PARSE の問合せの select list に名前の無い式がある")
        name = projection.alias_or_name.lower()
        oracle = (declared or {}).get(name, "")
        columns.append((name, bool(CHAR.match(oracle))))
    return tree.sql(dialect="oracle"), columns
