"""SQL*Plus のスクリプト（文書の例のコード）を文に分ける。

文の種類: plsql_unit（CREATE PROCEDURE / FUNCTION / PACKAGE [BODY] / TRIGGER / TYPE [BODY]）、block（無名ブロック）、
sql（SQL 文）、sqlplus（SET / SHOW / EXEC / VARIABLE / PRINT など SQL*Plus の指示）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SQLPLUS = re.compile(r"^\s*(SET|SHOW|SHO|EXEC|EXECUTE|VARIABLE|VAR|PRINT|COLUMN|COL|SPOOL|PROMPT|CONNECT|CONN|"
                     r"DESCRIBE|DESC|@|START|CLEAR|TTITLE|BTITLE|BREAK|COMPUTE|WHENEVER|DEFINE|UNDEFINE|ACCEPT|"
                     r"PAUSE|REMARK|REM|HOST|TIMING|RUN|LIST|SQLPLUS)\b", re.I)
UNIT = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?"
                  r"(PROCEDURE|FUNCTION|PACKAGE\s+BODY|PACKAGE|TRIGGER|TYPE\s+BODY|TYPE|LIBRARY)\s+"
                  r"(?:\"?(\w+)\"?\.)?\"?([\w$#]+)\"?", re.I)
BLOCK = re.compile(r"^\s*(DECLARE|BEGIN|<<\s*\w+\s*>>)", re.I)
# SQL*Plus は SET 行の後も SQL の SET を区別しない。UPDATE … SET は行頭に来ない前提（文の途中）
SQL_SET_OPTION = re.compile(r"^\s*SET\s+(SERVEROUTPUT|ECHO|FEEDBACK|LINESIZE|PAGESIZE|TIMING|VERIFY|HEADING|"
                            r"AUTOTRACE|TERMOUT|DEFINE|LONG|NUMWIDTH|TRIMSPOOL|WRAP|SQLPROMPT|NULL|COLSEP)\b", re.I)


@dataclass
class Statement:
    kind: str           # plsql_unit / block / sql / sqlplus
    text: str
    unit_type: str = ""
    name: str = ""


def split(script: str) -> list[Statement]:
    lines = script.replace("\r", "").split("\n")
    out: list[Statement] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("--") or stripped == "/":
            i += 1
            continue
        if stripped.startswith("/*"):
            while i < len(lines) and "*/" not in lines[i]:
                i += 1
            i += 1
            continue
        first = stripped.split()[0].upper()
        if first == "SET" and not SQL_SET_OPTION.match(line) and re.match(r"^\s*SET\s+TRANSACTION", line, re.I):
            pass    # SET TRANSACTION は SQL
        elif SQLPLUS.match(line) and not (first == "SET" and re.match(r"^\s*SET\s+(TRANSACTION|ROLE|CONSTRAINTS?)\b", line, re.I)):
            # SQL*Plus の指示は 1 行（行末の '-' で続く）
            text = line
            while text.rstrip().endswith("-") and i + 1 < len(lines):
                i += 1
                text += "\n" + lines[i]
            out.append(Statement("sqlplus", text.strip()))
            i += 1
            continue
        unit = UNIT.match(line) if first == "CREATE" else None
        # CREATE 行が複数行に分かれている（CREATE OR REPLACE\n PROCEDURE p）ものを拾う
        if first == "CREATE" and not unit:
            joined = " ".join(l.strip() for l in lines[i:i + 3])
            unit = UNIT.match(joined)
        is_type_spec = unit and unit.group(1).upper() == "TYPE"
        if unit or BLOCK.match(line):
            # "/" 単独行まで。TYPE の仕様部も "/" で終わる
            body = []
            while i < len(lines) and lines[i].strip() != "/":
                body.append(lines[i])
                i += 1
            i += 1
            text = "\n".join(body).rstrip()
            if unit:
                out.append(Statement("plsql_unit", text, re.sub(r"\s+", " ", unit.group(1).upper()), unit.group(3)))
            else:
                out.append(Statement("block", text))
            continue
        # SQL 文: 行末の ';' か、単独の '/' まで
        body = []
        while i < len(lines):
            body.append(lines[i])
            s = lines[i].rstrip()
            i += 1
            if re.sub(r"--.*$", "", s).rstrip().endswith(";"):
                break
            if i < len(lines) and lines[i].strip() == "/":
                i += 1
                break
        text = "\n".join(body).strip()
        text = re.sub(r";\s*(--[^\n]*)?$", "", text).rstrip()
        out.append(Statement("sql", text))
    return out
