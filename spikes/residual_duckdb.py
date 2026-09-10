"""Spike: "fetch from ScalarDB, then run the ORIGINAL SQL on the fetched rows with DuckDB".

The rows below stand in for the result of a ScalarDB fetch (SELECT <columns> FROM <table> WHERE <pushdown>).
The original Oracle / PostgreSQL / MySQL SQL is transpiled to DuckDB with sqlglot and executed in-process.

Run: .venv/bin/python spikes/residual_duckdb.py
Result on 2026-09-10 (sqlglot 30.18, duckdb latest): 13/14 OK; the only failure is Oracle (+)/ROWNUM,
which the static converter rewrites before this stage.
"""

import duckdb
import sqlglot

emp = [dict(empno=1, ename="smith", sal=800, comm=None, deptno=10, hiredate="2021-03-01"),
       dict(empno=2, ename="allen", sal=1600, comm=300, deptno=30, hiredate="2019-05-02"),
       dict(empno=3, ename="ward", sal=1250, comm=500, deptno=30, hiredate="2022-01-15"),
       dict(empno=4, ename="jones", sal=2975, comm=None, deptno=20, hiredate="2020-11-11")]
dept = [dict(deptno=10, dname="ACCOUNTING"), dict(deptno=20, dname="RESEARCH"), dict(deptno=30, dname="SALES")]
bonus = [dict(empno=2), dict(empno=4)]

# ScalarDB type -> DuckDB type
SCALARDB_TO_DUCKDB = {"BOOLEAN": "BOOLEAN", "INT": "INTEGER", "BIGINT": "BIGINT", "FLOAT": "FLOAT", "DOUBLE": "DOUBLE",
                      "TEXT": "VARCHAR", "BLOB": "BLOB", "DATE": "DATE", "TIME": "TIME", "TIMESTAMP": "TIMESTAMP",
                      "TIMESTAMPTZ": "TIMESTAMPTZ"}


def load(con, name, rows, scalardb_types):
    cols = ", ".join(f"{c} {SCALARDB_TO_DUCKDB[t]}" for c, t in scalardb_types.items())
    con.execute(f"CREATE TABLE {name} ({cols})")
    con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in scalardb_types)})",
                    [[r[c] for c in scalardb_types] for r in rows])


TESTS = [
    ("oracle", "SELECT ename, NVL(comm, 0) AS comm, sal * 1.1 AS newsal FROM emp WHERE deptno = 30"),
    ("oracle", "SELECT DISTINCT deptno FROM emp ORDER BY deptno"),
    ("oracle", "SELECT ename FROM emp WHERE empno IN (SELECT empno FROM bonus)"),
    ("oracle", "SELECT d.dname, COUNT(*) AS n, SUM(e.sal) AS total FROM emp e JOIN dept d ON e.deptno = d.deptno "
               "GROUP BY d.dname HAVING COUNT(*) > 1"),
    ("oracle", "SELECT UPPER(ename) AS u, CASE WHEN sal > 1500 THEN 'HIGH' ELSE 'LOW' END AS grade FROM emp "
               "WHERE SUBSTR(ename, 1, 1) = 'a' OR sal > 2000"),
    ("postgres", "SELECT ename FROM emp ORDER BY sal DESC LIMIT 2 OFFSET 1"),
    ("postgres", "SELECT ename FROM emp WHERE deptno = 10 UNION SELECT ename FROM emp WHERE sal > 2000"),
    ("oracle", "SELECT ename, hiredate FROM emp WHERE hiredate > TO_DATE('2020-06-01','YYYY-MM-DD')"),
    ("oracle", "SELECT deptno, COUNT(DISTINCT deptno) AS d FROM emp GROUP BY deptno"),
    ("oracle", "SELECT ename, ROW_NUMBER() OVER (PARTITION BY deptno ORDER BY sal DESC) AS rn FROM emp"),
    ("oracle", "SELECT ename FROM emp e WHERE EXISTS (SELECT 1 FROM bonus b WHERE b.empno = e.empno)"),
    ("oracle", "SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+) AND ROWNUM <= 3"),
    ("mysql", "SELECT DATE_FORMAT(hiredate, '%Y-%m') AS ym, IFNULL(comm, 0) AS c FROM emp WHERE deptno = 30"),
    ("oracle", "SELECT ename, DECODE(deptno, 10, 'A', 30, 'S', '?') AS d, TRUNC(sal / 1000) AS k FROM emp"),
]


def main():
    con = duckdb.connect()
    load(con, "emp", emp, dict(empno="INT", ename="TEXT", sal="DOUBLE", comm="DOUBLE", deptno="INT", hiredate="DATE"))
    load(con, "dept", dept, dict(deptno="INT", dname="TEXT"))
    load(con, "bonus", bonus, dict(empno="INT"))
    ok = 0
    for dialect, sql in TESTS:
        try:
            residual = sqlglot.transpile(sql, read=dialect, write="duckdb")[0]
            rows = con.execute(residual).fetchall()
            ok += 1
            print(f"OK   {sql[:70]:<70} -> {rows[:4]}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {sql[:70]:<70} -> {type(e).__name__}: {str(e).splitlines()[0][:80]}")
    print(f"\n{ok}/{len(TESTS)} OK")


if __name__ == "__main__":
    main()
