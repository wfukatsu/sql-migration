"""P2-2: the rule engine.

The judgement lives in YAML, not here. This module evaluates rules against the IR and the analysis, computes the
confidence from `docs/plsql-kpi.md` §3, and applies the threshold. What it must never do is decide anything on its
own: a verdict that cannot be traced to a rule file is a verdict nobody can review or change.

    ruleset = RuleSet.load()                    # plsql/rules/*.yaml
    decisions = decide(program, analysis, ruleset, evidence)
    decisions["pkg_order_status.status_of"].verdict      # AUTO | REVIEW | REDESIGN

## The threshold is a product, and that is the point

    confidence = ruleCoverage × symbolResolution × typeResolution × targetCapability × testEvidence

Five factors, AUTO at >= 0.95. Each factor at 0.99 already lands on 0.95, so AUTO means every side is nearly
perfect, and any single factor at 0 makes the product 0 whatever the others say. `testEvidence` is 0 when a routine
has no capture, so an untested routine cannot become AUTO -- that is deliberate (P0-5 found four private routines
in exactly that position).

## Severity of a match is not the same as confidence

A rule that fires states a verdict floor: REDESIGN can never be softened by a high confidence, and REVIEW can never
be raised to AUTO by one. Confidence only decides between AUTO and REVIEW once no rule has said otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..analysis import ProgramAnalysis
from ..ir import model as M
from ..lower import _walk, dynamic_texts
from ..source import Issue

RULES_DIR = Path(__file__).parent
VERDICTS = ("AUTO", "REVIEW", "REDESIGN")
RANK = {"AUTO": 0, "REVIEW": 1, "REDESIGN": 2}
AUTO_THRESHOLD = 0.95  # docs/plsql-kpi.md §3; change both together


@dataclass
class Rule:
    """One YAML rule. `match` is a set of predicates; every one of them must hold."""

    id: str
    decision: str
    message: str
    match: dict = field(default_factory=dict)
    severity: str = "warning"
    remediation: list[str] = field(default_factory=list)
    required_tests: list[str] = field(default_factory=list)
    source: str = ""

    def __post_init__(self) -> None:
        if self.decision not in VERDICTS:
            raise ValueError(f"{self.id}: decision must be one of {VERDICTS}, not {self.decision!r}")


@dataclass
class Match:
    rule: Rule
    where: str                       # the node id the rule matched on
    detail: str = ""

    def issue(self, node: M.Node | None) -> Issue:
        severity = {"critical": "ERROR", "warning": "WARN"}.get(self.rule.severity, "INFO")
        text = self.rule.message + (f": {self.detail}" if self.detail else "")
        return Issue(severity, self.rule.id, text, node.source_range if node else None)


@dataclass
class Confidence:
    rule_coverage: float = 1.0
    symbol_resolution: float = 1.0
    type_resolution: float = 1.0
    target_capability: float = 1.0
    test_evidence: float = 0.0

    @property
    def value(self) -> float:
        return (self.rule_coverage * self.symbol_resolution * self.type_resolution
                * self.target_capability * self.test_evidence)

    def zeros(self) -> list[str]:
        return [name for name, value in self.as_dict().items() if value == 0]

    def as_dict(self) -> dict[str, float]:
        return {"ruleCoverage": self.rule_coverage, "symbolResolution": self.symbol_resolution,
                "typeResolution": self.type_resolution, "targetCapability": self.target_capability,
                "testEvidence": self.test_evidence}


@dataclass
class Decision:
    """`rule_verdict` and `verdict` answer different questions, and conflating them hides both.

    `rule_verdict` is what the rules say about the routine itself -- the judgement KPI-3 compares against the
    manifest, and the one that is stable before any verification has run. `verdict` additionally requires the
    evidence: with no capture, `testEvidence` is 0, so nothing can be AUTO until Phase 3 has produced results.
    Reading the second as if it were the first would make the rules look wrong for a reason that has nothing to
    do with them.
    """

    routine: str
    verdict: str
    confidence: Confidence
    rule_verdict: str = "AUTO"
    matches: list[Match] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def issues(self) -> list[Issue]:
        return [m.issue(None) for m in self.matches]

    def required_tests(self) -> list[str]:
        return sorted({test for m in self.matches for test in m.rule.required_tests})

    def remediation(self) -> list[str]:
        return [step for m in self.matches for step in m.rule.remediation]


@dataclass
class Evidence:
    """What verification knows so far. Supplied from outside so the engine stays pure.

    `captures` maps a routine id to (passed, total). A routine absent from the mapping has no capture at all,
    which is not the same as having one that failed -- both give 0, but only the second is a defect.
    """

    captures: dict[str, tuple[int, int]] = field(default_factory=dict)
    # routine id -> why its scenarios were not counted: the comparison was run on another source or by another
    # generator (plsql/fingerprint.py). Not a failure and not a pass -- it is evidence about something else
    stale: dict[str, str] = field(default_factory=dict)

    def test_evidence(self, routine_id: str) -> float:
        passed, total = self.captures.get(routine_id, (0, 0))
        return 0.0 if total == 0 else passed / total

    def failed(self, routine_id: str) -> int:
        """Scenarios that ran and did not match Oracle. One is a known difference, however many others passed."""
        passed, total = self.captures.get(routine_id, (0, 0))
        return max(total - passed, 0)


class RuleSet:
    def __init__(self, rules: list[Rule]) -> None:
        self.rules = rules
        ids = [r.id for r in rules]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate rule ids: {sorted(duplicates)}")

    @classmethod
    def load(cls, directory: str | Path | None = None) -> "RuleSet":
        directory = Path(directory or RULES_DIR)
        rules: list[Rule] = []
        for path in sorted(directory.glob("*.yaml")):
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for entry in document.get("rules", []):
                rules.append(Rule(source=path.name, **{
                    "id": entry["id"], "decision": entry["decision"], "message": entry["message"],
                    "match": entry.get("match", {}), "severity": entry.get("severity", "warning"),
                    "remediation": entry.get("remediation", []),
                    "required_tests": entry.get("requiredTests", [])}))
        return cls(rules)

    def evaluate(self, module: M.Module, routine: M.Routine, analysis: ProgramAnalysis) -> list[Match]:
        return [m for rule in self.rules for m in _match(rule, module, routine, analysis)]


# --- predicates -------------------------------------------------------------------------------------------
#
# Each key a rule may use in `match:`. Keeping them here, small and named, is what lets a rule stay declarative:
# a new rule is a YAML entry, and a new *kind* of condition is a function added below.

def _statements(routine: M.Routine) -> list[M.Statement]:
    return _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]


# `statementKind: Expression` in a rule: anywhere PL/SQL evaluates an expression. The text rules used to list
# `[Assignment, Return, If]`, which left out a WHILE condition, an EXIT WHEN, a CASE selector, a call's arguments,
# a RAISE message -- and every declaration: `v_x NUMBER := ROUND(p / 3, 2);` reached no rule at all.
EXPRESSION = "Expression"
_TEXT_FIELDS = ("original_sql", "expression", "cursor", "target", "condition", "callee", "selector", "message",
                "initial", "default", "chunk")


def _text(node) -> str:
    parts = [str(getattr(node, a, "") or "") for a in _TEXT_FIELDS]
    parts += [b.condition or "" for b in getattr(node, "branches", []) or []]
    parts += [str(a) for a in getattr(node, "arguments", []) or []]
    return " ".join(p for p in parts if p)


def _declarations(routine: M.Routine) -> list:
    """Declarations that evaluate something: initialisers and parameter defaults, nested blocks included.
    A cursor's query is left to the cursor rules."""
    nested = [d for s in _statements(routine) for d in getattr(s, "declarations", []) or []]
    return [d for d in list(routine.declarations) + nested
            if getattr(d, "initial", None) and d.declaration_kind != "cursor"] \
        + [p for p in routine.parameters if p.default]


