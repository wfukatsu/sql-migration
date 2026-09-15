#!/usr/bin/env python3
"""変換先の組み込み関数一覧 (scripts/catalogs/<方言>.json) を、実際のデータベースから作り直す。

関数の移植性の判定は、この一覧に関数名があるかで行う。一覧はデータベースのバージョンに依存するので、
対象のバージョンが変わったら作り直す。指定した方言だけを更新する。

使い方 (リポジトリルートから):
    .venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --duckdb
    .venv/bin/python skills/sql-transpile/scripts/build_catalogs.py \\
        --postgres postgresql://postgres:postgres@localhost:15432/source \\
        --oracle source/source@localhost:1521/FREEPDB1 \\
        --mysql root:verify@127.0.0.1:13306

必要なドライバ: duckdb、psycopg、oracledb、pymysql (使う方言の分だけ)

終了コード: 0 = 成功 / 1 = いずれかの方言で失敗 / 2 = 引数の誤り
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "catalogs"


def duckdb_functions() -> tuple[str, list[str]]:
    import duckdb
    con = duckdb.connect(":memory:")
    names = [r[0] for r in con.execute("SELECT DISTINCT function_name FROM duckdb_functions()").fetchall()]
    return f"DuckDB {duckdb.__version__}", names


def postgres_functions(dsn: str) -> tuple[str, list[str]]:
    import psycopg
    with psycopg.connect(dsn) as con:
        version = con.execute("SHOW server_version").fetchone()[0]
        names = [r[0] for r in con.execute(
            "SELECT DISTINCT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'pg_catalog'").fetchall()]
    return f"PostgreSQL {version}", names


def oracle_functions(spec: str) -> tuple[str, list[str]]:
    import oracledb
    user_pass, _, dsn = spec.partition("@")
    user, _, password = user_pass.partition("/")
    with oracledb.connect(user=user, password=password, dsn=dsn) as con:
        cur = con.cursor()
        version = con.version
        cur.execute("SELECT DISTINCT name FROM v$sqlfn_metadata")
        names = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT procedure_name FROM all_procedures "
                    "WHERE owner = 'SYS' AND object_name = 'STANDARD' AND procedure_name IS NOT NULL")
        names += [r[0] for r in cur.fetchall()]
    return f"Oracle Database {version}", names


def mysql_functions(spec: str) -> tuple[str, list[str]]:
    import pymysql
    user_pass, _, host_port = spec.partition("@")
    user, _, password = user_pass.partition(":")
    host, _, port = host_port.partition(":")
    con = pymysql.connect(host=host, port=int(port or 3306), user=user, password=password)
    with con.cursor() as cur:
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
        cur.execute("SELECT DISTINCT t.name FROM mysql.help_topic t JOIN mysql.help_category c "
                    "ON c.help_category_id = t.help_category_id "
                    "WHERE c.name LIKE '%Function%' OR c.name LIKE '%Operator%'")
        names = [r[0] for r in cur.fetchall()]
    con.close()
    return f"MySQL {version}", names


def write(dialect: str, version: str, source: str, names: list[str]) -> None:
    clean = sorted({n.strip().upper() for n in names if n and n.strip()})
    data = {"database": version, "source": source, "extracted": datetime.date.today().isoformat(),
            "note": "scripts/build_catalogs.py で作り直せる", "functions": clean}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{dialect}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{dialect:<9} {len(clean):>5} 件  {version}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="変換先の組み込み関数一覧を作り直す")
    ap.add_argument("--duckdb", action="store_true", help="プロセス内の DuckDB から作る")
    ap.add_argument("--postgres", metavar="DSN", help="postgresql://user:pass@host:port/db")
    ap.add_argument("--oracle", metavar="SPEC", help="user/pass@host:port/service")
    ap.add_argument("--mysql", metavar="SPEC", help="user:pass@host:port")
    args = ap.parse_args(argv)
    jobs = []
    if args.duckdb:
        jobs.append(("duckdb", "duckdb_functions()", duckdb_functions, ()))
    if args.postgres:
        jobs.append(("postgres", "pg_proc の pg_catalog スキーマ分", postgres_functions, (args.postgres,)))
    if args.oracle:
        jobs.append(("oracle", "V$SQLFN_METADATA と SYS.STANDARD のプロシージャ", oracle_functions, (args.oracle,)))
    if args.mysql:
        jobs.append(("mysql", "mysql.help_topic の関数と演算子の分類", mysql_functions, (args.mysql,)))
    if not jobs:
        ap.print_usage(sys.stderr)
        return 2
    failed = False
    for dialect, source, fn, fn_args in jobs:
        try:
            version, names = fn(*fn_args)
            write(dialect, version, source, names)
        except Exception as e:  # noqa: BLE001
            print(f"{dialect}: 失敗 {str(e).splitlines()[0][:120]}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
