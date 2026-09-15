"""Golden verification for app-side (Java) implementations of Oracle queries.

When an Oracle query runs neither on ScalarDB SQL nor in the H2 residual engine (CONNECT BY, window functions over
fetched rows, ...), it is reimplemented in Java as a com.scalar.migrate.appside.AppSideQuery. This script freezes
Oracle's behaviour once and checks the Java implementation against it afterwards without Docker or Oracle.

  capture : run setup + query on Oracle, write the input tables and the result to <out>/golden.json   (needs Oracle)
  check   : run the Java implementation on golden.json with GoldenCheck                               (JVM only)

The Oracle connection comes from a profile (difftest/sources.py; default difftest/conf/sources/oracle-local.json).
capture runs the setup script, so it only accepts a disposable database. To take the golden data from an existing
database that is not disposable (e.g. the production source, read once), pass --no-setup --allow-production with a
profile whose values come from environment variables; the capture then runs in a read-only transaction. golden.json
holds the rows of the dumped tables -- keep a capture of real data out of the repository.

  .venv/bin/python difftest/golden.py capture --profile oracle=difftest/conf/sources/oracle-production.example.json \
      --no-setup --allow-production --query query.sql --tables organization_master,sales_transactions --out /secure/dir

  .venv/bin/python difftest/golden.py capture --setup difftest/golden/area-sales/setup.sql \
      --query difftest/golden/area-sales/query.sql --tables organization_master,sales_transactions \
      --out difftest/golden/area-sales
  .venv/bin/python difftest/golden.py check --golden difftest/golden/area-sales \
      --impl com.scalar.migrate.examples.AreaSalesReport

golden.json: {"query", "ordered", "tables": {name: [{col: value}]}, "expected": {"columns", "rows": [[value]]}}
with Decimal -> {"$dec": "1.5"}, datetime -> {"$ts": ISO}, date -> {"$date": ISO}, NULL -> null.
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "runtime-java/build/install/residual-runner/lib"
MAIN = "com.scalar.migrate.appside.golden.GoldenCheck"
sys.path.insert(0, str(ROOT / "difftest"))
from sources import DISPOSABLE, ProfileError, parse_profile_args, source_config  # noqa: E402


def split_statements(text: str) -> list[str]:
    """Split a script on ';' at line ends; drop comment-only chunks."""
    out = []
    for chunk in re.split(r";[ \t]*$", text, flags=re.M):
        body = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            out.append(chunk.strip())
    return out


def is_ordered(query: str) -> bool:
    """True if the top-level statement has ORDER BY (row order is then part of the expected result)."""
    import sqlglot

    tree = sqlglot.parse_one(query, read="oracle")
    return tree.args.get("order") is not None


def encode(v):
    if isinstance(v, decimal.Decimal):
        return {"$dec": str(v)}
    if isinstance(v, datetime.datetime):  # before date: datetime is a date subclass
        return {"$ts": v.isoformat()}
    if isinstance(v, datetime.date):
        return {"$date": v.isoformat()}
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    raise TypeError(f"cannot encode {type(v).__name__} value {v!r}")


def capture(args) -> int:
    import oracledb

    # check the database before reading anything else, so a refused profile is reported first
    cfg = source_config("oracle", parse_profile_args(args.profile), writes=not args.no_setup,
                        allow_production=args.allow_production)
    oracledb.defaults.fetch_decimals = True  # NUMBER as Decimal, not float
    query = Path(args.query).read_text().strip().rstrip(";").rstrip()
    tables = [t.strip().lower() for t in args.tables.split(",") if t.strip()]
    print(f"capture from {cfg.label()}{' (read-only, no setup)' if args.no_setup else ''}")
    con = oracledb.connect(**cfg.oracle_kwargs())
    try:
        cur = con.cursor()
        if args.no_setup:
            cur.execute("SET TRANSACTION READ ONLY")  # the dumps and the query see one consistent, unmodifiable state
        else:
            for stmt in split_statements(Path(args.setup).read_text()):
                try:
                    cur.execute(stmt)
                except oracledb.DatabaseError:
                    body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).lstrip()
                    if not body.upper().startswith("DROP"):
                        raise
            con.commit()

        dumped = {}
        for t in tables:
            cur.execute(f"SELECT * FROM {t}")
            cols = [d[0].lower() for d in cur.description]
            dumped[t] = [dict(zip(cols, map(encode, r))) for r in cur.fetchall()]

        cur.execute(query)
        columns = [d[0].lower() for d in cur.description]
        rows = [[encode(v) for v in r] for r in cur.fetchall()]
        if args.no_setup:
            con.rollback()  # end the read-only transaction
    finally:
        con.close()

    golden = {"query": query, "ordered": is_ordered(query), "tables": dumped,
              "expected": {"columns": columns, "rows": rows}}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "golden.json").write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n")
    sizes = ", ".join(f"{t}={len(r)}" for t, r in dumped.items())
    print(f"wrote {out / 'golden.json'}: {sizes}, expected={len(rows)} rows, ordered={golden['ordered']}")
    if cfg.environment not in DISPOSABLE:
        print(f"note: golden.json holds rows from environment {cfg.environment!r}; do not commit it", file=sys.stderr)
    return 0


def check(args) -> int:
    golden = Path(args.golden)
    if golden.is_dir():
        golden = golden / "golden.json"
    if not LIB.is_dir():
        print(f"{LIB} not found; run: cd runtime-java && gradle installDist", file=sys.stderr)
        return 2
    cp = str(LIB) + "/*" + (os.pathsep + args.cp if args.cp else "")  # the JVM expands the '*' itself
    return subprocess.run(["java", "-cp", cp, MAIN, str(golden), args.impl]).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="run setup + query on Oracle and write golden.json")
    c.add_argument("--setup", help="script creating and filling the tables (required unless --no-setup)")
    c.add_argument("--no-setup", action="store_true",
                   help="read existing tables only, in a read-only transaction (no setup script is run)")
    c.add_argument("--allow-production", action="store_true",
                   help="with --no-setup: allow a profile whose environment is not disposable (e.g. production)")
    c.add_argument("--profile", action="append", metavar="oracle=PATH",
                   help="Oracle profile (difftest/sources.py); default difftest/conf/sources/oracle-local.json")
    c.add_argument("--query", required=True)
    c.add_argument("--tables", required=True, help="comma-separated tables to dump as the implementation's input")
    c.add_argument("--out", required=True, help="directory for golden.json")
    k = sub.add_parser("check", help="run a Java AppSideQuery against golden.json")
    k.add_argument("--golden", required=True, help="golden.json or its directory")
    k.add_argument("--impl", required=True, help="fully qualified AppSideQuery class name")
    k.add_argument("--cp", help="extra classpath for the implementation")
    args = ap.parse_args()
    if args.cmd == "check":
        return check(args)
    if not args.no_setup and not args.setup:
        ap.error("capture needs --setup, or --no-setup to read existing tables")
    if args.allow_production and not args.no_setup:
        ap.error("--allow-production is only for a read-only capture (--no-setup)")
    try:
        return capture(args)
    except ProfileError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        if args.no_setup and "ORA-01466" in str(e):
            # a read-only transaction cannot read a table whose definition changed within the last few seconds
            print("capture failed: ORA-01466 -- the tables were (re)created moments ago; retry in a few seconds",
                  file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
