#!/usr/bin/env python3
"""既存の PL/SQL の仕様書（Markdown）の骨組みを IR から作り、書き上がった仕様書を IR と突き合わせる。

    # 解析（python -m plsql.cli の --out-dir）から、事実の欄を入れた仕様書の骨組みを作る。
    # すでに仕様書があれば、事実の欄だけを書き直す（人が書いた文章には触れない）
    python skills/plsql-spec/scripts/spec_facts.py facts --analysis out/plsql-spec/analysis \\
        --out-dir out/plsql-spec/spec

    # 書き上がった仕様書を確かめる（未記入、事実の欄の古さ、文章に出てこないエラーコードと表、
    # 原文に無い位置の引用）
    python skills/plsql-spec/scripts/spec_facts.py check --analysis out/plsql-spec/analysis \\
        --out-dir out/plsql-spec/spec

事実の欄（`<!-- facts:begin ID -->` から `<!-- facts:end ID -->` まで）は、IR から機械的に出したもので、
読んだ人の解釈を含まない。文章（動作・業務ルール・エラーと例外・確かめたいこと）は、原文を読んで書く。
2 つを分けておくのは、文章が事実から離れたときに `check` で気づくためである。

終了コード: 0 = 問題なし / 1 = 仕様書に問題がある（`check`） / 2 = 実行エラー。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

UNWRITTEN = "（未記入"
PROSE_SECTIONS = (
    ("動作", "原文を上から読み、何をどの順で行うかを業務の言葉で書く。文ごとに原文の位置（`ファイル:行`）を添える"),
    ("業務ルール", "条件分岐・計算・既定値から読み取れる規則を 1 行ずつ。原文の位置を添える。推測は「確かめたいこと」へ"),
    ("エラーと例外", "事実の欄のエラーコードごとに、いつ起きるか・呼び出し側に何が返るか・途中までの書き込みがどうなるか"),
    ("確かめたいこと", "原文からは決められないこと（意図が読めない分岐、使われていない引数、コメントと食い違う処理）。無ければ「なし」"),
)
MODULE_PROSE = ("概要", "この module が業務で受け持つことを 2〜4 文で。routine を並べ直すのではなく、何のためにあるかを書く")
WRITE_LETTER = {"INSERT": "I", "UPDATE": "U", "DELETE": "D", "MERGE": "M"}
FIRES_ON = {"INSERT": ("INSERT",), "UPDATE": ("UPDATE",), "DELETE": ("DELETE",), "MERGE": ("INSERT", "UPDATE")}


class InputError(Exception):
    """解析の出力が読めない。仕様書の問題（1）ではなく、入力の誤り（2）として扱う。"""


# --- IR から事実を取る ---------------------------------------------------------------------------------------


@dataclass
class Facts:
    id: str
    module: str
    kind: str
    visibility: str
    file: str
    start: int
    end: int
    parameters: list[dict] = field(default_factory=list)
    returns: str | None = None
    sql: list[dict] = field(default_factory=list)
    raises: list[dict] = field(default_factory=list)
    handlers: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    transaction: list[dict] = field(default_factory=list)
    autonomous: bool = False
    dynamic: list[dict] = field(default_factory=list)
    loops: list[dict] = field(default_factory=list)
    fires: list[dict] = field(default_factory=list)
    ambient: list[dict] = field(default_factory=list)
    node: dict = field(default_factory=dict, repr=False)

    @property
    def tables(self) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for op in self.sql:
            for table in op["reads"]:
                found.setdefault(table, set()).add("R")
            for table in op["writes"]:
                found.setdefault(table, set()).add(WRITE_LETTER.get(op["kind"], "W"))
        return found

    @property
    def error_codes(self) -> list[int]:
        return sorted({r["code"] for r in self.raises if r["code"] is not None}, reverse=True)


def _line(node: dict) -> int | None:
    return (node.get("sourceRange") or {}).get("startLine")


def _type(t: dict | None) -> str:
    if not t:
        return ""
    oracle, resolved = t.get("oracle") or "", t.get("resolved") or ""
    return f"{oracle}（= {resolved}）" if resolved and resolved != oracle else oracle


def _one_line(text: str, width: int = 110) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


# 原文から来た文の id（`<routine>#stmt-3`、`#handler-1`、カーソルの問い合わせ `#stmt-5#query`）
FROM_SOURCE = re.compile(r"#(?:stmt|handler)-\d+(?:#query)?$")
AMBIENT = re.compile(r"\b(\w+\.(?:NEXTVAL|CURRVAL)|SYSDATE|SYSTIMESTAMP|CURRENT_TIMESTAMP|CURRENT_DATE|USER|SYS_CONTEXT|DBMS_RANDOM\.\w+)\b", re.I)


def _branch_word(node: dict, index: int, branch: dict, width: int) -> str:
    """分岐の条件を 1 語目つきで返す（`IF x > 0` / `ELSIF …` / `WHEN p_mode = 'A'`）。"""
    condition = _one_line(branch.get("condition") or "", width)
    if node.get("kind") == "Case":
        return f"WHEN {node['selector']} = {condition}" if node.get("selector") else f"WHEN {condition}"
    return f"{'IF' if index == 0 else 'ELSIF'} {condition}"


def _ambient(facts: Facts, node: dict, text: str | None) -> None:
    """引数と表の中身だけでは結果が決まらないもの（採番、現在時刻、実行者）。試験で固定しなければならない。"""
    for name in dict.fromkeys(m.upper() for m in AMBIENT.findall(text or "")):
        entry = {"line": _line(node), "name": name}
        if entry not in facts.ambient:
            facts.ambient.append(entry)


def _collect(node, facts: Facts, context: tuple[str, ...], triggers: set[str]) -> None:
    """文を 1 つずつ見て事実に足す。`context` は、その文に至る条件（IF / WHEN / LOOP）である。"""
    if isinstance(node, list):
        for child in node:
            _collect(child, facts, context, triggers)
        return
    if not isinstance(node, dict):
        return
    kind, where = node.get("kind"), " → ".join(context)
    if "#" in node.get("id", "") and not FROM_SOURCE.search(node["id"]):
        # lowering が足した文（trigger の織り込み、行数上限の検査、%ROWTYPE の展開）。原文の仕様ではない。
        # 中に原文の文が入っていれば、それは拾う
        for key in ("body", "elseBody", "exceptionHandlers"):
            _collect(node.get(key), facts, context, triggers)
        for branch in node.get("branches") or []:
            _collect(branch.get("body"), facts, context, triggers)
        return
    _ambient(facts, node, " ".join(str(node.get(k) or "") for k in ("originalSql", "expression", "message")))
    callee = node.get("resolvedTo") or node.get("callee") or ""
    if kind == "SqlOperation":
        facts.sql.append({"line": _line(node), "kind": node.get("sqlKind") or "SQL",
                          "reads": node.get("readSet") or [], "writes": node.get("writeSet") or [],
                          "lock": node.get("lockingMode"), "into": node.get("intoTargets") or [],
                          "text": _one_line(node.get("originalSql", "")), "when": where})
    elif kind == "Raise":
        facts.raises.append({"line": _line(node), "code": node.get("errorCode"), "exception": node.get("exception"),
                             "message": node.get("message"), "when": where})
    elif kind == "Call":
        # lowering が織り込んだ trigger の呼び出しは原文に無い。trigger は「発火する trigger」に定義から出す
        if callee.split(".")[0] not in triggers:
            facts.calls.append({"line": _line(node), "callee": callee, "resolved": bool(node.get("resolvedTo")),
                                "arguments": node.get("arguments") or [], "when": where})
    elif kind in ("Commit", "Rollback", "Savepoint"):
        facts.transaction.append({"line": _line(node), "kind": kind.upper(), "savepoint": node.get("savepoint"), "when": where})
    elif kind == "DynamicSql":
        facts.dynamic.append({"line": _line(node), "expression": _one_line(node.get("expression", "")),
                              "variants": [_one_line(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                                           for v in node.get("variants") or []], "when": where})
    elif kind in ("If", "Case"):
        for index, branch in enumerate(node.get("branches") or []):
            _collect(branch.get("body"), facts, context + (_branch_word(node, index, branch, 70),), triggers)
        _collect(node.get("elseBody"), facts, context + ("ELSE",), triggers)
        return
    elif kind == "Loop":
        facts.loops.append({"line": _line(node), "kind": node.get("loopKind"), "over": _one_line(node.get("cursor") or "", 90)})
        inner = context + (f"LOOP（{node.get('loopKind')}）",)
        _collect(node.get("query"), facts, context, triggers)
        _collect(node.get("body"), facts, inner, triggers)
        return
    elif kind == "ExceptionHandler":
        names = " OR ".join(node.get("exceptions") or [])
        facts.handlers.append({"line": _line(node), "exceptions": names, "when": where})
        _collect(node.get("body"), facts, context + (f"WHEN {names}",), triggers)
        return
    for key in ("body", "exceptionHandlers"):
        _collect(node.get(key), facts, context, triggers)


def load(analysis: Path) -> tuple[list[dict], dict[str, Facts], dict]:
    ir_path = analysis / "program.ir.json"
    if not ir_path.exists():
        raise InputError(f"{ir_path} が無い。python -m plsql.cli <src> --out-dir {analysis} を先に回す")
    program = json.loads(ir_path.read_text(encoding="utf-8"))
    inventory_path = analysis / "inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.exists() else {}
    modules = program.get("modules") or []
    triggers = {m["name"] for m in modules if m.get("moduleKind") == "trigger"}
    facts: dict[str, Facts] = {}
    for module in modules:
        for routine in module.get("routines") or []:
            where = routine.get("sourceRange") or module.get("sourceRange") or {}
            f = Facts(id=routine["id"], module=module["name"], kind=routine.get("routineKind") or "routine",
                      visibility=routine.get("visibility") or "", file=where.get("file", ""),
                      start=where.get("startLine", 0), end=where.get("endLine", 0),
                      parameters=routine.get("parameters") or [], returns=_type(routine.get("returnType")) or None,
                      autonomous=bool((routine.get("transactionEffects") or {}).get("autonomous")),
                      node=routine)
            _collect(routine.get("body"), f, (), triggers)
            _collect(routine.get("exceptionHandlers"), f, (), triggers)
            facts[f.id] = f
    for f in facts.values():
        for call in f.calls:
            if call["callee"] in facts and f.id not in facts[call["callee"]].called_by:
                facts[call["callee"]].called_by.append(f.id)
        for op in f.sql:
            for module in modules:
                if module.get("moduleKind") != "trigger" or module.get("triggerTable") not in op["writes"]:
                    continue
                event = (module.get("triggerEvent") or "").upper()
                if any(e in event for e in FIRES_ON.get(op["kind"], ())):
                    fire = {"trigger": module["name"], "table": module["triggerTable"], "line": op["line"],
                            "timing": module.get("triggerTiming"), "event": module.get("triggerEvent")}
                    if fire not in f.fires:
                        f.fires.append(fire)
    return modules, facts, inventory


# --- Mermaid ------------------------------------------------------------------------------------------------
# 図も事実の欄に入れる。IR から決定的に描くので、文章のように原文から離れていかない。


def _label(text, width: int = 60) -> str:
    """Mermaid の引用符つきラベルに入れられる形にする。`"` と `<` `>` は図の構文に食われる。"""
    text = _one_line(str(text or ""), width).replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")
    return text.replace("`", "'").replace("|", "#124;")


class _Flow:
    """routine の制御の流れ。文を上から辿り、分岐・ループ・例外で枝を分ける。"""

    def __init__(self, routine: dict, file: str):
        self.lines: list[str] = ["flowchart TD"]
        self.count = 0
        self.file = file
        self.classes: dict[str, list[str]] = {}
        self.loops: list[dict] = []   # いま中にいるループ（内側が末尾）。EXIT / CONTINUE の行き先
        start = self.node('(["開始"])')
        exits = self.sequence(routine.get("body") or [], [(start, None)])
        handlers = routine.get("exceptionHandlers") or []
        if handlers:
            exits += self.handlers(handlers, "routine の中で例外が起きたら")
        if exits:
            end = self.node('(["終了"])')
            for source, label in exits:
                self.edge(source, end, label)
        for name, style in (("sql", "fill:#e8f1fb,stroke:#3b6ea5"), ("write", "fill:#fdf0d5,stroke:#b7791f"),
                            ("error", "fill:#fde2e2,stroke:#c53030"), ("tx", "fill:#e9e3f7,stroke:#6b46c1")):
            if self.classes.get(name):
                self.lines.append(f"  classDef {name} {style};")
                self.lines.append(f"  class {','.join(self.classes[name])} {name};")

    def node(self, shape: str, kind: str | None = None) -> str:
        self.count += 1
        name = f"n{self.count}"
        self.lines.append(f"  {name}{shape}")
        if kind:
            self.classes.setdefault(kind, []).append(name)
        return name

    def edge(self, source: str, target: str, label: str | None = None, dotted: bool = False) -> None:
        arrow = "-.->" if dotted else "-->"
        self.lines.append(f"  {source} {arrow}|\"{_label(label, 40)}\"| {target}" if label else f"  {source} {arrow} {target}")

    def join(self, prev: list, target: str) -> None:
        for source, label in prev:
            self.edge(source, target, label)

    def at(self, node: dict) -> str:
        line = _line(node)
        return f"L{line}: " if line else ""

    def handlers(self, handlers: list[dict], why: str) -> list:
        exits = []
        raised = self.node(f'{{{{"{_label(why)}"}}}}', "error")
        for handler in handlers:
            names = " OR ".join(handler.get("exceptions") or [])
            caught = self.node(f'["{self.at(handler)}WHEN {_label(names)}"]', "error")
            self.edge(raised, caught, dotted=True)
            exits += self.sequence(handler.get("body") or [], [(caught, None)])
        return exits

    def sequence(self, statements: list, prev: list) -> list:
        for statement in statements or []:
            prev = self.one(statement, prev)
        return prev

    def one(self, s: dict, prev: list) -> list:
        kind = s.get("kind")
        if "#" in s.get("id", "") and not FROM_SOURCE.search(s["id"]):   # lowering が足した文は描かない
            inner = list(s.get("body") or []) + [x for b in s.get("branches") or [] for x in b.get("body") or []]
            return self.sequence(inner, prev)
        if not prev:          # RAISE / RETURN のあとの文には届かない
            return prev
        if kind in ("If", "Case"):
            exits = []
            for index, branch in enumerate(s.get("branches") or []):
                test = self.node(f'{{"{self.at(s)}{_label(_branch_word(s, index, branch, 50).split(" ", 1)[1], 50)}"}}')
                self.join(prev, test)
                exits += self.sequence(branch.get("body") or [], [(test, "はい")])
                prev = [(test, "いいえ")]
            if kind == "Case" and not s.get("elseBody"):
                # ELSE の無い CASE は、どの WHEN にも当たらなければ CASE_NOT_FOUND（ORA-06592）を上げる
                missed = self.node(f'(["{self.at(s)}エラー CASE_NOT_FOUND"])', "error")
                self.join(prev, missed)
                return exits
            return exits + self.sequence(s.get("elseBody") or [], prev)
        if kind == "Loop":
            over = s.get("cursor") or s.get("loopKind") or "LOOP"
            loop = self.node(f'{{{{"{self.at(s)}繰り返す: {_label(over, 50)}"}}}}', "sql" if s.get("query") else None)
            self.join(prev, loop)
            self.loops.append({"node": loop, "exits": []})
            for source, label in self.sequence(s.get("body") or [], [(loop, "1 件ずつ")]):
                self.edge(source, loop, label or "次へ")
            left = self.loops.pop()["exits"]
            # 条件の無い LOOP … END LOOP は、EXIT でしか終わらない
            return left + ([] if s.get("loopKind") == "basic" and left else [(loop, "終わり")])
        if kind in ("Exit", "Continue") and self.loops:
            # ラベルつき（EXIT outer WHEN …）は外側のループへ行くが、ラベルとループの対応は IR に無い。内側として描く
            word, condition = ("抜ける" if kind == "Exit" else "次の反復へ"), s.get("condition")
            node = self.node(f'{{"{self.at(s)}{kind.upper()} WHEN {_label(condition, 45)}"}}' if condition
                             else f'["{self.at(s)}{kind.upper()}"]')
            self.join(prev, node)
            if kind == "Exit":
                self.loops[-1]["exits"].append((node, "はい" if condition else word))
            else:
                self.edge(node, self.loops[-1]["node"], "はい" if condition else word)
            return [(node, "いいえ")] if condition else []
        if kind == "Block":
            exits = self.sequence(s.get("body") or [], prev)
            return exits + (self.handlers(s["exceptionHandlers"], "この塊の中で例外が起きたら") if s.get("exceptionHandlers") else [])
        if kind == "SqlOperation":
            writes = s.get("writeSet") or []
            tables = "、".join(writes or s.get("readSet") or [])
            lock = f"（{s['lockingMode']}）" if s.get("lockingMode") else ""
            node = self.node(f'[("{self.at(s)}{s.get("sqlKind")} {_label(tables, 40)}{_label(lock)}")]', "write" if writes else "sql")
        elif kind == "Raise":
            what = s.get("errorCode") if s.get("errorCode") is not None else (s.get("exception") or "再送出")
            node = self.node(f'(["{self.at(s)}エラー {_label(what)}"])', "error")
            self.join(prev, node)
            return []
        elif kind == "Return":
            node = self.node(f'(["{self.at(s)}返す: {_label(s.get("expression"), 45)}"])')
            self.join(prev, node)
            return []
        elif kind == "Call":
            node = self.node(f'[["{self.at(s)}呼ぶ: {_label(s.get("resolvedTo") or s.get("callee"), 45)}"]]')
        elif kind in ("Commit", "Rollback", "Savepoint"):
            node = self.node(f'["{self.at(s)}{kind.upper()} {_label(s.get("savepoint") or "")}"]', "tx")
        elif kind == "DynamicSql":
            node = self.node(f'[("{self.at(s)}動的 SQL: {_label(s.get("expression"), 45)}")]', "write")
        elif kind == "Assignment":
            node = self.node(f'["{self.at(s)}{_label(s.get("target"), 20)} := {_label(s.get("expression"), 40)}"]')
        else:
            node = self.node(f'["{self.at(s)}{_label(kind)}"]')
        self.join(prev, node)
        return [(node, None)]


def _mermaid(lines: list[str]) -> list[str]:
    return ["```mermaid", *lines, "```"]


def flow_diagram(routine: dict, file: str) -> list[str]:
    return _mermaid(_Flow(routine, file).lines)


def _ident(prefix: str, name: str) -> str:
    return prefix + re.sub(r"\W", "_", name)


def data_diagram(facts: list[Facts], by_module: bool) -> list[str]:
    """誰がどの表を読み書きするか。実線 = 書く（読みもするなら R も付く）、点線 = 読むだけ。索引では module の粒度、module の頁では routine の粒度。"""
    lines, edges, left, tables = ["flowchart LR"], {}, {}, set()
    for f in facts:
        who = f.module if by_module else f.id
        for table, ops in f.tables.items():
            edges.setdefault((who, table), set()).update(ops)
            left[who] = None
            tables.add(table)
    if not edges:
        return []
    for who in left:
        lines.append(f'  {_ident("r_", who)}["{_label(who)}"]')
    for table in sorted(tables):
        lines.append(f'  {_ident("t_", table)}[("{_label(table)}")]')
    for (who, table), ops in edges.items():
        # 矢印はどれも「誰が → どの表を」の向きにそろえる。向きを読み書きで変えると、左右 2 列に並ばず絡まる
        arrow = "-->" if ops - {"R"} else "-.->"
        lines.append(f'  {_ident("r_", who)} {arrow}|"{" ".join(sorted(ops))}"| {_ident("t_", table)}')
    return _mermaid(lines)


def call_diagram(facts: dict[str, Facts], modules: list[dict]) -> list[str]:
    """routine どうしの呼び出しと、書き込みで発火する trigger。"""
    lines, names = ["flowchart LR"], {}
    for f in facts.values():
        for call in f.calls:
            names[f.id] = names[call["callee"]] = None
            lines.append(f'  {_ident("r_", f.id)} --> {_ident("r_", call["callee"])}')
        for fire in {(x["trigger"], x["table"]) for x in f.fires}:
            names[f.id] = names[fire[0]] = None
            lines.append(f'  {_ident("r_", f.id)} -.->|"{_label(fire[1])} への書き込みで発火"| {_ident("r_", fire[0])}')
    if not names:
        return []
    triggers = {m["name"] for m in modules if m.get("moduleKind") == "trigger"}
    head = [f'  {_ident("r_", n)}{{{{"{_label(n)}"}}}}' if n in triggers else f'  {_ident("r_", n)}["{_label(n)}"]' for n in names]
    return _mermaid([lines[0], *head, *lines[1:]])



# --- Markdown -------------------------------------------------------------------------------------------------


def _at(f: Facts, line: int | None) -> str:
    return f"`{f.file}:{line}`" if line else "—"


def _cell(text) -> str:
    return str(text if text not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]


def routine_facts(f: Facts) -> str:
    out = [f"**事実**（IR から機械的に出した。手で書き換えない。原文: `{f.file}:{f.start}`〜`{f.end}`）", ""]
    head = f"- 種類: {f.kind}" + (f"（{f.visibility}）" if f.visibility else "")
    out.append(head + (f" / 戻り値: `{f.returns}`" if f.returns else "") + (" / **自律トランザクション**" if f.autonomous else ""))
    if f.called_by:
        out.append("- 呼び出し元: " + "、".join(f"`{c}`" for c in sorted(f.called_by)))
    out += ["", "処理の流れ（`L` は原文の行。青 = 読む、橙 = 書く、赤 = エラー、紫 = トランザクション制御）", ""] + flow_diagram(f.node, f.file)
    if f.parameters:
        out += ["", "引数", ""] + _table(["名前", "方向", "型", "既定値"], [
            [f"`{p['name']}`", p.get("direction"), f"`{_type(p.get('type'))}`", f"`{p['default']}`" if p.get("default") else None]
            for p in f.parameters])
    if f.tables:
        out += ["", "表（R = 読む / I・U・D・M = 書く）", ""] + _table(["表", "操作"], [
            [f"`{t}`", " ".join(sorted(ops))] for t, ops in sorted(f.tables.items())])
    if f.sql:
        out += ["", "SQL", ""] + _table(["位置", "種類", "読む", "書く", "条件", "文"], [
            [_at(f, op["line"]), op["kind"] + (f"（{op['lock']}）" if op["lock"] else ""),
             "、".join(op["reads"]), "、".join(op["writes"]), op["when"], f"`{op['text']}`"] for op in f.sql])
    if f.raises:
        out += ["", "上げるエラー", ""] + _table(["位置", "コード・例外", "メッセージ（式）", "条件"], [
            [_at(f, r["line"]), r["code"] if r["code"] is not None else (r["exception"] or "再送出（RAISE）"),
             f"`{r['message']}`" if r["message"] else None, r["when"]] for r in f.raises])
    if f.handlers:
        out += ["", "例外ハンドラ", ""] + _table(["位置", "受ける例外", "場所"], [
            [_at(f, h["line"]), h["exceptions"], h["when"] or "routine の末尾"] for h in f.handlers])
    if f.transaction:
        out += ["", "トランザクション制御", ""] + _table(["位置", "文", "条件"], [
            [_at(f, t["line"]), t["kind"] + (f" {t['savepoint']}" if t["savepoint"] else ""), t["when"]] for t in f.transaction])
    if f.calls:
        out += ["", "呼び出す routine", ""] + _table(["位置", "呼び先", "引数", "条件"], [
            [_at(f, c["line"]), f"`{c['callee']}`" + ("" if c["resolved"] else "（解決できず）"),
             "、".join(f"`{a}`" for a in c["arguments"]), c["when"]] for c in f.calls])
    if f.fires:
        out += ["", "発火する trigger（書き込む表に定義されているもの）", ""] + _table(["位置", "trigger", "表", "時点"], [
            [_at(f, x["line"]), f"`{x['trigger']}`", f"`{x['table']}`", f"{x['timing']} {x['event']}"] for x in f.fires])
    if f.dynamic:
        out += ["", "動的 SQL", ""] + _table(["位置", "組み立てる式", "列挙できた文"], [
            [_at(f, d["line"]), f"`{d['expression']}`", "<br>".join(f"`{v}`" for v in d["variants"]) or "列挙できず"]
            for d in f.dynamic])
    if f.ambient:
        out += ["", "引数と表の中身だけでは決まらないもの（採番・現在時刻・実行者）", ""] + _table(["位置", "使うもの"], [
            [_at(f, a["line"]), f"`{a['name']}`"] for a in f.ambient])
    if f.loops:
        out += ["", "ループ（OPEN / FETCH で回す明示カーソルも cursor-for と出る。位置はループの行で、問い合わせは宣言にある）", ""] + _table(["位置", "種類", "対象"], [
            [_at(f, x["line"]), x["kind"], f"`{x['over']}`" if x["over"] else None] for x in f.loops])
    return "\n".join(out)


def module_facts(module: dict, facts: dict[str, Facts]) -> str:
    where = module.get("sourceRange") or {}
    out = ["**事実**（IR から機械的に出した。手で書き換えない）", "",
           f"- 種類: {module.get('moduleKind')} / 原文: `{where.get('file', '')}`"]
    if module.get("moduleKind") == "trigger":
        columns = module.get("trigger_columns") or []
        out.append(f"- 発火: `{module.get('triggerTable')}` の {module.get('triggerTiming')} {module.get('triggerEvent')}"
                   + (f"（列: {'、'.join(columns)}）" if columns else "")
                   + (f" / WHEN `{_one_line(module['triggerWhen'])}`" if module.get("triggerWhen") else ""))
        writers = sorted({f.id for f in facts.values() for x in f.fires if x["trigger"] == module["name"]})
        out.append("- この trigger を発火させる routine: " + ("、".join(f"`{w}`" for w in writers) or "解析した範囲には無い"))
    state = [d for d in module.get("declarations") or [] if module.get("moduleKind") == "package"]
    if state:
        out += ["", "package の変数・定数（セッションの間、値が残る）", ""] + _table(["名前", "種類", "型", "初期値"], [
            [f"`{d.get('name')}`", d.get("declarationKind"), f"`{_type(d.get('type'))}`",
             f"`{_one_line(d['initial'], 60)}`" if d.get("initial") else None] for d in state])
    routines = [r["id"] for r in module.get("routines") or []]
    mine = [facts[r] for r in routines if r in facts]
    picture = data_diagram(mine, by_module=False)
    if picture:
        out += ["", "routine と表（実線 = 書く、点線 = 読むだけ。R = 読む / I・U・D・M = 書く）", ""] + picture
    out += ["", "routine: " + "、".join(f"`{r}`" for r in routines)]
    return "\n".join(out)


def index_facts(modules: list[dict], facts: dict[str, Facts], inventory: dict) -> str:
    out = ["**事実**（IR から機械的に出した。手で書き換えない）", ""]
    failed = (inventory.get("kpi") or {}).get("failedFiles") or []
    if failed:
        out += ["**解析できなかったファイル（この仕様書に入っていない）**: "
                + "、".join(f"`{x if isinstance(x, str) else x.get('file', x)}`" for x in failed), ""]
    out += ["module", ""] + _table(["module", "種類", "routine 数", "仕様"], [
        [f"`{m['name']}`", m.get("moduleKind"), len(m.get("routines") or []), f"[{m['name']}.md]({m['name']}.md)"]
        for m in modules])
    picture = data_diagram(list(facts.values()), by_module=True)
    if picture:
        out += ["", "module と表（実線 = 書く、点線 = 読むだけ。R = 読む / I・U・D・M = 書く）", ""] + picture
    picture = call_diagram(facts, modules)
    if picture:
        out += ["", "呼び出しと trigger（実線 = 呼ぶ、点線 = 書き込みで発火する）", ""] + picture
    tables = sorted({t for f in facts.values() for t in f.tables})
    if tables:
        out += ["", "表と routine（R = 読む / I・U・D・M = 書く）", ""] + _table(["表", "routine"], [
            [f"`{t}`", "、".join(f"`{f.id}` {' '.join(sorted(f.tables[t]))}" for f in facts.values() if t in f.tables)]
            for t in tables])
    errors = sorted(((r["code"], f, r) for f in facts.values() for r in f.raises if r["code"] is not None),
                    key=lambda x: (-x[0], x[1].id))
    if errors:
        out += ["", "エラーコード", ""] + _table(["コード", "routine", "位置", "メッセージ（式）"], [
            [code, f"`{f.id}`", _at(f, r["line"]), f"`{r['message']}`" if r["message"] else None] for code, f, r in errors])
    control = [f for f in facts.values() if f.transaction or f.autonomous]
    if control:
        out += ["", "自分でトランザクションを制御する routine", ""] + _table(["routine", "文"], [
            [f"`{f.id}`", "、".join(sorted({t["kind"] for t in f.transaction}) + (["自律トランザクション"] if f.autonomous else []))]
            for f in control])
    calls = [(f.id, c["callee"]) for f in facts.values() for c in f.calls]
    if calls:
        out += ["", "呼び出しの関係", ""] + [f"- `{a}` → `{b}`" for a, b in sorted(set(calls))]
    return "\n".join(out)


def _block(block_id: str, body: str) -> str:
    return f"<!-- facts:begin {block_id} -->\n{body}\n<!-- facts:end {block_id} -->"


BLOCK = re.compile(r"<!-- facts:begin (?P<id>\S+) -->\n.*?\n<!-- facts:end (?P=id) -->", re.S)


def _prose(title: str, hint: str, level: str) -> str:
    return f"{level} {title}\n\n{UNWRITTEN}: {hint}）\n"


def _routine_section(f: Facts) -> str:
    return "\n".join([f"## `{f.id}`", "", _block(f.id, routine_facts(f)), ""]
                     + [_prose(title, hint, "###") for title, hint in PROSE_SECTIONS])


def render(path: Path, title: str, blocks: list[tuple[str, str, str]]) -> tuple[str, list[str]]:
    """`blocks` は (ID, 事実, 初めて書くときの節全体)。すでにある節は事実の欄だけを差し替える。"""
    text = path.read_text(encoding="utf-8") if path.exists() else f"# {title}\n"
    existing = {m.group("id") for m in BLOCK.finditer(text)}
    wanted = {block_id for block_id, _, _ in blocks}
    bodies = {block_id: body for block_id, body, _ in blocks}
    text = BLOCK.sub(lambda m: _block(m.group("id"), bodies[m.group("id")]) if m.group("id") in bodies else m.group(0), text)
    for block_id, _, section in blocks:
        if block_id not in existing:
            text = text.rstrip("\n") + "\n\n" + section.rstrip("\n") + "\n"
    return text, sorted(existing - wanted)


def documents(modules: list[dict], facts: dict[str, Facts], inventory: dict) -> dict[str, tuple[str, list]]:
    index = index_facts(modules, facts, inventory)
    docs = {"README.md": ("PL/SQL の仕様", [("index", index, "\n".join([
        _block("index", index), "", _prose("全体の概要", "この PL/SQL 一式が業務で受け持つこと、主な流れ、module どうしの関係を書く", "##")]))])}
    for module in modules:
        block_id = f"module:{module['name']}"
        body = module_facts(module, facts)
        blocks = [(block_id, body, "\n".join([_block(block_id, body), "", _prose(*MODULE_PROSE, "##")]))]
        blocks += [(r["id"], routine_facts(facts[r["id"]]), _routine_section(facts[r["id"]])) for r in module.get("routines") or []]
        docs[f"{module['name']}.md"] = (f"`{module['name']}`（{module.get('moduleKind')}）", blocks)
    return docs


# --- commands -------------------------------------------------------------------------------------------------


def cmd_facts(args) -> int:
    modules, facts, inventory = load(Path(args.analysis))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, (title, blocks) in documents(modules, facts, inventory).items():
        text, gone = render(out / name, title, blocks)
        (out / name).write_text(text, encoding="utf-8")
        stale += [f"{name}: {block_id}" for block_id in gone]
    for entry in stale:
        print(f"IR に無くなった節が残っている（消すかどうかは人が決める）: {entry}", file=sys.stderr)
    print(f"MODULES={len(modules)} ROUTINES={len(facts)} STALE={len(stale)}", file=sys.stderr)
    return 0


def _sections(text: str) -> dict[str, str]:
    """routine の ID → その節の文章（事実の欄を除いたもの）。"""
    found = {}
    for part in re.split(r"(?m)^## ", text)[1:]:
        match = BLOCK.search(part)
        if match and not match.group("id").startswith("module:") and match.group("id") != "index":
            found[match.group("id")] = BLOCK.sub("", part)
    return found


def check(modules: list[dict], facts: dict[str, Facts], inventory: dict, out: Path) -> tuple[list[str], int]:
    problems, unwritten = [], 0
    files = {f.file for f in facts.values() if f.file}
    citation = re.compile(r"(?<![\w./-])(" + "|".join(re.escape(x) for x in sorted(files, key=len, reverse=True)) + r"):(\d+)") if files else None
    ranges: dict[str, int] = {}
    for f in facts.values():
        ranges[f.file] = max(ranges.get(f.file, 0), f.end)
    for module in modules:
        where = module.get("sourceRange") or {}
        ranges[where.get("file", "")] = max(ranges.get(where.get("file", ""), 0), where.get("endLine", 0))
    for name, (_, blocks) in documents(modules, facts, inventory).items():
        path = out / name
        if not path.exists():
            problems.append(f"{name}: 仕様書が無い（facts を回す）")
            continue
        text = path.read_text(encoding="utf-8")
        current = {m.group("id"): m.group(0) for m in BLOCK.finditer(text)}
        for block_id, body, _ in blocks:
            if block_id not in current:
                problems.append(f"{name}: `{block_id}` の節が無い（facts を回す）")
            elif current[block_id] != _block(block_id, body):
                problems.append(f"{name}: `{block_id}` の事実の欄が IR と違う（原文が変わったか、手で書き換えた。facts を回し、文章を見直す）")
        for block_id in sorted(set(current) - {b for b, _, _ in blocks}):
            problems.append(f"{name}: `{block_id}` は IR に無い（原文から消えた routine の節が残っている）")
        holes = text.count(UNWRITTEN)
        unwritten += holes
        if holes:
            problems.append(f"{name}: 未記入が {holes} か所")
        prose_all = BLOCK.sub("", text)
        for file, line in (citation.findall(prose_all) if citation else []):
            if not 1 <= int(line) <= ranges.get(file, 0):
                problems.append(f"{name}: 引用 `{file}:{line}` は原文の範囲（1〜{ranges.get(file, 0)} 行）の外")
        for routine_id, prose in _sections(text).items():
            f = facts.get(routine_id)
            if f is None or UNWRITTEN in prose:
                continue
            for code in f.error_codes:
                if str(code) not in prose:
                    problems.append(f"{name}: `{routine_id}` の文章にエラーコード {code} が出てこない")
            for table, ops in sorted(f.tables.items()):
                if ops - {"R"} and table.lower() not in prose.lower():
                    problems.append(f"{name}: `{routine_id}` の文章に、書き込む表 `{table}` が出てこない")
            cited = [int(line) for file, line in (citation.findall(prose) if citation else []) if file == f.file]
            if not cited:
                problems.append(f"{name}: `{routine_id}` の文章に原文の位置（`{f.file}:行`）が 1 つも無い")
            for line in cited:
                if not f.start <= line <= f.end:
                    problems.append(f"{name}: `{routine_id}` が引く `{f.file}:{line}` は、この routine（{f.start}〜{f.end} 行）の外")
    return problems, unwritten


def cmd_check(args) -> int:
    modules, facts, inventory = load(Path(args.analysis))
    problems, unwritten = check(modules, facts, inventory, Path(args.out_dir))
    for problem in problems:
        print(problem)
    print(f"ROUTINES={len(facts)} UNWRITTEN={unwritten} PROBLEMS={len(problems)}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (("facts", cmd_facts, "事実の欄を入れた仕様書の骨組みを作る（文章には触れない）"),
                             ("check", cmd_check, "書き上がった仕様書を IR と突き合わせる")):
        command = sub.add_parser(name, help=text)
        command.add_argument("--analysis", required=True, help="python -m plsql.cli の --out-dir（program.ir.json がある）")
        command.add_argument("--out-dir", required=True, help="仕様書（Markdown）のディレクトリ")
        command.set_defaults(func=func)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except InputError as e:
        print(f"入力が読めない: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
