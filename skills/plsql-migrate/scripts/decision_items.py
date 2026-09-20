#!/usr/bin/env python3
"""生成コードの外で決めること（docs/plsql-migration/plsql-decisions-outside-generator.md）を、生成物から拾って記録する。

    # 生成物から「出た」項目を拾い、記録と突き合わせて一覧を出す（記録は書き換えない）
    python skills/plsql-migrate/scripts/decision_items.py scan --generated out/plsql \\
        --limits fixtures/plsql/limits.yaml --scalardb-schema fixtures/plsql/scalardb-schema.json \\
        --record fixtures/plsql/decisions-outside-generator.yaml

    # 同じことをして、出た項目を「未決」として記録に足す（決定済みの項目には触れない）
    python ... scan ... --write

    # 人が答えた項目を記録する。決めた人と日付が無ければ受け付けない
    python ... set --record ... OPS-1 --status 決定 --decision "a. CronJob から回す。..." \\
        --by 運用担当 --date 2026-09-20 --where 運用設計書

項目の定義と「出たら確認」の対応表は文書が正である。このスクリプトは §0.1 の表を読み、行ごとに
生成物のどこを見れば「出た」と言えるかだけを持つ。表に行が増えて見分け方が無ければ、黙って
飛ばさずに失敗する——見分けられない項目は、確認されないまま残るからである。

終了コード: 0 = 問題なし / 1 = 記録が壊れている（決めた人の無い「決定」など）、または `--strict` で
出た項目に未決が残っている / 2 = 実行エラー。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# リポジトリの根からの相対。別のディレクトリから呼んでも同じ文書を読む
DEFAULT_DOC = str(Path(__file__).resolve().parents[3] / "docs" / "plsql-migration" / "plsql-decisions-outside-generator.md")


class RecordError(Exception):
    """記録が読めない形をしている。問題（終了コード 1）ではなく、入力の誤り（2）として扱う。"""


STATUSES = ("未決", "決定", "対象外")
ITEM_HEADER = re.compile(r"^####\s+((?:OPS|CALL|BIZ)-\d+)\s+(.+?)\s*$")
ITEM_ID = re.compile(r"(OPS|CALL|BIZ)-(\d+)")
RANGE = re.compile(r"(OPS|CALL|BIZ)-(\d+)\s*〜\s*(?:(OPS|CALL|BIZ)-)?(\d+)")

RECORD_HEADER = """\
# 生成コードの外で決めること — 記録（docs/plsql-migration/plsql-decisions-outside-generator.md §0.2）
#
# skills/plsql-migrate/scripts/decision_items.py が読み書きする。手で直してもよいが、
# 「決定」には 決定・決めた人・日付 が要る。答えた人のいない項目を「決定」と書かない。
# **コメントは保存されない**（書き込みのたびにファイル全体を作り直す）。残したいことは メモ に書く。
#
#   状態      未決 / 決定 / 対象外（生成物に出ていない）
#   出た      スクリプトが生成物から拾った根拠（毎回書き直す）
#   案        業務文書などから引いた答えの候補。決定ではない（出典を書く）
#   食い違い  業務文書と生成物（または元の PL/SQL）が食い違うところ。再設計の要否の問題として残す
#   残り      決定のうち、まだ決まっていない部分（例: 時刻は決めたが、間隔に収まるかの測定は未了）
#   メモ      自由記述（set --note）
#   決定時の題 / 決定時の根拠
#             決めたときの問いと、そのとき生成物から出ていた根拠。いまの文書・生成物と違えば、その決定は
#             別の問いに対するものかもしれない——scan が「要再確認」として挙げる
#   履歴      上書きされた、または取り消された以前の決定（決定・決めた人・日付）
"""


# --- the document ---------------------------------------------------------------------------------------------

@dataclass
class Row:
    """§0.1 の 1 行: 生成物・記録と、それが出たら確認する項目。"""
    artifact: str
    items: list[str]


@dataclass
class Doc:
    titles: dict[str, str]
    rows: list[Row]


def _expand(cell: str) -> list[str]:
    out: list[str] = []
    for match in RANGE.finditer(cell):
        prefix, start, end = match.group(1), int(match.group(2)), int(match.group(4))
        out.extend(f"{prefix}-{n}" for n in range(start, end + 1))
    rest = RANGE.sub("", cell)
    out.extend(f"{m.group(1)}-{m.group(2)}" for m in ITEM_ID.finditer(rest))
    return list(dict.fromkeys(out))


def read_doc(path: Path) -> Doc:
    text = path.read_text(encoding="utf-8")
    titles = {}
    for line in text.splitlines():
        match = ITEM_HEADER.match(line)
        if match:
            titles[match.group(1)] = match.group(2)
    section = text.split("### 0.1", 1)[1].split("### 0.2", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2 or set(cells[0]) <= {"-"} or cells[0] == "生成物・記録":
            continue
        rows.append(Row(cells[0], _expand(cells[1])))
    if not titles or not rows:
        raise SystemExit(f"{path}: 項目の見出しか §0.1 の表が読めない")
    return Doc(titles, rows)


# --- what the generated tree shows ----------------------------------------------------------------------------

@dataclass
class Tree:
    generated: Path
    limits: dict
    schema: dict
    java: dict[Path, str] = field(default_factory=dict)
    diagnostics: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, generated: Path, limits: Path | None, schema: Path | None) -> "Tree":
        tree = cls(generated,
                   yaml.safe_load(limits.read_text(encoding="utf-8")) or {} if limits else {},
                   json.loads(schema.read_text(encoding="utf-8")) if schema else {})
        for path in sorted(generated.rglob("*.java")):
            tree.java[path] = path.read_text(encoding="utf-8")
        report = generated / "generation-report.json"
        if report.exists():
            tree.diagnostics = json.loads(report.read_text(encoding="utf-8")).get("diagnostics", {})
        return tree

    def grep(self, pattern: str, flags: int = 0) -> list[str]:
        """`ファイル:行: 一致した部分` の一覧。"""
        regex = re.compile(pattern, flags)
        out = []
        for path, text in self.java.items():
            for number, line in enumerate(text.splitlines(), 1):
                match = regex.search(line)
                if match:
                    out.append(f"{path.name}:{number}: {match.group(0).strip()}")
        return out

    def with_code(self, *codes: str) -> list[str]:
        return [f"{routine}: {', '.join(c for c in found if c in codes)}"
                for routine, found in self.diagnostics.items() if any(c in codes for c in found)]


def _files(tree: Tree, *names: str) -> list[str]:
    return [p.name for p in tree.java if p.name in names]


def _db(tree: Tree, name: str) -> list[str]:
    path = tree.generated / "db" / name
    if not path.exists():
        return []
    tables = re.findall(r"ON\s+([\w.]+)\s+FROM", path.read_text(encoding="utf-8"), re.IGNORECASE)
    return [f"db/{name}" + (f"（{', '.join(dict.fromkeys(tables))}）" if tables else "")]


def _split(tree: Tree) -> list[str]:
    return sorted({re.search(r"(\w+)One\($", m).group(1) for m in tree.grep(r"public void \w+One\(")})


def _paged(tree: Tree) -> list[str]:
    return sorted({re.search(r"(\w+)\($", m).group(1) for m in tree.grep(r"public static \w+ \w+After\(")}
                  | {f"{m.split(':')[0]}: pAfterKey / pBatch" for m in tree.grep(r"\bpAfterKey\b.*\bpBatch\b")})


def _scan_rows(tree: Tree) -> list[str]:
    routines = (tree.limits.get("scanRows") or {}).get("routines") or {}
    return [f"scanRows.routines.{r}: {v}" for r, v in routines.items()]


def _dynamic(tree: Tree) -> list[str]:
    return [f"dynamicTables.{r}: {', '.join(v)}" for r, v in (tree.limits.get("dynamicTables") or {}).items()]


def _timestamptz(tree: Tree) -> list[str]:
    return [f"{table}.{column}" for table, spec in tree.schema.items()
            for column, kind in (spec.get("columns") or {}).items() if str(kind).upper() == "TIMESTAMPTZ"]


def _redesign(tree: Tree) -> list[str]:
    # 値を書き換える trigger は、書き込む文に TRIGGER_REDESIGN が付く。キーを渡して INSERT する corpus では
    # 付かないが、採番 trigger そのものはあり、照合に C 型として出る。どちらでも問いは同じである
    return tree.with_code("TRIGGER_REDESIGN") + tree.grep(r"C 型（\w+）: [\w.]+ の最大値")


# §0.1 の行（生成物・記録の欄に含まれる語）→ 見分け方。表の行と 1 対 1 に対応させる
DETECTORS = {
    "TriggerChecks.java": lambda t: _files(t, "TriggerChecks.java", "TriggerCheckJob.java"),
    "restrict-direct-writes.sql": lambda t: _db(t, "restrict-direct-writes.sql"),
    "trigger-check-baseline.sql": lambda t: _db(t, "trigger-check-baseline.sql"),
    "（割った routine）": _split,
    "<routine>After": _paged,
    "tx.runSeparately": lambda t: t.grep(r"tx\.runSeparately\(.*"),
    "OPTIMISTIC": lambda t: t.with_code("OPTIMISTIC", "RMW_SPLIT", "MERGE_SPLIT"),
    "AuditContext audit": lambda t: sorted({m.split(":")[0] for m in t.grep(r"AuditContext audit\b")}),
    "scanRows.routines": _scan_rows,
    "dynamicTables": _dynamic,
    "TRIGGER_APPLIED": lambda t: t.with_code("TRIGGER_APPLIED", "TRIGGER_CALL"),
    "TRIGGER_REDESIGN": _redesign,
    "TIMESTAMP WITH TIME ZONE": _timestamptz,
    "TRUNCATE": lambda t: t.grep(r'"TRUNCATE\s+TABLE\s+\w+"', re.IGNORECASE),
}


# 生成物ではなく、生成に渡したファイルを読む見分け方。そのファイルが渡されていなければ、結果は
# 「出ていない」ではなく「分からない」である
NEEDS = {"scanRows.routines": "limits", "dynamicTables": "limits", "TIMESTAMP WITH TIME ZONE": "schema"}


def detector_key(row: Row) -> str:
    found = [key for key in DETECTORS if key in row.artifact]
    if len(found) != 1:
        raise SystemExit(f"§0.1 の行「{row.artifact}」の見分け方が {len(found)} 個ある（1 個であるべき）。"
                         f"DETECTORS を直す")
    return found[0]


def undetectable(doc: Doc, given: set[str]) -> dict[str, str]:
    """項目 ID → 渡されなかった入力。その項目が出たかどうかは、今回の scan では言えない。"""
    out: dict[str, str] = {}
    for row in doc.rows:
        need = NEEDS.get(detector_key(row))
        if need and need not in given:
            for item in row.items:
                out[item] = {"limits": "--limits", "schema": "--scalardb-schema"}[need]
    return out


def detector_for(row: Row):
    return DETECTORS[detector_key(row)]


def detect(doc: Doc, tree: Tree) -> dict[str, list[str]]:
    """項目 ID → 出た根拠（§0.1 の行 + 生成物の場所）。出ていない項目は含まない。"""
    fired: dict[str, list[str]] = {}
    for row in doc.rows:
        evidence = detector_for(row)(tree)
        if not evidence:
            continue
        for item in row.items:
            fired.setdefault(item, []).append(f"{row.artifact} — " + "; ".join(evidence[:8])
                                              + (f"; ほか {len(evidence) - 8} 件" if len(evidence) > 8 else ""))
    return fired


# --- the record -----------------------------------------------------------------------------------------------

def read_record(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise RecordError(f"{path}: YAML として読めない: {str(e).splitlines()[0]}") from None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(items or {}, dict):
        raise RecordError(f"{path}: 最上位は `items:` の下に 項目 ID → 内容 を並べた形であるべき")
    for item, entry in (items or {}).items():
        if not ITEM_ID.fullmatch(str(item)):   # 文の中から ID を拾う形（先頭一致）なので、全体で当てる
            raise RecordError(f"{path}: 「{item}」は項目 ID（OPS-1 / CALL-2 / BIZ-3 の形）ではない")
        if not isinstance(entry, dict):
            raise RecordError(f"{path}: {item} の内容が 名前: 値 の形になっていない")
    return items or {}


def write_record(path: Path, items: dict[str, dict]) -> None:
    order = {"OPS": 0, "CALL": 1, "BIZ": 2}
    ordered = dict(sorted(items.items(), key=lambda kv: (order[kv[0].split("-")[0]], int(kv[0].split("-")[1]))))
    path.parent.mkdir(parents=True, exist_ok=True)
    text = RECORD_HEADER + "\n" + yaml.safe_dump({"items": ordered}, allow_unicode=True, sort_keys=False,
                                                  width=110)
    # 書きかけで落ちても、決定の記録が半分のファイルにならないようにする
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def problems(items: dict[str, dict], doc: Doc) -> list[str]:
    """記録の中で、§0.2 の約束を破っているもの。"""
    out = []
    for item, entry in items.items():
        if item not in doc.titles:
            out.append(f"{item}: 文書に無い ID")
            continue
        status = entry.get("状態")
        if status not in STATUSES:
            out.append(f"{item}: 状態「{status}」は {' / '.join(STATUSES)} のどれでもない")
        if status == "決定":
            missing = [k for k in ("決定", "決めた人", "日付") if not str(entry.get(k) or "").strip()]
            if missing:
                out.append(f"{item}: 「決定」だが {' / '.join(missing)} が無い——答えた人のいない項目は決定と書かない")
            elif not _is_date(entry.get("日付")):
                out.append(f"{item}: 日付「{entry.get('日付')}」が YYYY-MM-DD の日付ではない")
            # 決めたあとで問いか根拠が変わったなら、その決定は別のものに対する答えかもしれない
            if "決定時の題" in entry and entry["決定時の題"] != doc.titles[item]:
                out.append(f"{item}: 要再確認——決めたときの問いは「{entry['決定時の題']}」だったが、"
                           f"いまの文書では「{doc.titles[item]}」")
            if "決定時の根拠" in entry and "出た" in entry and entry["決定時の根拠"] != entry["出た"]:
                out.append(f"{item}: 要再確認——決めたあとで生成物から出る根拠が変わった"
                           f"（決定時: {entry['決定時の根拠']} / いま: {entry['出た']}）。"
                           f"決定がまだ当てはまるなら set で記録し直す")
        else:
            stray = [k for k in ("決定", "決めた人", "日付") if k in entry]
            if stray:
                out.append(f"{item}: 状態は「{status}」なのに {' / '.join(stray)} が残っている（決定に見えてしまう）")
    return out


def _is_date(value) -> bool:
    if isinstance(value, datetime.date):
        return True
    try:
        datetime.date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def merge(items: dict[str, dict], fired: dict[str, list[str]], doc: Doc,
          unknown: dict[str, str] | None = None) -> dict[str, dict]:
    """出た項目を未決として足し、根拠を書き直す。決定は変えない。出なくなった未決は対象外にする。

    `unknown` の項目は、見分けるための入力（--limits / --scalardb-schema）が渡されなかったもの。出たとも
    出ていないとも言えないので、**記録に触らない**——以前は「出ていない」と読んで、未決を黙って対象外にしていた。
    """
    out = {k: dict(v) for k, v in items.items()}
    for item in doc.titles:
        entry = out.get(item)
        if item in (unknown or {}) and item not in fired:
            continue
        if item in fired:
            if entry is None:
                entry = out[item] = {"状態": "未決"}
            elif entry.get("状態") == "対象外":
                entry["状態"] = "未決"
            entry["出た"] = fired[item]
        elif entry is not None:
            entry.pop("出た", None)
            if entry.get("状態") == "未決":
                entry["状態"] = "対象外"
    return out


# --- output ---------------------------------------------------------------------------------------------------

def render(doc: Doc, items: dict[str, dict], fired: dict[str, list[str]]) -> str:
    lines = ["# 生成コードの外で決めること — 確認一覧", ""]
    groups = (("OPS", "運用で決めること"), ("CALL", "呼び出し側で決めること"), ("BIZ", "業務ロジックとの整合"))
    counts = {s: 0 for s in STATUSES}
    for item in doc.titles:
        status = (items.get(item) or {}).get("状態") or ("未決" if item in fired else "対象外")
        counts[status] += 1
    lines.append(f"出た項目 {len(fired)} / {len(doc.titles)}　"
                 + "　".join(f"{s} {n}" for s, n in counts.items()))
    for prefix, heading in groups:
        lines += ["", f"## {prefix} — {heading}", "", "| ID | 項目 | 状態 | 決定・案 | 出た根拠 |", "|---|---|---|---|---|"]
        for item, title in doc.titles.items():
            if not item.startswith(prefix + "-"):
                continue
            entry = items.get(item) or {}
            status = entry.get("状態") or ("未決" if item in fired else "対象外")
            if status == "対象外" and item not in fired:
                continue
            decided = entry.get("決定") or ""
            if decided:
                decided = f"{decided}（{entry.get('決めた人', '?')}、{entry.get('日付', '?')}）"
            elif entry.get("案"):
                decided = f"案: {entry['案']}"
            if entry.get("残り"):
                decided += f"<br>残り: {entry['残り']}"
            if entry.get("食い違い"):
                decided += f"<br>**食い違い**: {entry['食い違い']}"
            evidence = "<br>".join(fired.get(item, [])) or "（今回は出ていない）"
            lines.append(f"| {item} | {title} | {status} | {_cell(decided)} | {_cell(evidence)} |")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


# --- commands -------------------------------------------------------------------------------------------------

def cmd_scan(args) -> int:
    doc = read_doc(Path(args.doc))
    tree = Tree.load(Path(args.generated), Path(args.limits) if args.limits else None,
                     Path(args.scalardb_schema) if args.scalardb_schema else None)
    if not tree.java:
        print(f"{args.generated}: 生成物（*.java）が無い。先に python -m plsql.generate を回す", file=sys.stderr)
        return 2
    if not tree.diagnostics:
        print("注意: generation-report.json に diagnostics が無い。OPTIMISTIC / TRIGGER_CALL などの"
              "診断で出る項目は拾えない（生成器が古い）", file=sys.stderr)
    fired = detect(doc, tree)
    unknown = undetectable(doc, {name for name, given in (("limits", args.limits),
                                                         ("schema", args.scalardb_schema)) if given})
    record = Path(args.record) if args.record else None
    items = read_record(record) if record else {}
    merged = merge(items, fired, doc, unknown)
    for item, option in sorted(unknown.items()):
        if item not in fired:
            print(f"注意: {item} は {option} が無いので見分けられない。記録はそのままにした", file=sys.stderr)
    for item in sorted(set(items) | set(merged)):
        before, after = (items.get(item) or {}).get("状態"), (merged.get(item) or {}).get("状態")
        if before != after:
            print(f"状態が変わった: {item} {before or '（新規）'} → {after}", file=sys.stderr)
    text = render(doc, merged, fired)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.write:
        if record is None:
            print("--write には --record が要る", file=sys.stderr)
            return 2
        write_record(record, merged)
    bad = problems(merged, doc)
    for line in bad:
        print(f"記録の問題: {line}", file=sys.stderr)
    open_items = [i for i in fired if (merged.get(i) or {}).get("状態", "未決") == "未決"]
    print(f"FIRED={len(fired)} UNDECIDED={len(open_items)} PROBLEMS={len(bad)}", file=sys.stderr)
    return 1 if bad or (args.strict and open_items) else 0


def cmd_set(args) -> int:
    doc = read_doc(Path(args.doc))
    if args.item not in doc.titles:
        print(f"{args.item}: 文書に無い ID", file=sys.stderr)
        return 2
    record = Path(args.record)
    items = read_record(record)
    entry = dict(items.get(args.item) or {})
    previous = {k: entry[k] for k in ("決定", "決めた人", "日付") if k in entry}
    changed = args.status != "決定" or (args.decision is not None and args.decision != entry.get("決定"))
    if previous and changed:
        if args.status == "決定" and (args.by is None or args.date is None):
            # 黙って引き継ぐと、新しい決定が「前に決めた人が、前の日付に決めた」ことになる
            print(f"{args.item}: 決定を書き換えるなら --by と --date も要る（前の決定は"
                  f" {previous.get('決めた人')} / {previous.get('日付')} のもの）", file=sys.stderr)
            return 1
        entry.setdefault("履歴", []).append(previous)
        for key in previous:
            entry.pop(key)
        entry.pop("決定時の題", None)
        entry.pop("決定時の根拠", None)
    entry["状態"] = args.status
    for key, value in (("決定", args.decision), ("決めた人", args.by), ("日付", args.date),
                       ("記録先", args.where), ("案", args.proposal), ("食い違い", args.conflict),
                       ("残り", args.remaining), ("メモ", args.note)):
        if value is not None:
            entry[key] = value
    if args.status == "決定":
        # 何に対して決めたのかを残す。あとで問いか根拠が変われば、scan が「要再確認」として挙げる
        entry["決定時の題"] = doc.titles[args.item]
        if "出た" in entry:
            entry["決定時の根拠"] = list(entry["出た"])
    candidate = {**items, args.item: entry}
    bad = problems({args.item: entry}, doc)
    if bad:
        for line in bad:
            print(line, file=sys.stderr)
        return 1
    write_record(record, candidate)
    print(f"{args.item}: {args.status} を記録した（{record}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--doc", default=DEFAULT_DOC, help="項目の定義（既定: %(default)s）")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="生成物から出た項目を拾い、記録と突き合わせる")
    scan.add_argument("--generated", required=True, help="python -m plsql.generate の --out-dir")
    scan.add_argument("--limits", help="生成に渡した limits.yaml")
    scan.add_argument("--scalardb-schema", help="生成に渡した Schema Loader JSON")
    scan.add_argument("--record", help="記録の YAML（無ければ作る）")
    scan.add_argument("--write", action="store_true", help="出た項目を未決として記録に足す")
    scan.add_argument("--strict", action="store_true", help="出た項目に未決が残っていたら 1 で終わる")
    scan.add_argument("--out", help="一覧（Markdown）の書き出し先。無ければ標準出力")
    scan.set_defaults(func=cmd_scan)

    setter = sub.add_parser("set", help="1 項目の答えを記録する")
    setter.add_argument("item", help="項目 ID（例: OPS-1）")
    setter.add_argument("--record", required=True)
    setter.add_argument("--status", required=True, choices=STATUSES)
    setter.add_argument("--decision", help="選んだ選択肢と理由 1〜2 行")
    setter.add_argument("--by", help="決めた人（役割）")
    setter.add_argument("--date", help="決めた日（YYYY-MM-DD）")
    setter.add_argument("--where", help="記録先（設計書・設定ファイル・issue）")
    setter.add_argument("--proposal", help="答えの候補と出典。決定ではない")
    setter.add_argument("--conflict", help="業務文書との食い違い（出典つき）。再設計の要否として残す")
    setter.add_argument("--remaining", help="決定のうち、まだ決まっていない部分")
    setter.add_argument("--note", help="メモ（自由記述）。ファイルのコメントは保存されないので、ここに書く")
    setter.set_defaults(func=cmd_set)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RecordError as e:
        print(f"記録が読めない: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"ファイルが無い: {e.filename}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
