#!/usr/bin/env python3
"""Convert the DML test SQL (skills/sql-transpile/examples/dml/<dialect>.sql) to ScalarDB SQL and benchmark it: the
source database (Oracle / PostgreSQL / MySQL) queried directly vs the converted statements on ScalarDB Cluster, timed
from one JVM (residual-runner bench).

  * writes (INSERT / UPDATE / DELETE / MERGE) run on the base data set of the file. Before every iteration both sides
    are reset to it (untimed), so each iteration applies the statement to the same rows and the timing includes COMMIT
  * reads (SELECT) run on the base data plus generated rows (--orders), loaded once
  * statements the converter reports as ERROR are listed, not timed

For ScalarDB only, the IDENTITY / AUTO_INCREMENT clause of audit_log is dropped before conversion, so that the table
exists in ScalarDB (with its key known, a write that leaves the key to the database is reported as ERROR).

  .venv/bin/python difftest/bench_dml.py --dialect oracle [--orders 20000] [--iterations 15] [--out out/dml-bench]

Needs the source database of the dialect (difftest docker compose: source-oracle / source-postgres, MySQL on 13306 as in
README) and ScalarDB Cluster on the PostgreSQL backend (docker compose --profile cluster).
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import json
import logging
import random
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "difftest"))
from scalardb_migrate.cli import render_markdown  # noqa: E402
from scalardb_migrate.converter import _split_statements, convert_script  # noqa: E402
from backends import BACKENDS, schema_loader  # noqa: E402
from bench import compare_samples, stats, strip_comments  # noqa: E402
from run import RUNNER, sh  # noqa: E402
from sources import PROFILES, ProfileError, jdbc_spec, parse_profile_args, source_config  # noqa: E402

logging.getLogger("sqlglot").setLevel(logging.ERROR)
DML = ROOT / "skills/sql-transpile/examples/dml"
NAMESPACE = "bench"
WORK = ROOT / "difftest/work/dml-bench"
READ_KINDS = ("SELECT", "UNION", "INTERSECT", "EXCEPT")
PG_SCHEMA = "dml_bench"



# --------------------------------------------------------------------------------------------------
# source databases (the harness talks to them only for setup and for reading back the loaded rows)
# --------------------------------------------------------------------------------------------------

class Source:
    def __init__(self, dialect: str):
        self.dialect = dialect
        if dialect == "oracle":
            import oracledb
            self.con = oracledb.connect(**source_config("oracle", PROFILES).oracle_kwargs())
        elif dialect == "postgres":
            import psycopg
            self.con = psycopg.connect(**source_config("postgres", PROFILES).psycopg_kwargs())
        else:
            import pymysql
            self.con = pymysql.connect(**source_config("mysql", PROFILES).pymysql_kwargs())

    def execute(self, sql: str) -> None:
        cur = self.con.cursor()
        cur.execute(sql)
        self.con.commit()

    def reset(self, tables: list[str], sequences: list[str]) -> None:
        cur = self.con.cursor()
        if self.dialect == "postgres":
            cur.execute(f"DROP SCHEMA IF EXISTS {PG_SCHEMA} CASCADE")
            cur.execute(f"CREATE SCHEMA {PG_SCHEMA}")
            cur.execute(f"SET search_path TO {PG_SCHEMA}")
        for t in tables:
            try:
                cur.execute(f"DROP TABLE {t} CASCADE CONSTRAINTS PURGE" if self.dialect == "oracle"
                            else f"DROP TABLE IF EXISTS {t}")
            except Exception:  # noqa: BLE001  (Oracle has no IF EXISTS)
                pass
        for s in sequences if self.dialect == "oracle" else []:
            try:
                cur.execute(f"DROP SEQUENCE {s}")
            except Exception:  # noqa: BLE001
                pass
        self.con.commit()

    def insert(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        cols = list(rows[0])
        marks = ", ".join(f":{i + 1}" for i in range(len(cols))) if self.dialect == "oracle" else ", ".join(["%s"] * len(cols))
        data = [[int(v) if isinstance(v, bool) and self.dialect == "oracle" else v for v in (r[c] for c in cols)]
                for r in rows]
        cur = self.con.cursor()
        for i in range(0, len(data), 5000):
            cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", data[i:i + 5000])
        self.con.commit()

    def rows(self, table: str) -> list[dict]:
        cur = self.con.cursor()
        cur.execute(f"SELECT * FROM {table}")
        names = [d[0].lower() for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def close(self) -> None:
        self.con.close()


# --------------------------------------------------------------------------------------------------
# the test file
# --------------------------------------------------------------------------------------------------

def parse(dialect: str) -> tuple[str, list[dict]]:
    text = (DML / f"{dialect}.sql").read_text(encoding="utf-8")
    stmts, in_tests = [], False
    for chunk in _split_statements(text, dialect):
        if re.search(r"^--\s*@tests\s*$", chunk, re.M):
            in_tests = True
        body = "\n".join(ln for ln in chunk.splitlines() if not ln.strip().startswith("--")).strip().rstrip(";")
        stmts.append({"chunk": chunk, "body": body, "setup": not in_tests,
                      "ann": {k: v.strip() for k, v in re.findall(r"--\s*@([\w-]+):\s*(.+)", chunk)}})
    return text, stmts


def for_scalardb(text: str) -> str:
    """Drop the key-generation clauses so that audit_log gets a ScalarDB table (ScalarDB has no generated keys)."""
    text = re.sub(r"\s+GENERATED BY DEFAULT AS IDENTITY \(START WITH \d+\)", "", text)
    text = re.sub(r"\s+AUTO_INCREMENT(?=\s+PRIMARY KEY)", "", text)
    return re.sub(r"\)\s*AUTO_INCREMENT\s*=\s*\d+\s*;", ");", text)


def scaled_rows(n_orders: int) -> dict[str, list[dict]]:
    """Rows added for the read benchmark. Keys stay clear of the base data (customers >= 10000, products >= 1000,
    orders >= 100000, warehouses 3..12, audit_log >= 1000), so every statement still finds its base rows."""
    rnd = random.Random(42)
    n_cust, n_prod = max(10, n_orders // 10), 200
    regions = ["EAST", "WEST", "NORTH", "SOUTH", None]
    categories = ["PERIPHERAL", "DISPLAY", "COMPUTER", "ACCESSORY"]
    statuses = ["NEW", "PAID", "SHIPPED", "CANCELLED"]
    customers = [{"customer_id": 10000 + i, "name": f"customer {i:05d}", "email": f"c{i}@example.com" if i % 7 else None,
                  "region": regions[i % 5], "vip": i % 9 == 0,
                  "created_at": datetime.date(2023, 1, 1) + datetime.timedelta(days=i % 600)} for i in range(n_cust)]
    products = [{"product_id": 1000 + i, "name": f"product {i:03d}", "category": categories[i % 4],
                 "price": None if i % 50 == 0 else decimal.Decimal(f"{5 + (i * 37) % 1500}.99"), "active": i % 11 != 0}
                for i in range(n_prod)]
    orders, items = [], []
    for i in range(n_orders):
        oid, total = 100000 + i, decimal.Decimal("0")
        for line in range(1, 2 + i % 4):
            qty, price = 1 + rnd.randrange(5), decimal.Decimal(rnd.randrange(500, 200000)) / 100
            items.append({"order_id": oid, "line_no": line, "product_id": 1000 + rnd.randrange(n_prod), "qty": qty,
                          "unit_price": price})
            total += qty * price
        orders.append({"order_id": oid, "customer_id": 10000 + rnd.randrange(n_cust),
                       "order_date": datetime.date(2024, 1, 1) + datetime.timedelta(days=rnd.randrange(365)),
                       "status": statuses[rnd.randrange(4)], "total": total, "note": None if i % 5 else "auto"})
    stock = [{"warehouse_id": w, "product_id": 1000 + p, "qty": rnd.randrange(500),
              "updated_at": datetime.datetime(2024, 8, 1, 9)} for w in range(3, 13) for p in range(n_prod)]
    audit = [{"log_id": 1000 + i, "table_name": "orders", "action": ("INSERT", "UPDATE", "DELETE")[i % 3],
              "logged_at": datetime.datetime(2024, 1, 1) + datetime.timedelta(minutes=7 * i)} for i in range(n_orders // 2)]
    return {"customers": customers, "products": products, "orders": orders, "order_items": items, "stock": stock,
            "audit_log": audit}


# --------------------------------------------------------------------------------------------------
# ScalarDB side
# --------------------------------------------------------------------------------------------------

def coerce(v, ty: str):
    """A source-database value as the JSON value residual-runner load expects for the ScalarDB column type."""
    if v is None:
        return None
    if ty in ("INT", "BIGINT"):
        return int(v)
    if ty in ("FLOAT", "DOUBLE"):
        return float(v)
    if ty == "BOOLEAN":
        return bool(v)
    if ty == "DATE":
        return (v.date() if isinstance(v, datetime.datetime) else v).isoformat() if hasattr(v, "isoformat") else str(v)
    if ty == "TIMESTAMP":
        return v.isoformat(sep=" ") if isinstance(v, datetime.datetime) else str(v)
    return str(v)


def sdb_literal(v, ty: str) -> str:
    if v is None:
        return "NULL"
    if ty == "BOOLEAN":
        return "TRUE" if v else "FALSE"
    if ty in ("INT", "BIGINT", "FLOAT", "DOUBLE"):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def load_scalardb(registry, data: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Recreate the ScalarDB tables and load the rows; returns the rows as loaded (coerced to ScalarDB types)."""
    WORK.mkdir(parents=True, exist_ok=True)
    backend = BACKENDS["postgres"]
    tables = [t for t in registry.tables() if t.name in data]
    (WORK / "schema.json").write_text(json.dumps({f"{NAMESPACE}.{t.name}": t.to_schema_loader() for t in tables}, indent=2))
    delete, create = schema_loader(backend, "/work/dml-bench/schema.json")
    sh(*delete, check=False)
    sh(*create)
    loaded = {}
    for t in tables:
        rows = [{c: coerce(r.get(c), ty) for c, ty in t.columns.items()} for r in data[t.name]]
        loaded[t.name] = rows
        f = WORK / f"{t.name}.rows.json"
        f.write_text(json.dumps(rows))
        print("   ", sh(str(RUNNER), "load", "--properties", backend.core, "--namespace", NAMESPACE, "--table", t.name,
                       "--rows", str(f), "--chunk", "500").strip())
    return loaded


