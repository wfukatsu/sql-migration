"""P2-1: control flow, call graph, effects.

The rules (P2-2, P2-3, P2-4) decide; this module supplies the evidence they decide from. Three questions the IR
alone cannot answer:

* **Where can control go?** A `COMMIT` inside a loop that an exception can jump out of is a different problem from
  a `COMMIT` at the end of a body. The CFG makes that difference visible.
* **What does this routine reach?** A routine with no `COMMIT` of its own still controls the transaction if it
  calls one that does. Transaction effects therefore have to propagate along the call graph, not stop at the
  routine that happens to contain the statement.
* **What does it touch?** Read and write sets come from the SQL, and their intersection is what makes
  write-then-scan (docs/design/plsql-kpi.md §2, condition 11) detectable before anything runs.

    result = analyse(program)
    result.effective[routine_id].controls_transaction   # including through calls
    result.write_then_scan()                            # the ScalarDB restriction, statically
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot

from .ir import model as M
from .lower import _walk
from .source import Issue

TERMINATORS = {"Return", "Raise", "Goto"}
LOOP_KINDS = {"basic", "while", "for", "cursor-for", "forall"}


# --- control flow -------------------------------------------------------------------------------------

@dataclass
class ControlFlowGraph:
    """Statement ids and the edges between them, per routine.

    `ENTRY` and `EXIT` are synthetic so that every real statement has a predecessor and a successor, which is what
    makes reachability a plain graph question rather than a special case at the ends.
    """

    routine: str
    nodes: dict[str, M.Statement] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)
    entry: str = "ENTRY"
    exit: str = "EXIT"

    def successors(self, node: str) -> set[str]:
        return {b for a, b in self.edges if a == node}

    def predecessors(self, node: str) -> set[str]:
        return {a for a, b in self.edges if b == node}

    def reachable(self) -> set[str]:
        seen = {self.entry}
        stack = [self.entry]
        while stack:
            for nxt in self.successors(stack.pop()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    def unreachable(self) -> list[str]:
        """Statements no path reaches. Dead code in the source stays dead in the translation."""
        return sorted(set(self.nodes) - self.reachable())

    def inside_loop(self, node: str) -> bool:
        return node in self._loop_members

    _loop_members: set[str] = field(default_factory=set)


def build_cfg(routine: M.Routine) -> ControlFlowGraph:
    cfg = ControlFlowGraph(routine=routine.id)
    for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
        cfg.nodes[statement.id] = statement

    exits = _link(cfg, routine.body, {cfg.entry})
    for node in exits:
        cfg.edges.add((node, cfg.exit))

    # every statement can raise, so every handler is reachable from the body
    for handler in routine.exception_handlers:
        handler_exits = _link(cfg, handler.body, set(cfg.nodes) - {cfg.exit} or {cfg.entry})
        for node in handler_exits:
            cfg.edges.add((node, cfg.exit))
        if not handler.body:
            cfg.edges.add((cfg.entry, cfg.exit))
    if not routine.body:
        cfg.edges.add((cfg.entry, cfg.exit))
    return cfg


def _link(cfg: ControlFlowGraph, statements: list[M.Statement], incoming: set[str]) -> set[str]:
    """Wire a sequence, returning the nodes control can leave it from."""
    current = set(incoming)
    for statement in statements:
        for node in current:
            cfg.edges.add((node, statement.id))
        current = {statement.id}

        if statement.kind == "If":
            ends: set[str] = set()
            for branch in statement.branches:
                ends |= _link(cfg, branch.body, {statement.id})
            ends |= _link(cfg, statement.else_body, {statement.id})
            if not statement.else_body:
                ends.add(statement.id)  # the condition may be false and skip every branch
            current = ends or {statement.id}
        elif statement.kind == "Case":
            ends = set()
            for branch in statement.branches:
                ends |= _link(cfg, branch.body, {statement.id})
            ends |= _link(cfg, statement.else_body, {statement.id})
            current = ends or {statement.id}
        elif statement.kind == "Loop":
            query = getattr(statement, "query", None)
            if query is not None:
                # a cursor FOR loop runs its query once, on entry. It is not a branch point, so it hangs off
                # the loop rather than sitting in the chain -- enough to make it reachable, which is what the
                # dead-code check asks.
                cfg.edges.add((statement.id, query.id))
            body_ends = _link(cfg, statement.body, {statement.id})
            for node in body_ends:
                cfg.edges.add((node, statement.id))  # back edge
            for node in _walk(statement.body):
                cfg._loop_members.add(node.id)
            cfg._loop_members.add(statement.id)
            current = {statement.id}
        elif statement.kind == "Block":
            # a nested `BEGIN ... EXCEPTION ... END` (#18). Its body runs in sequence; its handlers are
            # reachable from anywhere in that body, because any statement in it can raise -- the same rule
            # the routine's own handlers get, applied to the block's extent rather than the whole routine.
            ends = _link(cfg, statement.body, {statement.id})
            for handler in statement.exception_handlers:
                ends |= _link(cfg, handler.body, {statement.id} | {s.id for s in _walk(statement.body)})
            current = ends or {statement.id}
        elif statement.kind in TERMINATORS:
            cfg.edges.add((statement.id, cfg.exit))
            current = set()
        elif statement.kind == "Exit":
            current = {statement.id} if statement.condition else set()
    return current


# --- call graph ---------------------------------------------------------------------------------------

@dataclass
class CallGraph:
    """Who calls whom. An unresolved callee is kept by name: an external call is information, not a gap."""

    calls: dict[str, set[str]] = field(default_factory=dict)
    external: dict[str, set[str]] = field(default_factory=dict)

    def callees(self, routine: str) -> set[str]:
        return self.calls.get(routine, set())

    def reachable_from(self, routine: str) -> set[str]:
        seen: set[str] = set()
        stack = [routine]
        while stack:
            current = stack.pop()
            for callee in self.calls.get(current, set()):
                if callee not in seen:
                    seen.add(callee)
                    stack.append(callee)
        return seen

    def cycles(self) -> list[list[str]]:
        """Recursive and mutually recursive routines: they need a depth story before they can be generated."""
        found: list[list[str]] = []
        colour: dict[str, int] = {}
        path: list[str] = []

        def visit(node: str) -> None:
            colour[node] = 1
            path.append(node)
            for callee in sorted(self.calls.get(node, set())):
                if callee == node:
                    found.append([node, node])  # direct recursion
                elif colour.get(callee, 0) == 0:
                    visit(callee)
                elif colour.get(callee) == 1:
                    found.append(path[path.index(callee):] + [callee])
            path.pop()
            colour[node] = 2

        for node in sorted(self.calls):
            if colour.get(node, 0) == 0:
                visit(node)
        return found


AMBIGUOUS = "<ambiguous>"


OVERLOADED = "<overloaded>"


class _Names(dict):
    """name -> routine id, plus the functions that can be called with no argument list at all, plus the overload
    sets: a name that several routines of one package share maps to OVERLOADED, and which one a call means is
    decided from its arguments (`_pick`)."""

    no_args: set[str]
    overloads: dict[str, list[M.Routine]]


def _names(program: M.Program) -> dict[str, str]:
    """Every name a call may use -> the routine id. A bare name that two packages both define maps to AMBIGUOUS:
    "the first one seen" linked `pkg_b.run`'s call to its own `helper` (which commits) to `pkg_a.helper` (which
    does not), and `pkg_b.run` came out AUTO."""
    by_name = _Names()
    # `v_n := pkg.open_count;` is a call: PL/SQL needs no parentheses when every parameter can be left out
    by_name.no_args = {r.id for m in program.modules for r in m.routines
                       if r.routine_kind == "function" and all(p.default for p in r.parameters)}
    by_name.overloads = {}
    for module in program.modules:
        for routine in module.routines:
            by_name[routine.id.lower()] = routine.id
            if module.module_kind == "package":
                qualified = f"{module.name}.{routine.name}".lower()
                if qualified != routine.id.lower():      # `pkg.put~2`: one of several `pkg.put`
                    by_name.overloads.setdefault(qualified, []).append(routine)
                    by_name[qualified] = OVERLOADED
                else:
                    by_name[qualified] = routine.id
    for module in program.modules:
        for routine in module.routines:
            bare = routine.name.lower()
            if by_name.get(bare, routine.id) != routine.id:
                by_name[bare] = AMBIGUOUS
            else:
                by_name.setdefault(bare, routine.id)
    return by_name


def _resolve(callee: str, by_name: dict[str, str], module: str, arguments: list[str] | None = None) -> str | None:
    """PL/SQL's own order: a bare name is the caller's package first, and only then anything global.

    An overloaded name resolves only when the arguments leave one candidate (`_pick`); `arguments=None` -- a call
    inside an expression, whose argument list is not captured -- leaves it unresolved."""
    lowered = callee.strip().lower()
    found = None
    if "." not in lowered:
        found = by_name.get(f"{module}.{lowered}".lower())
        lowered = f"{module}.{lowered}".lower() if found is not None else lowered
    if found is None:
        found = by_name.get(lowered)
    if found == OVERLOADED:
        return _pick(getattr(by_name, "overloads", {}).get(lowered, []), arguments)
    return None if found == AMBIGUOUS else found


def overloaded(callee: str, by_name: dict[str, str], module: str) -> bool:
    """Whether the name is an overload set of the program -- known code, even when the call cannot be resolved."""
    lowered = callee.strip().lower()
    names = [f"{module}.{lowered}".lower(), lowered] if "." not in lowered else [lowered]
    return any(by_name.get(n) == OVERLOADED for n in names)


def _pick(candidates: list[M.Routine], arguments: list[str] | None) -> str | None:
    """The one overload a call can mean, from the number of its arguments and the names of the named ones. Nothing
    here infers the type of an argument, so overloads that differ by type only are not told apart: the call stays
    unresolved and the caller is reviewed, rather than given one overload's verdict (decided 2026-09-20)."""
    if arguments is None:
        return None
    named = {a.partition("=>")[0].strip().lower() for a in arguments if "=>" in a}
    fitting = []
    for routine in candidates:
        parameters = [p.name.lower() for p in routine.parameters]
        required = sum(1 for p in routine.parameters if not p.default)
        if required <= len(arguments) <= len(parameters) and named <= set(parameters):
            fitting.append(routine.id)
    return fitting[0] if len(fitting) == 1 else None


