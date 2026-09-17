"""P3-5: compute every KPI in `docs/plsql-kpi.md` from the artifacts, so the numbers are read and not tallied.

    python -m plsql.kpi --evidence difftest/work/plsql-diff.json --generated generated

Each number comes from a file some earlier stage produced, and each says which file. A completion report whose
numbers were typed by hand is a report nobody can re-check; this one can be re-run.

Two of the seven do not come out of an artifact and are reported as such rather than filled in:

* **KPI-6** is not measured in this PoC (decision of 2026-09-17, docs/plsql-kpi.md). It reports as a decision
  rather than a shortfall, and the mechanism stays: `--fix-times` still takes a file of measured minutes if
  somebody later wants the number. What the decision costs is stated on every run -- this PoC does not measure
  migration effort, and the other six KPIs are about the tool, not about how long the migration takes.
* **KPI-4** needs a compiler. This reports whether every AUTO routine was generated cleanly, which is the part
  that can be read off the artifacts, and says plainly that `gradle compileJava` is the other half.

Every number here is measured on the synthetic corpus (plan §9). None of it is evidence about real customer
code, and the report says so on every run rather than in a footnote somebody can drop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import review
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
    evidence = review.evidence_from_diff(evidence_path, variant)
    decisions = decide(analysis.program, analyse_program(analysis.program), RuleSet.load(), evidence)
    document = review.decisions_document(analysis.program, decisions, review.FixTimes.load(fix_times))

    return {
        "corpus": {"source": str(src), "origin": "synthetic",
                   "note": "合成 corpus。実案件コードでの達成の証拠ではない（計画 §9）"},
        "kpi1": _kpi1(data),
        "kpi2": _kpi2(data),
        "kpi3": _kpi3(decisions),
        "kpi4": _kpi4(analysis, decisions, generated),
        "kpi5": _kpi5(evidence_path, variant),
        "kpi6": _kpi6(document),
        "kpi7": _kpi7(src, analysis),
    }


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


def _kpi5(evidence_path: str | None, variant: str | None) -> dict:
    """Semantic equivalence, from P3-2's comparison. AUTO must be 100%; REVIEW need only be explainable."""
    if evidence_path is None or not Path(evidence_path).exists():
        return {"name": "意味的同等性", "unit": "rate", "target": 1.0, "value": None,
                "detail": "比較結果が無い（difftest/plsql_diff.py --full --json ...）", "source": None}
    report = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    per_variant = {}
    for name in ([variant] if variant else sorted(report)):
        scenarios = (report.get(name) or {}).get("scenarios", {})
        auto = [s for s in scenarios.values() if s["verdict"] == "AUTO"]
        agreed = [s for s in auto if not s["differences"]]
        other = [s for s in scenarios.values() if s["verdict"] != "AUTO" and s["differences"]]
        per_variant[name] = {
            "auto": {"agreed": len(agreed), "compared": len(auto),
                     "rate": len(agreed) / len(auto) if auto else None},
            "reviewOrRedesignWithDifferences": len(other),
            "notCompared": len((report.get(name) or {}).get("not_compared", {})),
        }
    rates = [v["auto"]["rate"] for v in per_variant.values() if v["auto"]["rate"] is not None]
    return {"name": "意味的同等性", "unit": "rate", "target": 1.0, "value": min(rates) if rates else None,
            "detail": {k: f"AUTO {v['auto']['agreed']}/{v['auto']['compared']}"
                       for k, v in per_variant.items()},
            "byVariant": per_variant, "source": evidence_path}


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
    lines = ["KPI (docs/plsql-kpi.md)", f"  corpus: {result['corpus']['note']}",
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
    missed = result["kpi3"]["autoProhibitionsMissed"]
    if missed:
        lines += ["", f"  AUTO 禁止条件の取りこぼし: {missed}"]
    dirty = result["kpi4"]["notCleanlyGenerated"]
    if dirty:
        lines += ["", f"  AUTO だが生成が完結しなかった: {dirty}"]
    return "\n".join(lines) + "\n"


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
