"""Differential test harness (docs/app-side-processing-plan.md §5).

  1. convert the case file with scalardb_migrate  -> ScalarDB SQL / app-side plans / Schema Loader JSON
  2. source database  : apply the DDL verbatim, load the dataset          (harness only; expected results)
  3. ScalarDB         : create tables with Schema Loader, load the dataset through ScalarDB Core (residual-runner load)
  4. for every SELECT : expected = source database; actual = residual-runner (plans) or ScalarDB SQL (converted,
                        only when a licensed cluster is available, --fetcher jdbc); compare result sets

The harness is the ONLY component that connects to the source database. Nothing here connects to ScalarDB's backend.

  .venv/bin/python difftest/run.py difftest/cases/postgres.sql --dialect postgres [--fetcher core|jdbc] \
      [--backend postgres|cassandra]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import datetime
import decimal

import oracledb
import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "difftest"))
from scalardb_migrate.converter import convert_script  # noqa: E402
from backends import BACKENDS, schema_loader  # noqa: E402

NAMESPACE = "difftest"
RUNNER = ROOT / "runtime-java/build/install/residual-runner/bin/residual-runner"
SOURCE_DSN = "postgresql://postgres:postgres@localhost:15432/source"
ORACLE = dict(user="source", password="source", dsn="localhost:1521/FREEPDB1")


class Source:
    """The migration-source database (PostgreSQL or Oracle). Used by the harness only, for expected results."""

    def __init__(self, dialect: str):
        self.dialect = dialect
        self.con = psycopg.connect(SOURCE_DSN, autocommit=True) if dialect == "postgres" else oracledb.connect(**ORACLE)

    def execute(self, sql: str, params=None):
        cur = self.con.cursor()
        if params is None:
            cur.execute(sql)  # no parameters: psycopg must not interpret '%' (e.g. LIKE 'S%') as a placeholder
        else:
            cur.execute(sql, params)
        return cur

    def fetch(self, sql: str):
        return self.execute(sql).fetchall()

    def ddl(self, sql: str):
        m = re.search(r"CREATE\s+(TABLE|INDEX)\s+(\w+)", sql, re.I)
        if m:
            kind, name = m.group(1).upper(), m.group(2)
            try:
                self.execute(f"DROP TABLE {name} CASCADE CONSTRAINTS PURGE" if self.dialect == "oracle" and kind == "TABLE"
                             else f"DROP TABLE IF EXISTS {name} CASCADE" if kind == "TABLE"
                             else f"DROP INDEX {name}" if self.dialect == "oracle" else f"DROP INDEX IF EXISTS {name}")
            except Exception:  # noqa: BLE001  (Oracle has no IF EXISTS)
                pass
        self.execute(sql)

    def insert(self, table: str, rows: list[dict], types: dict[str, str]):
        cols = list(rows[0].keys())
        marks = ", ".join(f":{i + 1}" for i in range(len(cols))) if self.dialect == "oracle" else ", ".join("%s" for _ in cols)
        data = []
        for row in rows:
            vals = []
            for c in cols:
                v = row[c]
                if self.dialect == "oracle" and isinstance(v, str) and types.get(c) in ("DATE", "TIMESTAMP", "TIMESTAMPTZ"):
                    v = datetime.datetime.fromisoformat(v)
                vals.append(v)
            data.append(vals)
        self.con.cursor().executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", data)
        if self.dialect == "oracle":
            self.con.commit()

    def close(self):
        self.con.close()


def sh(*cmd: str, check=True) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(runner_error(p.stderr + p.stdout))
    return p.stdout


def runner_error(text: str) -> str:
    """First meaningful line of a Java stack trace (exception message), stripped of the stack frames."""
    for line in text.splitlines():
        if line.startswith("Exception in thread") or line.startswith("Caused by"):
            return line.split(":", 2)[-1].strip()[:300] if line.startswith("Exception") else line[:300]
    return text.strip().splitlines()[-1][:300] if text.strip() else "unknown error"


def feature_of(sql: str) -> str:
    m = re.search(r"--\s*@feature:\s*(.+)", sql)
    return m.group(1).strip() if m else ""


def body_of(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).strip()


def norm(v):
    """Make values from PostgreSQL / Oracle / H2 / ScalarDB comparable: numbers as int-if-integral floats, dates as ISO."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, decimal.Decimal):
        v = float(v)
    if isinstance(v, float):
        v = round(v, 6)
        return int(v) if v.is_integer() else v
    if isinstance(v, datetime.datetime):
        return v.date().isoformat() if v.time() == datetime.time(0) else v.isoformat(sep=" ")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, str):
        try:  # H2 / JDBC may return a date-time string for a DATE value
            d = datetime.datetime.fromisoformat(v.replace("T", " ").split(".")[0])
            return d.date().isoformat() if d.time() == datetime.time(0) else d.isoformat(sep=" ")
        except ValueError:
            return v
    if v is None or isinstance(v, int):
        return v
    return str(v)