# --------------------------------------------------------------------------------------------------
# benchmark
# --------------------------------------------------------------------------------------------------

def restart_cluster() -> None:
    """Restart the ScalarDB Cluster node and wait until it accepts connections. The node pools JDBC connections to the
    PostgreSQL backend, and PostgreSQL keeps prepared plans per connection: after the tables are recreated with other
    column types (a previous dialect's run), those plans fail with "cached plan must not change result type"."""
    import socket
    import time
    compose = ["docker", "compose", "-f", str(ROOT / "difftest/docker-compose.yml"), "--profile", "cluster"]
    print("== restarting ScalarDB Cluster")
    sh(*compose, "restart", "scalardb-cluster")
    for _ in range(90):
        try:
            with socket.create_connection(("localhost", 60053), timeout=1):
                time.sleep(5)  # the gRPC port opens before the node finishes loading metadata
                return
        except OSError:
            time.sleep(2)
    raise RuntimeError("ScalarDB Cluster did not come back on localhost:60053")


def setup_source(src: Source, stmts: list[dict], tables: list[str]) -> None:
    src.reset(tables, ["order_seq"])
    for s in stmts:
        if s["setup"]:
            src.execute(s["body"])


def build_spec(args, dialect: str, cases: list[dict], reset: dict | None) -> dict:
    queries = []
    for c in cases:
        q = {"id": c["id"], "label": c["note"], "oracle_sql": c["body"], "write": c["write"], "path": c["path"]}
        if c["path"] == "plan":
            q["plan_file"], q["fetcher"] = c["plan_file"], "jdbc"
        else:
            q["scalardb_sql"] = c["scalardb_sql"]
        if reset:
            q["reset_source"], q["reset_scalardb"] = reset["source"], reset["scalardb"]
        queries.append(q)
    return {"iterations": args.iterations, "warmup": args.warmup, "verify_rows": args.verify_rows,
            "source": jdbc_spec(source_config(dialect, PROFILES), PG_SCHEMA if dialect == "postgres" else None), "scalardb_sql_properties": BACKENDS["postgres"].sql_bench,
            "core_properties": BACKENDS["postgres"].core, "h2_indexes": args.h2_indexes, "queries": queries}


