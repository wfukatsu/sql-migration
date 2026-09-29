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
from dataclasses import dataclass, field

from .ir import model as M
from .source import Issue

# How many variants are worth enumerating. Ten is already a lot to review; past that the honest answer is that
# nobody knows what this statement runs, which is what REVIEW means.
MAX_VARIANTS = 8

from .limits import MAX_HOLE_COMBINATIONS  # noqa: E402 - #165: the cap on one statement's combinations

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
    lists = hole_lists(routine.id)
    bare = re.fullmatch(r"[\w$#]+", target) is not None
    if not bare and not lists:
        # an expression, not a variable: `'DELETE FROM ' || p_table_name` and the like. Knowable only when
        # a person has listed the table names it may take (2026-09-19)
        return _allowed(routine, target)

    def otherwise() -> list[Variant] | None:
        return None if bare else _allowed(routine, target)

    context = _Context(locals={p.name.lower() for p in routine.parameters}
                              | {d.name.lower() for d in routine.declarations if d.declaration_kind != "cursor"},
                       carried=_carried_back(routine, module), lists=lists)
    states = _trace(routine.body, statement.id, [_State(guard=(), values={})], context)
    if states is None:
        return otherwise()
    built: list[tuple[_State, str]] = []
    for state in states:
        if state.tainted:
            return otherwise()   # its guard may no longer say what it said when the string was built (#107)
        value = state.values.get(target.lower()) if bare else _concat(state, target, context)
        if value is None:
            return otherwise()  # one path leaves the statement unknown, so the set is not knowable
        built.append((state, value))
    if any(_MARK in value for _, value in built):
        # #165: a hole with a list of values: one statement per combination
        return _combinations(built, lists) or otherwise()
    variants = [Variant(guard=" AND ".join(state.guard), sql=value) for state, value in built]
    unique = list(dict.fromkeys(variants))
    return unique if 0 < len(unique) <= MAX_VARIANTS else None


import contextvars

_ALLOWED: "contextvars.ContextVar[dict[str, list[str]]]" = contextvars.ContextVar("allowed", default={})


def set_allowed_tables(allowed: dict[str, list[str]]) -> None:
    _ALLOWED.set(dict(allowed))


# #165: limits.yaml `dynamicSql` -- routine id -> hole (the variable's name, lower case) -> the values it may take
_HOLES: "contextvars.ContextVar[dict[str, dict[str, list[str]]]]" = contextvars.ContextVar("holes", default={})


def set_hole_lists(lists: dict[str, dict[str, list[str]]]) -> None:
    _HOLES.set({routine: {k.lower(): list(v) for k, v in by_hole.items()} for routine, by_hole in lists.items()})


def hole_lists(routine_id: str) -> dict[str, list[str]]:
    return _HOLES.get().get(routine_id) or {}


# a listed hole spliced into the string being traced: \x01name\x1fexpression\x02, replaced by each value at the end
_MARK = "\x01"
_MARKED = re.compile("\x01([^\x1f]*)\x1f([^\x02]*)\x02")


def hole_key(expression: str) -> str | None:
    """The name a person writes for a hole in limits.yaml `dynamicSql`: the variable spliced in, or the one a
    `DBMS_ASSERT` function wraps. None for any other expression (`UPPER(p)`, `f(x)`): its value is not the value of
    one variable, so a list of the variable's values would not say what is spliced."""
    text = (expression or "").strip()
    wrapped = ASSERT.match(text)
    name = wrapped.group("name") if wrapped else text
    return name.lower() if re.fullmatch(r"[A-Za-z][\w$#]*", name) else None


def _concat(state: "_State", expression: str, context: "_Context") -> str | None:
    """#165: the text a concatenation makes on one path, with each listed hole marked; None when a term is unknown.

    A term is a literal, a number, a variable this path has a text for, or a hole with a list in limits.yaml
    `dynamicSql` (a parameter or a local of the routine). The hole's variable is watched from here on: written
    after it was spliced, the value the generated code compares at the EXECUTE IMMEDIATE is no longer the one the
    string was built with (#107)."""
    out = ""
    for term in _terms(expression):
        literal = _unquote(term)
        if literal is not None:
            out += literal
            continue
        key = hole_key(term)
        if key is not None and key in context.lists and key in context.locals:
            out += f"{_MARK}{key}\x1f{term}\x02"
            state.watched = state.watched | {key}
        elif re.fullmatch(r"[A-Za-z][\w$#]*", term) and term.lower() in state.values:
            out += state.values[term.lower()]
        elif re.fullmatch(r"-?\d+(\.\d+)?", term):
            out += term
        else:
            return None
    return out


