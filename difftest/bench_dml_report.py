#!/usr/bin/env python3
"""Summary tables across dialects from difftest/bench_dml.py results (out/dml-bench/<dialect>/bench.json).

  .venv/bin/python difftest/bench_dml_report.py [out/dml-bench]
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIALECTS = ("oracle", "postgres", "mysql")
NAMES = {"oracle": "Oracle", "postgres": "PostgreSQL", "mysql": "MySQL"}


def load(out: Path) -> dict[str, dict]:
    return {d: json.loads((out / d / "bench.json").read_text(encoding="utf-8"))
            for d in DIALECTS if (out / d / "bench.json").is_file()}


def p50(r: dict, key: str):
    return (r.get(key) or {}).get("p50")


def cell(r: dict | None) -> str:
    if r is None:
        return "—"
    if r["verdict"] not in ("PASS", "PASS_COUNT_ONLY"):
        return f"{r['verdict']}"
    s, d = p50(r, "source_ms"), p50(r, "scalardb_ms")
    return f"{s:.1f} / {d:.1f} ({r.get('ratio', '—')}x)"


def render(data: dict[str, dict]) -> str:
    dialects = [d for d in DIALECTS if d in data]
    L = ["## Summary by dialect", "",
         "| dialect | statements | timed | PASS | FAIL | not convertible | writes p50 ratio (median) | reads p50 ratio (median) |",
         "|---|---|---|---|---|---|---|---|"]
    for d in dialects:
        res, not_run = data[d]["results"], data[d]["not_convertible"]
        verdicts = Counter(r["verdict"] for r in res)
        def med(write):
            ratios = [r["ratio"] for r in res if r["write"] == write and r.get("ratio") and r["verdict"].startswith("PASS")]
            return f"{statistics.median(ratios):.1f}x" if ratios else "—"
        L.append(f"| {NAMES[d]} | {len(res) + len(not_run)} | {len(res)} | "
                 f"{verdicts.get('PASS', 0) + verdicts.get('PASS_COUNT_ONLY', 0)} | {verdicts.get('FAIL', 0)} | "
                 f"{len(not_run)} | {med(True)} | {med(False)} |")

    by_id: dict[str, dict[str, dict]] = {}
    notes: dict[str, str] = {}
    for d in dialects:
        for r in data[d]["results"]:
            by_id.setdefault(r["id"], {})[d] = r
            notes[r["id"]] = r["note"]
        for c in data[d]["not_convertible"]:
            by_id.setdefault(c["id"], {})[d] = {"verdict": "NOT_CONVERTIBLE", **c}
            notes[c["id"]] = c["note"]
    order = sorted(by_id, key=lambda i: ("IUDST".index(i[0]), i))
    L += ["", "## Statements (source p50 / ScalarDB p50 in ms, ratio)", "",
          "| id | statement | " + " | ".join(f"{NAMES[d]} conversion | {NAMES[d]}" for d in dialects) + " |",
          "|---|---|" + "---|---|" * len(dialects)]
    for i in order:
        cells = []
        for d in dialects:
            r = by_id[i].get(d)
            cells += [r["status"] if r and "status" in r else "—", cell(r) if r and r["verdict"] != "NOT_CONVERTIBLE" else "—"]
        L.append(f"| {i} | {notes[i]} | " + " | ".join(cells) + " |")

    fails = [(d, r) for d in dialects for r in data[d]["results"] if r["verdict"] in ("FAIL", "ORACLE_ERROR")]
    if fails:
        L += ["", "## Failures", "", "| dialect | id | conversion | detail |", "|---|---|---|---|"]
        for d, r in fails:
            L.append(f"| {NAMES[d]} | {r['id']} | {r['status']} | {(r.get('detail') or '').replace('|', '/')[:200]} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out/dml-bench"
    print(render(load(out)))