# Packages whose calls change nothing a migration has to carry: output for a developer's console, and the
# assertion helpers. Anything else that resolves to no routine in the program is a call into code nobody analysed
HARMLESS_CALLEES = re.compile(r"^(DBMS_OUTPUT\.\w+|DBMS_ASSERT\.\w+)$", re.IGNORECASE)
_QUALIFIED_CALL = re.compile(r"\b([A-Za-z][\w$#]*)\.([A-Za-z][\w$#]*)\s*\(")
# `v_ids.COUNT(...)`-style collection and cursor methods, which are not calls into a package
_COLLECTION_METHODS = {"count", "exists", "first", "last", "next", "prior", "delete", "extend", "trim", "limit"}


def _declared_names(module: M.Module, routine: M.Routine) -> set[str]:
    names = {d.name.lower() for d in list(routine.declarations) + list(module.declarations)}
    names |= {p.name.lower() for p in routine.parameters}
    for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
        names |= {d.name.lower() for d in getattr(statement, "declarations", []) or []}
        if getattr(statement, "variable", None):
            names.add(statement.variable.lower())
    return names


def build_call_graph(program: M.Program) -> CallGraph:
    graph = CallGraph()
    by_name = _names(program)

    for module in program.modules:
        for routine in module.routines:
            graph.calls.setdefault(routine.id, set())
            graph.external.setdefault(routine.id, set())
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            declared = _declared_names(module, routine)
            # a declaration can call too: `v_n NUMBER := f_commits(p_id);` runs before the first statement
            initialisers = [d.initial for d in routine.declarations if d.initial and d.declaration_kind != "cursor"] \
                + [p.default for p in routine.parameters if p.default] \
                + [d.initial for st in statements for d in getattr(st, "declarations", []) or []
                   if d.initial and d.declaration_kind != "cursor"]
            for expression in initialisers:
                graph.calls[routine.id] |= _called_in(expression, by_name, module.name, routine.id, declared)
                graph.external[routine.id] |= _external_in(expression, by_name, module.name, declared)
            for statement in statements:
                if statement.kind == "Call":
                    resolved = _resolve(statement.callee, by_name, module.name, statement.arguments or [])
                    if resolved is not None:
                        graph.calls[routine.id].add(resolved)
                        statement.resolved_to = resolved
                    elif overloaded(statement.callee, by_name, module.name):
                        _unresolved_overload(statement, statement.callee.strip())
                    elif resolved is None:
                        graph.external[routine.id].add(statement.callee.strip())
                # A function call is usually not a statement: `v := order_total(id)` is an assignment, and a
                # condition may call one too. Looking only at Call nodes found one edge in the whole corpus and
                # made every transitive transaction effect disappear.
                for expression in _expressions(statement):
                    if statement.kind != "Call":
                        for name in _CALLABLE.findall(expression):
                            if overloaded(name, by_name, module.name):
                                _unresolved_overload(statement, name)
                    for resolved in _called_in(expression, by_name, module.name, routine.id, declared):
                        graph.calls[routine.id].add(resolved)
                    if statement.kind != "SqlOperation":   # SQL has its own functions; the converter judges those
                        graph.external[routine.id] |= _external_in(expression, by_name, module.name, declared)
    return graph


