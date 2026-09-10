"""Render the comparison tables of docs/bench-report.md from the bench.json files of several runs.

  .venv/bin/python difftest/bench_report.py out/bench-5000 out/bench-20000 out/bench-40000
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(paths: list[str]) -> list[tuple[int, dict]]:
    runs = []
    for p in paths:
        d = json.loads((Path(p) / "bench.json").read_text())
        runs.append((d["config"]["rows"], d))
    return sorted(runs, key=lambda r: r[0])


def path_of(r: dict) -> str:
    return f"plan / {r['pattern']}" if r.get("pattern") else ("ScalarDB SQL" if r["status"] in ("OK", "WARN") else "—")


def main() -> None:
    runs = load(sys.argv[1:])
    sizes = [n for n, _ in runs]
    base = runs[-1][1]["results"]

    print("### 互換性と応答時間 (emp {} 行)\n".format(sizes[-1]))
    print("| # | 文 | ScalarDB 経路 | 結果行 | 一致 | Oracle p50 (ms) | ScalarDB p50 (ms) | 倍率 | 取得行 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in base:
        o = r.get("oracle_ms", {}).get("p50", "—")
        s = r.get("scalardb_ms", {}).get("p50", "—")
        print(f"| {r['id']} | {r['label']} | {path_of(r)} | {r.get('rows', '—')} | {r['verdict']} | "
              f"{o} | {s} | {r.get('ratio', '—')} | {r.get('fetched_rows', '—')} |")

    print("\n### 表サイズに対する伸び (ScalarDB p50, ms)\n")
    header = " | ".join(f"emp {n}" for n in sizes)
    print(f"| # | 文 | 経路 | {header} | 取得行 ({sizes[-1]}) |")
    print("|---|---|---|" + "---|" * (len(sizes) + 1))
    by_size = {n: {r["id"]: r for r in d["results"]} for n, d in runs}
    for r in base:
        cells = []
        for n in sizes:
            rr = by_size[n].get(r["id"], {})
            cells.append(str(rr.get("scalardb_ms", {}).get("p50", "—")))
        print(f"| {r['id']} | {r['label']} | {path_of(r)} | " + " | ".join(cells) + f" | {r.get('fetched_rows', '—')} |")

    print("\n### 内訳: fetch と残余処理 (emp {} 行)\n".format(sizes[-1]))
    print("| # | 取得行 | fetch (ms) | 残余 H2 (ms) | 合計 p50 (ms) |")
    print("|---|---|---|---|---|")
    for r in base:
        if r.get("pattern"):
            print(f"| {r['id']} | {r.get('fetched_rows')} | {r.get('fetch_ms')} | {r.get('residual_ms')} | "
                  f"{r.get('scalardb_ms', {}).get('p50')} |")


if __name__ == "__main__":
    main()
