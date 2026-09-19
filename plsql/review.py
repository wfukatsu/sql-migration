"""P3-5: the three files a reviewer works from, and the trail back to the PL/SQL.

The tool's output is not the generated Java. It is a decision about every routine, the evidence behind it, and a
way to check it against the original. Those are what this writes:

* `decisions.json` -- one record per routine: the verdict, what the rules said before any evidence, the five
  confidence factors, which rule fired and where it is written down, what a reviewer has to do, what has to be
  tested, and the measured human fix time. A tool reads this; KPI-3 and KPI-6 are computed from it.
* `unresolved.md` -- the REVIEW and REDESIGN routines for a person, worst first, each with the reason, the
  alternatives the rule offers, and the tests that have to pass before it can be accepted.
* `traceability.csv` -- generated Java member -> the PL/SQL file and line it came from. Every row is checked
  against the generated source, so a row is evidence that the member exists and not a guess about its name.

## The fix time is measured, never estimated

KPI-6 is the only KPI that measures the migration rather than the tool, and it cannot be derived from anything
here. `decisions.json` carries a slot for it per routine, filled from a file a person maintains
(`--fix-times`), and `null` where nobody has recorded one. A number invented to fill the field would make the
one KPI about real effort the least trustworthy of the six.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .gen_java.types import java_class_name, java_name, routine_stem
from .ir import model as M
from .rules.engine import Evidence

VERDICT_ORDER = {"REDESIGN": 0, "REVIEW": 1, "AUTO": 2}


def evidence_from_diff(path: str | Path | None, variant: str | None = None,
                       known_ids: set[str] | None = None, current: dict | None = None) -> Evidence:
    """Turn P3-2's comparison report into the evidence the rule engine weighs.

    Nothing is AUTO until something has been verified: `testEvidence` is 0 for a routine with no capture, and
    that is deliberate (rules/engine.py). So a decisions file written without this reports every routine as
    REVIEW or worse -- true, but only because nobody asked what the comparison found.

    A routine is credited for the scenarios that agreed with Oracle out of those that were compared. Scenarios
    that could not run at all are not counted either way: they are not evidence of agreement, and calling them
    failures would blame a routine for a fixture the target could not seed.

    With no variant given, a routine must agree under *every* money convention reported. Agreeing under one and
    not the other is not agreement -- it is a result that depends on a decision nobody has taken yet.

    `current` is `fingerprint.of(program, root)` for the program being judged. With it, a scenario only counts
    if the report says it was measured on this source, by this toolchain (#27-33). Without that check any file
    of the right shape was believed: a hand-written report, or last month's, made a routine AUTO. A report that
    carries no fingerprint at all is one nobody can vouch for, so it counts for nothing either. A stale routine
    is left with no capture -- `testEvidence` 0, so REVIEW -- and `Evidence.stale` says why.
    """
    if path is None or not Path(path).exists():
        return Evidence()
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    wanted = [variant] if variant else sorted(report)
    tally: dict[str, list[int]] = {}
    stale: dict[str, str] = {}
    for name in wanted:
        recorded = (report.get(name) or {}).get("fingerprints")
        for scenario in (report.get(name) or {}).get("scenarios", {}).values():
            routine = _resolve(scenario["routine"], known_ids)
            reason = _stale(routine, recorded, current)
            if reason:
                stale[routine] = reason
                continue
            counts = tally.setdefault(routine, [0, 0])
            counts[1] += 1
            if not scenario["differences"]:
                counts[0] += 1
    # stale under one variant is stale: the routine has not been shown to agree under every convention
    return Evidence(captures={routine: (passed, total) for routine, (passed, total) in tally.items()
                              if routine not in stale}, stale=stale)


def _stale(routine: str, recorded: dict | None, current: dict | None) -> str | None:
    if current is None:
        return None
    if not recorded:
        return "比較結果に fingerprint が無い（どのソース・どの生成器で測ったものか分からない）"
    if recorded.get("toolchain") != current.get("toolchain"):
        return "比較のあとで生成器か実行時ヘルパが変わった"
    if routine in current.get("sources", {}) \
            and recorded.get("sources", {}).get(routine) != current["sources"][routine]:
        return "比較のあとで PL/SQL のソースが変わった"
    return None


def _resolve(routine: str, known_ids: set[str] | None) -> str:
    """A scenario names its routine `unit.routine`; the IR does not always.

    A standalone procedure is one routine in a unit of the same name, and the IR gives it the bare name. So a
    scenario for `prc_add_product` says `prc_add_product.prc_add_product` and matches nothing, and the routine
    is never credited for a comparison it passed. Found in P4-1, where a routine with a scenario that agreed on
    both money conventions was still sitting in the "nobody verified it" list.

    Resolution only ever narrows to an id the program actually has, so it cannot invent a match.
    """
    if known_ids is None or routine in known_ids:
        return routine
    unit, _, name = routine.rpartition(".")
    if unit == name and name in known_ids:
        return name
    return routine


def credit_private_callees(evidence: Evidence, program: M.Program, call_graph) -> Evidence:
    """Give a private routine the evidence of the public routines that exercise it.

    A routine a package does not expose cannot be called from a scenario, so it can never be compared against
    Oracle directly and can never reach AUTO on its own. Since the engine also refuses to call a routine better
    than its callees, one unverifiable private helper holds back every public routine that uses it --
    `pkg_shipment.is_shippable` was fully verified and still REVIEW because `line_count` was not.

    The comparison did run the whole call chain against Oracle, so the evidence is real; it is just indirect.
    Two things keep that from becoming a way to inflate AUTO:

    * a private routine is credited only when **every** caller is verified, and never above the weakest of them
    * a rule that objects to the private routine still blocks it -- evidence is one factor of five, not a verdict

    Public routines are untouched: their evidence is their own.
    """
    public = {r.id for m in program.modules for r in m.routines if r.visibility == "public"}
    private = [r.id for m in program.modules for r in m.routines if r.id not in public]
    callers: dict[str, set[str]] = {}
    for routine in {r.id for m in program.modules for r in m.routines}:
        for callee in call_graph.callees(routine):
            callers.setdefault(callee, set()).add(routine)

    captures = dict(evidence.captures)
    for routine in private:
        if routine in captures:
            continue
        mine = callers.get(routine, set())
        if not mine or any(c not in captures for c in mine):
            continue  # a caller nobody verified is not evidence about the callee
        rates = [captures[c][0] / captures[c][1] for c in mine if captures[c][1]]
        if not rates or min(rates) < 1.0:
            continue  # never above the weakest caller, and a caller that disagreed credits nothing
        captures[routine] = (sum(captures[c][0] for c in mine), sum(captures[c][1] for c in mine))
    return Evidence(captures=captures, stale=evidence.stale)


def unmatched_scenarios(path: str | Path | None, known_ids: set[str], variant: str | None = None) -> list[str]:
    """Scenario routine names that match no routine in the program.

    Reported rather than dropped: a scenario that credits nothing looks exactly like a routine nobody wrote a
    scenario for, and the two want opposite fixes.
    """
    if path is None or not Path(path).exists():
        return []
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    names = {scenario["routine"]
             for name in ([variant] if variant else sorted(report))
             for scenario in (report.get(name) or {}).get("scenarios", {}).values()}
    return sorted(n for n in names if _resolve(n, known_ids) not in known_ids)


@dataclass
class FixTimes:
    """Measured human work, in minutes, per routine. Absent means nobody measured it -- not zero."""

    minutes: dict[str, float]
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "FixTimes":
        if path is None:
            return cls(minutes={})
        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(minutes={str(k): float(v) for k, v in (data.get("minutes") or {}).items()},
                   source=str(path))

    def for_routine(self, routine: str) -> float | None:
        return self.minutes.get(routine)


# --------------------------------------------------------------------------------------------------
# decisions.json
# --------------------------------------------------------------------------------------------------

def routine_ids(program: M.Program) -> set[str]:
    return {r.id for m in program.modules for r in m.routines}


def decisions_document(program: M.Program, decisions: dict, fix_times: FixTimes | None = None,
                       unmatched: list[str] | None = None, stale: dict[str, str] | None = None) -> dict:
    fix_times = fix_times or FixTimes(minutes={})
    routines = {r.id: (m, r) for m in program.modules for r in m.routines}

    records = []
    for routine_id in sorted(decisions):
        decision = decisions[routine_id]
        module, routine = routines.get(routine_id, (None, None))
        records.append({
            "routine": routine_id,
            "verdict": decision.verdict,
            "ruleVerdict": decision.rule_verdict,
            "confidence": {"value": round(decision.confidence.value, 6),
                           **{k: round(v, 6) for k, v in decision.confidence.as_dict().items()},
                           "zeroFactors": decision.confidence.zeros()},
            "reasons": list(decision.reasons),
            # the rule, and the file it is written in: a verdict nobody can trace to a rule is one nobody can
            # review or change (rules/engine.py)
            "rules": [{"id": m.rule.id, "decision": m.rule.decision, "message": m.rule.message,
                       "source": m.rule.source, "severity": m.rule.severity}
                      for m in decision.matches],
            "remediation": decision.remediation(),
            "requiredTests": decision.required_tests(),
            "whyNotAuto": _why_not_auto(decision, stale),
            "source": _range(routine) if routine is not None else None,
            "generated": _generated_names(module, routine) if routine is not None else None,
            "humanFixMinutes": fix_times.for_routine(routine_id),
        })

    by_verdict: dict[str, list[float]] = {}
    for record in records:
        if record["humanFixMinutes"] is not None:
            by_verdict.setdefault(record["verdict"], []).append(record["humanFixMinutes"])

    return {
        "schemaVersion": M.SCHEMA_VERSION,
        "counts": _counts(records),
        # a scenario that credits nothing looks exactly like a routine nobody wrote a scenario for
        "scenariosMatchingNoRoutine": unmatched or [],
        # comparison results that were passed in and not believed, and why (plsql/fingerprint.py)
        "staleEvidence": dict(sorted((stale or {}).items())),
        # KPI-6. `measured` says how many routines the median rests on, because a median of one is not one.
        "humanFixMinutes": {
            "source": fix_times.source,
            "byVerdict": {verdict: {"measured": len(values), "median": _median(values)}
                          for verdict, values in sorted(by_verdict.items())},
            "unmeasured": sorted(r["routine"] for r in records if r["humanFixMinutes"] is None),
        },
        "routines": records,
    }


def _why_not_auto(decision, stale: dict[str, str] | None = None) -> list[str]:
    """Why this routine still needs a person, in the reviewer's words.

    Two different things send a routine here and they are easy to confuse. A rule can say so outright -- dynamic
    SQL, a commit inside a routine -- and then the rule is the answer. Or no rule fired and the confidence
    simply did not reach the threshold, most often because nothing has verified the routine yet. The second
    leaves no rule to point at, so it has to be stated, or the record looks like a verdict with no cause.
    """
    if decision.verdict == "AUTO":
        return []
    reasons = [f"{m.rule.id} ({m.rule.decision}): {m.rule.message}"
               for m in decision.matches if m.rule.decision != "AUTO"]
    zeros = decision.confidence.zeros()
    if zeros:
        old = (stale or {}).get(decision.routine)
        reasons.append("確信度が 0: " + ", ".join(zeros)
                       + (f"（testEvidence が 0 なのは、渡された比較結果が古いから: {old}。capture を取り直す）"
                          if "testEvidence" in zeros and old else
                          "（testEvidence が 0 なのは、この routine をまだ誰も Oracle と突き合わせて "
                          "いないから。P3-2 の比較結果を --evidence で渡す）"
                          if "testEvidence" in zeros else ""))
    if not reasons:
        # the engine knows causes neither rules nor confidence express -- chiefly "calls X, which is REVIEW",
        # since a routine is never better than what it calls. Saying "confidence 1.0 is below the threshold"
        # instead, which is what re-deriving produced, is worse than saying nothing.
        reasons = [r for r in decision.reasons if not r.startswith("confidence ")] or \
            [f"確信度 {decision.confidence.value:.4f} が AUTO のしきい値に届いていない"]
    return reasons


def _counts(records: list[dict]) -> dict:
    out: dict[str, int] = {}
    for record in records:
        out[record["verdict"]] = out.get(record["verdict"], 0) + 1
    return dict(sorted(out.items()))


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _range(node: M.Node) -> dict | None:
    if node.source_range is None:
        return None
    return {"file": node.source_range.file, "startLine": node.source_range.start_line,
            "endLine": node.source_range.end_line}


def _generated_names(module: M.Module | None, routine: M.Routine) -> dict | None:
    """The Java the generator would have produced for this routine, by its own naming rules."""
    if module is None:
        return None
    return {"class": java_class_name(module.name) + "Service", "method": java_name(routine_stem(routine))}


# --------------------------------------------------------------------------------------------------
# unresolved.md
# --------------------------------------------------------------------------------------------------

def unresolved_markdown(program: M.Program, decisions: dict) -> str:
    routines = {r.id: (m, r) for m in program.modules for r in m.routines}
    open_items = [(routine_id, decisions[routine_id]) for routine_id in decisions
                  if decisions[routine_id].verdict != "AUTO"]
    open_items.sort(key=lambda item: (VERDICT_ORDER.get(item[1].verdict, 9), item[0]))

    lines = ["# 人手が要る項目", "",
             f"{len(open_items)} 件（REDESIGN 先頭）。AUTO は無人で生成してよいという判定なので、ここには出ない。",
             ""]
    if not open_items:
        lines.append("なし。")
        return "\n".join(lines) + "\n"

    for routine_id, decision in open_items:
        module, routine = routines.get(routine_id, (None, None))
        where = ""
        if routine is not None and routine.source_range is not None:
            where = f" — `{routine.source_range.file}:{routine.source_range.start_line}`"
        lines += [f"## {decision.verdict}: `{routine_id}`{where}", ""]

        if decision.reasons:
            lines += ["**根拠**", ""] + [f"- {reason}" for reason in decision.reasons] + [""]
        if decision.matches:
            lines += ["**判定したルール**", ""]
            lines += [f"- `{m.rule.id}` ({m.rule.decision}, `{m.rule.source}`): {m.rule.message}"
                      for m in decision.matches]
            lines += [""]
        remediation = decision.remediation()
        lines += ["**代替案**", ""] + ([f"- {step}" for step in remediation] or
                                     ["- ルールに代替案が書かれていない。ルール側に足すべき。"]) + [""]
        tests = decision.required_tests()
        lines += ["**受け入れに必要なテスト**", ""] + ([f"- {test}" for test in tests] or
                                                  ["- ルールに必要テストが書かれていない。ルール側に足すべき。"]) + [""]
        zeros = decision.confidence.zeros()
        if zeros:
            lines += [f"**確信度が 0 になっている要因**: {', '.join(zeros)}", ""]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------------
# traceability.csv
# --------------------------------------------------------------------------------------------------

TRACE_HEADER = ["javaFile", "javaMember", "plsqlFile", "plsqlStartLine", "plsqlEndLine", "kind", "verdict",
                "generated"]


def traceability_rows(program: M.Program, decisions: dict, generated_root: str | Path | None = None,
                      package: str = "com.example.migrated") -> list[list]:
    """One row per routine and per statement, from the generated Java back to the PL/SQL line.

    Where the generated tree is available, each row says whether the member was actually found in it. A
    traceability file that only restates the naming convention would agree with itself while the generator
    disagreed; checking the source is what makes a row evidence.
    """
    sources = _generated_sources(generated_root, package)
    rows: list[list] = []
    for module in program.modules:
        java_file = f"{package.replace('.', '/')}/application/{java_class_name(module.name)}Service.java"
        text = sources.get(java_file)
        for routine in module.routines:
            decision = decisions.get(routine.id)
            verdict = decision.verdict if decision else ""
            method = java_name(routine_stem(routine))
            rows.append(_row(java_file, method, routine, "routine", verdict, text, method))
            for statement in _statements(routine):
                # a statement's anchor in the generated code is the `file:line` comment the generator writes
                anchor = _anchor(statement)
                rows.append(_row(java_file, f"{method} @ {anchor}", statement, statement.kind, verdict,
                                 text, anchor))
    return rows


def _row(java_file: str, member: str, node: M.Node, kind: str, verdict: str,
         text: str | None, needle: str) -> list:
    """`generated` distinguishes the three states a reviewer needs to tell apart.

    `yes` -- the member is in the generated source at that anchor. `not-generated` -- the class itself is not
    there, so the whole module was refused. `not-translated` -- the class is there and this line is not, which
    means the generator refused this statement and said so in its place. Writing `no` for the last two would
    merge "nothing was produced" with "something was produced that deliberately omits this".
    """
    where = node.source_range
    if text is None:
        found = "not-generated"
    elif kind == "routine":
        found = "yes" if _declares(text, needle) else "not-translated"
    else:
        found = _anchored(text, needle)
    return [java_file, member,
            where.file if where else "", where.start_line if where else "",
            where.end_line if where else "", kind, verdict, found]


def _declares(text: str, method: str) -> bool:
    """A method declaration, not the name turning up somewhere -- in a call, a comment, another method's name."""
    return bool(method) and re.search(
        rf"^\s*(?:public|private|protected)\b[^;{{=]*\b{re.escape(method)}\s*\(", text, re.MULTILINE) is not None


def _anchored(text: str, anchor: str) -> str:
    """What the generator wrote under a statement's `// file:line` comment.

    The comment goes out *before* the statement is translated, so it is there for a statement the generator then
    refused -- and `anchor in text` called that `yes` (#27-34). The anchor has to be the whole comment, too:
    as a substring `x.pkb:4` is found in `x.pkb:40`.
    """
    if not anchor:
        return "not-translated"
    found = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != f"// {anchor}":
            continue
        found = True
        # every refusal is "comments saying why, then a throw": an unplaced name, an external call, a statement
        # kind the generator does not model. The first line of code under the anchor tells which it was
        code = next((after.strip() for after in lines[index + 1:]
                     if after.strip() and not after.strip().startswith("//")), "")
        if code.startswith("throw new UnsupportedOperationException("):
            return "not-translated"
    return "yes" if found else "not-translated"


def _statements(routine: M.Routine) -> list[M.Statement]:
    from .lower import _walk

    body = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
    return [s for s in body if s.source_range is not None]


def _anchor(statement: M.Statement) -> str:
    where = statement.source_range
    return f"{where.file}:{where.start_line}" if where else ""


def _generated_sources(root: str | Path | None, package: str) -> dict[str, str]:
    if root is None:
        return {}
    base = Path(root) / "src" / "main" / "java"
    if not base.is_dir():
        return {}
    return {str(path.relative_to(base)): path.read_text(encoding="utf-8")
            for path in base.rglob("*.java")}


def traceability_csv(program: M.Program, decisions: dict, generated_root: str | Path | None = None,
                     package: str = "com.example.migrated") -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TRACE_HEADER)
    writer.writerows(traceability_rows(program, decisions, generated_root, package))
    return buffer.getvalue()


# --------------------------------------------------------------------------------------------------

def write(program: M.Program, decisions: dict, out_dir: str | Path, *,
          generated_root: str | Path | None = None, package: str = "com.example.migrated",
          fix_times: FixTimes | None = None, unmatched: list[str] | None = None,
          stale: dict[str, str] | None = None) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = {"decisions": out / "decisions.json", "unresolved": out / "unresolved.md",
               "traceability": out / "traceability.csv"}
    written["decisions"].write_text(
        json.dumps(decisions_document(program, decisions, fix_times, unmatched, stale),
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    written["unresolved"].write_text(unresolved_markdown(program, decisions), encoding="utf-8")
    written["traceability"].write_text(
        traceability_csv(program, decisions, generated_root, package), encoding="utf-8")
    return written