def _unresolved_overload(statement: M.Statement, name: str) -> None:
    if any(d.code == "OVERLOAD_UNRESOLVED" for d in statement.diagnostics):
        return
    statement.add("WARN", "OVERLOAD_UNRESOLVED",
                  f"{name} is overloaded, and which one this call means cannot be told from the number and the names "
                  f"of its arguments (a call inside an expression carries no argument list here)")


def _external_in(expression: str, by_name: dict[str, str], module: str, declared: set[str]) -> set[str]:
    """`pkg.fn(...)` inside an expression that resolves to nothing in the program. A bare `fn(...)` is left to the
    generator, which refuses any function it does not know; a qualified one names code that lives elsewhere."""
    found: set[str] = set()
    for prefix, name in _QUALIFIED_CALL.findall(re.sub(r"'(?:[^']|'')*'", "''", expression)):
        if prefix.lower() in declared or name.lower() in _COLLECTION_METHODS:
            continue
        if _resolve(f"{prefix}.{name}", by_name, module) is None and not overloaded(f"{prefix}.{name}", by_name, module):
            found.add(f"{prefix}.{name}")
    return found


_CALLABLE = re.compile(r"\b([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)\s*\(")
# a name with no argument list: a call only if it resolves to a function that takes none (see _Names.no_args)
_BARE_NAME = re.compile(r"(?<![\w$#.:])([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)(?![\w$#.]|\s*\()")


