"""P2-1: control flow, call graph, effects.

The rules (P2-2, P2-3, P2-4) decide; this module supplies the evidence they decide from. Three questions the IR
alone cannot answer:

* **Where can control go?** A `COMMIT` inside a loop that an exception can jump out of is a different problem from
  a `COMMIT` at the end of a body. The CFG makes that difference visible.
* **What does this routine reach?** A routine with no `COMMIT` of its own still controls the transaction if it
  calls one that does. Transaction effects therefore have to propagate along the call graph, not stop at the
  routine that happens to contain the statement.
* **What does it touch?** Read and write sets come from the SQL, and their intersection is what makes
  write-then-scan (docs/plsql-kpi.md §2, condition 11) detectable before anything runs.

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
            body_ends = _link(cfg, statement.body, {statement.id})
            for node in body_ends:
                cfg.edges.add((node, statement.id))  # back edge
            for node in _walk(statement.body):
                cfg._loop_members.add(node.id)
            cfg._loop_members.add(statement.id)
            current = {statement.id}
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
                if colour.get(callee, 0) == 0:
                    visit(callee)
                elif colour.get(callee) == 1:
                    found.append(path[path.index(callee):] + [callee])
            path.pop()
            colour[node] = 2

        for node in sorted(self.calls):
            if colour.get(node, 0) == 0:
                visit(node)
        return found


def build_call_graph(program: M.Program) -> CallGraph:
    graph = CallGraph()
    by_name: dict[str, str] = {}
    for module in program.modules:
        for routine in module.routines:
            by_name[routine.id.lower()] = routine.id
            by_name.setdefault(routine.name.lower(), routine.id)
            if module.module_kind == "package":
                by_name[f"{module.name}.{routine.name}".lower()] = routine.id

    for module in program.modules:
        for routine in module.routines:
            graph.calls.setdefault(routine.id, set())
            graph.external.setdefault(routine.id, set())
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            for statement in statements:
                if statement.kind == "Call":
                    callee = statement.callee.strip().lower()
                    resolved = by_name.get(callee) or (
                        by_name.get(f"{module.name}.{callee}".lower()) if "." not in callee else None)
                    if resolved is not None and resolved != routine.id:
                        graph.calls[routine.id].add(resolved)
                        statement.resolved_to = resolved
                    elif resolved is None:
                        graph.external[routine.id].add(statement.callee.strip())
                # A function call is usually not a statement: `v := order_total(id)` is an assignment, and a
                # condition may call one too. Looking only at Call nodes found one edge in the whole corpus and
                # made every transitive transaction effect disappear.
                for expression in _expressions(statement):
                    for resolved in _called_in(expression, by_name, module.name, routine.id):
                        graph.calls[routine.id].add(resolved)
    return graph


_CALLABLE = re.compile(r"\b([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)\s*\(")


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


def _called_in(expression: str, by_name: dict[str, str], module: str, caller: str) -> set[str]:
    found: set[str] = set()
    for name in _CALLABLE.findall(expression):
        lowered = name.lower()
        resolved = by_name.get(lowered) or (
            by_name.get(f"{module}.{lowered}") if "." not in lowered else None)
        if resolved is not None and resolved != caller:
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