def _visible_declarations(module: M.Module, routine: M.Routine) -> list:
    """Everything declared where the routine can see it: its own declarations, those of the blocks nested in it,
    and the package's. The last two used to be left out, so a `DECLARE v unknown_pkg.t%TYPE` inside a block cost
    no confidence, and a `CURSOR c IS ... FOR UPDATE` declared on the package never reached LOCK-002."""
    nested = [d for s in _statements(routine) for d in getattr(s, "declarations", []) or []]
    return list(routine.declarations) + nested + list(module.declarations)


def _locking_cursor(module: M.Module, routine: M.Routine) -> bool:
    body = None
    for d in _visible_declarations(module, routine):
        if d.declaration_kind != "cursor" or not d.initial or not re.search(r"\bFOR\s+UPDATE\b", d.initial, re.I):
            continue
        if d not in module.declarations:
            return True
        # a package's cursor counts for the routines that use it, not for every routine of the package
        body = body if body is not None else repr([routine.body, routine.exception_handlers])
        if re.search(rf"\b{re.escape(d.name)}\b", body, re.IGNORECASE):
            return True
    return False


def _match(rule: Rule, module: M.Module, routine: M.Routine, analysis: ProgramAnalysis) -> list[Match]:
    criteria = rule.match
    hits: list[Match] = []

    if "statementKind" in criteria:
        wanted = _as_set(criteria["statementKind"])
        anywhere = EXPRESSION in wanted
        for statement in _statements(routine):
            if (statement.kind in wanted or (anywhere and statement.kind != "SqlOperation")) \
                    and _extra(criteria, statement, module, routine, analysis):
                hits.append(Match(rule, statement.id, _detail(statement)))
        if anywhere and "textMatches" in criteria:
            for declaration in _declarations(routine):
                if re.search(criteria["textMatches"], _text(declaration), re.IGNORECASE):
                    hits.append(Match(rule, declaration.id, f"{declaration.name}: {_text(declaration)}"[:120]))
        return hits

    if _routine_level(criteria, module, routine, analysis):
        hits.append(Match(rule, routine.id, _routine_detail(criteria, module, routine, analysis)))
    return hits


