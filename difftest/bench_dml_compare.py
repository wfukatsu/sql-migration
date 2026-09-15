#!/usr/bin/env python3
"""Before / after table for the execution-plan reads of difftest/bench_dml.py (e.g. before and after H2 indexes).

  .venv/bin/python difftest/bench_dml_compare.py out/dml-bench-before-h2index out/dml-bench

Each directory holds <dialect>/bench.json; the fetch / H2 split comes from raw/result.<dialect>-reads.json in the
"before" directory and difftest/work/dml-bench/result.<dialect>-reads.json for the current run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIALECTS = ("oracle", "postgres", "mysql")
NAMES = {"oracle": "Oracle", "postgres": "PostgreSQL", "mysql": "MySQL"}


def load(out: Path, raw_dir: Path) -> dict[str, tuple[dict, dict]]:
    data = {}
    for d in DIALECTS:
        bench, raw = out / d / "bench.json", raw_dir / f"result.{d}-reads.json"
        if bench.is_file() and raw.is_file():
            results = {r["id"]: r for r in json.loads(bench.read_text(encoding="utf-8"))["results"]}
            queries = {q["id"]: q for q in json.loads(raw.read_text(encoding="utf-8"))["queries"]}
            data[d] = (results, queries)
    return data


def split(queries: dict, i: str) -> str:
    s = queries.get(i, {}).get("scalardb", {})
    if s.get("fetch_ms") is None:
        return "—"
    return f"{s['fetch_ms']:,.0f} + {s['residual_ms']:,.0f}"


def p50(r: dict | None) -> float | None:
    return ((r or {}).get("scalardb_ms") or {}).get("p50") if r and r["verdict"].startswith("PASS") else None


def render(before: dict, after: dict) -> str:
    L = ["| dialect | id | statement | before p50 (ms) | after p50 (ms) | speed-up | before fetch + H2 (ms) | after fetch + H2 (ms) |",
         "|---|---|---|---|---|---|---|---|"]
    for d in DIALECTS:
        if d not in before or d not in after:
            continue
        (rb, qb), (ra, qa) = before[d], after[d]
        for i in sorted(ra):
            a, b = ra[i], rb.get(i)
            if a["write"] or a.get("path") != "plan":
                continue
            pb, pa = p50(b), p50(a)
            speed = f"{pb / pa:.1f}x" if pb and pa else "—"
            L.append(f"| {NAMES[d]} | {i} | {a['note'][:30]} | {'—' if pb is None else f'{pb:,.1f}'} | "
                     f"{'—' if pa is None else f'{pa:,.1f}'} | {speed} | {split(qb, i)} | {split(qa, i)} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    before_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out/dml-bench-before-h2index"
    after_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "out/dml-bench"
    print(render(load(before_dir, before_dir / "raw"), load(after_dir, ROOT / "difftest/work/dml-bench")))
