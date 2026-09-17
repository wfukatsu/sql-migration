"""P4-11: turn what people actually decided into rule candidates, and stop there.

Every REVIEW a person resolves is a judgement the rules did not make. When the same judgement is made about the
same shape several times, that is a rule waiting to be written. This reads the resolutions and says which ones
look like one.

**A proposal is not a rule, and cannot become one by accident.** Proposals are written to their own file,
outside the rules directory, in a form that is not loadable as a rule. `RuleSet.load` reads `plsql/rules/*.yaml`
and nothing else, so a proposal takes effect when a person copies it there — which is the point. A tool that
promoted its own suggestions would be deciding the thing the whole verdict system exists to keep decidable by
people (rules/engine.py: "a verdict that cannot be traced to a rule is a verdict nobody can review or change").

Resolutions come from a file a person maintains:

    # fixtures/plsql/resolutions.yaml
    resolutions:
      pkg_order_report.mark_reviewed:
        outcome: redesigned          # redesigned | accepted | rejected
        pattern: cursor-D            # which recorded pattern was applied
        note: 1 件 1 トランザクションに分割した

    python -m plsql.propose --resolutions fixtures/plsql/resolutions.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# How many routines have to agree before a shape is worth proposing a rule for. Two is a coincidence.
MIN_SUPPORT = 3

OUTCOMES = {"redesigned", "accepted", "rejected"}


@dataclass
class Resolution:
    routine: str
    outcome: str
    pattern: str | None = None
    note: str = ""


@dataclass
class Proposal:
    """A rule candidate. Deliberately not in the shape `RuleSet.load` reads."""

    shape: tuple[str, ...]          # the rule ids that fired together
    outcome: str
    pattern: str | None
    routines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # every rule that fired on any member, so a reader can see what the grouping ignored
    also_fired: set = field(default_factory=set)

    @property
    def support(self) -> int:
        return len(self.routines)

    def as_dict(self) -> dict:
        return {"decidedBy": list(self.shape), "alsoFired": sorted(self.also_fired - set(self.shape)),
                "outcome": self.outcome, "pattern": self.pattern,
                "routines": sorted(self.routines), "support": self.support, "notes": self.notes}


def load_resolutions(path: str | Path | None) -> dict[str, Resolution]:
    if path is None or not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out = {}
    for routine, entry in (data.get("resolutions") or {}).items():
        outcome = str(entry.get("outcome", "")).lower()
        if outcome not in OUTCOMES:
            raise ValueError(f"{routine}: outcome must be one of {sorted(OUTCOMES)}, not {outcome!r}")
        out[routine] = Resolution(routine=routine, outcome=outcome,
                                  pattern=entry.get("pattern"), note=entry.get("note", ""))
    return out


def deciding_rules(decision) -> tuple[str, ...]:
    """The rules that set the verdict, not every rule that fired.

    A routine picks up incidental matches -- a `SYSDATE` somewhere, a statement ScalarDB cannot run -- and
    grouping on the full set makes every routine its own shape, which proposes nothing. What two routines have
    in common, when a person handled them the same way, is the rule that forced the verdict.
    """
    return tuple(sorted({m.rule.id for m in decision.matches if m.rule.decision == decision.rule_verdict}))


def propose(decisions: dict, resolutions: dict[str, Resolution]) -> list[Proposal]:
    """Group resolved routines by the rules that decided them, and by what the person did about it.

    Grouping on the rule set rather than on the text: two routines a person handled the same way, that the
    engine decided the same way, are evidence about the engine. Two routines that merely read similarly are not.
    """
    groups: dict[tuple, Proposal] = {}
    for routine, resolution in sorted(resolutions.items()):
        decision = decisions.get(routine)
        if decision is None:
            continue
        shape = deciding_rules(decision)
        if not shape:
            continue
        key = (shape, resolution.outcome, resolution.pattern)
        proposal = groups.setdefault(key, Proposal(shape=shape, outcome=resolution.outcome,
                                                   pattern=resolution.pattern))
        proposal.routines.append(routine)
        proposal.also_fired.update(m.rule.id for m in decision.matches)
        if resolution.note:
            proposal.notes.append(f"{routine}: {resolution.note}")
    return sorted((p for p in groups.values() if p.support >= MIN_SUPPORT),
                  key=lambda p: (-p.support, p.shape))


def render(proposals: list[Proposal], resolutions: dict[str, Resolution]) -> str:
    lines = [
        "# ルール候補", "",
        "**これはルールではない。** 人が `plsql/rules/*.yaml` へ書いて初めて有効になる。",
        "ツールが自分の提案を自分で採用すれば、判定を人がレビューできるようにしている仕組みそのものが"
        "意味を失う。", "",
        f"- 解決済みの routine: {len(resolutions)} 件",
        f"- 候補: {len(proposals)} 件（同じ形に対して同じ判断が {MIN_SUPPORT} 件以上あったもの）", "",
    ]
    if not proposals:
        lines += ["候補なし。同じ判断が繰り返されるまでは、ルールにする根拠が無い。", ""]
        return "\n".join(lines)

    for proposal in proposals:
        lines += [f"## `{' + '.join(proposal.shape)}` → {proposal.outcome}"
                  + (f"（{proposal.pattern}）" if proposal.pattern else ""), "",
                  f"**根拠**: {proposal.support} 件が同じ扱いになった", ""]
        lines += [f"- `{r}`" for r in sorted(proposal.routines)] + [""]
        if proposal.notes:
            lines += ["**人が書いた理由**", ""] + [f"- {n}" for n in proposal.notes] + [""]
        lines += ["**書くとしたらこの形**（そのまま貼らず、条件を自分で確かめること）", "",
                  "```yaml",
                  "  - id: <未採番>",
                  f"    match: {{}}   # {' と '.join(proposal.shape)} が当たる形を、"
                  "この routine 群を見て書く",
                  f"    decision: {'REDESIGN' if proposal.outcome == 'redesigned' else 'REVIEW'}",
                  "    message: <何が問題かを 1 行で>",
                  "    remediation:",
                  f"      - <{proposal.pattern or '適用した型'} を参照>",
                  "```", ""]
    lines += ["---", "",
              "**採用する前に確かめること**", "",
              "1. その形を持つ routine が、まだ解決していないものも含めて同じ扱いでよいか",
              "2. ルールを足した後も KPI-3 が下がらないか（holdout 上でも）",
              "3. AUTO 禁止条件を緩めていないか", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    from . import review
    from .analysis import analyse as analyse_program
    from .report import analyse
    from .rules.engine import RuleSet, decide

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default="fixtures/plsql/src")
    ap.add_argument("--schema", default="fixtures/plsql/src/schema.sql")
    ap.add_argument("--scalardb-schema", default="fixtures/plsql/scalardb-schema.json")
    ap.add_argument("--resolutions", help="YAML of what people decided about each REVIEW")
    ap.add_argument("--evidence")
    ap.add_argument("--out", default="out/plsql/rule-proposals.md")
    args = ap.parse_args(argv)

    resolutions = load_resolutions(args.resolutions)
    program_analysis = analyse(args.src, args.schema, scalardb_schema=args.scalardb_schema)
    known = review.routine_ids(program_analysis.program)
    evidence = review.evidence_from_diff(args.evidence, None, known)
    decisions = decide(program_analysis.program, analyse_program(program_analysis.program),
                       RuleSet.load(), evidence)

    proposals = propose(decisions, resolutions)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(proposals, resolutions), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps([p.as_dict() for p in proposals], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"{len(resolutions)} resolution(s) -> {len(proposals)} proposal(s)")
    print(f"  {out}")
    print("  これはルールではない。plsql/rules/*.yaml へ人が書いて初めて有効になる。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
