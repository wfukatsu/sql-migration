"""Spike: "fetch from ScalarDB, then run the original SQL (transpiled to SQLite) on the fetched rows with sqlite3".

sqlite3 is in the Python standard library, so the Python reference runtime has no extra dependency.
Run: .venv/bin/python spikes/residual_sqlite.py
Result on 2026-09-10: 12/14 OK; failures are Oracle (+)/ROWNUM (statically rewritten by the converter) and TO_DATE.
"""

import sqlite3
import sys
from pathlib import Path

import sqlglot

sys.path.insert(0, str(Path(__file__).parent))
from residual_duckdb import TESTS, bonus, dept, emp  # noqa: E402

# ScalarDB type -> sqlite3 storage class (dates/times as ISO-8601 TEXT)
SCALARDB_TO_SQLITE = {"BOOLEAN": "INTEGER", "INT": "INTEGER", "BIGINT": "INTEGER", "FLOAT": "REAL", "DOUBLE": "REAL",
                      "TEXT": "TEXT", "BLOB": "BLOB", "DATE": "TEXT", "TIME": "TEXT", "TIMESTAMP": "TEXT",
                      "TIMESTAMPTZ": "TEXT"}


def load(con, name, rows, scalardb_types):
    cols = ", ".join(f"{c} {SCALARDB_TO_SQLITE[t]}" for c, t in scalardb_types.items())
    con.execute(f"CREATE TABLE {name} ({cols})")
    con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in scalardb_types)})",
                    [[r[c] for c in scalardb_types] for r in rows])


def main():
    con = sqlite3.connect(":memory:")
    load(con, "emp", emp, dict(empno="INT", ename="TEXT", sal="DOUBLE", comm="DOUBLE", deptno="INT", hiredate="DATE"))
    load(con, "dept", dept, dict(deptno="INT", dname="TEXT"))
    load(con, "bonus", bonus, dict(empno="INT"))
    ok = 0
    for dialect, sql in TESTS:
        try:
            residual = sqlglot.transpile(sql, read=dialect, write="sqlite")[0]
            rows = con.execute(residual).fetchall()
            ok += 1
            print(f"OK   {sql[:70]:<70} -> {rows[:4]}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {sql[:70]:<70} -> {type(e).__name__}: {str(e)[:80]}")
    print(f"\n{ok}/{len(TESTS)} OK")


if __name__ == "__main__":
    main()