def _combinations(built: list[tuple["_State", str]], lists: dict[str, list[str]]) -> list[Variant] | None:
    """#165: every combination of the listed values, each a static statement guarded by the values it was made with.

    A hole that lands where an identifier or a keyword goes (a column, the ORDER BY direction) is compared without
    regard to case, as Oracle reads an unquoted name; one inside a quoted literal or a value position is compared
    exactly, because there the case is part of the value. A value nobody listed matches no variant, and the
    generated code refuses it at run time."""
    import itertools

    total = 0
    for _, text in built:
        names = list(dict.fromkeys(m.group(1) for m in _MARKED.finditer(text)))
        count = 1
        for name in names:
            count *= len(lists[name])
        total += count
    if len(built) > MAX_VARIANTS or total > MAX_HOLE_COMBINATIONS:
        return None
    variants: list[Variant] = []
    for state, text in built:
        names = list(dict.fromkeys(m.group(1) for m in _MARKED.finditer(text)))
        exact = {name: False for name in names}
        for match in _MARKED.finditer(text):
            before = _MARKED.sub(f" {_HOLE} ", text[:match.start()])
            after = _MARKED.sub(f" {_HOLE} ", text[match.end():])
            quoted = before.rstrip().endswith('"') or after.lstrip().startswith('"')
            if quoted or not Hole(match.group(2), _position(match.group(2), before, after)).identifier:
                exact[match.group(1)] = True
        branch = [f"({condition})" for condition in state.guard]   # an OR in a branch condition stays inside
        for values in itertools.product(*(lists[name] for name in names)):
            chosen = dict(zip(names, values))
            sql = _MARKED.sub(lambda m: chosen[m.group(1)], text)
            checks = [f"{name} = '{_quote(value)}'" if exact[name] else f"UPPER({name}) = '{_quote(value.upper())}'"
                      for name, value in chosen.items()]
            variants.append(Variant(guard=" AND ".join(branch + checks), sql=sql))
    unique = list(dict.fromkeys(variants))
    return unique or None


def _quote(text: str) -> str:
    return text.replace("'", "''")


def unexpanded_reason(routine: M.Routine, statement: M.Statement, lists: dict[str, list[str]],
                      tables: list[str]) -> str:
    """#165: why a dynamic statement in a routine that has a list (`dynamicSql` or `dynamicTables`) was not expanded,
    for the person deciding: which holes have no list, or how far past the limit the combinations go."""
    lists = {k.lower(): v for k, v in (lists or {}).items()}
    found = list(dict.fromkeys(h.text for h in holes(routine, statement)))
    if not lists:
        if len(found) > 1:
            return (f"連結する箇所（穴）が {len(found)} つある（{', '.join(found)}）。dynamicTables は穴が 1 つの文にしか"
                    f"効かない。limits.yaml の dynamicSql.{routine.id}.holes に穴ごとの一覧を書く")
        return ("dynamicTables の表名で展開できない（表名を 1 つの引数か局所変数で連結する形でない、表名が "
                f"{MAX_VARIANTS} を超える、など）")
    missing = [text for text in found if hole_key(text) not in lists]
    if missing:
        return (f"穴 {', '.join(missing)} に limits.yaml dynamicSql.{routine.id}.holes の一覧が無い。"
                f"一覧が要るのは連結している変数（引数か局所変数）で、式で加工した値は変数に入れてから連結する")
    count = 1
    for key in dict.fromkeys(hole_key(text) for text in found):
        count *= len(lists[key])
    if count > MAX_HOLE_COMBINATIONS:
        return (f"穴ごとの一覧の組み合わせが {count} 通りあり、上限 {MAX_HOLE_COMBINATIONS} を超える。"
                f"一覧を絞るか、文を分ける")
    return ("穴ごとの一覧はあるが、文の組み立てを追えない（ループの中で連結する、連結したあとで変数を書き換える、"
            "条件が関数やパッケージ変数を読む、など）")


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


