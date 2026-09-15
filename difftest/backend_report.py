"""Tables for docs/cassandra-verification-report.md and docs/scalardb-backend-comparison.md, from the runs of
difftest/backend_compare.sh (one directory per ScalarDB backend).

  .venv/bin/python difftest/backend_report.py \
      --run "ScalarDB+Oracle=out/cassandra-verify/oracle-backend:oracle" \
      --run "ScalarDB+Cassandra=out/cassandra-verify/after:cassandra" \
      --run "ScalarDB+PostgreSQL=out/cassandra-verify/pg:postgres" \
      --reference "Cassandra (走査あり)=out/cassandra-verify/main:cassandra" \
      --judge ScalarDB+Oracle --judge ScalarDB+Cassandra

  --run        LABEL=DIR:BACKEND, in column order
  --reference  a run shown only for statements a judged run cannot fetch by key (what it would cost with
               cross-partition scans)
  --judge      runs that get a suitability verdict per NoSQL pattern

Where a fresh-node rerun of a compatibility case exists (<backend>.<case>.fresh.json), it is used.

Suitability (docs/cassandra-verification-plan.md §5):
  取得不可  the converter finds no key or index to fetch by (FULL_SCAN / NO_CROSS_PARTITION): needs a design change
  動かない  error or result mismatch at any size
  向く      p50 grows by at most 30 % from the smallest to the largest table (key-bounded access)
  全件走査  otherwise, when the converter reports a cross-partition scan (CROSS_PARTITION / PLAN_CROSS_PARTITION): it runs,
            but reads every partition, so its cost grows with the table (only on backends given cross-partition scans)
  条件付き  otherwise: a key or index access that returns rows in proportion to the table
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

SIZES = (5000, 20000, 40000)
OK = ("PASS", "PASS_COUNT_ONLY")
NO_KEY = {"FULL_SCAN", "NO_CROSS_PARTITION"}


@dataclass
class Run:
    label: str
    dir: Path
    backend: str

    @classmethod
    def parse(cls, spec: str) -> "Run":
        label, _, rest = spec.partition("=")
        d, _, backend = rest.rpartition(":")
        return cls(label, Path(d), backend)

    def bench(self, prefix: str) -> dict[int, dict[str, dict]]:
        out = {}
        for n in SIZES:
            f = self.dir / f"{prefix}-{self.backend}-{n}" / "bench.json"
            if f.exists():
                out[n] = {r["id"]: r for r in json.loads(f.read_text())["results"]}
        return out

    def compat(self, case: str) -> tuple[dict[int, dict], bool]:
        for name, fresh in ((f"{self.backend}.{case}.fresh.json", True), (f"{self.backend}.{case}.json", False)):
            f = self.dir / name
            if f.exists():
                return {r["index"]: r for r in json.loads(f.read_text())}, fresh
        return {}, False


def no_key(r: dict | None) -> bool:
    return bool(r) and r.get("verdict") == "NOT_CONVERTIBLE" and bool(NO_KEY & set(r.get("codes") or []))


def p50(r: dict | None):
    return None if not r or r.get("verdict") not in OK else (r.get("scalardb_ms") or {}).get("p50")


def num(v) -> str:
    return "—" if v is None else (f"{v:,.0f}" if v >= 100 else f"{v:.1f}")


def mark(r: dict | None) -> str:
    if not r:
        return "—"
    if no_key(r):
        return "🚫 取得不可"
    if r.get("verdict") in OK:
        return f"{num(p50(r))} (×{r['ratio']:.0f})" if r.get("ratio") else num(p50(r))
    return {"FAIL": "❌ 失敗", "NOT_CONVERTIBLE": "🚫 変換不可"}.get(r["verdict"], r["verdict"])


def classify(by_size: dict[int, dict[str, dict]], q: str) -> str:
    sizes = [n for n in SIZES if n in by_size]
    rs = [by_size[n].get(q) for n in sizes]
    if any(no_key(r) for r in rs):
        return "取得不可"
    if not rs or any(r is None or r.get("verdict") not in OK for r in rs):
        return "動かない"
    if p50(rs[-1]) <= 1.3 * p50(rs[0]):
        return "向く"
    return "全件走査" if {"CROSS_PARTITION", "PLAN_CROSS_PARTITION"} & set(rs[-1].get("codes") or []) else "条件付き"


def compat_section(runs: list[Run]) -> list[str]:
    lines = ["## 互換性 (Oracle の結果との一致)", ""]
    for case in ("oracle", "oracle-features"):
        data = [(r, *r.compat(case)) for r in runs]
        lines += [f"### {case}.sql", "", "| 系統 | 一致 | 不一致・エラー | 変換不可 (キーで取得できない) | 変換不可 (その他) |", "|---|---|---|---|---|"]
        for run, recs, fresh in data:
            res = list(recs.values())
            nk = sum(1 for x in res if x["result"] == "NOT_CONVERTIBLE" and NO_KEY & set(x.get("codes") or []))
            other = sum(1 for x in res if x["result"] not in ("PASS", "FAIL")) - nk
            lines.append(f"| {run.label}{' ※' if fresh else ''} | {sum(x['result'] == 'PASS' for x in res)} | "
                         f"{sum(x['result'] == 'FAIL' for x in res)} | {nk} | {other} |")
        if any(fresh for _, _, fresh in data):
            lines.append("\n※ ScalarDB Cluster ノードを起動し直してから単独で実行した結果")
        fails = sorted({i for _, recs, _ in data for i, x in recs.items() if x["result"] == "FAIL"})
        if fails:
            lines += ["", "不一致・エラーになった文:", "", "| # | 機能 | " + " | ".join(r.label for r in runs) + " | 内容 |",
                      "|---|---|" + "---|" * len(runs) + "---|"]
            for i in fails:
                rec = next(recs[i] for _, recs, _ in data if i in recs)
                why = next((str(recs[i].get("error")) for _, recs, _ in data if recs.get(i, {}).get("result") == "FAIL"), "")
                cells = " | ".join(recs.get(i, {}).get("result", "—") for _, recs, _ in data)
                lines.append(f"| {i} | {rec.get('feature') or rec['sql'][:50]} | {cells} | {why.split('Operation:')[0][:110].replace('|', '/')} |")
        lines.append("")
    return lines


def bench_section(title: str, prefix: str, runs: list[Run], refs: list[Run], judged: list[str]) -> list[str]:
    data = {r.label: r.bench(prefix) for r in runs + refs}
    big = max((n for d in data.values() for n in d), default=None)
    if big is None:
        return []
    first = next(d[big] for d in data.values() if big in d)
    ids = list(first.keys())
    lines = [f"## {title}", "", f"### 応答時間 p50 (ms)、{big:,} 行 (括弧内は同じ回に測った Oracle 直接実行に対する倍率)", "",
             "| # | 文 | Oracle 直接 | " + " | ".join(r.label for r in runs) + " |", "|---|---|---|" + "---|" * len(runs)]
    for q in ids:
        o = first[q].get("oracle_ms", {}).get("p50")
        lines.append(f"| {q} | {first[q]['label']} | {num(o)} | " + " | ".join(mark(data[r.label].get(big, {}).get(q)) for r in runs) + " |")
    for label in judged:
        d = data[label]
        sizes = [n for n in SIZES if n in d]
        ref = refs[0] if refs else None
        lines += ["", f"### {label}: 表サイズに対する伸び (p50 ms)", "",
                  "| # | 文 | " + " | ".join(f"{n // 1000}k" for n in sizes) + " | 返却行 " + "/".join(f"{n // 1000}k" for n in (sizes[0], sizes[-1]))
                  + (f" | 参考: {ref.label} {big // 1000}k" if ref else "") + " | 判定 |",
                  "|---|---|" + "---|" * (len(sizes) + 2 + (1 if ref else 0))]
        for q in ids:
            rs = [d[n].get(q) for n in sizes]
            rows = [(r.get("fetched_rows") if r and r.get("pattern") else (r or {}).get("rows")) for r in (rs[0], rs[-1])]
            refcell = ""
            if ref:
                rr = data[ref.label].get(big, {}).get(q)
                refcell = f" | {mark(rr) if no_key(rs[-1]) else ''}"
            lines.append(f"| {q} | {first[q]['label']} | " + " | ".join(num(p50(r)) if r and r.get('verdict') in OK else mark(r) for r in rs)
                         + f" | {'—' if rows[0] is None else rows[0]} / {'—' if rows[1] is None else rows[1]}{refcell} | {classify(d, q)} |")
    lines.append("")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--reference", action="append", default=[])
    ap.add_argument("--judge", action="append", default=[])
    a = ap.parse_args()
    runs, refs = [Run.parse(s) for s in a.run], [Run.parse(s) for s in a.reference]
    lines = compat_section(runs)
    lines += bench_section("既存ベンチ (bench.sql)", "bench", runs, refs, [])
    lines += bench_section("NoSQL 適性ケース (nosql-patterns.sql)", "nosql", runs, refs, a.judge)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
