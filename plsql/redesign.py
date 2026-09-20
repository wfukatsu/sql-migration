"""What has become of a REDESIGN: nobody decided / decided / decided and verified.

REDESIGN says what the *source* is -- a row lock, a COMMIT inside a routine, a trigger -- and that does not change
when somebody decides how to redesign it: those are the conditions that must never be judged AUTO
(docs/design/plsql-kpi.md §2). But the report showed every REDESIGN the same way ("a design is needed; alternatives: ..."),
whether the redesign was still open or had been decided, written down with its reason, generated, and compared
with Oracle on the real databases. A reviewer could not tell the two routines that still need a decision from the
twenty-four that do not (decided 2026-09-20: keep the verdict, show the state).

The state is derived, never declared: a routine is *decided* when every REDESIGN rule that matched it is answered
by a recorded decision (`limits.yaml`, or a project-wide decision), and *verified* when, on top of that, every
scenario that ran it agreed with Oracle. A trigger body is never called by a scenario, so it is verified through
the routines that call it -- the same indirection `review.credit_private_callees` uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ir import model as M
from .limits import Boundaries, DbLinks, DynamicTables, Limits, RowLocks

STATES = ("undecided", "decided", "verified")
LABELS = {"undecided": "未決定", "decided": "決定済み（実 DB では未検証、または相違あり）",
          "verified": "決定済み・実 DB で一致"}

# project-wide decisions, not per routine. #12 §0: a trigger is called by the routines that write the table, and
# coverage of other write paths is followed by reconciliation (docs/plsql-migration/plsql-trigger-patterns.md)
TRIGGER_DECISION = ("#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う",
                    "docs/plsql-migration/plsql-trigger-patterns.md")


SEQUENCE_DECISION = ("計画 §9: 採番は移行先の方式（counters 表 / hi-lo）で取り、書き込む側の INSERT に織り込む",
                     "キーを書かない INSERT は ScalarDB が拒否するので、この経路を通らない書き込みは黙って通らず、失敗する")


@dataclass
class Decided:
    """The project's recorded decisions, as the generator reads them."""

    row_locks: RowLocks = field(default_factory=RowLocks)
    boundaries: Boundaries = field(default_factory=Boundaries)
    dynamic_tables: DynamicTables = field(default_factory=DynamicTables)
    limits: Limits = field(default_factory=Limits)
    db_links: DbLinks = field(default_factory=DbLinks)

    @classmethod
    def load(cls, path: str | Path | None) -> "Decided":
        if path is None:
            return cls()
        return cls(RowLocks.load(path), Boundaries.load(path), DynamicTables.load(path), Limits.load(path),
                   DbLinks.load(path))

    def for_analysis(self) -> dict:
        return {"row_locks": self.row_locks, "boundaries": self.boundaries, "limits": self.limits,
                "db_links": self.db_links}


@dataclass
class Status:
    routine: str
    state: str = "undecided"
    decisions: list[dict] = field(default_factory=list)   # {rule, decidedBy, why}
    open: list[str] = field(default_factory=list)         # REDESIGN rules (or callees) nobody has answered
    evidence: tuple[int, int] | None = None               # (scenarios that agreed, scenarios compared)
    through: list[str] = field(default_factory=list)      # callers whose evidence stands in (trigger bodies)

    def as_dict(self) -> dict:
        return {"state": self.state, "label": LABELS[self.state], "decisions": self.decisions, "open": self.open,
                "evidence": None if self.evidence is None else {"agreed": self.evidence[0], "compared": self.evidence[1]},
                "verifiedThrough": self.through}