# --------------------------------------------------------------------------------------------------
# #157 DYN-001: where in the statement a concatenated value lands.
#
# 設計書 §6.8 は「動的 table / column」と「動的 where / order」を REDESIGN にしている。以前の検出は表の位置
# （FROM / INTO / TABLE / JOIN / UPDATE の直後）だけを見ていて、列名・ORDER BY・PL/SQL ブロックの routine 名を
# 連結する文は DYN-002（REVIEW）に落ちていた。ここでは連結の「穴」（literal でない項）ごとに、組み上がる文の中の
# 位置を判定する。値の位置（引用符の中、比較の右辺、VALUES など）は DYN-002 のまま、識別子や SQL の断片の位置は
# DYN-001 にする。

# 識別子の位置。どれかに穴があれば DYN-001（REDESIGN）
IDENTIFIER_POSITIONS = frozenset({"object", "column", "order", "routine", "fragment", "name"})


@dataclass(frozen=True)
class Hole:
    """One non-literal term spliced into a dynamic statement, and where it lands."""

    text: str        # the PL/SQL expression, as written
    position: str    # object | column | order | routine | fragment | name | value | unknown

    @property
    def identifier(self) -> bool:
        return self.position in IDENTIFIER_POSITIONS


# 名前を確かめる DBMS_ASSERT の関数で包んだ項は、位置を見るまでもなく識別子である。ENQUOTE_LITERAL は値
_NAME_ASSERT = re.compile(r"^\s*(?:SYS\s*\.\s*)?DBMS_ASSERT\s*\.\s*(ENQUOTE_NAME|SIMPLE_SQL_NAME|QUALIFIED_SQL_NAME|"
                          r"SQL_OBJECT_NAME|SCHEMA_NAME)\s*\(", re.IGNORECASE)
_LITERAL_ASSERT = re.compile(r"^\s*(?:SYS\s*\.\s*)?DBMS_ASSERT\s*\.\s*ENQUOTE_LITERAL\s*\(", re.IGNORECASE)
# the keyword right before the hole names an object: `DELETE FROM ' || p_tab`, `DROP SEQUENCE ' || s.name`
_OBJECT_BEFORE = re.compile(r"\b(FROM|INTO|TABLE|JOIN|UPDATE|SEQUENCE|VIEW|INDEX|SYNONYM|PROCEDURE|FUNCTION|"
                            r"PACKAGE|TRIGGER|CALL)$")
_CLAUSE = re.compile(r"\b(SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|SET|VALUES|INTO|ON|BEGIN|DECLARE|"
                     r"FETCH|OFFSET|LIMIT|UNION|INTERSECT|MINUS|RETURNING|USING|CONNECT\s+BY|START\s+WITH)\b")
# a value follows: an operator, or a keyword that takes a value (`BETWEEN 1 AND '` is handled apart)
_VALUE_BEFORE = re.compile(r"(<>|!=|<=|>=|:=|=|<|>|\+|-|\*|/|\|\||\b(LIKE|BETWEEN|WHEN|THEN|ELSE|LIMIT|OFFSET|FIRST|"
                           r"NEXT|RETURN|ESCAPE))$")
_BETWEEN_AND = re.compile(r"\bBETWEEN\s+[^\s]+\s+AND$")
_COMPARED_AFTER = re.compile(r"^(<>|!=|<=|>=|=|<|>|\b(IS|IN|LIKE|BETWEEN|NOT)\b)")
_PREDICATE_START = re.compile(r"(\b(WHERE|AND|OR|NOT|ON|HAVING)|\()$")
_PLSQL_STATEMENT_START = re.compile(r"(\b(BEGIN|THEN|ELSE|LOOP|DECLARE)|;)$")
# how many alternatives one variable may take before the rest are dropped: the positions, not the statements,
# are what is asked, and a routine that builds more than this is REVIEW or REDESIGN anyway
_MAX_SKELETONS = 16
_HOLE = "\x00"