def _extra(criteria: dict, statement: M.Statement, module: M.Module, routine: M.Routine,
           analysis: ProgramAnalysis) -> bool:
    if criteria.get("insideLoop") and not analysis.cfgs[routine.id].inside_loop(statement.id):
        return False
    if "lockingMode" in criteria and not getattr(statement, "locking_mode", None):
        return False
    if criteria.get("constantSql") is False and getattr(statement, "constant_sql", None) is not None:
        return False
    if "identifierInterpolation" in criteria:
        if _interpolates_identifier(statement, routine) is not criteria["identifierInterpolation"]:
            return False
    if "targetStatus" in criteria and getattr(statement, "target_status", None) not in \
            _as_set(criteria["targetStatus"]):
        return False
    # `evaluatedByTarget: <regex>`: the text is looked for in what the target evaluates, not in what the source says.
    # A statement the converter took (OK / WARN) reaches ScalarDB as `target_sql`, where an expression the generator
    # lifted out is a bind the runtime computed. A PLANNED statement runs its original SQL in the residual engine.
    # With no ScalarDB schema nobody knows what reaches the target, so the source text stands in. A refused
    # statement (ERROR) evaluates nothing, and SQL-001 already says so.
    if "evaluatedByTarget" in criteria:
        status = getattr(statement, "target_status", None)
        if status == "ERROR":
            return False
        texts = list(getattr(statement, "target_sql", None) or []) if status in ("OK", "WARN") \
            else [getattr(statement, "original_sql", None) or ""]
        if not any(re.search(criteria["evaluatedByTarget"], text, re.IGNORECASE) for text in texts):
            return False
    # `targetStatusNot: OK`: the statement is not one the converter took as it stands -- it warned, planned or
    # refused it, or no ScalarDB schema was given and nobody asked (target_status is None)
    if "targetStatusNot" in criteria and getattr(statement, "target_status", None) in \
            _as_set(criteria["targetStatusNot"]):
        return False
    if "sqlKind" in criteria and (getattr(statement, "sql_kind", None) or "").upper() not in \
            {k.upper() for k in _as_set(criteria["sqlKind"])}:
        return False
    if "textMatches" in criteria:
        if not re.search(criteria["textMatches"], _text(statement), re.IGNORECASE):
            return False
    if "loopKind" in criteria and getattr(statement, "loop_kind", None) not in _as_set(criteria["loopKind"]):
        return False
    if "hasDiagnostic" in criteria:
        codes = {d.code for d in statement.diagnostics}
        if not (codes & _as_set(criteria["hasDiagnostic"])):
            return False
    if "intoTargets" in criteria:
        has = bool(getattr(statement, "into_targets", None))
        if has is not criteria["intoTargets"]:
            return False
    return True


