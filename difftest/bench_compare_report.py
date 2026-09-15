"""Old vs new benchmark comparison tables (difftest/bench_compare_versions.sh).

  .venv/bin/python difftest/bench_compare_report.py [out/bench-compare]
"""
import json
import sys
from pathlib import Path

O = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "out/bench-compare")


def load(name):
    p = O / name / "bench.json"
    return json.loads(p.read_text())["results"] if p.is_file() else None


def path_of(r):
    if r.get("path"):
        return {"scalardb_sql": "ScalarDB SQL", "plan": "plan", "appside": "app-side Java"}[r["path"]]
    if r.get("pattern"):
        return "plan"
    return "ScalarDB SQL" if r["status"] in ("OK", "WARN") else "—"


def p50(r, key):
    v = (r.get(key) or {}).get("p50")
    return "—" if v is None else f"{v:,.1f}"


def cell(r):
    if r is None:
        return ["—"] * 5
    return [path_of(r), r["verdict"], p50(r, "oracle_ms"), p50(r, "scalardb_ms"),
            "—" if r.get("fetched_rows") is None else f"{r['fetched_rows']:,}"]


def table(case, fetcher):
    old, new = load(f"old-{case}-{fetcher}"), load(f"new-{case}-{fetcher}")
    if old is None and new is None:
        return ""
    lines = [f"### {case} (fetcher {fetcher})", "",
             "| # | statement | old path | old verdict | old Oracle p50 | old ScalarDB p50 | old fetched | "
             "new path | new verdict | new Oracle p50 | new ScalarDB p50 | new fetched | new / old |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    by_new = {r["id"]: r for r in new or []}
    for o in old or []:
        n = by_new.get(o["id"])
        ratio = "—"
        if n and (o.get("scalardb_ms") or {}).get("p50") and (n.get("scalardb_ms") or {}).get("p50"):
            ratio = f"{n['scalardb_ms']['p50'] / o['scalardb_ms']['p50']:.2f}"
        lines.append("| " + " | ".join([o["id"], o["label"], *cell(o), *cell(n), ratio]) + " |")
    return "\n".join(lines) + "\n"


def details(name):
    rows = load(name) or []
    out = []
    for r in rows:
        if r.get("detail") or r.get("note"):
            out.append(f"- {name} {r['id']}: {(r.get('detail') or r.get('note'))[:220]}")
        if r.get("fetch_ms") is not None:
            out.append(f"- {name} {r['id']}: fetch {r['fetch_ms']:.1f} ms / residual {r['residual_ms']:.1f} ms (last iteration)")
    return out


if __name__ == "__main__":
    parts = [table(c, f) for c in ("bench", "appside", "area") for f in ("jdbc", "core")]
    print("\n".join(p for p in parts if p))
    for name in sorted(p.name for p in O.iterdir() if p.is_dir() and p.name.startswith(("old-", "new-"))):
        for line in details(name):
            print(line)
    sys.exit(0)