def run_spec(spec: dict, name: str) -> dict:
    spec_file, result_file = WORK / f"spec.{name}.json", WORK / f"result.{name}.json"
    spec_file.write_text(json.dumps(spec, indent=2, default=str))
    print(f"== bench {name}: {len(spec['queries'])} statements x {spec['iterations']} iterations")
    subprocess.run([str(RUNNER), "bench", "--spec", str(spec_file), "--out", str(result_file)], check=True)
    return {q["id"]: q for q in json.loads(result_file.read_text())["queries"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dialect", required=True, choices=["oracle", "postgres", "mysql"])
    ap.add_argument("--orders", type=int, default=20000, help="generated orders for the read benchmark")
    ap.add_argument("--iterations", type=int, default=15)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--verify-rows", type=int, default=5000)
    ap.add_argument("--row-limit", type=int, default=200_000, help="plan guardrail: max rows fetched per table")
    ap.add_argument("--only", choices=["reads", "writes"], help="run one phase only")
    ap.add_argument("--h2-indexes", action="store_true",
                    help="build the plans' H2 indexes (off by default; the joins of the read data need them)")
    ap.add_argument("--restart-cluster", action="store_true",
                    help="restart ScalarDB Cluster first (needed when a previous run used other column types)")
    ap.add_argument("--out", default=str(ROOT / "out/dml-bench"))
    ap.add_argument("--profile", action="append", metavar="DIALECT=PATH",
                    help="source-database profile (difftest/sources.py); default difftest/conf/sources/<dialect>-local.json")
    args = ap.parse_args()
    try:
        PROFILES.update(parse_profile_args(args.profile))
        source_config(args.dialect, PROFILES)  # refuse a non-disposable database before converting or connecting
    except ProfileError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    dialect = args.dialect
    out = Path(args.out) / dialect
    out.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    # 1. convert (for ScalarDB: key-generation clauses dropped so that audit_log exists). The annotation comments are
    # left out: application SQL does not carry them, and the converter keeps comments in the SQL it hands to H2
    _, stmts = parse(dialect)
    text = "".join(s["body"] + ";\n" for s in stmts)
    results, registry = convert_script(for_scalardb(text), dialect, row_limit=args.row_limit)
    assert len(results) == len(stmts)
    (out / "convert.md").write_text(render_markdown(results, dialect, f"{DML / dialect}.sql (key generation dropped)"),
                                    encoding="utf-8")
    tables = [t.name for t in registry.tables()]
    cases, not_run = [], []
    for s, r in zip(stmts, results):
        if s["setup"] or "id" not in s["ann"]:
            continue
        case = {"id": s["ann"]["id"], "note": s["ann"].get("note", ""), "body": s["body"], "status": r.status,
                "expected": s["ann"].get("expect-scalardb"), "write": r.kind not in READ_KINDS,
                "codes": sorted({i.code for i in r.issues if i.severity in ("ERROR", "WARN")})}
        if r.status in ("OK", "WARN"):
            case["path"], case["scalardb_sql"] = "scalardb_sql", strip_comments(r.converted[0])
        elif r.status == "PLANNED":
            plan = json.loads(json.dumps(r.plan))
            for f in plan["fetch"]:
                f["namespace"], f["max_rows"] = NAMESPACE, args.row_limit
            case["plan_file"] = str(WORK / f"plan.{dialect}.{case['id']}.json")
            Path(case["plan_file"]).write_text(json.dumps(plan))
            case["path"] = "plan"
        else:
            case["path"] = None
            case["reason"] = "; ".join(i.message for i in r.issues if i.severity == "ERROR")[:240]
            not_run.append(case)
            continue
        cases.append(case)
    writes, reads = [c for c in cases if c["write"]], [c for c in cases if not c["write"]]
    print(f"== {dialect}: {len(cases)} statements runnable on ScalarDB ({len(writes)} writes, {len(reads)} reads), "
          f"{len(not_run)} not convertible")

    if args.restart_cluster:
        restart_cluster()
    src = Source(dialect)
    raw: dict[str, dict] = {}
    meta = {t.name: t for t in registry.tables()}

    # 2. writes on the base data, reset before every iteration
    if args.only != "reads" and writes:
        print("== setup: base data")
        setup_source(src, stmts, tables)
        base = {t: src.rows(t) for t in tables}
        loaded = load_scalardb(registry, base)
        reset = {"source": [f"DELETE FROM {t}" for t in tables] + [s["body"] for s in stmts
                                                                    if s["setup"] and s["body"].upper().startswith("INSERT")],
                 "scalardb": [f"DELETE FROM {t}" for t in tables]
                             + [f"INSERT INTO {t} ({', '.join(meta[t].columns)}) VALUES "
                                f"({', '.join(sdb_literal(row[c], ty) for c, ty in meta[t].columns.items())})"
                                for t in tables for row in loaded[t]]}
        raw.update(run_spec(build_spec(args, dialect, writes, reset), f"{dialect}-writes"))

    # 3. reads on the base data plus generated rows
    if args.only != "writes" and reads:
        print(f"== setup: base data + {args.orders} generated orders")
        setup_source(src, stmts, tables)
        for t, rows in scaled_rows(args.orders).items():
            src.insert(t, rows)
        load_scalardb(registry, {t: src.rows(t) for t in tables})
        raw.update(run_spec(build_spec(args, dialect, reads, None), f"{dialect}-reads"))
    src.close()

    # 4. report
    report = []
    for c in cases:
        q = raw.get(c["id"])
        row = {k: v for k, v in c.items() if k not in ("plan_file",)}
        if q is None:
            row["verdict"] = "SKIPPED"
            report.append(row)
            continue
        o, s = q["oracle"], q["scalardb"]
        row["source_ms"], row["scalardb_ms"] = stats(o.get("ms", [])), stats(s.get("ms", []))
        row["rows"], row["fetched_rows"] = o.get("rows"), s.get("fetched_rows")
        row["verdict"], row["detail"] = compare_samples(o, s, "ORDER BY" in c["body"].upper(), "rows", c["write"])
        if row["source_ms"] and row["scalardb_ms"]:
            row["ratio"] = round(row["scalardb_ms"]["p50"] / max(row["source_ms"]["p50"], 0.001), 1)
        report.append(row)
    previous = out / "bench.json"
    if args.only and previous.is_file():
        # a one-phase rerun keeps the other phase's results from the previous run
        kept = {r["id"]: r for r in json.loads(previous.read_text(encoding="utf-8"))["results"]
                if r["verdict"] != "SKIPPED" and r["write"] == (args.only == "reads")}
        report = [kept.get(r["id"], r) if r["verdict"] == "SKIPPED" else r for r in report]
    config = {"dialect": dialect, "orders": args.orders, "iterations": args.iterations, "warmup": args.warmup,
              "tables_read_phase": {t: len(r) for t, r in scaled_rows(args.orders).items()}}
    (out / "bench.json").write_text(json.dumps({"config": config, "results": report, "not_convertible": not_run},
                                               indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "bench.md").write_text(markdown(dialect, args, report, not_run), encoding="utf-8")
    for r in report:
        sp, dp = (r.get("source_ms") or {}).get("p50"), (r.get("scalardb_ms") or {}).get("p50")
        print(f"{r['id']:<4} {r['verdict']:<16} {r['status']:<8} {'write' if r['write'] else 'read ':<6}"
              f"{'-' if sp is None else f'{sp:9.2f}'} {'-' if dp is None else f'{dp:9.2f}'} {r.get('ratio', '-'):>7}  "
              f"{(r.get('detail') or '')[:80]}")
    print(f"\nwrote {out / 'bench.json'} and {out / 'bench.md'}")
    return 1 if any(r["verdict"] in ("FAIL", "ORACLE_ERROR") for r in report) else 0


def markdown(dialect: str, args, report: list[dict], not_run: list[dict]) -> str:
    name = {"oracle": "Oracle", "postgres": "PostgreSQL", "mysql": "MySQL"}[dialect]
    lines = [f"# DML test SQL: {name} vs ScalarDB", "",
             f"- writes: base data set, reset before every iteration (untimed); timing includes COMMIT",
             f"- reads: base data + {args.orders} generated orders; {args.warmup} warm-up + {args.iterations} iterations, p50",
             "", "| id | statement | kind | conversion | path | verdict | source p50 (ms) | ScalarDB p50 (ms) | ratio | fetched rows |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in report:
        sp, dp = (r.get("source_ms") or {}).get("p50"), (r.get("scalardb_ms") or {}).get("p50")
        path = {"scalardb_sql": "ScalarDB SQL", "plan": "plan"}.get(r["path"], "—")
        lines.append(f"| {r['id']} | {r['note']} | {'write' if r['write'] else 'read'} | {r['status']} | {path} | "
                     f"{r['verdict']} | {'—' if sp is None else sp} | {'—' if dp is None else dp} | {r.get('ratio', '—')} | "
                     f"{r.get('fetched_rows') if r.get('fetched_rows') is not None else '—'} |")
    lines += ["", "## Not convertible (not timed)", "", "| id | statement | codes | reason |", "|---|---|---|---|"]
    for c in not_run:
        lines.append(f"| {c['id']} | {c['note']} | {', '.join(c['codes'])} | {c['reason'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