def _answer(rule_id: str, routine: M.Routine, module: M.Module | None, decided: Decided) -> tuple[str, str] | None:
    """(where it was decided, the recorded reason) for one REDESIGN rule on one routine, or None."""
    name = routine.id
    if rule_id in ("LOCK-001", "LOCK-002") and decided.row_locks.decided(name):
        return "limits.yaml: rowLocks.optimistic", decided.row_locks.why(name) or ""
    if rule_id in ("TX-001", "TX-002", "TX-003", "BULK-002") and decided.boundaries.decided(name):
        where = "perIteration" if name in decided.boundaries.per_iteration else "separate"
        return f"limits.yaml: transactions.{where}", decided.boundaries.why(name) or ""
    if rule_id == "LINK-001":
        # every link the routine reaches has to lead somewhere: one that does not is still a distributed transaction
        # nobody has redesigned
        links = list(routine.external_effects.db_links)
        if links and all(decided.db_links.namespace(link) for link in links):
            where = ", ".join(f"{link} -> namespace {decided.db_links.namespace(link)}" for link in links)
            return "limits.yaml: dbLinks", f"{where}。{decided.db_links.why(links[0]) or ''}".strip()
        return None
    if rule_id == "DYN-001" and decided.dynamic_tables.for_routine(name):
        tables = ", ".join(decided.dynamic_tables.for_routine(name))
        return "limits.yaml: dynamicTables", f"受け付ける表名を決めてある: {tables}。それ以外は実行時に拒否する"
    if rule_id == "TRG-001" and module is not None and module.module_kind == "trigger":
        from .triggers import CORRELATION
        from .lower import _walk

        assigns = any(s.kind == "Assignment" and CORRELATION.match((s.target or "").strip())
                      for s in _walk(routine.body))
        if not assigns:
            return TRIGGER_DECISION
        # `:NEW.id := seq.NEXTVAL` changes the written row, so a call cannot stand in for it (trigger-patterns C).
        # The one shape that is decided: a trigger that does nothing but take the key from a sequence. The writer
        # takes the number itself (`triggers._inline_sequence`), by the scheme the DDL asks for (plan §9). Any
        # other assigning trigger is still open.
        from .triggers import Trigger

        shape = Trigger(module=module, routine=routine, table=(module.trigger_table or "").lower(),
                        timing=(module.trigger_timing or "BEFORE").upper(), event=(module.trigger_event or "").upper())
        return SEQUENCE_DECISION if shape.sequence_key() is not None else None
    return None


def statuses(program: M.Program, decisions: dict, call_graph, decided: Decided | None, evidence=None) -> dict[str, Status]:
    decided = decided or Decided()
    routines = {r.id: (m, r) for m in program.modules for r in m.routines}
    captures = dict(getattr(evidence, "captures", None) or {})
    redesigns = {name for name, d in decisions.items() if d.verdict == "REDESIGN"}
    out: dict[str, Status] = {}

    def build(name: str, seen: frozenset[str]) -> Status:
        if name in out:
            return out[name]
        status = Status(routine=name)
        module, routine = routines.get(name, (None, None))
        decision = decisions[name]
        for match in decision.matches:
            if match.rule.decision != "REDESIGN":
                continue
            if any(item["rule"] == match.rule.id for item in status.decisions) or match.rule.id in status.open:
                continue
            answer = _answer(match.rule.id, routine, module, decided) if routine is not None else None
            if answer is None:
                status.open.append(match.rule.id)
            else:
                status.decisions.append({"rule": match.rule.id, "decidedBy": answer[0], "why": answer[1]})
        # a routine that is REDESIGN only because of what it calls is as decided as what it calls
        for callee in sorted(call_graph.callees(name)) if call_graph is not None else []:
            if callee not in redesigns or callee == name or callee in seen:
                continue
            inner = build(callee, seen | {name})
            if inner.open:
                status.open.append(f"calls {callee}")
            else:
                status.decisions.append({"rule": f"calls {callee}",
                                         "decidedBy": inner.decisions[0]["decidedBy"] if inner.decisions else "",
                                         "why": inner.decisions[0]["why"] if inner.decisions else ""})
        status.evidence = captures.get(name)
        out[name] = status
        return status

    for name in sorted(redesigns):
        build(name, frozenset())

    callers: dict[str, list[str]] = {}
    if call_graph is not None:
        for caller in decisions:
            for callee in call_graph.callees(caller):
                callers.setdefault(callee, []).append(caller)
    # a trigger that was woven into the INSERT instead of being called leaves no call edge: the writer says so
    from .lower import _walk

    bodies = {m.name: r.id for m in program.modules if m.module_kind == "trigger" for r in m.routines}
    for module in program.modules:
        for routine in module.routines:
            for statement in _walk(routine.body):
                for diagnostic in statement.diagnostics:
                    if diagnostic.code == "TRIGGER_INLINED":
                        body = bodies.get(diagnostic.message.split(":", 1)[0].strip())
                        if body and routine.id not in callers.setdefault(body, []):
                            callers[body].append(routine.id)
    for name, status in out.items():
        if status.open or not status.decisions:
            status.state = "undecided"
            continue
        status.state = "decided"
        if status.evidence is not None:
            if status.evidence[1] and status.evidence[0] == status.evidence[1]:
                status.state = "verified"
            continue
        # never called by a scenario (a trigger body): the routines that call it carry the evidence, and all of
        # the ones that were compared have to agree
        compared = sorted(c for c in callers.get(name, []) if captures.get(c) and captures[c][1])
        if compared and all(captures[c][0] == captures[c][1] for c in compared):
            status.state = "verified"
            status.through = compared
    return out


def counts(found: dict[str, Status]) -> dict[str, int]:
    return {state: sum(1 for s in found.values() if s.state == state) for state in STATES}