def _expressions(statement: M.Statement) -> list[str]:
    """Every piece of a statement that can hold a call."""
    out: list[str] = []
    for attribute in ("expression", "condition", "original_sql", "selector", "cursor", "message"):
        value = getattr(statement, attribute, None)
        if isinstance(value, str) and value:
            out.append(value)
    for branch in getattr(statement, "branches", []) or []:
        out.append(branch.condition)
    out.extend(getattr(statement, "arguments", []) or [])
    return out


def _called_in(expression: str, by_name: dict[str, str], module: str, caller: str,
               declared: set[str] | frozenset[str] = frozenset()) -> set[str]:
    """Self-calls count. Direct recursion is a self-edge, and excluding it hides the plainest recursion there is.
    `declared` are the caller's own names: a variable hides a parameterless function of the same name."""
    found: set[str] = set()
    for name in _CALLABLE.findall(expression):
        resolved = _resolve(name, by_name, module)
        if resolved is not None:
            found.add(resolved)
    no_args = getattr(by_name, "no_args", None)
    if no_args:
        for name in _BARE_NAME.findall(re.sub(r"'(?:[^']|'')*'", "''", expression)):
            if name.lower() in declared:
                continue
            resolved = _resolve(name, by_name, module)
            if resolved in no_args:
                found.add(resolved)
    return found