def holes(routine: M.Routine | None, statement: M.Statement) -> list[Hole]:
    """Every non-literal term the dynamic statement is built from, with where it lands in the statement (#157).

    The string is followed through the routine's assignments, in the order they are written, ignoring the
    branches (a variable takes every value any assignment gave it so far): `v := 'SELECT ... ORDER BY '` followed
    by `v := v || p_col` puts `p_col` in the ORDER BY. A variable nobody assigned a string to -- a parameter, a
    function's result, one an INTO or a call wrote -- is a hole. So is a variable that holds a bare name, even a
    known one (`v_table := 'EMPLOYEES'; ... FROM ' || v_table`): the name is still chosen at run time, which is
    what `dynamicTables` decides (samples/oracle-samples b06_3).
    """
    values: dict[str, list[tuple[tuple[str, str], ...]]] = {}
    if routine is not None:
        from .lower import _walk

        for declaration in routine.declarations:
            initial = getattr(declaration, "initial", None)
            if initial and declaration.declaration_kind != "cursor":
                values[declaration.name.lower()] = _evaluate(initial, values)
        context = _Context(locals={p.name.lower() for p in routine.parameters}
                                  | {d.name.lower() for d in routine.declarations}, carried={})
        body = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
        for other in body:
            if other.id == statement.id:
                break
            if other.kind == "Assignment" and other.target and other.expression:
                name = other.target.strip().lower()
                if re.fullmatch(r"[\w$#]+", name):
                    known = values.get(name, [])
                    values[name] = list(dict.fromkeys(known + _evaluate(other.expression, values)))[:_MAX_SKELETONS]
            elif other.kind not in ("If", "Case", "Loop", "Block"):
                # `SELECT ... INTO v_sql`, `pick(v_sql)`: from here on the variable may hold anything
                for name in _writes(other, context):
                    if name in values:
                        values[name] = list(dict.fromkeys(values[name] + [(("hole", name),)]))[:_MAX_SKELETONS]
    found: dict[tuple[str, str], Hole] = {}
    for skeleton in _evaluate(getattr(statement, "expression", "") or "", values):
        for index, (kind, text) in enumerate(skeleton):
            if kind != "hole":
                continue
            before = "".join(t if k == "lit" else f" {_HOLE} " for k, t in skeleton[:index])
            after = "".join(t if k == "lit" else f" {_HOLE} " for k, t in skeleton[index + 1:])
            hole = Hole(text=text, position=_position(text, before, after))
            found.setdefault((hole.text, hole.position), hole)
    return list(found.values())


def interpolates_identifier(routine: M.Routine | None, statement: M.Statement) -> bool:
    """DYN-001: does the dynamic statement splice something into an identifier (or a piece of SQL syntax)?"""
    return any(hole.identifier for hole in holes(routine, statement))


def _evaluate(expression: str, values: dict) -> list[tuple[tuple[str, str], ...]]:
    """The skeletons an expression can make: each a list of ("lit", text) and ("hole", expression)."""
    skeletons: list[tuple[tuple[str, str], ...]] = [()]
    for index, term in enumerate(_terms(expression)):
        literal = _unquote(term)
        if literal is not None:
            options = [(("lit", literal),)]
        elif re.fullmatch(r"[A-Za-z][\w$#]*", term) and term.lower() in values:
            options = values[term.lower()] or [(("hole", term),)]
            if index > 0 and any(_bare_name(o) for o in options):
                options = [(("hole", term),)]   # a name spliced in: chosen at run time even when it is known
        elif re.fullmatch(r"-?\d+(\.\d+)?", term):
            options = [(("lit", term),)]   # a number is spliced as its digits
        else:
            options = [(("hole", term),)]
        skeletons = [s + o for s in skeletons for o in options][:_MAX_SKELETONS]
    return skeletons


def _bare_name(skeleton: tuple[tuple[str, str], ...]) -> bool:
    """A value that is one name (or number), not a piece of SQL: `'EMPLOYEES'`, not `' AND x = 1'`."""
    return all(kind == "lit" for kind, _ in skeleton) and \
        re.fullmatch(r'\s*[\w$#."]+\s*', "".join(text for _, text in skeleton)) is not None


