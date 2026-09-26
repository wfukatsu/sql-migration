"""結果を例ごとにまとめる: 分類（build_projects.py）、文書との照合（doc_check.py）、解析の判定、javac、実 DB 比較。

    python3 samples/oracle-plsql-docs/summarize.py      # -> work/summary.json と result/（git に入れる表）
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from examples import WORK, load  # noqa: E402

RESULT = HERE / "result"
CHAPTERS = {
    "overview.htm": "1 概要", "fundamentals.htm": "2 言語の基本", "datatypes.htm": "3 データ型",
    "controlstatements.htm": "4 制御文", "composites.htm": "5 コレクションとレコード", "static.htm": "6 静的 SQL",
    "dynamic.htm": "7 動的 SQL", "subprograms.htm": "8 サブプログラム", "triggers.htm": "9 トリガー",
    "packages.htm": "10 パッケージ", "errors.htm": "11 エラー処理", "tuning.htm": "12 最適化",
    "wrap.htm": "A ソースの隠蔽", "nameresolution.htm": "B 名前解決",
}
JAVAC = re.compile(r"/(\w+)\.java:(\d+): (?:エラー|error): (.*)")
SEVERITY = {"REDESIGN": 3, "REVIEW": 2, "AUTO": 1}


def chapter(page: str) -> str:
    return CHAPTERS.get(page, "14 PL/SQL ユニットの SQL 文（CREATE / ALTER / DROP）")


def javac_errors(log: Path) -> list[str]:
    if not log.exists():
        return []
    seen, out = set(), []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = JAVAC.search(line)
        if m and (m.group(1), m.group(2)) not in seen:
            seen.add((m.group(1), m.group(2)))
            out.append(f"{m.group(1)}.java:{m.group(2)}: {m.group(3).strip()}")
    return out


def routines(analysis: Path) -> list[dict]:
    path = analysis / "decisions.json"
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    return [{"routine": r["routine"], "verdict": r["verdict"], "ruleVerdict": r.get("ruleVerdict"),
             "rules": sorted({x.get("id", x) if isinstance(x, dict) else x for x in r.get("rules") or []})}
            for r in d.get("routines", [])]


def difference_kind(differences: list[str]) -> str:
    first_exception = next((d for d in differences if d.startswith("exception")), None)
    if first_exception:
        if "UnsupportedOperationException" in first_exception:
            return "UNSUPPORTED"
        if "直接の DML" in first_exception:
            return "DIRECT_DML"
        if "actual=none" in first_exception:
            return "NOT_RAISED"
        return "EXCEPTION"
    return "VALUE"


def same_lines_other_order(root: Path, scenario: str) -> bool:
    golden = root / "golden" / f"{scenario}.json"
    target = root / "work" / "plsql-scalardb-double" / f"{scenario}.json"
    if not golden.exists() or not target.exists():
        return False
    a = json.loads(golden.read_text(encoding="utf-8")).get("output") or []
    b = json.loads(target.read_text(encoding="utf-8")).get("output") or []
    return a != b and sorted(a) == sorted(b)


def run_example(key: str) -> dict:
    root = WORK / "projects" / key
    work = root / "work"
    out: dict = {"routines": routines(work / "analysis")}
    errors = javac_errors(work / "capture.log")
    if errors:
        out.update(outcome="JAVA_COMPILE", javac=errors)
        return out
    diff_path = work / "plsql-diff.json"
    unrunnable_path = work / "plsql-scalardb-double" / "unrunnable.json"
    unrunnable = json.loads(unrunnable_path.read_text(encoding="utf-8")) if unrunnable_path.exists() else {}
    if not diff_path.exists():
        tail = (work / "capture.log").read_text(encoding="utf-8", errors="replace")[-600:] if (work / "capture.log").exists() else ""
        out.update(outcome="HARNESS", detail=tail)
        return out
    report = json.loads(diff_path.read_text(encoding="utf-8"))["double"]
    scenarios = {}
    for name, s in report["scenarios"].items():
        diffs = s.get("differences") or []
        kind = difference_kind(diffs) if diffs else None
        if kind == "VALUE" and all(d.startswith("output") for d in diffs) and same_lines_other_order(root, name):
            kind = "ORDER"      # 同じ行が別の順で出た: ORDER BY の無い問合せの読み順（どちらも正しい）
        scenarios[name] = {"status": "IDENTICAL" if not diffs else "DIFFERS", "kind": kind,
                           "differences": diffs[:6], "direct": s.get("direct"), "accepted": s.get("accepted")}
    for name, reason in {**report.get("not_compared", {}), **unrunnable}.items():
        scenarios.setdefault(name, {"status": "NOT_RUN", "reason": str(reason)[:400]})
    out["scenarios"] = scenarios
    statuses = {s["status"] for s in scenarios.values()}
    if statuses == {"IDENTICAL"}:
        out["outcome"] = "IDENTICAL"
    elif "DIFFERS" in statuses:
        kinds = [s["kind"] for s in scenarios.values() if s["status"] == "DIFFERS"]
        order = ["VALUE", "NOT_RAISED", "EXCEPTION", "ORDER", "DIRECT_DML", "UNSUPPORTED"]
        out["outcome"] = "DIFFERS"
        out["kind"] = min(kinds, key=order.index)
    else:
        out["outcome"] = "NOT_RUN"
    return out


def static_example(key: str) -> dict:
    work = WORK / "static" / key / "work"
    out = {"routines": routines(work / "analysis")}
    log = (work / "generate.log").read_text(encoding="utf-8", errors="replace") if (work / "generate.log").exists() else ""
    analysis = (work / "analysis.log").read_text(encoding="utf-8", errors="replace") if (work / "analysis.log").exists() else ""
    m = re.search(r"parse rate\s+([\d.]+)%", analysis)
    out["parse_rate"] = float(m.group(1)) if m else None
    errors = javac_errors(work / "generate.log") or [l.strip() for l in log.splitlines() if re.search(r"\.java:\d+", l)][:5]
    if "compile check: gradle compileJava succeeded" in log:
        out["compile"] = "OK"
    elif errors:
        out["compile"] = "JAVAC_ERRORS"
        out["javac"] = errors[:5]
    else:
        out["compile"] = "NOT_RUN"
        out["detail"] = log[-400:]
    return out


def main() -> int:
    categories = json.loads((WORK / "categories.json").read_text(encoding="utf-8"))
    doc = json.loads((WORK / "doc-check.json").read_text(encoding="utf-8"))
    summary = {}
    for ex in load():
        c = categories[ex.number]
        entry = {"number": ex.number, "title": ex.title, "chapter": chapter(ex.page), "url": ex.url,
                 "category": c["category"], "reason": c["reason"], "doc": doc[ex.number]["verdict"]}
        if c["category"] == "RUN":
            entry.update(run_example(ex.key))
        elif c["category"] in ("UNITS_ONLY", "SOURCE_REJECTS"):
            entry.update(static_example(ex.key))
        verdicts = [r["verdict"] for r in entry.get("routines", [])]
        rule = [r["ruleVerdict"] for r in entry.get("routines", []) if r.get("ruleVerdict")]
        entry["verdict"] = max(verdicts, key=SEVERITY.get) if verdicts else None
        entry["ruleVerdict"] = max(rule, key=SEVERITY.get) if rule else None
        summary[ex.number] = entry
    (WORK / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    RESULT.mkdir(exist_ok=True)
    (RESULT / "summary.json").write_text(json.dumps(
        {k: {x: v[x] for x in ("number", "title", "chapter", "category", "doc", "outcome", "kind", "verdict",
                                 "ruleVerdict", "compile") if x in v} for k, v in summary.items()},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(Counter(v["category"] for v in summary.values()))
    print(Counter((v.get("outcome"), v.get("kind")) for v in summary.values() if v["category"] == "RUN"))
    print(Counter(v.get("compile") for v in summary.values() if v["category"] in ("UNITS_ONLY", "SOURCE_REJECTS")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
