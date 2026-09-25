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


def enumerate_variants(routine: M.Routine, statement: M.DynamicSql) -> list[Variant] | None:
    """Every statement `statement` can run, or None when that is not a finite knowable set."""
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

    states = _trace(routine.body, statement.id, [_State(guard=(), values={})])
    if states is None:
        return None
    variants: list[Variant] = []
    for state in states:
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

    def fork(self, condition: str | None) -> "_State":
        return _State(guard=self.guard + ((condition,) if condition else ()), values=dict(self.values))


def _trace(statements: list[M.Statement], stop_at: str, states: list[_State]) -> list[_State] | None:
    """Follow the assignments down every branch until the dynamic statement, carrying one state per path."""
    for statement in statements:
        if statement.id == stop_at:
            return states
        if statement.kind == "Assignment":
            for state in states:
                _apply(state, statement)
        elif statement.kind in ("If", "Case"):
            branched: list[_State] = []
            for branch in statement.branches or []:
                inner = _trace(branch.body, stop_at, [s.fork(branch.condition) for s in states])
                if inner is None:
                    return None
                if any(_contains(branch.body, stop_at) for _ in [0]):
                    return inner  # the statement is inside this branch; that path is the answer
                branched.extend(inner)
            else_states = [s.fork(None) for s in states]
            if statement.else_body:
                inner = _trace(statement.else_body, stop_at, else_states)
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

def annotate(routine: M.Routine, statement: M.DynamicSql) -> list[Variant] | None:
    """Record the variants on the statement, with what folding does *not* establish."""
    variants = enumerate_variants(routine, statement)
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