# --- effects --------------------------------------------------------------------------------------------

@dataclass
class EffectiveEffects:
    """A routine's own effects plus everything it reaches. This is what a rule should read."""

    transaction: M.TransactionEffects
    external: M.ExternalEffects
    reads: list[str]
    writes: list[str]
    through: list[str] = field(default_factory=list)   # the callees that contributed

    @property
    def controls_transaction(self) -> bool:
        return self.transaction.controls_transaction


def fill_sql_sets(program: M.Program) -> list[Issue]:
    """Give every SqlOperation its read and write sets, from the SQL itself.

    The bridge (P1-6) fills these when it runs, but analysis must not depend on having a ScalarDB schema to hand:
    a rule about which tables a routine writes is answerable from the source alone.
    """
    from .sqlbridge import read_write_sets

    problems: list[Issue] = []
    for module in program.modules:
        for routine in module.routines:
            for statement in _walk(routine.body) + [s for h in routine.exception_handlers
                                                    for s in _walk(h.body)]:
                sql = _statement_sql(statement)
                if sql is None or (statement.read_set or statement.write_set):
                    continue
                try:
                    tree = sqlglot.parse_one(sql, dialect="oracle")
                except Exception as e:  # noqa: BLE001 - an unparsable fragment is a diagnostic
                    problems.append(Issue("WARN", "SQL_PARSE",
                                          f"{type(e).__name__}: {e}", statement.source_range))
                    continue
                statement.read_set, statement.write_set = read_write_sets(tree)
    return problems


_CURSOR_QUERY = re.compile(r"\b(SELECT\b.*)$", re.IGNORECASE | re.DOTALL)


def _statement_sql(statement: M.Statement) -> str | None:
    """The SQL a statement runs, including the query a cursor FOR loop iterates.

    A `FOR r IN (SELECT ...)` is a loop in the IR, not a SqlOperation, so reading only SqlOperation nodes made
    every table a routine iterates invisible -- and with it the write-then-scan pairs that matter most.
    """
    if statement.kind == "SqlOperation":
        return statement.original_sql
    if statement.kind == "Loop" and statement.cursor:
        match = _CURSOR_QUERY.search(statement.cursor)
        if match:
            return match.group(1).strip().rstrip(")").strip()
    return None