CLOCK = re.compile(r"\b(SYSDATE|SYSTIMESTAMP|CURRENT_DATE|CURRENT_TIMESTAMP|LOCALTIMESTAMP)\b",
                   re.IGNORECASE)


def _clock_reads(routine: M.Routine) -> int:
    """How many times the routine asks the database what time it is."""
    total = 0
    for statement in _statements(routine):
        text = " ".join(str(getattr(statement, field, "") or "")
                        for field in ("original_sql", "expression", "cursor", "target", "condition"))
        total += len(CLOCK.findall(text))
    return total


def _routine_level(criteria: dict, module: M.Module, routine: M.Routine, analysis: ProgramAnalysis) -> bool:
    effects = analysis.effective.get(routine.id)
    checks = {
        "autonomous": lambda v: routine.transaction_effects.autonomous is v,
        # the routine's own statements count as well as what it reaches: `effective` is keyed by routine id, and
        # overloads share one, so the entry may be another overload's
        "controlsTransaction": lambda v: ((effects.controls_transaction if effects else False)
                                          or routine.transaction_effects.controls_transaction) is v,
        "packageState": lambda v: module.has_package_state is v,
        "moduleKind": lambda v: module.module_kind in _as_set(v),
        "authId": lambda v: routine.auth_id in _as_set(v),
        "dbLink": lambda v: bool(effects and effects.external.db_links) is v,
        "externalPackage": lambda v: bool(effects and effects.external.packages) is v,
        "writeThenScan": lambda v: (routine.id in {r for r, _ in analysis.write_then_scan()}) is v,
        "recursive": lambda v: any(routine.id in cycle for cycle in analysis.call_graph.cycles()) is v,
        # a call that resolves to no routine in the program: code nobody analysed, which may commit, send mail,
        # or take a lock. `externalPackage` only knows a short list of names; this is everything else
        # a handler for one of these names, anywhere in the routine (nested blocks included)
        "handlesException": lambda v: bool(_handled(routine) & {n.upper() for n in _as_set(v)}),
        "unresolvedCallee": lambda v: bool(_unresolved_callees(routine, analysis)) is v,
        # `WHEN OTHERS THEN NULL`, anywhere in the routine
        "swallowsOthers": lambda v: _swallows_others(routine) is v,
        # SQL%ROWCOUNT read in a routine where something other than a static statement sets it
        "rowCountUntracked": lambda v: bool(_untracked_row_count(routine)) is v,
        # P4-3: how many times the routine reads the database clock. Two reads can return two values, and
        # nothing in the recorded Oracle evidence pins that -- a scenario pins the clock to one value, so a
        # routine that reads it twice is compared against something the comparison cannot distinguish.
        "clockReadsAtLeast": lambda v: _clock_reads(routine) >= v,
        # row locking hides in a cursor declaration as well as in a statement (found in P1-7)
        "cursorLocking": lambda v: _locking_cursor(module, routine) is v,
        "saveExceptions": lambda v: any(
            s.kind == "Loop" and s.loop_kind == "forall"
            and any(d.code == "FORALL_SAVE_EXCEPTIONS" for d in s.diagnostics)
            for s in _statements(routine)) is v,
    }
    if not criteria:
        return False
    for key, value in criteria.items():
        check = checks.get(key)
        if check is None:
            return False  # an unknown key never matches; the schema test catches the typo
        if not check(value):
            return False
    return True


_INTERPOLATED_IDENTIFIER = re.compile(
    r"(FROM|INTO|TABLE|JOIN|UPDATE)\s+'\s*\|\||(FROM|INTO|TABLE|JOIN|UPDATE)\s*'\s*\|\|", re.IGNORECASE)


def _handled(routine: M.Routine) -> set[str]:
    handlers = list(routine.exception_handlers) + [
        h for s in _statements(routine) for h in getattr(s, "exception_handlers", []) or []]
    return {name.upper() for h in handlers for name in h.exceptions}


def _swallows_others(routine: M.Routine) -> bool:
    handlers = list(routine.exception_handlers) + [
        h for s in _statements(routine) for h in getattr(s, "exception_handlers", []) or []]
    return any("OTHERS" in {e.upper() for e in h.exceptions} and all(s.kind == "Null" for s in h.body)
               for h in handlers)


