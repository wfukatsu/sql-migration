"""P3-5: compute every KPI in `docs/design/plsql-kpi.md` from the artifacts, so the numbers are read and not tallied.

    python -m plsql.kpi --evidence difftest/work/plsql-diff.json --generated generated

Each number comes from a file some earlier stage produced, and each says which file. A completion report whose
numbers were typed by hand is a report nobody can re-check; this one can be re-run.

Two of the seven do not come out of an artifact and are reported as such rather than filled in:

* **KPI-6** is not measured in this PoC (decision of 2026-09-17, docs/design/plsql-kpi.md). It reports as a decision
  rather than a shortfall, and the mechanism stays: `--fix-times` still takes a file of measured minutes if
  somebody later wants the number. What the decision costs is stated on every run -- this PoC does not measure
  migration effort, and the other six KPIs are about the tool, not about how long the migration takes.
* **KPI-4** needs a compiler. This reports whether every AUTO routine was generated cleanly, which is the part
  that can be read off the artifacts, and says plainly that `gradle compileJava` is the other half.

Every number is also reported **by where the code came from** (`origin` in the manifest: synthetic, or real code
that was anonymised) and **by how far its expected verdicts can be trusted** (an independent holdout, a holdout
somebody has since opened, or the units the rules were developed against). Today every unit is synthetic, and the
report says "real code: none measured" rather than leaving the row out -- the day real code is added, its numbers
must not be averaged into the synthetic ones (#15).

None of the synthetic numbers is evidence about real customer code (plan §9), and the report says so on every run
rather than in a footnote somebody can drop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import corpus as corpora
from . import fingerprint, review
from .analysis import analyse as analyse_program
from .report import analyse, inventory
from .rules.engine import RuleSet, decide

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "plsql"
CORPUS_SUFFIXES = (".pks", ".pkb", ".prc", ".fnc", ".trg", ".sql")


def expected_verdicts() -> dict[str, str]:
    manifest = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
    return {r["name"].lower(): r["expected"] for u in manifest["units"] for r in u["routines"]}


def _wanted(routine_id: str, expected: dict[str, str]) -> str | None:
    short = routine_id.split(".")[-1]
    return expected.get(short if short != "body" else routine_id.split(".")[0])


def measure(src: Path, ddl: Path | None, scalardb: Path | None, evidence_path: str | None,
            generated: str | None, fix_times: str | None, variant: str | None) -> dict:
    analysis = analyse(str(src), str(ddl) if ddl else None,
                       scalardb_schema=str(scalardb) if scalardb else None)
    data = inventory(analysis)
    known = review.routine_ids(analysis.program)
    evidence = review.evidence_from_diff(evidence_path, variant, known,
                                         current=fingerprint.of(analysis.program, src))
    decisions = decide(analysis.program, analyse_program(analysis.program), RuleSet.load(), evidence)
    document = review.decisions_document(analysis.program, decisions, review.FixTimes.load(fix_times))

    kpi4 = _kpi4(analysis, decisions, generated)
    manifest = _manifest_for(src)
    real = [u.name for u in manifest.groups("origin")["real-anonymized"]] if manifest else []
    return {
        "corpus": {"source": str(src), "origin": "mixed" if real else "synthetic",
                   "note": ("合成と実案件（匿名化）が混ざっている。全体の値は出自別の値の代わりにならない" if real else
                            "合成 corpus。実案件コードでの達成の証拠ではない（計画 §9）")},
        "breakdown": _breakdown(manifest, src, analysis, decisions, kpi4, evidence_path, variant, evidence.stale),
        "kpi1": _kpi1(data),
        "kpi2": _kpi2(data),
        "kpi3": _kpi3(decisions),
        "kpi4": kpi4,
        "kpi5": _kpi5(evidence_path, variant, decisions, evidence.stale),
        "kpi6": _kpi6(document),
        "kpi7": _kpi7(src, analysis),
    }


def _manifest_for(src: Path) -> "corpora.Corpus | None":
    """The manifest that describes `src`, if there is one: `<src>/../manifest.yaml`. A project that has none (a
    customer's tree handed to `--src`) is measured as one group of unknown origin, and the report says so."""
    path = src.resolve().parent / "manifest.yaml"
    return corpora.Corpus.load(path) if path.exists() else None


def _breakdown(manifest, src: Path, analysis, decisions: dict, kpi4: dict, evidence_path: str | None,
               variant: str | None, stale: dict) -> dict | None:
    """The same KPIs, per group. A rate over nothing is `None`, and the group is still listed."""
    if manifest is None:
        return None
    expected = expected_verdicts()
    routine_file = {routine.id: (routine.source_range.file if routine.source_range else None)
                    for module in analysis.program.modules for routine in module.routines}
    failed = {Path(f).name for f in inventory(analysis)["kpi"]["failedFiles"]}
    errors_by_file: dict[str, int] = {}
    for issue in analysis.issues():
        if issue.severity == "ERROR" and issue.range is not None:
            name = Path(issue.range.file).name
            errors_by_file[name] = errors_by_file.get(name, 0) + 1
    dirty = set(kpi4["notCleanlyGenerated"])
    scenarios = _auto_scenarios(evidence_path, variant, decisions, stale)

    def numbers(units: list) -> dict:
        names = {Path(f).name for unit in units for f in unit.files}
        routines = sorted(r for r, file in routine_file.items() if file and Path(file).name in names)
        rated = [(r, _wanted(r, expected), decisions[r].rule_verdict) for r in routines
                 if r in decisions and _wanted(r, expected) is not None]
        agree = [r for r, want, got in rated if want == got]
        auto = [r for r in routines if r in decisions and decisions[r].rule_verdict == "AUTO"]
        clean = [r for r in auto if r not in dirty]
        lines = 0
        for unit in units:
            for file in unit.files:
                path = src / file
                if path.is_file():
                    lines += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        errors = sum(count for name, count in errors_by_file.items() if name in names)
        compared = [s for s in scenarios if s["routine"] in routines]

        def rate(part: int, whole: int) -> float | None:
            return part / whole if whole else None

        return {
            "units": len(units), "files": len(names), "routines": len(routines), "lines": lines,
            "kpi1": {"parsed": len(names - failed), "total": len(names), "value": rate(len(names - failed), len(names))},
            "kpi3": {"agree": len(agree), "total": len(rated), "value": rate(len(agree), len(rated)),
                     "mismatches": [{"routine": r, "expected": want, "got": got}
                                    for r, want, got in rated if want != got]},
            "kpi4": {"clean": len(clean), "auto": len(auto), "value": rate(len(clean), len(auto))},
            "kpi5": {"agreed": sum(1 for s in compared if s["agreed"]), "compared": len(compared),
                     "value": rate(sum(1 for s in compared if s["agreed"]), len(compared))},
            "kpi7": {"errors": errors, "value": (errors / (lines / 1000)) if lines else None},
        }

    return {
        "origin": {key: {"label": corpora.ORIGIN_LABELS[key], **numbers(units)}
                   for key, units in manifest.groups("origin").items()},
        "evidence": {key: {"label": corpora.EVIDENCE_LABELS[key], **numbers(units)}
                     for key, units in manifest.groups("evidence").items()},
    }


def _auto_scenarios(evidence_path: str | None, variant: str | None, decisions: dict, stale: dict) -> list[dict]:
    """AUTO scenarios that were compared, one entry per scenario and money convention. A scenario agrees only if it
    agrees under every convention reported -- the same rule `_kpi5` uses for the overall number."""
    if evidence_path is None or not Path(evidence_path).exists():
        return []
    report = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    known = set(decisions)
    by_name: dict[str, dict] = {}
    for name in ([variant] if variant else sorted(report)):
        for scenario_name, scenario in ((report.get(name) or {}).get("scenarios") or {}).items():
            routine = review._resolve(scenario["routine"], known)
            current = decisions.get(routine)
            if routine in stale or current is None or current.rule_verdict != "AUTO":
                continue
            entry = by_name.setdefault(scenario_name, {"routine": routine, "agreed": True})
            entry["agreed"] = entry["agreed"] and not scenario["differences"]
    return list(by_name.values())


def _kpi1(data: dict) -> dict:
    kpi = data["kpi"]
    return {"name": "parse 率", "unit": "rate", "target": 0.90, "value": kpi["parseRate"],
            "detail": f"{kpi['parsedFiles']}/{kpi['totalFiles']} files", "source": "inventory.json"}


def _kpi2(data: dict) -> dict:
    kpi = data["kpi"]
    return {"name": "型解決率", "unit": "rate", "target": 0.95, "value": kpi["typeResolutionRate"],
            "detail": f"{kpi['resolvedSymbols']}/{kpi['typedSymbols']} typed symbols",
            "source": "inventory.json"}


def _kpi3(decisions: dict) -> dict:
    """Agreement is measured on `rule_verdict`: what the rules say before any evidence has arrived."""
    expected = expected_verdicts()
    agree = total = 0
    mismatches = []
    missed = []
    for routine_id, decision in sorted(decisions.items()):
        want = _wanted(routine_id, expected)
        if want is None:
            continue
        total += 1
        if want == decision.rule_verdict:
            agree += 1
        else:
            mismatches.append({"routine": routine_id, "expected": want, "got": decision.rule_verdict})
        if want == "REDESIGN" and decision.rule_verdict == "AUTO":
            missed.append(routine_id)
    return {"name": "判定一致", "unit": "rate", "target": 0.90, "value": agree / total if total else None,
            "detail": f"{agree}/{total}", "autoProhibitionsMissed": missed, "mismatches": mismatches,
            "source": "rules + fixtures/plsql/manifest.yaml"}


def _kpi4(analysis, decisions: dict, generated: str | None) -> dict:
    """Whether every routine the rules cleared for unattended generation came out whole.

    A routine that is AUTO by the rules and still holds something the generator refused is the disagreement the
    plan says must be resolved, so it is counted here rather than averaged away.
    """
    from .gen_java.project import generate
    from .generate import _dirty_auto

    auto = [r for r, d in decisions.items() if d.rule_verdict == "AUTO"]
    project = generate(analysis.program, generated or "/tmp/plsql-kpi-generated", "com.example.migrated",
                       decisions)
    # the same check `python -m plsql.generate` exits non-zero on, rather than a second one that could disagree
    dirty = sorted(_dirty_auto(project, decisions))
    return {"name": "compile 率", "unit": "rate", "target": 1.0,
            "value": (len(auto) - len(dirty)) / len(auto) if auto else None,
            "detail": f"{len(auto) - len(dirty)}/{len(auto)} AUTO routines generated cleanly",
            "notCleanlyGenerated": dirty,
            "source": "plsql.generate",
            "note": "javac そのものは `gradle compileJava` が担う（`plsql.generate --verify-compile` がそれを合否ゲートとして呼ぶ）。ここで測るのは生成が完結したかまで"}


def _kpi5(evidence_path: str | None, variant: str | None, decisions: dict | None = None,
          stale: dict[str, str] | None = None) -> dict:
    """Semantic equivalence, from P3-2's comparison. AUTO must be 100%; REVIEW need only be explainable.

    The rate is over the scenarios that were compared, so on its own it cannot tell "every AUTO routine agrees"
    from "the two that were looked at agree". It used to print 100% 合格 with scenarios not compared and routines
    nobody had a scenario for (#27-33). So the entry also says what the rate does not cover: AUTO routines with no
    compared scenario, AUTO scenarios that could not run, and results left out because they are stale.

    "AUTO" is what the rules say *now* (`decisions`), not the verdict written into the report when it was made --
    a routine that has since become AUTO or stopped being AUTO is counted as what it is.
    """
    if evidence_path is None or not Path(evidence_path).exists():
        return {"name": "意味的同等性", "unit": "rate", "target": 1.0, "value": None,
                "detail": "比較結果が無い（difftest/plsql_diff.py --full --json ...）", "source": None}
    report = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    known = set(decisions) if decisions else None
    stale = stale or {}

    def routine_of(scenario: dict) -> str:
        return review._resolve(scenario["routine"], known)

    def is_auto(scenario: dict) -> bool:
        current = decisions.get(routine_of(scenario)) if decisions else None
        return (current.rule_verdict if current is not None else scenario["verdict"]) == "AUTO"

    auto_routines = sorted(r for r, d in (decisions or {}).items() if d.rule_verdict == "AUTO")
    per_variant = {}
    for name in ([variant] if variant else sorted(report)):
        scenarios = (report.get(name) or {}).get("scenarios", {})
        fresh = [s for s in scenarios.values() if routine_of(s) not in stale]
        auto = [s for s in fresh if is_auto(s)]
        agreed = [s for s in auto if not s["differences"]]
        other = [s for s in fresh if not is_auto(s) and s["differences"]]
        not_compared = (report.get(name) or {}).get("not_compared", {})
        covered = {routine_of(s) for s in auto}
        per_variant[name] = {
            "auto": {"agreed": len(agreed), "compared": len(auto),
                     "rate": len(agreed) / len(auto) if auto else None},
            "reviewOrRedesignWithDifferences": len(other),
            "notCompared": len(not_compared),
            "autoScenariosNotCompared": sorted(n for n, s in not_compared.items() if is_auto(s)),
            "autoRoutinesWithoutComparison": [r for r in auto_routines if r not in covered],
            "staleScenarios": sorted(n for n, s in scenarios.items() if routine_of(s) in stale),
        }
    rates = [v["auto"]["rate"] for v in per_variant.values() if v["auto"]["rate"] is not None]

    def shown(v: dict) -> str:
        text = f"AUTO {v['auto']['agreed']}/{v['auto']['compared']}"
        gaps = [f"{label} {len(v[key])}" for key, label in (
            ("autoRoutinesWithoutComparison", "比較の無い AUTO routine"),
            ("autoScenariosNotCompared", "実行できなかった AUTO シナリオ"),
            ("staleScenarios", "古くて数えなかったシナリオ")) if v[key]]
        return text + (f"（率に入っていないもの: {'、'.join(gaps)}）" if gaps else "")

    return {"name": "意味的同等性", "unit": "rate", "target": 1.0, "value": min(rates) if rates else None,
            "detail": {k: shown(v) for k, v in per_variant.items()},
            "byVariant": per_variant, "staleEvidence": dict(sorted(stale.items())), "source": evidence_path}


def _kpi6(document: dict) -> dict:
    """Not measured, by decision. Reported as such rather than as a target that was missed.

    If somebody does supply measured minutes, the number is shown -- the decision was to stop asking for it,
    not to refuse it.
    """
    measured = document["humanFixMinutes"]
    total = sum(entry["measured"] for entry in measured["byVerdict"].values())
    return {"name": "人手修正時間", "unit": "minutes", "target": None, "measured": total > 0,
            "value": None if total == 0 else {k: v["median"] for k, v in measured["byVerdict"].items()},
            "detail": (f"{total} routine(s) measured" if total else
                       "計測しない（2026-09-17 の決定）。本 PoC は移行工数を測っていない"),
            "source": measured["source"],
            "note": "他の 6 指標はツール内部の健全性であり、移行にかかる時間ではない"}


def _kpi7(src: Path, analysis) -> dict:
    lines = sum(len(p.read_text(encoding="utf-8", errors="replace").splitlines())
                for p in src.rglob("*") if p.suffix in CORPUS_SUFFIXES)
    errors = sum(1 for issue in analysis.issues() if issue.severity == "ERROR")
    return {"name": "未解決重大リスク密度", "unit": "per1000", "target": None,
            "value": (errors / (lines / 1000)) if lines else None,
            "detail": f"{errors} ERROR / {lines} lines", "source": "diagnostics",
            "note": "絶対値の目標は置かない。トレンドのみ（KPI 定義 §KPI-7）"}


def render(result: dict) -> str:
    lines = ["KPI (docs/design/plsql-kpi.md)", f"  corpus: {result['corpus']['note']}",
             "  KPI-6 は計測しない（2026-09-17 の決定）。したがって本 PoC は移行工数を測っていない", ""]
    for key in ("kpi1", "kpi2", "kpi3", "kpi4", "kpi5", "kpi6", "kpi7"):
        entry = result[key]
        value = entry["value"]
        if key == "kpi6" and value is None:
            shown = "計測しない"
        elif value is None:
            shown = "未計測"
        elif entry.get("unit") == "rate" and isinstance(value, float):
            shown = f"{value:.1%}"
        elif entry.get("unit") == "per1000" and isinstance(value, float):
            shown = f"{value:.1f} /1000行"
        else:
            shown = json.dumps(value, ensure_ascii=False)
        target = "" if entry["target"] is None else f"  (目標 {entry['target']:.0%})"
        verdict = ""
        if entry.get("unit") == "rate" and isinstance(value, float) and entry["target"] is not None:
            verdict = "  合格" if value >= entry["target"] else "  未達"
        lines.append(f"  {key.upper():<6} {entry['name']:<20} {shown:<16}{target}{verdict}")
        lines.append(f"         {json.dumps(entry['detail'], ensure_ascii=False)}")
    lines += _render_breakdown(result.get("breakdown"))
    missed = result["kpi3"]["autoProhibitionsMissed"]
    if missed:
        lines += ["", f"  AUTO 禁止条件の取りこぼし: {missed}"]
    dirty = result["kpi4"]["notCleanlyGenerated"]
    if dirty:
        lines += ["", f"  AUTO だが生成が完結しなかった: {dirty}"]
    return "\n".join(lines) + "\n"


def _render_breakdown(breakdown: dict | None) -> list[str]:
    if breakdown is None:
        return ["", "  出自別・証拠の独立性別: manifest.yaml が無いので分けていない（出自の分からないコードとして 1 つに数えた）"]

    def shown(entry: dict, unit: str = "rate") -> str:
        if entry["value"] is None:
            return "—"
        return f"{entry['value']:.1%}" if unit == "rate" else f"{entry['value']:.1f}"

    lines = []
    for key, title in (("origin", "出自別"), ("evidence", "証拠の独立性別（判定一致は、上の行ほど信用できる）")):
        lines += ["", f"  {title}"]
        for group in breakdown[key].values():
            if not group["units"]:
                lines.append(f"    {group['label']}: 0 unit — まだ測っていない")
                continue
            lines.append(f"    {group['label']}: {group['units']} unit / {group['routines']} routine / {group['lines']} 行")
            lines.append(f"      parse {shown(group['kpi1'])}（{group['kpi1']['parsed']}/{group['kpi1']['total']}）"
                         f"  判定一致 {shown(group['kpi3'])}（{group['kpi3']['agree']}/{group['kpi3']['total']}）"
                         f"  compile {shown(group['kpi4'])}（{group['kpi4']['clean']}/{group['kpi4']['auto']}）"
                         f"  同等性 {shown(group['kpi5'])}（{group['kpi5']['agreed']}/{group['kpi5']['compared']}）"
                         f"  リスク密度 {shown(group['kpi7'], 'per1000')}")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default=str(FIXTURES / "src"))
    ap.add_argument("--schema", default=str(FIXTURES / "src" / "schema.sql"))
    ap.add_argument("--scalardb-schema", default=str(FIXTURES / "scalardb-schema.json"))
    ap.add_argument("--evidence", help="P3-2's comparison report")
    ap.add_argument("--variant", choices=["scaled", "double"])
    ap.add_argument("--generated", help="where the generated tree is (or should be written)")
    ap.add_argument("--fix-times", help="YAML of measured human fix minutes, for KPI-6")
    ap.add_argument("--json", help="write the numbers here as well")
    args = ap.parse_args(argv)

    result = measure(Path(args.src), Path(args.schema) if args.schema else None,
                     Path(args.scalardb_schema) if args.scalardb_schema else None,
                     args.evidence, args.generated, args.fix_times, args.variant)
    print(render(result), end="")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