def compare(expected, actual, ordered: bool) -> bool:
    e = [tuple(norm(x) for x in r) for r in expected]
    a = [tuple(norm(x) for x in r) for r in actual]
    return e == a if ordered else sorted(map(str, e)) == sorted(map(str, a))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_file")
    ap.add_argument("--dialect", required=True, choices=["postgres", "oracle"])
    ap.add_argument("--fetcher", default="core", choices=["core", "jdbc"])
    ap.add_argument("--backend", default="postgres", choices=sorted(BACKENDS), help="storage behind ScalarDB")
    ap.add_argument("--convert-storage", choices=["jdbc", "cassandra"],
                    help="storage the converter targets (default: the backend's; jdbc = conversion unaware of Cassandra)")
    ap.add_argument("--skip-setup", action="store_true", help="tables and data already loaded")
    ap.add_argument("--json-out", help="write structured per-statement results here")
    args = ap.parse_args()
    records: list[dict] = []

    case = Path(args.case_file)
    text = case.read_text(encoding="utf-8")
    data = json.loads(case.with_suffix(".data.json").read_text(encoding="utf-8"))
    results, registry = convert_script(text, args.dialect, storage=args.convert_storage or BACKENDS[args.backend].storage)
    work = ROOT / "difftest/work"
    work.mkdir(exist_ok=True)

    # ScalarDB schema (namespace-qualified for Schema Loader)
    schema = {f"{NAMESPACE}.{t.name}": t.to_schema_loader() for t in registry.tables()}
    (work / "schema.json").write_text(json.dumps(schema, indent=2))

    backend = BACKENDS[args.backend]
    props = backend.core
    if not args.skip_setup:
        print(f"== source database ({args.dialect}): DDL + data")
        src = Source(args.dialect)
        for r in results:
            if r.kind == "CREATE":
                src.ddl(r.source_sql.strip())
        for table, rows in data.items():
            meta = registry.get(table)
            src.insert(table, rows, meta.columns if meta else {})
        src.close()
        print(f"== ScalarDB ({args.backend}): Schema Loader + data through ScalarDB Core")
        delete, create = schema_loader(backend, "/work/schema.json")
        sh(*delete, check=False)  # idempotent re-runs: drop the tables from a previous run
        sh(*create)
        for table, rows in data.items():
            rows_file = work / f"{table}.rows.json"
            rows_file.write_text(json.dumps(rows))
            print("  ", sh(str(RUNNER), "load", "--properties", props, "--namespace", NAMESPACE, "--table", table,
                          "--rows", str(rows_file)).strip())

    print("== queries")
    summary = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    src = Source(args.dialect)
    for r in results:
        if r.kind not in ("SELECT", "UNION", "EXCEPT", "INTERSECT", "PARSE_ERROR") and not (r.kind == "COMMAND" and "SELECT" in r.source_sql.upper()):
            continue
        body = body_of(r.source_sql)
        rec = {"index": r.index, "feature": feature_of(r.source_sql), "sql": body, "convert_status": r.status,
               "codes": sorted({i.code for i in r.issues if i.severity in ("ERROR", "WARN")}),
               "pattern": (r.plan or {}).get("pattern"), "result": None, "error": None, "fetched": None,
               "fetch_sql": [f["scalardb_sql"] for f in (r.plan or {}).get("fetch", [])],
               "unresolved": (r.plan or {}).get("unresolved", [])}
        records.append(rec)
        ordered = "ORDER BY" in body.upper()
        try:
            expected = src.fetch(body)
        except Exception as e:  # noqa: BLE001
            rec["result"], rec["error"] = "CASE_ERROR", f"source database rejected the statement: {str(e).splitlines()[0][:200]}"
            summary["SKIP"] += 1
            print(f"CASE [{r.index}] {body[:70]}  ({rec['error']})")
            continue
        if r.status == "PLANNED":
            plan = dict(r.plan)
            for f in plan["fetch"]:
                f["namespace"] = NAMESPACE
            plan_file = work / f"plan.{r.index}.json"
            plan_file.write_text(json.dumps(plan))
            try:
                out = json.loads(sh(str(RUNNER), "run", "--plan", str(plan_file), "--properties",
                                    props if args.fetcher == "core" else backend.sql,
                                    "--fetcher", args.fetcher))
            except RuntimeError as e:
                summary["FAIL"] += 1
                rec["result"], rec["error"] = "FAIL", str(e)
                print(f"FAIL [{r.index}] {body[:70]}\n      runner error: {e}")
                continue
            ok = compare(expected, out["rows"], ordered)
            summary["PASS" if ok else "FAIL"] += 1
            rec["result"], rec["fetched"] = "PASS" if ok else "FAIL", out["stats"]["fetched_rows"]
            print(f"{'PASS' if ok else 'FAIL'} [{r.index}] plan {plan['pattern']:<6} fetched={out['stats']['fetched_rows']:<3} {body[:70]}")
            if not ok:
                rec["error"] = f"result mismatch: expected {expected[:5]} actual {out['rows'][:5]}"
                print(f"      expected {expected}\n      actual   {out['rows']}")
        elif r.status in ("OK", "WARN") and args.fetcher == "jdbc":
            try:
                out = json.loads(sh(str(RUNNER), "sql", "--properties", backend.sql,
                                    "--sql", r.converted[0]))  # default namespace comes from the client properties
            except RuntimeError as e:
                summary["FAIL"] += 1
                rec["result"], rec["error"] = "FAIL", str(e)
                print(f"FAIL [{r.index}] {body[:70]}\n      {e}")
                continue
            ok = compare(expected, out["rows"], ordered)
            summary["PASS" if ok else "FAIL"] += 1
            rec["result"] = "PASS" if ok else "FAIL"
            if not ok:
                rec["error"] = f"result mismatch: expected {expected[:5]} actual {out['rows'][:5]}"
            print(f"{'PASS' if ok else 'FAIL'} [{r.index}] scalardb-sql {body[:70]}")
        elif r.status in ("OK", "WARN"):
            summary["SKIP"] += 1
            rec["result"], rec["error"] = "SKIP", "ScalarDB SQL needs the licensed cluster (--fetcher jdbc)"
            print(f"SKIP [{r.index}] {r.status:<7} {body[:70]}  (ScalarDB SQL needs the licensed cluster: --fetcher jdbc)")
        else:
            summary["SKIP"] += 1
            reason = "; ".join(i.message for i in r.issues if i.severity == "ERROR")[:300]
            rec["result"], rec["error"] = "NOT_CONVERTIBLE", reason
            print(f"SKIP [{r.index}] {r.status:<7} {body[:70]}  ({reason[:80]})")
    src.close()
    print(f"\nPASS={summary['PASS']} FAIL={summary['FAIL']} SKIP={summary['SKIP']}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
