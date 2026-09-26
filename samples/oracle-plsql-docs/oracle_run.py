"""文書の例を Oracle でそのまま流し、文ごとの結果（成否・エラー・DBMS_OUTPUT・問合せの行）を記録する。

    SRC_ORACLE_USER=plsqldoc SRC_ORACLE_PASSWORD=plsqldoc .venv/bin/python samples/oracle-plsql-docs/oracle_run.py [例番号 ...]

例ごとにスキーマを空にして HR（work/hr の hr_create.sql / hr_populate.sql）を作り直し、前の例への依存
（examples.py の prerequisites）を流してから、例の文を SQL*Plus と同じ順に実行する。出力は work/oracle/<ex_n_m>.json。
SQL*Plus の指示のうち EXEC は無名ブロックにし、それ以外（SET / COLUMN / SHOW など）は流さない。
"""
from __future__ import annotations

import datetime
import decimal
import json
import os
import re
import sys
import time
from pathlib import Path

import oracledb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from examples import WORK, load  # noqa: E402
from script import Statement, split  # noqa: E402

OUT = WORK / "oracle"


def connect():
    con = oracledb.connect(user=os.environ.get("SRC_ORACLE_USER", "plsqldoc"),
                           password=os.environ.get("SRC_ORACLE_PASSWORD", "plsqldoc"),
                           dsn=f"{os.environ.get('SRC_ORACLE_HOST', 'localhost')}:"
                               f"{os.environ.get('SRC_ORACLE_PORT', '1521')}/FREEPDB1")
    con.call_timeout = 60_000
    return con


def hr_statements() -> list[Statement]:
    """HR スキーマの 12.1 版（db-sample-schemas の v12.1.0.2）。文書の結果（King の雇用日 2003-06-17 など）はこのデータ。
    hr_main.sql と同じく、勤務時間外の DML を断る trigger secure_employees は無効にしておく。"""
    out = []
    for name in ("hr_cre.sql", "hr_popul.sql", "hr_idx.sql", "hr_code.sql"):
        text = (WORK / "hr" / name).read_text(encoding="utf-8")
        out += [s for s in split(text) if s.kind != "sqlplus"]
    out.append(Statement("sql", "ALTER TRIGGER secure_employees DISABLE"))
    return out


DROP_ORDER = ["TRIGGER", "PACKAGE", "PROCEDURE", "FUNCTION", "VIEW", "MATERIALIZED VIEW", "SYNONYM", "TABLE",
              "TYPE", "SEQUENCE", "LIBRARY", "JAVA SOURCE", "JOB"]


def reset(cur, hr: list[Statement]) -> None:
    cur.execute("SELECT object_type, object_name FROM user_objects WHERE object_type IN (%s)"
                % ", ".join(f"'{t}'" for t in DROP_ORDER))
    objects = sorted(cur.fetchall(), key=lambda o: DROP_ORDER.index(o[0]))
    for kind, name in objects:
        suffix = {"TABLE": " CASCADE CONSTRAINTS PURGE", "TYPE": " FORCE"}.get(kind, "")
        try:
            if kind == "JOB":
                cur.callproc("DBMS_SCHEDULER.DROP_JOB", [name, True])
            else:
                cur.execute(f'DROP {kind} "{name}"{suffix}')
        except oracledb.DatabaseError:
            pass
    for s in hr:
        cur.execute(s.text)
    cur.connection.commit()


def encode(v):
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, oracledb.LOB):
        return v.read()
    if isinstance(v, oracledb.DbObject):
        return repr(v)
    return v


def read_output(cur) -> list[str]:
    lines = []
    line, status = cur.var(oracledb.DB_TYPE_VARCHAR, 32767), cur.var(oracledb.DB_TYPE_NUMBER)
    while True:
        cur.callproc("DBMS_OUTPUT.GET_LINE", [line, status])
        if status.getvalue() != 0:
            return lines
        lines.append(line.getvalue() or "")


def object_errors(cur, name: str) -> list[str]:
    cur.execute("SELECT type, line, position, text FROM user_errors WHERE name = :n ORDER BY sequence",
                n=name.upper())
    return [f"{t} {line}/{pos}: {text.strip()}" for t, line, pos, text in cur.fetchall()]


