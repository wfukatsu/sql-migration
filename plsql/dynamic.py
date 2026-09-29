"""P4-7: work out what a dynamic SQL statement can actually be, when that is a finite set.

`EXECUTE IMMEDIATE v_sql` hides the statement from every check the pipeline makes. Sometimes it does not have
to: the string is built from literals and a few branches, so the set of statements the routine can run is small
and knowable. Enumerating it turns one opaque statement into a handful of ordinary ones, each of which the
converter, the capability check and the differential test can look at.

    variants = enumerate_variants(routine, statement)   # [(guard, sql), ...] or None

Three rules keep this from claiming more than it knows:

* **Only literals compose.** A fragment that comes from a parameter (`'DELETE FROM ' || p_table_name`) makes the
  whole statement unknowable, and the answer is None rather than a guess. That is `purge`, and it stays
  REDESIGN.
* **The count is bounded.** Branches multiply, and a routine with ten of them has a thousand variants, which is
  not an enumeration anybody reviews. Past the limit the answer is None.
* **Folding is not clearance.** A folded statement still carries its `USING` binds, and `EXECUTE IMMEDIATE`
  runs with privileges the caller may not have on a static statement (`AUTHID`). Both are recorded as
  diagnostics on the statement, because a reader who sees plain SQL will otherwise assume both were checked.
* **A guard is evaluated again, so it has to give the same answer.** The generated code picks the variant with
  the branch conditions, at the EXECUTE IMMEDIATE -- not at the IF that built the string (#107):

      IF n = 0 THEN v_sql := '... sal = 3 ...'; n := 1; ELSE v_sql := '... sal = 4 ...'; END IF;
      EXECUTE IMMEDIATE v_sql;          -- Oracle runs sal = 3; `if (n == 0)` here picked sal = 4

  So a condition may read only the routine's own parameters and locals (and a few pure built-ins), and nothing
  on the way from the IF to the EXECUTE may write them -- an assignment, an INTO, an argument of a call, a nested
  procedure that carries it back. Otherwise the answer is None and the statement stays REVIEW (DYN-002). The same
  holds for the string itself: written by anything but a plain assignment (`pick(v_sql)`), it is unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ir import model as M
from .source import Issue

# How many variants are worth enumerating. Ten is already a lot to review; past that the honest answer is that
# nobody knows what this statement runs, which is what REVIEW means.
MAX_VARIANTS = 8

# `v := 'text'` and `v := v || 'text'`, the two shapes a SQL string is built with
ASSIGN_LITERAL = re.compile(r"^\s*'(?P<text>(?:[^']|'')*)'\s*$")
APPEND_LITERAL = re.compile(r"^\s*(?P<var>[\w$#]+)\s*\|\|\s*'(?P<text>(?:[^']|'')*)'\s*$")


@dataclass(frozen=True)
class Variant:
    """One statement the routine can run, and the branch path that produces it."""

    guard: str          # the conditions that had to hold, joined; "" when unconditional
    sql: str

    def as_dict(self) -> dict:
        return {"guard": self.guard, "sql": self.sql}


def enumerate_variants(routine: M.Routine, statement: M.DynamicSql,
                       module: M.Module | None = None) -> list[Variant] | None:
    """Every statement `statement` can run, or None when that is not a finite knowable set.

    `module` names the routines lifted out of this one (#80): a call to one of them writes what it carries back."""
    target = (statement.expression or "").strip()
    if not target:
        return None
    folded = _unquote(target)
    if folded is not None:
        return [Variant(guard="", sql=folded)]
    if not re.fullmatch(r"[\w$#]+", target):
        # an expression, not a variable: `'DELETE FROM ' || p_table_name` and the like. Knowable only when
        # a person has listed the table names it may take (2026-09-19)
        return _allowed(routine, target)

    context = _Context(locals={p.name.lower() for p in routine.parameters}
                              | {d.name.lower() for d in routine.declarations if d.declaration_kind != "cursor"},
                       carried=_carried_back(routine, module))
    states = _trace(routine.body, statement.id, [_State(guard=(), values={})], context)
    if states is None:
        return None
    variants: list[Variant] = []
    for state in states:
        if state.tainted:
            return None   # its guard may no longer say what it said when the string was built (#107)
        value = state.values.get(target.lower())
        if value is None:
            return None  # one path leaves the statement unknown, so the set is not knowable
        variants.append(Variant(guard=" AND ".join(state.guard), sql=value))
    unique = list(dict.fromkeys(variants))
    return unique if 0 < len(unique) <= MAX_VARIANTS else None


import contextvars

_ALLOWED: "contextvars.ContextVar[dict[str, list[str]]]" = contextvars.ContextVar("allowed", default={})


def set_allowed_tables(allowed: dict[str, list[str]]) -> None:
    _ALLOWED.set(dict(allowed))


_OMITTED_DDL: "contextvars.ContextVar[dict[str, str]]" = contextvars.ContextVar("omitted_ddl", default={})


def set_omitted_ddl(omit: dict[str, str]) -> None:
    """limits.yaml `ddl.omit`: the routines whose DDL a person decided not to run on the target (#52)."""
    _OMITTED_DDL.set(dict(omit))


def omitted_ddl(routine_id: str) -> str | None:
    return _OMITTED_DDL.get().get(routine_id)


# `DBMS_ASSERT.SIMPLE_SQL_NAME(p)` は名前の**形**を確かめるだけで、**どの表か**は決めない。
# 一覧で照合するなら同じ値である
ASSERT = re.compile(r"^DBMS_ASSERT\s*\.\s*\w+\s*\(\s*(?P<name>[\w$#]+)\s*\)$", re.IGNORECASE)


def _allowed(routine: M.Routine, expression: str) -> list[Variant] | None:
    """`'DELETE FROM ' || p_table_name || ' WHERE ...'` を、**許された表名ごと**の文にする。

    式は literal と **1 つの引数**の連結でなければならない。表名が 2 か所から来る、式の中で
    加工されている、といった形は数えない。照合は大文字小文字を区別しない——Oracle の識別子が
    そうだからである。一覧に無い値は、生成コードが実行時に拒否する（どの variant の条件にも
    当たらない）。
    """
    tables = _ALLOWED.get().get(routine.id)
    if not tables:
        return None   # 誰も表名を決めていない。数えられないのが正しい答えである
    parts = [part.strip() for part in _split_concat(expression)]
    names = [part for part in parts if _unquote(part) is None]
    if len(names) != 1:
        return None
    wrapped = ASSERT.match(names[0])
    parameter = wrapped.group("name") if wrapped else names[0]
    holders = {p.name.lower() for p in routine.parameters} | {d.name.lower() for d in routine.declarations}
    if not re.fullmatch(r"[\w$#]+", parameter) or parameter.lower() not in holders:
        return None   # a parameter or a local of this routine (samples/oracle-samples b06_3: `v_table`)
    variants = []
    for table in tables:
        sql = "".join(table if part == names[0] else _unquote(part) for part in parts)
        variants.append(Variant(guard=f"UPPER({parameter}) = '{table.upper()}'", sql=sql))
    return variants if len(variants) <= MAX_VARIANTS else None


def _split_concat(expression: str) -> list[str]:
    """`a || 'b' || c` を項に分ける。引用符の中の `||` では割らない。"""
    out, current, quoted = [], "", False
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            quoted = not quoted
        if not quoted and expression.startswith("||", index):
            out.append(current)
            current = ""
            index += 2
            continue
        current += char
        index += 1
    out.append(current)
    return out


@dataclass
class _State:
    guard: tuple[str, ...]
    values: dict[str, str]
    # the variables the guard's conditions read (#107): written after the IF, the guard re-evaluated at the
    # EXECUTE IMMEDIATE may pick another variant than the one the string was built for
    watched: frozenset = frozenset()
    tainted: bool = False

    def fork(self, condition: str | None, reads: frozenset = frozenset()) -> "_State":
        return _State(guard=self.guard + ((condition,) if condition else ()), values=dict(self.values),
                      watched=self.watched | reads, tainted=self.tainted)

    def written(self, names: set[str], by_assignment: bool = False) -> None:
        if names & self.watched:
            self.tainted = True
        if not by_assignment:
            # `pick(v_sql)`, `SELECT ... INTO v_sql`: a value this trace cannot follow (#107)
            for name in names:
                self.values.pop(name, None)


@dataclass
class _Context:
    locals: set[str]
    carried: dict[str, set[str]]   # a lifted routine's name -> the enclosing variables it hands back


# the built-ins a guard may call: they give the same answer for the same arguments, however often they run
_PURE = {"UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM", "NVL", "NVL2", "COALESCE", "LENGTH", "SUBSTR", "INSTR",
         "TO_CHAR", "TO_NUMBER", "ABS", "MOD", "ROUND", "TRUNC", "SIGN", "GREATEST", "LEAST", "REPLACE"}
_WORDS = {"AND", "OR", "NOT", "IS", "NULL", "IN", "BETWEEN", "LIKE", "ESCAPE", "TRUE", "FALSE"}
_IDENTIFIER = re.compile(r"(?<![\w$#.%])([A-Za-z][\w$#]*)(\s*\()?")


def _reads(condition: str | None, context: _Context) -> frozenset | None:
    """The variables a branch condition reads, or None when it reads something else -- a package variable, a
    function, SYSDATE -- that the generated code cannot be trusted to evaluate the same way twice (#107)."""
    text = re.sub(r"'(?:[^']|'')*'", "''", condition or "")
    if "%" in text or "." in text:
        return None   # SQL%FOUND, c%ROWCOUNT, pkg.var, r.field: not a local read the trace can follow
    names = set()
    for match in _IDENTIFIER.finditer(text):
        word = match.group(1)
        if word.upper() in _WORDS:
            continue
        if match.group(2):
            if word.upper() not in _PURE:
                return None
            continue
        if word.lower() not in context.locals:
            return None
        names.add(word.lower())
    return frozenset(names)


def _carried_back(routine: M.Routine, module: M.Module | None) -> dict[str, set[str]]:
    if module is None:
        return {}
    return {r.name.lower(): {(p.carried_from or p.name).lower() for p in r.parameters
                             if p.carried and p.direction in ("OUT", "IN OUT")}
            for r in module.routines if r.enclosing == routine.id}


def _writes(statement: M.Statement, context: _Context) -> set[str]:
    """What a statement other than a plain assignment may write, read conservatively: every INTO target, a
    variable handed as a call's argument whatever the parameter's mode, `USING OUT`, and what a lifted procedure
    hands back. A nested statement's writes count as its own."""
    from .lower import _walk

    names: set[str] = set()
    for node in [statement] + _walk([statement])[1:]:
        if node.kind == "Assignment" and node.target:
            names.add(re.split(r"[.(]", node.target.strip(), maxsplit=1)[0].lower())
        for target in getattr(node, "into_targets", None) or []:
            names.add(re.split(r"[.(]", target.strip(), maxsplit=1)[0].lower())
        for bind in getattr(node, "using", None) or []:
            if (bind.direction or "IN").upper() != "IN":
                names.add((bind.plsql_variable or bind.name or "").lower())
        if node.kind == "Call":
            callee = (node.callee or "").strip().lower()
            if not callee.startswith("dbms_output."):
                for argument in node.arguments or []:
                    value = re.sub(r"^\s*[A-Za-z][\w$#]*\s*=>", "", argument)
                    bare = re.fullmatch(r"\s*([A-Za-z][\w$#]*)(?:\s*\.\s*[A-Za-z][\w$#]*)?\s*", value)
                    if bare:
                        names.add(bare.group(1).lower())
            names |= context.carried.get(callee, set())
    for handler in getattr(statement, "exception_handlers", []) or []:
        for node in _walk(handler.body):
            names |= _writes(node, context)
    return names


def _trace(statements: list[M.Statement], stop_at: str, states: list[_State],
           context: _Context) -> list[_State] | None:
    """Follow the assignments down every branch until the dynamic statement, carrying one state per path."""
    for statement in statements:
        if statement.id == stop_at:
            return states
        if statement.kind == "Assignment":
            for state in states:
                state.written({re.split(r"[.(]", (statement.target or "").strip(), maxsplit=1)[0].lower()},
                              by_assignment=True)
                _apply(state, statement)
        elif statement.kind in ("If", "Case"):
            if statement.kind == "Case" and statement.selector:
                return None   # `CASE x WHEN 1`: the branch value alone is not a condition to evaluate again
            # an ELSIF or ELSE is taken because the conditions before it were false: every condition of the IF is
            # what picks the path, so every one is watched on each path
            reads = [_reads(b.condition, context) for b in statement.branches or []]
            if any(r is None for r in reads):
                return None
            watched = frozenset().union(*reads) if reads else frozenset()
            branched: list[_State] = []
            for branch in statement.branches or []:
                inner = _trace(branch.body, stop_at, [s.fork(branch.condition, watched) for s in states], context)
                if inner is None:
                    return None
                if any(_contains(branch.body, stop_at) for _ in [0]):
                    return inner  # the statement is inside this branch; that path is the answer
                branched.extend(inner)
            else_states = [s.fork(None, watched) for s in states]
            if statement.else_body:
                inner = _trace(statement.else_body, stop_at, else_states, context)
                if inner is None:
                    return None
                if _contains(statement.else_body, stop_at):
                    return inner
                branched.extend(inner)
            else:
                branched.extend(else_states)  # no ELSE: the conditions may all be false
            states = branched
        elif statement.kind == "Loop":
            # a loop can append any number of times; the set is not finite from here
            return None
        elif statement.kind == "Block" and _contains(statement.body, stop_at):
            # the EXECUTE IMMEDIATE is inside a nested block: its body runs in order up to it
            return _trace(statement.body, stop_at, states, context)
        else:
            written = _writes(statement, context)
            for state in states:
                state.written(written)
    return states


def _contains(statements: list[M.Statement], statement_id: str) -> bool:
    from .lower import _walk

    return any(s.id == statement_id for s in _walk(statements))


def _apply(state: _State, statement: M.Assignment) -> None:
    """Update one variable, or mark it unknown. An unknown value never becomes known again."""
    name = (statement.target or "").strip().lower()
    if not name:
        return
    expression = (statement.expression or "").strip()
    literal = _unquote(expression)
    if literal is not None:
        state.values[name] = literal
        return
    append = APPEND_LITERAL.match(expression)
    if append and append.group("var").lower() in state.values:
        state.values[name] = state.values[append.group("var").lower()] + _unescape(append.group("text"))
        return
    state.values.pop(name, None)


def _unquote(expression: str) -> str | None:
    match = ASSIGN_LITERAL.match(expression)
    return _unescape(match.group("text")) if match else None


def _unescape(text: str) -> str:
    return text.replace("''", "'")


# --------------------------------------------------------------------------------------------------

def annotate(routine: M.Routine, statement: M.DynamicSql,
             module: M.Module | None = None) -> list[Variant] | None:
    """Record the variants on the statement, with what folding does *not* establish."""
    variants = enumerate_variants(routine, statement, module)
    if variants is None:
        return None
    statement.variants = [v.as_dict() for v in variants]
    if len(variants) == 1 and not variants[0].guard:
        statement.constant_sql = variants[0].sql

    where = statement.source_range
    statement.diagnostics.append(Issue(
        "INFO", "DYN_FOLDED",
        f"dynamic SQL resolved to {len(variants)} statement(s); each is converted and checked like static SQL",
        where))
    # Folding makes the text visible. It does not make the two things below true, and a reader who sees plain
    # SQL will assume they were checked unless it is said here.
    if statement.using:
        statement.diagnostics.append(Issue(
            "WARN", "DYN_BIND",
            f"USING binds ({', '.join(b.name for b in statement.using)}) are restored positionally; "
            "confirm each lands on the placeholder it did before", where))
    statement.diagnostics.append(Issue(
        "WARN", "DYN_PRIVILEGE",
        "EXECUTE IMMEDIATE runs with the privileges of the executing user, which a static statement may not "
        "have; confirm the caller is allowed to run this", where))
    return variants


PLACEHOLDER = re.compile(r":(?P<name>[\w$#]+)")


def bind_using(sql: str, using: list) -> str:
    """動的 SQL の `:s` を、`USING` が渡す変数の名前に置き換える。

    Oracle は `USING` を**位置で**束縛する——placeholder の名前は呼び出し側の変数名と関係が無い。
    ここで**変数名そのもの**に直しておくと、畳んだ文がそのあと静的な文とまったく同じ道を通る:
    列への帰属も、型の変換も、生成される repository の引数も、書き分けずに済む。`:s` のまま
    渡すと、束縛する値を持たない placeholder として残る。

    置き換えられない（`USING` の数が足りない）ときは、そのまま返す。触らずに残った `:s` は
    束縛されない値として残り、生成物の側で見える——推測で埋めるより良い。
    """
    values = [b.plsql_variable or b.name for b in using or []]
    if not values:
        return sql
    index = 0

    def replace(match):
        nonlocal index
        if index >= len(values):
            return match.group(0)
        name = values[index]
        index += 1
        return name

    return PLACEHOLDER.sub(replace, sql)


# What Oracle runs as DDL: each commits the transaction before and after it, and on the target the schema is Schema
# Loader's, never a statement of the routine (#52). TRUNCATE is one of them, not a DELETE (#148 H1)
DDL = re.compile(r"^\s*(CREATE|DROP|ALTER|TRUNCATE|RENAME|GRANT|REVOKE|COMMENT|ANALYZE|AUDIT|NOAUDIT|ASSOCIATE|"
                 r"DISASSOCIATE|PURGE|FLASHBACK)\b", re.IGNORECASE)


# `TRUNCATE TABLE t` alone (storage clauses allowed): made the DELETE of every row (#154, the user's decision)
TRUNCATE = re.compile(r"^\s*TRUNCATE\s+TABLE\s+(?P<table>[A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?)"
                      r"(?:\s+(?:(?:DROP|REUSE)(?:\s+ALL)?\s+STORAGE|(?:PRESERVE|PURGE)\s+MATERIALIZED\s+VIEW\s+LOG))*"
                      r"\s*;?\s*$", re.IGNORECASE)
TRUNCATE_AS_DELETE = "TRUNCATE_AS_DELETE"


def fold(program: M.Program) -> None:
    """#148 H1: fold every dynamic statement whose text is knowable, **before** the lowering that works on statements.

    The folded statements used to be made by the capability check, the last step of the analysis. Everything that
    looks at a statement before it -- the trigger calls, the constraint guards, the row-lock and DDL checks -- had
    run already, and the rules and the write-then-scan walk did not look at `variant_statements` at all. The same
    `UPDATE` on a table with a trigger was REDESIGN written statically and AUTO written as `EXECUTE IMMEDIATE '...'`.
    Folding here gives each variant what a static statement has at this point: its kind, its tables, its row lock,
    and the DDL mark. The capability check then converts these same nodes.
    """
    from .lower import _walk

    for module in program.modules:
        for routine in module.routines:
            for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
                if statement.kind == "DynamicSql" and not statement.variants:
                    expand(routine, statement, module)


def expand(routine: M.Routine, statement: M.DynamicSql, module: M.Module | None = None) -> list[M.SqlOperation]:
    """The static statements `statement` can run, made into SqlOperations on `statement.variant_statements`."""
    from .lower import mark_row_lock

    for index, variant in enumerate(annotate(routine, statement, module) or [], start=1):
        # `USING` は**位置で**束縛される。placeholder を渡す変数の名前に直しておくと、畳んだ文がそのあと
        # 静的な文とまったく同じ道を通る（P4-7）
        sql = bind_using(variant.sql, statement.using)
        truncated = TRUNCATE.match(sql)
        if truncated:
            sql = f"DELETE FROM {truncated.group('table')}"
        keyword = re.match(r"\s*([A-Za-z]+)", sql)
        operation = M.SqlOperation(id=f"{statement.id}#variant-{index}", kind="SqlOperation",
                                   source_range=statement.source_range, original_sql=sql,
                                   sql_kind=keyword.group(1).upper() if keyword else "UNKNOWN",
                                   binds=list(statement.using), into_targets=list(statement.into_targets))
        mark_row_lock(operation, sql)
        operation.read_set, operation.write_set = _tables(sql)
        ddl = DDL.match(sql)
        if truncated:
            operation.add("INFO", TRUNCATE_AS_DELETE,
                          f"TRUNCATE TABLE {truncated.group('table')} を全行の DELETE にした（#154）。Oracle の TRUNCATE は"
                          f"前後で COMMIT し、取り消せない。移行先の DELETE はこのトランザクションの一部で、失敗すれば"
                          f"ほかの書き込みと一緒に戻る。DELETE の trigger は Oracle と同じく掛けない")
        elif ddl:
            why = omitted_ddl(routine.id)
            if why:
                operation.add("INFO", "DYNAMIC_DDL_OMITTED",
                              f"{ddl.group(1).upper()} は移行先では実行しない（limits.yaml ddl.omit: {why}）")
            else:
                operation.add("ERROR", "DYNAMIC_DDL",
                              f"routine の中の DDL（{ddl.group(1).upper()}）。Oracle では前後で COMMIT し、移行先では "
                              f"スキーマを Schema Loader が持つので、routine からは実行しない（#52）")
        statement.variant_statements.append(operation)
    # the dynamic statement touches what any of its variants does: the walks that read `read_set` / `write_set`
    # (write-then-scan, the routine's effects) see it without knowing about variants
    statement.read_set = list(dict.fromkeys(t for v in statement.variant_statements for t in v.read_set))
    statement.write_set = list(dict.fromkeys(t for v in statement.variant_statements for t in v.write_set))
    return statement.variant_statements


def _tables(sql: str) -> tuple[list[str], list[str]]:
    import sqlglot
    from sqlglot import exp

    from .sqlbridge import read_write_sets

    try:
        tree = sqlglot.parse_one(sql, dialect="oracle")
    except Exception:  # noqa: BLE001 - an unparsable variant is refused by the converter, with the reason
        return [], []
    if isinstance(tree, exp.TruncateTable):
        return [], list(dict.fromkeys(t.name.lower() for t in tree.expressions if isinstance(t, exp.Table)))
    if tree is None or not isinstance(tree, (exp.Select, exp.Union, exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        return [], []
    return read_write_sets(tree)
