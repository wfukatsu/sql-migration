"""Performance + compatibility benchmark: Oracle Database vs ScalarDB, on the same data set and the same statements.

  1. generate a data set (emp / dept / bonus) of the requested size
  2. load it into Oracle Database (the migration source) and, through ScalarDB, into the ScalarDB backend
  3. convert difftest/cases/bench.sql with scalardb_migrate -> ScalarDB SQL (OK / WARN) or an app-side plan (PLANNED)
  4. hand both variants of every statement to `residual-runner bench`, which times them from one JVM
  5. compare the result sets (compatibility) and summarise the latencies (performance)

  .venv/bin/python difftest/bench.py --rows 20000 --iterations 20 [--backend postgres|cassandra]
  .venv/bin/python difftest/bench.py --case difftest/cases/nosql-patterns.sql --rows 40000 --backend cassandra

Only the harness talks to Oracle. Everything on the ScalarDB side goes through ScalarDB (SQL/JDBC or the Core API).
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "difftest"))
from scalardb_migrate.converter import convert_script  # noqa: E402
from run import ORACLE, RUNNER, Source, norm, sh  # noqa: E402
from backends import BACKENDS, schema_loader  # noqa: E402

NAMESPACE = "bench"
WORK = ROOT / "difftest/work/bench"
ORACLE_URL = "jdbc:oracle:thin:@//localhost:1521/FREEPDB1"


def dataset(n_emp: int) -> dict:
    """Deterministic data set. sal spreads over 1000..10000 so that a >9900 filter selects about 1 %."""
    base = datetime.date(2015, 1, 1)
    emp = [{"empno": i,
            "ename": f"emp{i:06d}",
            "sal": round(1000 + (i * 37) % 9000 + 0.5, 2),
            "comm": None if i % 3 == 0 else float(100 + i % 500),
            "deptno": i % 40,
            "hiredate": (base + datetime.timedelta(days=(i * 7) % 3000)).isoformat()}
           for i in range(n_emp)]
    dept = [{"deptno": d, "dname": f"dept{d:02d}"} for d in range(40)]
    bonus = [{"empno": i, "amount": (i * 13) % 10000} for i in range(0, n_emp, 5)]
    return {"emp": emp, "dept": dept, "bonus": bonus}


def nosql_dataset(n_orders: int) -> dict:
    """Customers and orders (difftest/cases/nosql-patterns.sql). 40 orders per customer; the same orders go into the
    RDB-style table (key order_id) and the NoSQL-style table (partition customer_id, clustering order_date, order_id).
    status has 5 values (1/5 of the table each), amount spreads over 1..10000 so that >9900 selects about 1 %,
    order_date spreads over 2020-01-01 .. 2025-06 so that one customer has about 13 orders since 2024-01-01."""
    base = datetime.date(2020, 1, 1)
    n_cust = max(1, n_orders // 40)
    customers = [{"customer_id": c, "name": f"cust{c:05d}", "region": f"R{c % 10}"} for c in range(n_cust)]
    orders = [{"order_id": i,
               "customer_id": i // 40,
               "order_date": (base + datetime.timedelta(days=(i * 13) % 2000)).isoformat(),
               "status": f"S{i % 5}",
               "amount": 1 + (i * 37) % 10000,
               "memo": "init"}
              for i in range(n_cust * 40)]
    return {"customers": customers, "orders_rdb": orders, "orders_by_customer": [dict(o) for o in orders]}


def area_sales_dataset(n_sales: int) -> dict:
    """Organization tree + sales (difftest/cases/bench-area-sales.sql). Two head offices, 10 areas, 20 shops per area at
    level 3, plus nodes the query leaves out (a level-4 corner, an orphan). Sales spread over 2025-10-01 .. 2027-02 so
    that about 73 % fall inside the 2026 range; 1 % of the amounts are NULL, none is 0 (no zero divisor)."""
    nodes = [{"node_id": 1, "parent_id": None, "node_name": "本部"}, {"node_id": 2, "parent_id": None, "node_name": "第2本部"}]
    shops = []
    for a in range(10):
        area = 100 + a
        nodes.append({"node_id": area, "parent_id": 1 if a < 7 else 2, "node_name": f"エリア{a:02d}"})
        for s in range(20):
            shop = 1000 + a * 100 + s
            nodes.append({"node_id": shop, "parent_id": area, "node_name": f"店舗{shop}"})
            shops.append(shop)
    nodes += [{"node_id": 90000, "parent_id": 1000, "node_name": "直営コーナー"},
              {"node_id": 90001, "parent_id": 99999, "node_name": "孤立店"}]
    targets = shops + [90000, 90001, 100]
    base = datetime.datetime(2025, 10, 1)
    sales = [{"shop_id": targets[(i * 7919) % len(targets)],
              "sales_date": (base + datetime.timedelta(minutes=(i * 104729) % (500 * 24 * 60))).isoformat(),
              "order_id": i,
              "amount": None if i % 100 == 0 else 100 + (i * 37) % 50000}
             for i in range(n_sales)]
    return {"organization_master": nodes, "sales_transactions": sales}


DATASETS = {"bench": dataset, "nosql-patterns": nosql_dataset, "bench-area-sales": area_sales_dataset}
PATH_LABEL = {"scalardb_sql": "ScalarDB SQL", "plan": "plan", "appside": "app-side Java"}


def annotations(sql: str) -> dict:
    out = {}
    for key in ("bench", "iterate", "compare", "appside"):
        m = re.search(rf"--\s*@{key}:\s*(.+)", sql)
        if m:
            out[key] = m.group(1).strip()
    fetches = re.findall(r"--\s*@fetch:\s*(\w+)\s*=\s*(.+)", sql)
    if fetches:
        out["fetch"] = [{"table": t, "sql": s.strip()} for t, s in fetches]
    return out


def strip_comments(sql: str) -> str:
    """The converter carries the source comments over into its output; the ScalarDB SQL parser gets them stripped."""
    return re.sub(r"\s+", " ", re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)).strip()


def body_of(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).strip()


def setup_oracle(results, data: dict, registry) -> None:
    print("== Oracle: DDL + data")
    src = Source("oracle")
    for r in results:
        if r.kind == "CREATE":
            src.ddl(body_of(r.source_sql).rstrip(";"))
    for table, rows in data.items():
        meta = registry.get(table)
        for i in range(0, len(rows), 5000):
            src.insert(table, rows[i:i + 5000], meta.columns if meta else {})
        print(f"   {table}: {len(rows)} rows")
    # index and optimizer statistics: without them Oracle would plan the benchmark queries blind
    for table in data:
        src.execute(f"BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, '{table.upper()}', cascade => TRUE); END;")
    src.close()


def setup_scalardb(registry, data: dict, chunk: int, backend) -> None:
    print(f"== ScalarDB ({backend.name}): Schema Loader + data through the ScalarDB Core API")
    WORK.mkdir(parents=True, exist_ok=True)
    schema = {f"{NAMESPACE}.{t.name}": t.to_schema_loader() for t in registry.tables()}
    (WORK / "schema.json").write_text(json.dumps(schema, indent=2))
    delete, create = schema_loader(backend, "/work/bench/schema.json")
    sh(*delete, check=False)
    sh(*create)
    for table, rows in data.items():
        rows_file = WORK / f"{table}.rows.json"
        rows_file.write_text(json.dumps(rows))
        out = sh(str(RUNNER), "load", "--properties", backend.core,
                 "--namespace", NAMESPACE, "--table", table, "--rows", str(rows_file), "--chunk", str(chunk))
        print("   ", out.strip())


def build_spec(results, args) -> tuple[dict, list[dict]]:
    """Turn the converted statements into a bench spec for the Java runner. Returns (spec, per-query metadata)."""
    queries, meta = [], []
    for r in results:
        ann = annotations(r.source_sql)
        if "bench" not in ann or r.kind == "CREATE":
            continue
        body = body_of(r.source_sql).rstrip(";")
        write = r.kind not in ("SELECT", "UNION")
        entry = {"id": f"Q{r.index}", "label": ann["bench"], "oracle_sql": body, "write": write}
        info = {"id": entry["id"], "label": ann["bench"], "source_sql": body, "status": r.status, "write": write,
                "compare": ann.get("compare", "rows"), "ordered": "ORDER BY" in body.upper(),
                "codes": sorted({i.code for i in r.issues if i.severity in ("ERROR", "WARN")}),
                "pattern": (r.plan or {}).get("pattern"), "note": None}
        if r.status in ("OK", "WARN"):
            entry["path"] = "scalardb_sql"
            entry["scalardb_sql"] = strip_comments(r.converted[0])
            info["scalardb_sql"] = entry["scalardb_sql"]
        elif r.status == "PLANNED":
            plan = json.loads(json.dumps(r.plan))
            for f in plan["fetch"]:
                f["namespace"] = NAMESPACE
                f["max_rows"] = args.row_limit
            plan_file = WORK / f"plan.{r.index}.json"
            plan_file.write_text(json.dumps(plan))
            entry["path"] = "plan"
            entry["plan_file"] = str(plan_file)
            entry["fetcher"] = args.fetcher
            info["scalardb_sql"] = " ; ".join(f["scalardb_sql"] for f in plan["fetch"])
            info["residual_sql"] = plan["residual"]["java"]["sql"]
        elif "appside" in ann and "fetch" in ann:
            # not convertible and not plannable: the hand-written AppSideQuery implementation
            entry["path"] = "appside"
            entry["appside_class"] = ann["appside"]
            entry["fetch"] = ann["fetch"]
            info["scalardb_sql"] = " ; ".join(f["sql"] for f in ann["fetch"])
        else:
            info["note"] = "; ".join(i.message for i in r.issues if i.severity == "ERROR")[:300]
            meta.append(info)
            continue
        info["path"] = entry["path"]
        if "iterate" in ann:
            if entry["path"] == "plan":
                info["note"] = "@iterate ignored: the plan carries the literal inside the fetch predicates"
            else:
                entry["oracle_sql"] = entry["oracle_sql"].replace(ann["iterate"], "${i}")
                entry["scalardb_sql"] = entry["scalardb_sql"].replace(ann["iterate"], "${i}")
                entry["iterate_base"] = int(ann["iterate"])  # the literal is the base; ${i} becomes base + iteration
                info["iterate"] = ann["iterate"]
        queries.append(entry)
        meta.append(info)
    spec = {"iterations": args.iterations, "warmup": args.warmup, "verify_rows": args.verify_rows,
            "oracle": {"url": ORACLE_URL, "user": ORACLE["user"], "password": ORACLE["password"]},
            "scalardb_sql_properties": BACKENDS[args.backend].sql_bench,
            "core_properties": BACKENDS[args.backend].core,
            "queries": queries}
    return spec, meta


def stats(ms: list[float]) -> dict:
    if not ms:
        return {}
    s = sorted(ms)
    return {"n": len(s), "min": round(s[0], 2), "p50": round(statistics.median(s), 2),
            "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2), "max": round(s[-1], 2),
            "mean": round(statistics.fmean(s), 2)}


def compare_samples(o: dict, s: dict, ordered: bool, mode: str, write: bool = False) -> tuple[str, str]:
    if o.get("error"):
        return "ORACLE_ERROR", o["error"]
    if s.get("error"):
        return "FAIL", s["error"]
    if o.get("rows") != s.get("rows"):
        return "FAIL", f"{'affected' if write else 'result'} row count differs: Oracle {o.get('rows')} vs ScalarDB {s.get('rows')}"
    if write:
        return "PASS", f"{o.get('rows')} row(s) affected on both sides"
    if mode == "count":
        return "PASS", ""
    e = [tuple(norm(x) for x in row) for row in o.get("sample", [])]
    a = [tuple(norm(x) for x in row) for row in s.get("sample", [])]
    if not ordered:
        # without ORDER BY the two engines may return the rows in different orders, so a truncated sample is
        # not comparable row by row: only the row count can be verified.
        if o.get("rows", 0) > len(e):
            return "PASS_COUNT_ONLY", f"unordered result larger than the {len(e)}-row sample; only the row count was compared"
        e, a = sorted(map(str, e)), sorted(map(str, a))
    if e == a:
        return "PASS", ""
    first = next((i for i, (x, y) in enumerate(zip(e, a)) if x != y), 0)
    return "FAIL", f"row {first}: Oracle {e[first] if first < len(e) else None} vs ScalarDB {a[first] if first < len(a) else None}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=str(ROOT / "difftest/cases/bench.sql"))
    ap.add_argument("--rows", type=int, default=20000, help="number of emp rows")
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--verify-rows", type=int, default=5000, help="rows compared value by value per query")
    ap.add_argument("--row-limit", type=int, default=100_000,
                    help="plan guardrail: max rows fetched per table (the converter's default is 10,000; raised here so "
                         "the full-scan queries can be timed at 40k rows)")
    ap.add_argument("--fetcher", default="jdbc", choices=["core", "jdbc"], help="how a plan fetches through ScalarDB")
    ap.add_argument("--backend", default="postgres", choices=sorted(BACKENDS), help="storage behind ScalarDB")
    ap.add_argument("--convert-storage", choices=["jdbc", "cassandra"],
                    help="storage the converter targets (default: the backend's; jdbc = conversion unaware of Cassandra)")
    ap.add_argument("--dataset", choices=sorted(DATASETS), help="data generator (default: named after the case file)")
    ap.add_argument("--skip-setup", action="store_true")
    ap.add_argument("--chunk", type=int, default=500, help="rows per ScalarDB load transaction")
    ap.add_argument("--out", default=str(ROOT / "out/bench"))
    args = ap.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    text = Path(args.case).read_text(encoding="utf-8")
    convert_storage = args.convert_storage or BACKENDS[args.backend].storage
    results, registry = convert_script(text, "oracle", storage=convert_storage)
    args.convert_storage = convert_storage
    data = DATASETS[args.dataset or Path(args.case).stem](args.rows)

    if not args.skip_setup:
        setup_oracle(results, data, registry)
        setup_scalardb(registry, data, args.chunk, BACKENDS[args.backend])

    spec, meta = build_spec(results, args)
    spec_file, result_file = WORK / "spec.json", WORK / "result.json"
    spec_file.write_text(json.dumps(spec, indent=2))
    print(f"== bench: {len(spec['queries'])} statements x {args.iterations} iterations "
          f"({args.warmup} warm-up), {args.rows} emp rows")
    subprocess.run([str(RUNNER), "bench", "--spec", str(spec_file), "--out", str(result_file)], check=True)

    raw = json.loads(result_file.read_text())
    by_id = {q["id"]: q for q in raw["queries"]}
    report = []
    for info in meta:
        q = by_id.get(info["id"])
        row = dict(info)
        if q is None:
            row["verdict"] = "NOT_CONVERTIBLE"
            report.append(row)
            continue
        o, s = q["oracle"], q["scalardb"]
        row["oracle_ms"] = stats(o.get("ms", []))
        row["scalardb_ms"] = stats(s.get("ms", []))
        row["rows"] = o.get("rows")
        row["fetched_rows"] = s.get("fetched_rows")
        row["fetch_ms"] = round(s["fetch_ms"], 2) if s.get("fetch_ms") is not None else None
        row["residual_ms"] = round(s["residual_ms"], 2) if s.get("residual_ms") is not None else None
        verdict, detail = compare_samples(o, s, info["ordered"], info["compare"], info["write"])
        row["verdict"], row["detail"] = verdict, detail
        if row["oracle_ms"] and row["scalardb_ms"]:
            row["ratio"] = round(row["scalardb_ms"]["p50"] / max(row["oracle_ms"]["p50"], 0.001), 1)
        report.append(row)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bench.json").write_text(json.dumps({"config": {"rows": args.rows, "iterations": args.iterations,
                                                               "warmup": args.warmup, "fetcher": args.fetcher,
                                                               "backend": args.backend, "case": Path(args.case).name,
                                                               "convert_storage": convert_storage,
                                                               "tables": {t: len(r) for t, r in data.items()}},
                                                    "results": report}, indent=2, ensure_ascii=False))
    print_table(report)
    (out_dir / "bench.md").write_text(markdown(report, args))
    print(f"\nwrote {out_dir/'bench.json'} and {out_dir/'bench.md'}")
    return 1 if any(r.get("verdict") in ("FAIL", "ORACLE_ERROR") for r in report) else 0


def print_table(report: list[dict]) -> None:
    print(f"\n{'id':<5}{'verdict':<16}{'path':<14}{'rows':>7}{'oracle p50':>12}{'scalardb p50':>14}{'x':>7}  label")
    for r in report:
        o = r.get("oracle_ms", {}).get("p50")
        s = r.get("scalardb_ms", {}).get("p50")
        path = PATH_LABEL.get(r.get("path"), "-")
        ratio = r.get("ratio")
        cells = [f"{r['id']:<5}", f"{r['verdict']:<16}", f"{path:<14}", f"{str(r.get('rows', '-')):>7}",
                 f"{'-' if o is None else format(o, '.2f'):>12}", f"{'-' if s is None else format(s, '.2f'):>14}",
                 f"{'-' if ratio is None else format(ratio, '.1f'):>7}", f"  {r['label']}"]
        print("".join(cells))
        if r.get("detail"):
            print(f"      {r['detail'][:150]}")
        if r.get("note"):
            print(f"      note: {r['note'][:150]}")


def markdown(report: list[dict], args) -> str:
    lines = ["# Oracle Database vs ScalarDB: compatibility and performance", "",
             f"- ScalarDB backend: {args.backend}; converter storage: {args.convert_storage}; case: {Path(args.case).name}",
             f"- data set: {args.rows} rows ({args.dataset or Path(args.case).stem} generator)",
             f"- {args.iterations} measured iterations after {args.warmup} warm-up iterations, single client thread",
             f"- app-side plans fetch through `--fetcher {args.fetcher}`", "",
             "| # | statement | ScalarDB path | rows | verdict | Oracle p50 (ms) | ScalarDB p50 (ms) | ratio | fetched rows |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in report:
        path = f"plan ({r.get('pattern')})" if r.get("path") == "plan" else PATH_LABEL.get(r.get("path"), "—")
        o = r.get("oracle_ms", {}).get("p50")
        s = r.get("scalardb_ms", {}).get("p50")
        lines.append(f"| {r['id']} | {r['label']} | {path} | {r.get('rows', '—')} | {r['verdict']} | "
                     f"{'—' if o is None else o} | {'—' if s is None else s} | {r.get('ratio', '—')} | {r.get('fetched_rows', '—')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