def _untracked_row_count(routine: M.Routine) -> list[str]:
    """What else, besides a static INSERT / UPDATE / DELETE / SELECT INTO, sets SQL%ROWCOUNT in a routine that
    reads it. The generated `rowCount` follows the static statements only."""
    if not re.search(r"SQL%ROWCOUNT", repr([routine.body, routine.exception_handlers, routine.declarations]),
                     re.IGNORECASE):
        return []
    found = []
    for statement in _statements(routine):
        if statement.kind == "Loop" and statement.loop_kind == "forall":
            found.append("FORALL")
        elif statement.kind == "DynamicSql":
            found.append("EXECUTE IMMEDIATE")
        elif statement.kind == "SqlOperation" and (statement.sql_kind or "").upper() == "MERGE":
            found.append("MERGE")
        elif statement.kind == "Call" and getattr(statement, "resolved_to", None):
            found.append(f"call to {statement.resolved_to}")
    return sorted(set(found))


def _unresolved_callees(routine: M.Routine, analysis: ProgramAnalysis) -> list[str]:
    from ..analysis import HARMLESS_CALLEES

    return sorted(c for c in analysis.call_graph.external.get(routine.id, ())
                  if not HARMLESS_CALLEES.match(c))


def _interpolates_identifier(statement: M.Statement, routine: M.Routine | None = None) -> bool:
    """Does the dynamic SQL splice a value into an identifier position?

    This is the line between the two dynamic-SQL cases the design document separates. A statement that only
    concatenates predicates and passes values with `USING` is a finite set of variants, and can be turned into
    static queries (REVIEW). One that builds a table name cannot: the target has to be an allowlist or a
    dedicated repository (REDESIGN).
    """
    texts = dynamic_texts(routine, statement) if routine is not None else [getattr(statement, "expression", "") or ""]
    return any(_INTERPOLATED_IDENTIFIER.search(text) for text in texts)


def _as_set(value) -> set:
    return set(value) if isinstance(value, (list, tuple, set)) else {value}


def _detail(statement: M.Statement) -> str:
    for attribute in ("locking_mode", "callee", "expression", "original_sql", "savepoint"):
        value = getattr(statement, attribute, None)
        if isinstance(value, str) and value:
            return value[:120]
    return statement.kind


def _routine_detail(criteria: dict, module: M.Module, routine: M.Routine, analysis: ProgramAnalysis) -> str:
    effects = analysis.effective.get(routine.id)
    if criteria.get("dbLink") and effects:
        return ", ".join(effects.external.db_links)
    if criteria.get("rowCountUntracked"):
        return ", ".join(_untracked_row_count(routine))
    if criteria.get("handlesException"):
        return ", ".join(sorted(_handled(routine) & {n.upper() for n in _as_set(criteria["handlesException"])}))
    if criteria.get("unresolvedCallee"):
        return ", ".join(_unresolved_callees(routine, analysis))
    if criteria.get("controlsTransaction") and effects:
        through = f" (through {', '.join(effects.through)})" if effects.through else ""
        return (f"commits={effects.transaction.commits} rollbacks={effects.transaction.rollbacks} "
                f"savepoints={effects.transaction.savepoints}{through}")
    return ""


# --- confidence -------------------------------------------------------------------------------------------

def confidence_of(module: M.Module, routine: M.Routine, analysis: ProgramAnalysis,
                  matches: list[Match], evidence: Evidence) -> Confidence:
    statements = _statements(routine)
    unknown = sum(1 for s in statements if s.kind == "Unsupported")
    rule_coverage = 0.0 if unknown else 1.0

    typed = [d.type for d in _visible_declarations(module, routine) + list(routine.parameters)
             if d.type is not None]
    if routine.return_type is not None:
        typed.append(routine.return_type)
    resolved = [t for t in typed if t.is_resolved()]
    type_resolution = 1.0 if not typed else (0.0 if len(resolved) < len(typed) else 1.0)

    # A statement nobody has checked against ScalarDB scores 0, not 0.5. "Not analysed" is not half a capability:
    # until the checker (P2-4) has run, there is no evidence the target can execute the statement, and inventing
    # a half-answer would let a routine drift into AUTO on the strength of an unasked question.
    sql = [s for s in statements if s.kind == "SqlOperation"]
    if not sql:
        target_capability = 1.0
    else:
        scores = [{"OK": 1.0, "WARN": 1.0, "PLANNED": 0.5, "ERROR": 0.0}.get(s.target_status, 0.0) for s in sql]
        target_capability = 0.0 if any(s == 0.0 for s in scores) else sum(scores) / len(scores)

    # symbolResolution: every type resolved, and every call resolved to a routine that was analysed. It used to be
    # a copy of typeResolution, so a call into a package outside the program cost nothing
    symbol_resolution = 0.0 if type_resolution == 0.0 or _unresolved_callees(routine, analysis) else 1.0

    return Confidence(rule_coverage=rule_coverage, symbol_resolution=symbol_resolution,
                      type_resolution=type_resolution, target_capability=target_capability,
                      test_evidence=evidence.test_evidence(routine.id))