@dataclass
class ProgramAnalysis:
    program: M.Program
    cfgs: dict[str, ControlFlowGraph] = field(default_factory=dict)
    call_graph: CallGraph = field(default_factory=CallGraph)
    effective: dict[str, EffectiveEffects] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def routine(self, routine_id: str) -> M.Routine | None:
        for module in self.program.modules:
            for routine in module.routines:
                if routine.id == routine_id:
                    return routine
        return None

    def module_of(self, routine_id: str) -> M.Module | None:
        for module in self.program.modules:
            if any(r.id == routine_id for r in module.routines):
                return module
        return None

    def write_then_scan(self) -> list[tuple[str, str]]:
        """(routine, table) where the routine writes a table and then reads it by a scan.

        ScalarDB refuses to scan rows the same transaction has already written (measured in P2-9), so this is the
        static half of AUTO prohibition 11. A read counts as a scan when the statement has no equality on a key;
        that decision needs the ScalarDB schema, so P2-4 refines it. Here the pair is reported whenever a routine
        both writes and reads the same table, which is the superset P2-4 narrows.
        """
        found: list[tuple[str, str]] = []
        for module in self.program.modules:
            for routine in module.routines:
                writes: set[str] = set()
                for statement in _walk(routine.body):
                    for table in statement.read_set:
                        if table in writes:
                            found.append((routine.id, table))
                    writes.update(statement.write_set)
        return sorted(set(found))

    def gotos(self) -> list[tuple[str, str]]:
        """GOTO statements, as (routine, label). Detection only: restructuring is a REDESIGN decision."""
        out = []
        for module in self.program.modules:
            for routine in module.routines:
                for statement in _walk(routine.body):
                    if statement.kind == "Goto":
                        out.append((routine.id, statement.label or "<unknown>"))
        return out

    def unreachable(self) -> list[tuple[str, str]]:
        return [(routine, node) for routine, cfg in self.cfgs.items() for node in cfg.unreachable()]

    def transaction_control_in_loop(self) -> list[tuple[str, str]]:
        """COMMIT inside a loop: the batch commits partially, so a retry is not a retry of the whole thing."""
        out = []
        for routine_id, cfg in self.cfgs.items():
            for node_id, statement in cfg.nodes.items():
                if statement.kind in ("Commit", "Rollback") and cfg.inside_loop(node_id):
                    out.append((routine_id, node_id))
        return sorted(out)


def analyse(program: M.Program) -> ProgramAnalysis:
    result = ProgramAnalysis(program=program)
    result.issues.extend(fill_sql_sets(program))
    result.call_graph = build_call_graph(program)

    for module in program.modules:
        for routine in module.routines:
            result.cfgs[routine.id] = build_cfg(routine)

    for module in program.modules:
        for routine in module.routines:
            result.effective[routine.id] = _effective(result, routine)
    return result


def _effective(result: ProgramAnalysis, routine: M.Routine) -> EffectiveEffects:
    reached = result.call_graph.reachable_from(routine.id)
    transaction = M.TransactionEffects(
        commits=routine.transaction_effects.commits, rollbacks=routine.transaction_effects.rollbacks,
        savepoints=routine.transaction_effects.savepoints, autonomous=routine.transaction_effects.autonomous)
    external = M.ExternalEffects(
        db_links=list(routine.external_effects.db_links), packages=list(routine.external_effects.packages),
        dynamic_sql=routine.external_effects.dynamic_sql)
    # any statement may carry a set: a cursor FOR loop reads without being a SqlOperation
    reads = {t for s in _walk(routine.body) for t in s.read_set}
    writes = {t for s in _walk(routine.body) for t in s.write_set}
    contributed: list[str] = []

    for callee_id in sorted(reached):
        callee = result.routine(callee_id)
        if callee is None:
            continue
        before = (transaction.commits, transaction.rollbacks, transaction.savepoints,
                  transaction.autonomous, len(reads), len(writes))
        transaction.commits += callee.transaction_effects.commits
        transaction.rollbacks += callee.transaction_effects.rollbacks
        transaction.savepoints += callee.transaction_effects.savepoints
        transaction.autonomous = transaction.autonomous or callee.transaction_effects.autonomous
        external.dynamic_sql = external.dynamic_sql or callee.external_effects.dynamic_sql
        external.db_links = sorted(set(external.db_links) | set(callee.external_effects.db_links))
        external.packages = sorted(set(external.packages) | set(callee.external_effects.packages))
        reads |= {t for s in _walk(callee.body) for t in s.read_set}
        writes |= {t for s in _walk(callee.body) for t in s.write_set}
        after = (transaction.commits, transaction.rollbacks, transaction.savepoints,
                 transaction.autonomous, len(reads), len(writes))
        if before != after:
            contributed.append(callee_id)

    return EffectiveEffects(transaction=transaction, external=external,
                            reads=sorted(reads), writes=sorted(writes), through=contributed)