def _terms(expression: str) -> list[str]:
    """`a || 'b' || f(c || d)` -> ['a', "'b'", 'f(c || d)']: split on `||` outside quotes and parentheses."""
    out, current, quoted, depth, index = [], "", False, 0, 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
        if not quoted and depth == 0 and expression.startswith("||", index):
            out.append(current.strip())
            current = ""
            index += 2
            continue
        current += char
        index += 1
    out.append(current.strip())
    return [term for term in out if term]


def _position(text: str, before: str, after: str) -> str:
    """Where a hole lands, from the SQL text on either side of it."""
    if _LITERAL_ASSERT.match(text):
        return "value"
    if _NAME_ASSERT.match(text):
        return "name"
    if before.count("'") % 2 == 1:
        return "value"   # inside a quoted literal: `WHERE name = ''' || p || ''''`
    bare = re.sub(r"'[^']*'", "''", before).upper()
    tail = bare.rstrip()
    following = re.sub(r"'[^']*'", "''", after).upper().lstrip()
    if not tail.strip():
        return "unknown"   # the whole statement, or what the text before it holds is not known
    if _OBJECT_BEFORE.search(tail):
        return "object"
    plsql = re.match(r"\s*(BEGIN|DECLARE)\b", bare) is not None
    if plsql and _PLSQL_STATEMENT_START.search(tail):
        return "routine"   # `'BEGIN ' || l_fn || '(...); END;'`: the routine called, or a whole statement
    if _BETWEEN_AND.search(tail) or _VALUE_BEFORE.search(tail):
        return "value"
    clauses = _CLAUSE.findall(tail)
    clause = re.sub(r"\s+", " ", clauses[-1]) if clauses else ""
    if clause == "ORDER BY":
        return "order"    # a sort expression or its direction (`ORDER BY ' || p_col || ' ' || p_dir`)
    if clause == "GROUP BY":
        return "column"
    if clause == "FROM" and tail.endswith(","):
        return "object"
    if clause == "SELECT" and re.search(r"(\b(SELECT|DISTINCT)|,|\()$", tail):
        return "column"
    if clause in ("INTO", "INSERT") and re.search(r"[(,]$", tail):
        return "column"   # `INSERT INTO t (' || p_cols || ') VALUES ...`
    if clause == "SET" and re.search(r"(\bSET|,)$", tail):
        return "column" if following.startswith("=") else "fragment"
    if clause in ("WHERE", "ON", "HAVING", "CONNECT BY", "START WITH"):
        if re.search(r"\bIN\s*\($", tail):
            return "value"   # `IN (' || p_list || ')'`: a list of values
        if re.search(r"[\w$#]\s*\($", tail):
            return "value"   # a function's argument
        if _PREDICATE_START.search(tail):
            return "column" if _COMPARED_AFTER.match(following) else "fragment"
    if re.search(r"[(,]$", tail):
        return "value"       # an argument, a VALUES list
    if re.search(r"[\w$#)\"]$", tail):
        return "fragment"    # after a complete token and no operator: `'SELECT * FROM t ' || p_clause`
    return "unknown"


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
    lists: dict[str, list[str]] = field(default_factory=dict)   # #165: limits.yaml dynamicSql, hole -> values


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
                _apply(state, statement, context)
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


def _apply(state: _State, statement: M.Assignment, context: _Context | None = None) -> None:
    """Update one variable, or mark it unknown. An unknown value never becomes known again.

    With hole lists (#165) a concatenation of literals, known variables and listed holes is followed too."""
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
    if context is not None and context.lists:
        value = _concat(state, expression, context)
        if value is not None:
            state.values[name] = value
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
        lists, tables = hole_lists(routine.id), _ALLOWED.get().get(routine.id)
        if (lists or tables) and interpolates_identifier(routine, statement) and \
                not any(d.code == HOLES_UNDECIDED for d in statement.diagnostics):
            statement.diagnostics.append(Issue("WARN", HOLES_UNDECIDED,
                                               unexpanded_reason(routine, statement, lists, tables or []),
                                               statement.source_range))
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


# #165: a routine has a list, and this statement was still not expanded: it stays REDESIGN, and this says why
HOLES_UNDECIDED = "DYN_HOLES_UNDECIDED"


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