# --- deciding ---------------------------------------------------------------------------------------------

def decide(program: M.Program, analysis: ProgramAnalysis, ruleset: RuleSet,
           evidence: Evidence | None = None) -> dict[str, Decision]:
    evidence = evidence or Evidence()
    decisions: dict[str, Decision] = {}
    for module in program.modules:
        for routine in module.routines:
            matches = ruleset.evaluate(module, routine, analysis)
            confidence = confidence_of(module, routine, analysis, matches, evidence)
            decision = _verdict(routine, matches, confidence, evidence)
            # overloads share an id (lower.py holds them back from AUTO). Keep the worse of the two, so that one
            # overload's ROLLBACK is not replaced by the other's clean bill
            earlier = decisions.get(routine.id)
            if earlier is None or RANK[decision.rule_verdict] > RANK[earlier.rule_verdict]:
                decisions[routine.id] = decision
    _propagate(decisions, analysis)
    return decisions


def _propagate(decisions: dict[str, Decision], analysis: ProgramAnalysis) -> None:
    """A caller is never safer than what it calls.

    A routine whose own statements are unremarkable still needs review if it calls one that does not -- generating
    it as AUTO would hand out code whose behaviour depends on a routine nobody reviewed. The verdict therefore
    floors at the worst verdict reachable through the call graph, and the reason names the callee so the reader
    can follow it.
    """
    for routine_id, decision in decisions.items():
        for callee_id in sorted(analysis.call_graph.reachable_from(routine_id)):
            callee = decisions.get(callee_id)
            if callee is None or RANK[callee.rule_verdict] <= RANK[decision.rule_verdict]:
                continue
            decision.rule_verdict = callee.rule_verdict
            decision.verdict = callee.rule_verdict if RANK[callee.rule_verdict] > RANK[decision.verdict] \
                else decision.verdict
            decision.reasons.append(
                f"calls {callee_id}, which is {callee.rule_verdict}")


def _verdict(routine: M.Routine, matches: list[Match], confidence: Confidence,
             evidence: Evidence) -> Decision:
    floor = "AUTO"
    for match in matches:
        if RANK[match.rule.decision] > RANK[floor]:
            floor = match.rule.decision
    decision = Decision(routine=routine.id, verdict="AUTO", confidence=confidence,
                        rule_verdict=floor, matches=matches)

    if floor != "AUTO":
        decision.verdict = floor
        decision.reasons = [f"{m.rule.id}: {m.rule.message}" for m in matches
                            if m.rule.decision == floor]
        return decision

    # no rule objected: confidence decides between AUTO and REVIEW
    zeros = confidence.zeros()
    failed = evidence.failed(routine.id)
    if zeros:
        decision.verdict = "REVIEW"
        decision.reasons = [f"confidence factor {name} is 0" for name in zeros]
    elif failed:
        # AUTO は「Oracle と一致した」という意味である。19/20 は確信度 0.95 でしきい値を越えるが、
        # 残りの 1 件は「違うと分かっている」であって、割合で薄めてよいものではない
        decision.verdict = "REVIEW"
        decision.reasons = [f"{failed} scenario(s) differ from Oracle; AUTO requires every compared scenario to match"]
    elif confidence.value < AUTO_THRESHOLD:
        decision.verdict = "REVIEW"
        decision.reasons = [f"confidence {confidence.value:.3f} is below the AUTO threshold {AUTO_THRESHOLD}"]
    else:
        decision.reasons = [f"confidence {confidence.value:.3f} >= {AUTO_THRESHOLD}, no rule objected"]
    return decision