def execute(cur, s: Statement) -> dict:
    record: dict = {"kind": s.kind, "text": s.text}
    text = s.text
    if s.kind == "sqlplus":
        m = re.match(r"^\s*EXEC(?:UTE)?\s+(.*?);?\s*$", text, re.I | re.S)
        if not m:
            record["skipped"] = "SQL*Plus の指示"
            return record
        text = f"BEGIN {m.group(1)}; END;"
    elif s.kind in ("plsql_unit", "block"):
        text = text if text.rstrip().endswith(";") or s.unit_type in ("TYPE",) else text
    if re.search(r"(?<![:\w]):[A-Za-z]\w*", re.sub(r"'(?:[^']|'')*'", "", text)) and s.kind != "plsql_unit":
        # SQL*Plus のバインド変数（VARIABLE x）。ハーネスからは束縛できない
        if not re.search(r":=", text) or re.search(r"(?<![:\w]):[A-Za-z]\w*\s*:=|[=(,]\s*:[A-Za-z]", text):
            if re.search(r"(?<![:\w]):(?!NEW\b|OLD\b|PARENT\b)[A-Za-z]\w*", text, re.I):
                record["skipped"] = "SQL*Plus のバインド変数"
                return record
    started = time.monotonic()
    try:
        cur.execute(text)
        if cur.description:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(200)
            record["columns"] = cols
            record["rows"] = [[encode(v) for v in r] for r in rows]
        elif cur.rowcount is not None and cur.rowcount >= 0 and s.kind == "sql":
            record["rowcount"] = cur.rowcount
        if cur.warning is not None:
            record["warning"] = str(cur.warning)
        if s.kind == "plsql_unit":
            errors = object_errors(cur, s.name)
            if errors:
                record["compile_errors"] = errors
        record["ok"] = True
    except oracledb.DatabaseError as e:
        (err,) = e.args
        record["ok"] = False
        record["error"] = {"code": err.code, "message": (err.message or "").strip()}
    record["elapsed"] = round(time.monotonic() - started, 3)
    record["output"] = read_output(cur)
    return record


def run_example(con, ex, hr) -> dict:
    """con は例ごとに新しい接続である。7-18 が ALTER SESSION で変えた NLS_DATE_FORMAT が後の例へ漏れた（2026-09-26）。"""
    cur = con.cursor()
    reset(cur, hr)
    cur.callproc("DBMS_OUTPUT.ENABLE", [None])
    prerequisites = []
    for number, s in ex.prerequisites:
        r = execute(cur, s)
        prerequisites.append({"from": number, "ok": r.get("ok"), "error": r.get("error"),
                              "compile_errors": r.get("compile_errors"), "text": s.text[:200]})
    con.commit()
    statements = [execute(cur, s) for s in ex.statements]
    con.commit()
    return {"number": ex.number, "title": ex.title, "url": ex.url, "prerequisites": prerequisites,
            "statements": statements}


def main(argv=None) -> int:
    only = set(argv if argv is not None else sys.argv[1:])
    examples = [e for e in load() if not only or e.number in only]
    OUT.mkdir(parents=True, exist_ok=True)
    hr = hr_statements()
    con = connect()
    try:
        for ex in examples:
            con.close()
            con = connect()
            try:
                result = run_example(con, ex, hr)
            except oracledb.DatabaseError as e:
                # 接続が切れた（call_timeout など）。記録して繋ぎ直す
                result = {"number": ex.number, "title": ex.title, "url": ex.url, "harness_error": str(e)}
                try:
                    con.close()
                except Exception:
                    pass
                con = connect()
            (OUT / f"{ex.key}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                                encoding="utf-8")
            status = result.get("harness_error") or ", ".join(
                ("ok" if r.get("ok") else f"ORA-{r['error']['code']:05d}" if r.get("error") else r.get("skipped", "?"))
                + ("(compile errors)" if r.get("compile_errors") else "")
                for r in result["statements"])
            print(f"{ex.number:>6} {status[:150]}", flush=True)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
