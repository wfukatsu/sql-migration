"""work/examples.json を読み、例ごとに文へ分け、作るオブジェクトと前の例への依存を求める。

文書の例は章の中で前の例が作った表や手続きを使う（「例 6-x で作成した表」）。例ごとに HR スキーマから
やり直すので、前の例で作られたオブジェクトを参照する例には、それを作った文（と同じ例の中でその表へ書く文）を
「前提」として前に足す。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from script import Statement, split

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"

HR_OBJECTS = {"regions", "countries", "locations", "departments", "jobs", "employees", "job_history",
              "emp_details_view", "locations_seq", "departments_seq", "employees_seq",
              "secure_dml", "secure_employees", "add_job_history", "update_job_history"}
CREATES = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+TEMPORARY\s+|PRIVATE\s+TEMPORARY\s+)?"
                     r"(?:(?:NON)?EDITIONABLE\s+)?(TABLE|VIEW|SEQUENCE|SYNONYM|MATERIALIZED\s+VIEW)\s+"
                     r"(?:\"?\w+\"?\.)?\"?([\w$#]+)\"?", re.I)
WRITES = re.compile(r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|DELETE|ALTER\s+TABLE|CREATE\s+(?:UNIQUE\s+)?INDEX\s+\w+\s+ON)\s+"
                    r"(?:\w+\.)?([\w$#]+)", re.I)


# 文書の例の不備の最小修正（Oracle 26ai でそのままでは通らないもの）。理由は README の「文書の例について分かったこと」
FIXES = {
    # PUT_LINE がブロックの END; の外にある（SQL*Plus でも PLS-00103）
    "2-25": [("END;\n\nDBMS_OUTPUT.PUT_LINE('bonus = ' || TO_CHAR(bonus));",
              "  DBMS_OUTPUT.PUT_LINE('bonus = ' || TO_CHAR(bonus));\nEND;")],
    # 宣言されていない print(...) が紛れている（PLS-00201）
    "5-39": [(" print(t1_row.c2);", "")],
    # パッケージ仕様部の後の '/' が抜けていて、続く無名ブロックが仕様部の一部になる（PLS-00103）
    "6-30": [("  TYPE mytab IS TABLE OF rec INDEX BY pls_integer;\nEND;\n", "  TYPE mytab IS TABLE OF rec INDEX BY pls_integer;\nEND;\n/\n")],
    # DBMS_LOCK は SYS からの権限付与が要る。26ai では同じ働きの DBMS_SESSION.SLEEP を使う
    "9-4": [("DBMS_LOCK.SLEEP", "DBMS_SESSION.SLEEP")],
}


def _fixed(number: str, parts: list[dict]) -> list[dict]:
    fixes = FIXES.get(number)
    if not fixes:
        return parts
    code = [p for p in parts if p["role"] == "code"]
    for old, new in fixes:
        hit = next((p for p in code if old in p["text"]), None)
        if hit is None:
            raise SystemExit(f"example {number}: the fix no longer applies ({old[:40]!r})")
        hit["text"] = hit["text"].replace(old, new)
    return parts


@dataclass
class Example:
    number: str                 # "2-1"
    title: str
    page: str
    url: str
    parts: list[dict]
    statements: list[Statement] = field(default_factory=list)
    creates: set[str] = field(default_factory=set)
    prerequisites: list[tuple[str, Statement]] = field(default_factory=list)   # (例番号, 文)

    @property
    def key(self) -> str:
        return "ex_" + self.number.replace("-", "_")

    @property
    def code(self) -> str:
        return "\n".join(p["text"] for p in self.parts if p["role"] == "code")

    @property
    def results(self) -> list[str]:
        return [p["text"] for p in self.parts if p["role"] == "result"]


def created_by(statement: Statement) -> str | None:
    if statement.kind == "plsql_unit":
        return statement.name.lower()
    if statement.kind == "sql":
        m = CREATES.match(statement.text)
        if m:
            return m.group(2).lower()
    return None


def written_table(statement: Statement) -> str | None:
    if statement.kind != "sql":
        return None
    m = WRITES.match(statement.text)
    return m.group(1).lower() if m else None


def words(text: str) -> set[str]:
    text = re.sub(r"--[^\n]*", " ", text)
    text = re.sub(r"'(?:[^']|'')*'", " ", text)
    return {w.lower() for w in re.findall(r"[A-Za-z][\w$#]*", text)}


def load() -> list[Example]:
    raw = json.loads((WORK / "examples.json").read_text(encoding="utf-8"))
    examples = [Example(r["number"], r["title"], r["page"], r["url"], _fixed(r["number"], r["parts"])) for r in raw]
    defined: dict[str, tuple[Example, list[Statement]]] = {}   # 名前 -> 最後にそれを作った例と、作る文・書く文
    for ex in examples:
        ex.statements = split(ex.code)
        ex.creates = {n for n in (created_by(s) for s in ex.statements) if n}
        _resolve(ex, defined)
        for name in ex.creates:
            defining = [s for s in ex.statements if created_by(s) == name or written_table(s) == name]
            defined[name] = (ex, defining)
    return examples


def _resolve(ex: Example, defined: dict) -> None:
    seen: set[str] = set()
    ordered: list[tuple[str, Statement]] = []

    def need(text: str, depth: int) -> None:
        if depth > 5:
            return
        for w in sorted(words(text)):
            if w in seen or w in ex.creates or w in HR_OBJECTS or w not in defined:
                continue
            seen.add(w)
            source, statements = defined[w]
            for s in statements:
                need(s.text, depth + 1)
            ordered.extend((source.number, s) for s in statements)

    need(ex.code, 0)
    # 同じ文が 2 度入らないように
    unique, keys = [], set()
    for number, s in ordered:
        if (number, s.text) not in keys:
            keys.add((number, s.text))
            unique.append((number, s))
    ex.prerequisites = unique
