"""src/*.sql（SQL*Plus 用のスクリプト）を、変換ツールが読める形に分ける。

- sql/<同名>.sql      : SQL 文だけ（SET / WHENEVER / SHOW / SPOOL / VARIABLE / EXEC / PRINT の行と PL/SQL は除く）
- plsql/src/<名前>.<ext>: CREATE OR REPLACE PROCEDURE / FUNCTION / PACKAGE [BODY] / TRIGGER を 1 ユニット 1 ファイルに
- plsql/src/blocks/<名前>.prc : 無名ブロック（DECLARE … / BEGIN … END; /）を、引数なしの procedure に包んだもの
  （無名ブロックは呼び出し単位を持たないので、ツールが判定できる形にする。本文は変えない）
- plsql/src/types.sql   : CREATE OR REPLACE TYPE（スキーマレベルのオブジェクト型。変換ツールの対象外なので別に置く）

決定的な処理で、原文の SQL は書き換えない。python3 samples/oracle-samples/split_sources.py で作り直せる。
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
SQL_OUT = HERE / "sql"
PLSQL_OUT = HERE / "plsql" / "src"
BLOCK_OUT = PLSQL_OUT / "blocks"

# SQL*Plus の指示行。SET は SQL*Plus のオプション名が続くものだけ（UPDATE の SET 句や SET TRANSACTION は SQL なので残す）
SQLPLUS = re.compile(r"^\s*(SET\s+(ECHO|SERVEROUTPUT|LINESIZE|PAGESIZE|FEEDBACK|VERIFY|DEFINE|TIMING|HEADING|TERMOUT|AUTOCOMMIT|LONG|TRIMSPOOL)"
                     r"|WHENEVER|SHOW|SPOOL|VARIABLE|EXEC|PRINT|@@)\b", re.IGNORECASE)
UNIT = re.compile(
    r"^\s*CREATE\s+(OR\s+REPLACE\s+)?(PROCEDURE|FUNCTION|PACKAGE\s+BODY|PACKAGE|TRIGGER|TYPE)\s+([A-Za-z_][\w$#]*)",
    re.IGNORECASE,
)
BLOCK_START = re.compile(r"^\s*(DECLARE|BEGIN)\b", re.IGNORECASE)
WITH_FUNCTION = re.compile(r"^\s*WITH\s*$", re.IGNORECASE)

SUFFIX = {"PROCEDURE": ".prc", "FUNCTION": ".fnc", "PACKAGE": ".pks", "PACKAGE BODY": ".pkb", "TRIGGER": ".trg", "TYPE": ".typ"}

# 無名ブロックの名前（ファイル名と、包む procedure の名前）。原文の節の番号を残す
BLOCK_NAMES = {
    "00_setup.sql": ["setup_drop_objects", "setup_gather_stats"],
    "03_sql_dml.sql": ["dml_d_create_error_log"],
    "04_plsql_basics.sql": [
        "b04_1_variables", "b04_2_control_flow", "b04_3_implicit_cursor_attrs",
        "b04_4_1_explicit_cursor", "b04_4_2_cursor_for_loop", "b04_4_3_for_update_current_of",
        "b04_4_4_ref_cursor", "b04_5_records_collections", "b04_6_1_predefined_exceptions",
        "b04_6_2_user_exceptions",
    ],
    "05_plsql_units.sql": ["b05_1_call_raise_salary", "b05_3_call_emp_api", "b05_4_call_log_msg"],
    "06_plsql_advanced.sql": [
        "b06_1_bulk_collect_limit", "b06_2_forall_save_exceptions", "b06_2_2_forall_returning",
        "b06_3_native_dynamic_sql", "b06_3_6_dbms_sql", "b06_4_collection_in_sql",
        "b06_5_builtin_packages", "b06_5_2_scheduler_job", "b06_6_conditional_compilation",
    ],
}


def wrap_block(name: str, lines: list[str], origin: str) -> str:
    """DECLARE d BEGIN b END; -> CREATE OR REPLACE PROCEDURE name AS d BEGIN b END name;"""
    text = "\n".join(lines).rstrip()
    head = f"-- {origin} の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）\n"
    first = lines[0].strip().upper()
    if first.startswith("DECLARE"):
        body = text[len("DECLARE"):] if text.upper().startswith("DECLARE") else text
        return head + f"CREATE OR REPLACE PROCEDURE {name} AS" + body + "\n/\n"
    return head + f"CREATE OR REPLACE PROCEDURE {name} AS\n" + text + "\n/\n"


def split(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    sql: list[str] = []
    units: list[tuple[str, str, list[str]]] = []  # (kind, name, lines)
    blocks: list[list[str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if SQLPLUS.match(line):
            i += 1
            continue
        m = UNIT.match(line)
        if m:
            kind = re.sub(r"\s+", " ", m.group(2).upper())
            buf = []
            while i < len(lines) and lines[i].strip() != "/":
                buf.append(lines[i])
                i += 1
            i += 1  # the "/"
            units.append((kind, m.group(3), buf))
            continue
        if BLOCK_START.match(line):
            buf = []
            while i < len(lines) and lines[i].strip() != "/":
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(buf)
            continue
        if WITH_FUNCTION.match(line):  # 02 H-4: WITH FUNCTION … SELECT … / は SQL 文として残す（終端の / も残す。#30）
            buf = []
            while i < len(lines) and lines[i].strip() != "/":
                buf.append(lines[i])
                i += 1
            i += 1
            sql.extend(buf + ["/"])
            continue
        sql.append(line)
        i += 1

    SQL_OUT.mkdir(parents=True, exist_ok=True)
    body = "\n".join(sql).strip() + "\n"
    if re.search(r"^\s*[A-Za-z]", body, re.MULTILINE) and re.search(r";", body):
        (SQL_OUT / path.name).write_text(body, encoding="utf-8")

    PLSQL_OUT.mkdir(parents=True, exist_ok=True)
    types: list[str] = []
    for kind, name, buf in units:
        text = "\n".join(buf).rstrip() + "\n/\n"
        if kind == "TYPE":
            types.append(text)
            continue
        out = PLSQL_OUT / f"{name.lower()}{SUFFIX[kind]}"
        out.write_text(f"-- 出自: src/{path.name}\n" + text, encoding="utf-8")
    # CREATE TYPE は plsql/src/schema.sql に手で写してある（deploy が表と一緒に作る）。ここでは書き出さない

    names = BLOCK_NAMES.get(path.name, [])
    if len(names) != len(blocks):
        raise SystemExit(f"{path.name}: 無名ブロック {len(blocks)} 個に対して名前が {len(names)} 個")
    if blocks:
        BLOCK_OUT.mkdir(parents=True, exist_ok=True)
    for name, buf in zip(names, blocks):
        (BLOCK_OUT / f"{name}.prc").write_text(wrap_block(name, buf, f"src/{path.name}"), encoding="utf-8")


# 原文の不備で Oracle 自身がコンパイルできないユニットへの、最小の修正（2026-09-25、実 Oracle 26ai Free で確認）。
# 変換ツールの評価ではなく、Oracle で動く原文を得るための修正なので、ここに理由つきで置く。
FIXES = {
    # VALUES 句の中の `CASE WHEN DELETING …` は ORA-00984（trigger 述語は SQL の中では使えない）。
    # 先に PL/SQL の変数へ取り出してから INSERT する。意味は同じ
    "emp_salary_audit_trg.trg": (
        """BEGIN
  INSERT INTO emp_audit (employee_id, action, old_salary, new_salary)
  VALUES (:OLD.employee_id,
          CASE WHEN DELETING THEN 'DELETE' ELSE 'UPDATE' END,
          :OLD.salary, :NEW.salary);
END;""",
        """DECLARE
  v_action emp_audit.action%TYPE;
BEGIN
  IF DELETING THEN v_action := 'DELETE'; ELSE v_action := 'UPDATE'; END IF;   -- 原文は VALUES の中の CASE WHEN DELETING（ORA-00984）
  INSERT INTO emp_audit (employee_id, action, old_salary, new_salary)
  VALUES (:OLD.employee_id, v_action, :OLD.salary, :NEW.salary);
END;"""),
}


def apply_fixes() -> None:
    for name, (old, new) in FIXES.items():
        path = PLSQL_OUT / name
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise SystemExit(f"{name}: 直す箇所が見つからない（原文が変わった？）")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    for p in sorted(SRC.glob("0*.sql")):
        split(p)
    apply_fixes()
    print("sql:", sorted(q.name for q in SQL_OUT.glob("*.sql")))
    print("plsql:", sorted(q.name for q in PLSQL_OUT.glob("*")))
    print("blocks:", sorted(q.name for q in BLOCK_OUT.glob("*")) if BLOCK_OUT.exists() else [])


if __name__ == "__main__":
    main()
