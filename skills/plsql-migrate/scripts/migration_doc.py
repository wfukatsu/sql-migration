#!/usr/bin/env python3
"""変換後のコードの文書（アーキテクチャ・仕様・使い方・制限・どう移行したか）の骨組みを生成物から作り、
書き上がった文書を生成物と突き合わせる。

    # 生成物・解析・決定・証拠から、事実の欄を入れた文書の骨組みを作る。
    # すでに文書があれば、事実の欄だけを書き直す（人が書いた文章には触れない）
    python skills/plsql-migrate/scripts/migration_doc.py facts --generated out/plsql \\
        --analysis out/plsql/analysis --limits fixtures/plsql/limits.yaml \\
        --evidence difftest/work/plsql-diff.json --record fixtures/plsql/decisions-outside-generator.yaml \\
        --out-dir out/plsql/docs

    # 書き上がった文書を確かめる（未記入、古い事実、文章に出てこない判定の理由・受け入れた差・決定、
    # 生成物に無い Java ファイルの名前）
    python ... check （同じ引数）

事実の欄（`<!-- facts:begin ID -->` から `<!-- facts:end ID -->` まで）は、生成物（Java と
generation-report.json）、解析（`plsql.cli --out-dir` の program.ir.json と decisions.json）、決定
（limits.yaml と記録）、実 DB の比較（plsql-diff.json）から機械的に出す。文章は、生成された Java と原文を
読んで書く。`--analysis` は、生成に渡したのと同じ `--limits` と `--scalardb-schema` で回したものを渡す。

終了コード: 0 = 問題なし / 1 = 文書に問題がある（`check`） / 2 = 実行エラー。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

UNWRITTEN = "（未記入"
LAYERS = (
    ("application", "routine の本体（Service）と、分割した routine の部品、trigger の照合。トランザクションは開始も commit もしない"),
    ("infrastructure", "SQL（Repository）。渡された `Connection` の上で実行する"),
    ("domain", "行と戻り値の record、エラーコードごとの例外"),
)
INDEX_PROSE = (
    ("architecture", "アーキテクチャ", "層の役割と依存の向き、トランザクションの境界がどこにあるか、SQL がどこでどう実行されるか、"
                                  "実行時ライブラリ（`com.scalar.migrate.*`）が何を受け持つかを書く。事実の欄の表を言い直さない"),
    ("usage", "使い方", "呼び出し側が用意するもの（接続、採番、`AuditContext`）と、1 回の呼び出しの書き方（開始 → 呼ぶ → commit / "
                     "rollback → 衝突の再試行）をコード例つきで書く。コード例は生成された constructor と method の形に合わせる"),
    ("limits", "制限", "できないこと・Oracle と違うこと・確かめていないことを、読んだ人が踏まないように書く。判定が AUTO でない "
                     "routine、受け入れた差、比較が観ていない振る舞い、未決の項目を落とさない"),
    ("how", "どのように移行したか", "解析 → 判定 → 決定 → 生成 → 実 DB での比較の流れを、この案件で実際に起きたことで書く。"
                               "どの決定を誰がいつしたか、何を確かめて何を確かめていないか"),
)
ROUTINE_PROSE = (
    ("仕様", "Java から見た動作を書く。引数と戻り値の意味、どの例外がいつ出るか、どの表に何を書くか。"
             "元の PL/SQL の仕様書（plsql-spec）があれば、そこへの参照と、違うところだけを書く"),
    ("移行で変わったこと", "事実の欄の「文の対応」と診断を読み、PL/SQL と振る舞いが変わる点を業務の言葉で書く"
                         "（行ロック → 楽観制御、SET 式 → 読んでから書く、SYSDATE をアプリで求める、など）。変わらないなら「なし」"),
    ("制限と注意", "判定の理由（ルール ID）ごとに、呼び出し側が気をつけること。受け入れた差と、比較が観ていない振る舞い。無ければ「なし」"),
)
METHOD = re.compile(r"^\s*(?P<visibility>public|private|protected)\s+(?:static\s+)?(?P<ret>[\w<>\[\], .?]+?)\s+(?P<name>\w+)\((?P<args>[^)]*)\)", re.M)
SOURCE_AT = re.compile(r"^\s*//\s*(?P<file>[\w./-]+):(?P<line>\d+)\s*$")
FROM_SOURCE = re.compile(r"#(?:stmt|handler)-\d+(?:#query)?$")


class InputError(Exception):
    """入力が読めない。文書の問題（1）ではなく、入力の誤り（2）として扱う。"""


# --- 読む -----------------------------------------------------------------------------------------------------


@dataclass
class Routine:
    id: str
    module: str
    file: str
    start: int
    end: int
    parameters: list[dict]
    statements: list[dict] = field(default_factory=list)
    verdict: dict = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    decisions: list[tuple[str, str]] = field(default_factory=list)
    scenarios: list[dict] = field(default_factory=list)
    service: str | None = None
    method: dict | None = None
    repository_methods: dict[int, list[str]] = field(default_factory=dict)
    parts: list[str] = field(default_factory=list)
    from_source: bool = False

    @property
    def open_rules(self) -> list[str]:
        return sorted({r["id"] for r in self.rules if r.get("decision") in ("REVIEW", "REDESIGN")})


@dataclass
class Project:
    generated: Path
    report: dict
    java: dict[str, str]
    modules: list[dict]
    routines: dict[str, Routine]
    limits: dict
    evidence: dict
    record: dict
    other_files: list[str]


def _sql(target) -> str:
    """移行先の SQL。1 文が複数の文に割れると（読んでから書く、など）並びで入っている。"""
    parts = target if isinstance(target, list) else [target or ""]
    return " ; ".join(" ".join(str(part).split()) for part in parts if part)


def _walk_statements(node, found: list[dict]) -> None:
    if isinstance(node, list):
        for child in node:
            _walk_statements(child, found)
    elif isinstance(node, dict):
        if node.get("kind") == "SqlOperation" and FROM_SOURCE.search(node.get("id", "")):
            found.append({"line": (node.get("sourceRange") or {}).get("startLine"), "kind": node.get("sqlKind"),
                          "end": (node.get("sourceRange") or {}).get("endLine"),
                          "original": " ".join((node.get("originalSql") or "").split()),
                          "target": _sql(node.get("targetSql")), "status": node.get("targetStatus"),
                          "diagnostics": [(d["severity"], d["code"], d["message"]) for d in node.get("diagnostics") or []]})
        elif node.get("diagnostics") and FROM_SOURCE.search(node.get("id", "")):
            found.append({"line": (node.get("sourceRange") or {}).get("startLine"), "kind": node.get("kind"), "original": "",
                          "target": "", "status": None,
                          "diagnostics": [(d["severity"], d["code"], d["message"]) for d in node["diagnostics"]]})
        for value in node.values():
            _walk_statements(value, found)


def _decisions(limits: dict, path: str = "") -> list[tuple[str, str, str]]:
    """limits.yaml を (決定の種類, 対象, 値・理由) の並びにする。対象は routine の id（`dbLinks` は link の名前）。"""
    out = []
    for key, value in (limits or {}).items():
        if isinstance(value, dict) and path != "dbLinks":
            out += _decisions(value, f"{path}.{key}" if path else str(key))
        elif isinstance(value, dict):
            out.append((path, str(key), " / ".join(f"{k}: {' '.join(str(v).split())}" for k, v in value.items())))
        else:
            text = "、".join(map(str, value)) if isinstance(value, list) else " ".join(str(value).split())
            out.append((path or str(key), str(key), text))
    return out


def _method_name(routine_id: str) -> str:
    name = routine_id.split(".")[-1].replace("~", "")
    parts = name.split("_")
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def load(args) -> Project:
    generated = Path(args.generated)
    report_path = generated / "generation-report.json"
    if not report_path.exists():
        raise InputError(f"{report_path} が無い。python -m plsql.generate … --out-dir {generated} を先に回す")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    ir_path = Path(args.analysis) / "program.ir.json"
    if not ir_path.exists():
        raise InputError(f"{ir_path} が無い。python -m plsql.cli <src> --limits … --out-dir {args.analysis} を先に回す")
    program = json.loads(ir_path.read_text(encoding="utf-8"))
    decisions_path = Path(args.analysis) / "decisions.json"
    decided = {r["routine"]: r for r in json.loads(decisions_path.read_text(encoding="utf-8")).get("routines", [])} \
        if decisions_path.exists() else {}
    limits = yaml.safe_load(Path(args.limits).read_text(encoding="utf-8")) or {} if args.limits else {}
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8")) if args.evidence else {}
    record = (yaml.safe_load(Path(args.record).read_text(encoding="utf-8")) or {}).get("items", {}) if args.record else {}
    java = {str(p.relative_to(generated)): p.read_text(encoding="utf-8") for p in sorted(generated.rglob("*.java"))}
    other = sorted(str(p.relative_to(generated)) for p in generated.rglob("*")
                   if p.is_file() and p.suffix != ".java" and p.relative_to(generated).parts[0] not in ("docs", "analysis")
                   and p.name != "generation-report.json")

    sources = {f.name: f.read_text(encoding="utf-8").splitlines() for f in Path(args.src).rglob("*") if f.is_file()} \
        if getattr(args, "src", None) else {}
    routines: dict[str, Routine] = {}
    for module in program.get("modules") or []:
        for r in module.get("routines") or []:
            where = r.get("sourceRange") or {}
            routine = Routine(id=r["id"], module=module["name"], file=where.get("file", ""), start=where.get("startLine", 0),
                              end=where.get("endLine", 0), parameters=r.get("parameters") or [])
            _walk_statements([r.get("body"), r.get("exceptionHandlers")], routine.statements)
            if sources.get(Path(routine.file).name):
                # 解析は文を書き換えてから IR に載せる（`stock_qty - x` が `v_rmw_1 - x` になる）。変換前として見せるのは原文である
                lines = sources[Path(routine.file).name]
                routine.from_source = True
                for s in routine.statements:
                    if s.get("original") and s.get("line") and s.get("end"):
                        s["original"] = " ".join(" ".join(lines[s["line"] - 1:s["end"]]).split()).rstrip(";")
            routine.verdict = (report.get("verdicts") or {}).get(r["id"], {})
            routine.rules = (decided.get(r["id"]) or {}).get("rules") or []
            routine.remediation = (decided.get(r["id"]) or {}).get("remediation") or []
            routine.decisions = [(kind, value) for kind, target, value in _decisions(limits) if target == r["id"]]
            routines[r["id"]] = routine
    for variant in evidence.values():
        for name, scenario in (variant.get("scenarios") or {}).items():
            target = scenario.get("routine", "")
            routine = routines.get(target) or routines.get(target.split(".", 1)[-1]) or next(
                (x for x in routines.values() if f"{x.module}.{x.id.split('.')[-1]}" == target), None)
            if routine is not None:
                routine.scenarios.append({"variant": variant.get("variant"), "name": name, **scenario})
    for path, text in java.items():
        if "/application/" not in path and "/infrastructure/" not in path:
            continue
        lines, at = text.splitlines(), None
        for index, line in enumerate(lines):
            seen = SOURCE_AT.match(line)
            if seen:
                at = (seen.group("file"), int(seen.group("line")))
                continue
            found = METHOD.match(line)
            if found and at:
                for routine in routines.values():
                    if Path(routine.file).name == Path(at[0]).name and routine.start <= at[1] <= routine.end:
                        if "/infrastructure/" in path:
                            routine.repository_methods.setdefault(at[1], []).append(f"{Path(path).stem}.{found.group('name')}")
                        elif at[1] == routine.start and found.group("name").lower().startswith(_method_name(routine.id).lower()) \
                                and routine.method is None:
                            routine.service, routine.method = path, found.groupdict()
            if not line.strip().startswith("//"):
                at = None
    for routine in routines.values():
        if routine.method is None:
            # 1 反復 = 1 トランザクションに割った routine は、入口が 1 つではなく部品（Targets / One / Failed …）になる
            stem = _method_name(routine.id)
            routine.parts = [f"{Path(path).stem}.{m.group('name')}({_short(m.group('args'), 80)})"
                             for path, text in java.items() if "/application/" in path
                             for m in METHOD.finditer(text) if m.group("visibility") == "public"
                             and m.group("name").startswith(stem) and m.group("name")[len(stem):][:1].isupper()]
    return Project(generated, report, java, program.get("modules") or [], routines, limits, evidence, record, other)


# --- Markdown -------------------------------------------------------------------------------------------------


# --- 何がどう変わったか ---------------------------------------------------------------------------------------
# 診断コード → (区分, 変わること)。区分は 3 つ: 「意味が変わる」= 呼び出し側か業務が知っていなければならない、
# 「形が変わる」= 書き方は変わったが結果は同じ、「情報」= 変わらない。表に無いコードは「未分類」と出す——
# 黙って「変わらない」に入れない。
MEANING, SHAPE, INFO, UNKNOWN = "意味が変わる", "形が変わる（結果は同じ）", "情報", "未分類"
CHANGES = {
    "ROW_LOCK": (MEANING, "行ロック（待たせる・即座に断る）が無くなる"),
    "LOCK": (MEANING, "FOR UPDATE などのロック句を外した"),
    "OPTIMISTIC": (MEANING, "楽観制御へ移した。同時の書き込みは commit で弾かれ、呼び出し側の再試行が要る"),
    "TRANSACTION_IN_ROUTINE": (MEANING, "routine の中の COMMIT / ROLLBACK / SAVEPOINT が消え、境界が呼び出し側へ移った"),
    "PAGED": (MEANING, "対象をページごとに読む。途中で増えた行も処理されうる"),
    "CUR_SCAN": (MEANING, "カーソルを、先に行を読んでから回す形にした。行数の上限が付く"),
    "ROW_LIMIT_DECIDED": (MEANING, "読む行数に上限が付いた。超えると例外で止まる"),
    "TRIGGER_APPLIED": (MEANING, "trigger をこの書き込みのところで呼ぶ。掛かるのは生成コードが書く経路だけ"),
    "TRIGGER_CALL": (MEANING, "trigger を routine として呼ぶ。PL/SQL の外からの書き込みには掛からない"),
    "TRIGGER_INLINED": (MEANING, "採番 trigger を INSERT の値として織り込んだ。キーを渡さない INSERT は他の経路では動かない"),
    "FORALL_SAVE_EXCEPTIONS": (MEANING, "SAVE EXCEPTIONS（失敗した要素を飛ばして続ける）を 1 要素 = 1 トランザクションにした"),
    "DYNAMIC_SQL": (MEANING, "動的 SQL は、列挙できた文だけが動く"),
    "DYN_PRIVILEGE": (MEANING, "EXECUTE IMMEDIATE の実行者の権限という区別が無くなる"),
    "DYN_BIND": (MEANING, "USING の値を位置で対応させた。対応が合っているか確かめる"),
    "DBLINK_MAPPED": (MEANING, "DB link の先の表を、別の namespace として同じトランザクションで書く"),
    "MULTI_ROW_INTO": (MEANING, "キーで 1 行に絞れない SELECT INTO。2 行まで読んで Oracle と同じ例外にする"),
    "ROWNUM": (MEANING, "ROWNUM を LIMIT にした。ORDER BY と組み合わせると Oracle と順序の意味が違う"),
    "CROSS_PARTITION": (MEANING, "パーティションをまたぐ走査になる（RDBMS が下にあるときだけ使える。件数に比例して遅い）"),
    "NOW": (MEANING, "現在時刻を DB ではなくアプリで求めて渡す"),
    "JOIN_SCOPE": (MEANING, "ScalarDB SQL が受け付けない結合。実行計画（取得して H2 で実行）に回る"),
    "RMW_SPLIT": (SHAPE, "SET col = col ± x を、読んでからアプリで計算して書く 2 文に割った"),
    "MERGE_SPLIT": (SHAPE, "MERGE を、読んでから UPDATE か INSERT を選ぶ形に割った"),
    "BULK_CHUNKED": (SHAPE, "BULK COLLECT + FORALL を 1 つの走査ループにした"),
    "CUR_COUNT": (SHAPE, "件数を数えるだけのカーソルを COUNT(*) にした"),
    "CURRENT_OF": (SHAPE, "WHERE CURRENT OF を主キーの指定にした"),
    "ORDER_DROPPED": (SHAPE, "結果に効かない ORDER BY を落とした"),
    "JOIN_ORDER": (SHAPE, "FROM と JOIN の表を入れ替えた"),
    "LIMIT": (SHAPE, "FETCH FIRST n ROWS を LIMIT にした"),
    "DYN_FOLDED": (SHAPE, "動的 SQL を静的な文に畳んだ"),
    "TRIGGER_OLD": (SHAPE, "trigger が読む :OLD の値を、更新の前に読む"),
    "PLAN_FETCH": (SHAPE, "実行計画: ScalarDB から行を取得する"),
    "PLAN_RESIDUAL": (SHAPE, "実行計画: 取得した行に、H2 で元の SQL を実行する"),
    "ACCESS": (INFO, "アクセスパス（GET / パーティション走査）"),
    "CONFIG": (INFO, "読み取り専用のトランザクションにできる"),
    "COST": (INFO, "取得コストの見積もり"),
}
ORDER = {MEANING: 0, UNKNOWN: 1, SHAPE: 2, INFO: 3}


def _changes(r: Routine) -> list[dict]:
    rows = []
    for s in r.statements:
        for _, code, message in s["diagnostics"]:
            kind, what = CHANGES.get(code, (UNKNOWN, _short(message, 100)))
            if kind != INFO:
                rows.append({"line": s["line"], "code": code, "kind": kind, "what": what})
    return sorted(rows, key=lambda x: (ORDER[x["kind"]], x["line"] or 0))


def _label(text, width: int = 56) -> str:
    text = _short(str(text or ""), width).replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")
    return text.replace("`", "'").replace("|", "#124;")


def change_diagram(r: Routine) -> list[str]:
    """変換前の文（左）と変換後の method（右）を線で結ぶ。赤 = 意味が変わる、黄 = 形が変わる、灰 = そのまま。"""
    statements = [s for s in r.statements if s["original"] or r.repository_methods.get(s["line"])]
    if not statements:
        return []
    worst = {}
    for change in _changes(r):
        worst[change["line"]] = min(worst.get(change["line"], 9), ORDER[change["kind"]])
    lines = ["flowchart LR", '  subgraph before["変換前: PL/SQL"]', "    direction TB"]
    for index, s in enumerate(statements):
        lines.append(f'    b{index}["L{s["line"]}: {_label(s["original"] or s["kind"], 46)}"]')
    lines += ["  end", '  subgraph after["変換後: Java（Repository）"]', "    direction TB"]
    targets = []
    for index, s in enumerate(statements):
        methods = r.repository_methods.get(s["line"]) or []
        for number, method in enumerate(methods):
            lines.append(f'    a{index}_{number}["{_label(method.split(".")[-1])}"]')
            targets.append((index, f"a{index}_{number}"))
        if not methods:
            lines.append(f'    a{index}_0["（Service の中で処理する）"]')
            targets.append((index, f"a{index}_0"))
    lines.append("  end")
    lines += [f"  b{index} --> {target}" for index, target in targets]
    groups = {0: [], 1: [], 2: []}
    for index, s in enumerate(statements):
        level = worst.get(s["line"])
        if level in groups:
            groups[level] += [f"b{index}"] + [target for i, target in targets if i == index]
    for level, name, style in ((0, "meaning", "fill:#fde2e2,stroke:#c53030"), (1, "unknown", "fill:#e2e8f0,stroke:#4a5568"),
                               (2, "shape", "fill:#fdf6d5,stroke:#b7791f")):
        if groups[level]:
            lines += [f"  classDef {name} {style};", f"  class {','.join(groups[level])} {name};"]
    return ["```mermaid", *lines, "```"]


def architecture_diagram(p: Project) -> list[str]:
    count = {layer: len([f for f in p.java if f"/{layer}/" in f]) for layer, _ in LAYERS}
    plans = [f for f in p.other_files if "/plans/" in f]
    uses_sequences = any("Sequences" in text for f, text in p.java.items() if "/infrastructure/" in f)
    lines = ["flowchart TD", '  caller["呼び出し側（use case）<br/>トランザクションの開始・commit・再試行"]',
             '  subgraph generated["生成されたコード"]',
             f'    service["application: Service × {count["application"]}<br/>PL/SQL の本体（文の順のまま）"]',
             f'    repository["infrastructure: Repository × {count["infrastructure"]}<br/>SQL 1 文 = 1 method"]',
             f'    domain["domain × {count["domain"]}<br/>record と、エラーコードごとの例外"]', "  end",
             '  runtime["runtime-java: Plsql / AuditContext<br/>Oracle と同じ比較・算術・丸め・NULL"]',
             '  db[("ScalarDB SQL（JDBC）")]',
             '  caller -->|"Connection・AuditContext を渡して呼ぶ"| service', "  service --> repository", "  service -.-> domain",
             "  service -.-> runtime", '  repository -->|"渡された Connection で実行"| db', '  caller -->|"commit / rollback"| db']
    if uses_sequences:
        lines += ['  sequences["Sequences（採番）<br/>業務とは別のトランザクション"]', "  repository --> sequences", "  sequences --> db"]
    if plans:
        lines += [f'  plan["実行計画 × {len(plans)}<br/>取得して H2 で元の SQL"]', "  repository --> plan", "  plan --> db"]
    return ["```mermaid", *lines, "```"]


USAGE_DIAGRAM = ["```mermaid", "sequenceDiagram", "  participant C as 呼び出し側", "  participant S as Service", "  participant R as Repository",
                 "  participant D as ScalarDB", "  C->>D: 接続（setAutoCommit(false)）", "  C->>S: routine の method（引数, AuditContext）",
                 "  S->>R: SQL 1 文ごとの method", "  R->>D: PreparedStatement", "  D-->>R: 行 / 件数", "  R-->>S: 値",
                 "  alt 業務の失敗（MigratedException, code = Oracle のコード）", "    S-->>C: 例外", "    C->>D: rollback（再試行しない）",
                 "  else 正常", "    S-->>C: 戻り値の record", "    C->>D: commit",
                 "    alt 他のトランザクションと衝突", "      D-->>C: SQLTransactionRollbackException", "      C->>D: rollback",
                 "      C->>S: 最初から呼び直す（回数の上限つき）", "    end", "  end", "```"]


def _cell(text) -> str:
    return str(text if text not in (None, "", []) else "—").replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]


def _short(text: str, width: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _scenario_state(s: dict) -> str:
    if s.get("differences"):
        return f"**差がある**（{len(s['differences'])}）"
    if s.get("accepted"):
        return "受け入れた差"
    return "一致" + ("（桁の表記だけが違う）" if s.get("scale_only") else "")


def architecture_facts(p: Project) -> str:
    out = ["**事実**（生成物から機械的に出した。手で書き換えない）", "", "全体の形", ""] + architecture_diagram(p) + ["", "層", ""]
    rows = []
    for layer, role in LAYERS:
        files = [f for f in p.java if f"/{layer}/" in f]
        rows.append([f"`{layer}`", len(files), role])
    out += _table(["層", "ファイル数", "役割"], rows)
    if p.other_files:
        out += ["", "Java 以外の生成物", ""] + [f"- `{f}`" for f in p.other_files]
    classes = []
    for module in p.modules:
        routines = [r for r in p.routines.values() if r.module == module["name"]]
        services = sorted({Path(r.service).stem for r in routines if r.service})
        repositories = sorted({m.split(".")[0] for r in routines for ms in r.repository_methods.values() for m in ms})
        classes.append([f"`{module['name']}`（{module.get('moduleKind')}）", "、".join(f"`{s}`" for s in services),
                        "、".join(f"`{s}`" for s in repositories), f"[{module['name']}.md]({module['name']}.md)"])
    out += ["", "module と class", ""] + _table(["元の module", "Service", "Repository", "文書"], classes)
    constructors = []
    for path, text in p.java.items():
        if "/domain/" in path:
            continue
        name = Path(path).stem
        for found in re.finditer(rf"^\s*public\s+{re.escape(name)}\((?P<args>[^)]*)\)", text, re.M):
            constructors.append([f"`{name}`", f"`{found.group('args').strip() or '（引数なし）'}`"])
    if constructors:
        out += ["", "constructor（呼び出し側が渡すもの）", ""] + _table(["class", "引数"], constructors)
    runtime = sorted({m for text in p.java.values() for m in re.findall(r"^import (com\.scalar\.migrate\.[\w.]+);", text, re.M)})
    if runtime:
        out += ["", "使っている実行時ライブラリ（`runtime-java`）", ""] + [f"- `{r}`" for r in runtime]
    return "\n".join(out)


def usage_facts(p: Project) -> str:
    out = ["**事実**（生成物から機械的に出した。手で書き換えない）", "", "入口（public な routine の method）", ""]
    rows = []
    for r in p.routines.values():
        if r.method and r.method["visibility"] == "public":
            rows.append([f"`{r.id}`", f"`{Path(r.service).stem}.{r.method['name']}`", f"`{r.method['ret'].strip()}`",
                         f"`{_short(r.method['args'], 140)}`"])
        elif r.parts:
            rows.append([f"`{r.id}`", "<br>".join(f"`{x}`" for x in r.parts), "（部品）", None])
    out += _table(["元の routine", "Java", "戻り値", "引数"], rows)
    sequences = sorted({s for text in p.java.values() for s in re.findall(r"(?:nextSequenceValue|sequences\.next)\(\"([\w.]+)\"\)", text)})
    out += ["", "- 採番（`Sequences` に登録が要る sequence）: " + ("、".join(f"`{s}`" for s in sequences) or "なし")]
    audit = sorted(Path(f).stem for f, text in p.java.items() if "/application/" in f and "AuditContext audit" in text)
    out += ["- `AuditContext`（誰が・いつ）を受け取る Service: " + ("、".join(f"`{a}`" for a in audit) or "なし")]
    out += ["- トランザクション: 生成コードは開始も commit もしない。`Connection` は呼び出し側が `setAutoCommit(false)` で渡す"]
    out += ["", "1 回の呼び出し", ""] + USAGE_DIAGRAM
    return "\n".join(out)


def limits_facts(p: Project) -> str:
    summary = p.report.get("summary") or {}
    counts: dict[str, int] = {}
    for r in p.routines.values():
        counts[r.verdict.get("verdict", "—")] = counts.get(r.verdict.get("verdict", "—"), 0) + 1
    out = ["**事実**（生成物・決定・比較から機械的に出した。手で書き換えない）", "",
           "- 判定: " + " / ".join(f"{k} {v}" for k, v in sorted(counts.items())),
           f"- 変換できなかった文 {summary.get('untranslatedStatements', 0)} / ScalarDB が受け付けない SQL "
           f"{summary.get('unsupportedSql', 0)} / 実行計画に回した文 {summary.get('plannedSql', 0)}"]
    open_ = [r for r in p.routines.values() if r.verdict.get("verdict") in ("REVIEW", "REDESIGN")]
    if open_:
        out += ["", "AUTO でない routine", ""] + _table(["routine", "判定", "理由"], [
            [f"`{r.id}`", r.verdict.get("verdict"), "<br>".join(dict.fromkeys(r.verdict.get("reasons") or []))] for r in open_])
    accepted = [(r, s, a) for r in p.routines.values() for s in r.scenarios for a in s.get("accepted") or []]
    if accepted:
        out += ["", "受け入れた差（実 DB の比較で Oracle と違い、理由つきで受け入れたもの）", ""] + _table(
            ["routine", "シナリオ", "差", "理由", "決めた日"],
            [[f"`{r.id}`", f"`{s['name']}`", _short(a.get("difference", ""), 110), a.get("reason"), a.get("decided")] for r, s, a in accepted])
    different = [(r, s) for r in p.routines.values() for s in r.scenarios if s.get("differences")]
    if different:
        out += ["", "**受け入れていない差**", ""] + _table(["routine", "シナリオ", "差"], [
            [f"`{r.id}`", f"`{s['name']}`", "<br>".join(_short(d, 140) for d in s["differences"])] for r, s in different])
    if p.evidence:
        bare = sorted(r.id for r in p.routines.values() if not r.scenarios)
        out += ["", "- 実 DB で比べていない routine: " + ("、".join(f"`{b}`" for b in bare) or "なし")]
    else:
        out += ["", "- **実 DB の比較（`--evidence`）は渡されていない。** Oracle と同じ結果を返すかは、この文書では分からない"]
    undecided = [(k, v) for k, v in p.record.items() if v.get("状態") == "未決"]
    if p.record:
        out += ["- 生成コードの外で決めること（未決）: " + ("、".join(f"`{k}`" for k, _ in undecided) or "なし")]
    generator = [c for c in (p.report.get("errorCodes") or {}).get("codes", []) if c.get("raisedByGenerator")]
    if generator:
        out += ["", "生成コードが自分で上げる例外（Oracle では DB が上げていたもの）", ""] + _table(
            ["コード", "class", "Oracle"], [[c["code"], f"`{c['class']}`", c.get("oracle")] for c in generator])
    return "\n".join(out)


def how_facts(p: Project) -> str:
    out = ["**事実**（解析・決定から機械的に出した。手で書き換えない）", ""]
    decisions = _decisions(p.limits)
    if decisions:
        out += ["routine ごとの決定（`limits.yaml`）", ""] + _table(["決定", "対象", "値・理由"], [
            [f"`{kind}`", f"`{target}`", _short(value, 160)] for kind, target, value in decisions])
    else:
        out += ["- routine ごとの決定（`limits.yaml`）は無い"]
    rules: dict[tuple[str, str], list[str]] = {}
    for r in p.routines.values():
        for rule in r.rules:
            rules.setdefault((rule["id"], rule.get("decision", "")), []).append(r.id)
    if rules:
        out += ["", "当たった判定ルール", ""] + _table(["ルール", "判定", "routine 数"], [
            [f"`{rule}`", decision, len(set(ids))] for (rule, decision), ids in sorted(rules.items())])
    codes: dict[str, int] = {}
    for r in p.routines.values():
        for s in r.statements:
            for _, code, _ in s["diagnostics"]:
                codes[code] = codes.get(code, 0) + 1
    if codes:
        out += ["", "文ごとの診断（書き換えの種類）", ""] + _table(["診断", "文の数"], [[f"`{c}`", n] for c, n in sorted(codes.items())])
    every = [(r, c) for r in p.routines.values() for c in _changes(r)]
    if every:
        kinds: dict[tuple[str, str, str], set[str]] = {}
        for r, c in every:
            kinds.setdefault((c["kind"], c["code"], c["what"]), set()).add(r.id)
        out += ["", "何がどう変わったか（全体）", ""] + _table(["区分", "変わること", "診断", "routine"], [
            [f"**{kind}**" if kind in (MEANING, UNKNOWN) else kind, what, f"`{code}`",
             "、".join(f"`{x}`" for x in sorted(ids)[:6]) + (f" ほか {len(ids) - 6}" if len(ids) > 6 else "")]
            for (kind, code, what), ids in sorted(kinds.items(), key=lambda x: (ORDER[x[0][0]], x[0][1]))])
    decided = [(k, v) for k, v in p.record.items() if v.get("状態") == "決定"]
    if decided:
        out += ["", "生成コードの外で決めたこと", ""] + _table(["項目", "決定", "決めた人", "日付"], [
            [f"`{k}`", _short(v.get("決定", ""), 140), v.get("決めた人"), v.get("日付")] for k, v in decided])
    for variant in p.evidence.values():
        scenarios = variant.get("scenarios") or {}
        states = [_scenario_state(s) for s in scenarios.values()]
        out += ["", f"- 実 DB の比較（{variant.get('variant')}）: シナリオ {len(scenarios)} 本。"
                + " / ".join(f"{state.strip('*')} {states.count(state)}" for state in dict.fromkeys(states))]
    return "\n".join(out)


def routine_facts(r: Routine, p: Project) -> str:
    out = [f"**事実**（生成物・解析・決定・比較から機械的に出した。手で書き換えない。原文: `{r.file}:{r.start}`〜`{r.end}`）", ""]
    verdict = r.verdict.get("verdict", "—")
    reasons = list(dict.fromkeys(r.verdict.get("reasons") or []))
    out.append(f"- 判定: **{verdict}**" + (" — " + " / ".join(reasons) if reasons else ""))
    if r.method:
        out.append(f"- Java: `{r.service}` の `{r.method['ret'].strip()} {r.method['name']}({_short(r.method['args'], 160)})`"
                   + ("" if r.method["visibility"] == "public" else f"（{r.method['visibility']}。外からは呼べない）"))
    else:
        out.append("- Java: 入口は 1 つではなく、部品に割れている（回すのは呼び出し側）: " + "、".join(f"`{x}`" for x in r.parts) if r.parts
                   else "- Java: この routine の入口は見つからなかった（別の routine に織り込まれた trigger など）")
    changes = _changes(r)
    out += ["", "**何がどう変わったか**", ""]
    if changes:
        out += _table(["原文", "区分", "変わること", "診断"], [
            [f"`{r.file}:{c['line']}`", f"**{c['kind']}**" if c["kind"] in (MEANING, UNKNOWN) else c["kind"], c["what"], f"`{c['code']}`"]
            for c in changes])
    else:
        out += ["- 文の書き換えは無い（SQL はそのまま移行先で動く）"]
    picture = change_diagram(r)
    if picture:
        out += ["", "文の対応（赤 = 意味が変わる、黄 = 形が変わるが結果は同じ、灰 = 未分類、無色 = そのまま）", ""] + picture
    if r.parameters:
        out += ["", "引数の対応", ""] + _table(["PL/SQL", "方向", "Oracle の型", "Java"], [
            [f"`{x['name']}`", x.get("direction"), f"`{(x.get('type') or {}).get('resolved') or (x.get('type') or {}).get('oracle')}`",
             "戻り値の record に入る" if x.get("direction") in ("OUT", "IN OUT") and r.method and r.method["ret"].strip() != "void" else "引数"]
            for x in r.parameters])
    codes = [c for c in (p.report.get("errorCodes") or {}).get("codes", []) if r.id in (c.get("raisedBy") or [])]
    if codes:
        out += ["", "例外", ""] + _table(["コード", "class", "Oracle", "メッセージ"], [
            [c["code"], f"`{c['class']}`", c.get("oracle"), f"`{c.get('message')}`"] for c in codes])
    if r.statements:
        out += ["", "文の対応（原文の文 → Repository の method → 移行先の SQL）", ""] + _table(
            ["原文", "文" if r.from_source else "文（解析が書き換えたあと）", "Java", "移行先の SQL", "診断"], [
                [f"`{r.file}:{s['line']}`", f"{s['kind']} `{_short(s['original'], 70)}`" if s["original"] else s["kind"],
                 "<br>".join(f"`{m}`" for m in r.repository_methods.get(s["line"], [])),
                 (f"`{_short(s['target'], 90)}`" if s["target"] else None) if s["status"] != "ERROR" or s["target"] else None,
                 "<br>".join(f"{sev} `{code}`: {_short(msg, 110)}" for sev, code, msg in s["diagnostics"])] for s in r.statements])
    if r.rules:
        seen, rows = set(), []
        for rule in r.rules:
            if rule["id"] not in seen:
                seen.add(rule["id"])
                rows.append([f"`{rule['id']}`", rule.get("decision"), rule.get("message")])
        out += ["", "当たった判定ルール", ""] + _table(["ルール", "判定", "意味"], rows)
    if r.decisions:
        out += ["", "この routine の決定（`limits.yaml`）", ""] + _table(["決定", "値・理由"], [[f"`{k}`", v] for k, v in r.decisions])
    if r.scenarios:
        out += ["", "実 DB の比較", ""] + _table(["規約", "シナリオ", "結果", "受け入れた差の理由"], [
            [s["variant"], f"`{s['name']}`", _scenario_state(s), "<br>".join(a.get("reason", "") for a in s.get("accepted") or [])]
            for s in r.scenarios])
    elif p.evidence:
        out += ["", "- 実 DB の比較: **この routine を通るシナリオは無い**"]
    return "\n".join(out)


def _block(block_id: str, body: str) -> str:
    return f"<!-- facts:begin {block_id} -->\n{body}\n<!-- facts:end {block_id} -->"


BLOCK = re.compile(r"<!-- facts:begin (?P<id>\S+) -->\n.*?\n<!-- facts:end (?P=id) -->", re.S)


def _prose(title: str, hint: str, level: str) -> str:
    return f"{level} {title}\n\n{UNWRITTEN}: {hint}）\n"


def render(path: Path, title: str, blocks: list[tuple[str, str, str]]) -> tuple[str, list[str]]:
    """`blocks` は (ID, 事実, 初めて書くときの節全体)。すでにある節は事実の欄だけを差し替える。"""
    text = path.read_text(encoding="utf-8") if path.exists() else f"# {title}\n"
    existing = {m.group("id") for m in BLOCK.finditer(text)}
    bodies = {block_id: body for block_id, body, _ in blocks}
    text = BLOCK.sub(lambda m: _block(m.group("id"), bodies[m.group("id")]) if m.group("id") in bodies else m.group(0), text)
    for block_id, _, section in blocks:
        if block_id not in existing:
            text = text.rstrip("\n") + "\n\n" + section.rstrip("\n") + "\n"
    return text, sorted(existing - set(bodies))


def documents(p: Project) -> dict[str, tuple[str, list]]:
    makers = {"architecture": architecture_facts, "usage": usage_facts, "limits": limits_facts, "how": how_facts}
    blocks = []
    for block_id, title, hint in INDEX_PROSE:
        body = makers[block_id](p)
        blocks.append((block_id, body, "\n".join([f"## {title}", "", _block(block_id, body), "", f"{UNWRITTEN}: {hint}）"])))
    docs = {"README.md": ("変換後のコード", blocks)}
    for module in p.modules:
        blocks = []
        for r in (x for x in p.routines.values() if x.module == module["name"]):
            body = routine_facts(r, p)
            blocks.append((r.id, body, "\n".join([f"## `{r.id}`", "", _block(r.id, body), ""]
                                                + [_prose(t, h, "###") for t, h in ROUTINE_PROSE])))
        docs[f"{module['name']}.md"] = (f"`{module['name']}`（{module.get('moduleKind')}）の変換", blocks)
    return docs


# --- commands -------------------------------------------------------------------------------------------------


def cmd_facts(args) -> int:
    p = load(args)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, (title, blocks) in documents(p).items():
        text, gone = render(out / name, title, blocks)
        (out / name).write_text(text, encoding="utf-8")
        stale += [f"{name}: {block_id}" for block_id in gone]
    for entry in stale:
        print(f"生成物に無くなった節が残っている（消すかどうかは人が決める）: {entry}", file=sys.stderr)
    print(f"MODULES={len(p.modules)} ROUTINES={len(p.routines)} STALE={len(stale)}", file=sys.stderr)
    return 0


def _sections(text: str) -> dict[str, str]:
    found = {}
    for part in re.split(r"(?m)^## ", text)[1:]:
        match = BLOCK.search(part)
        if match:
            found[match.group("id")] = BLOCK.sub("", part)
    return found


def check(p: Project, out: Path) -> tuple[list[str], int]:
    problems, unwritten = [], 0
    classes = {Path(f).name for f in p.java}
    for name, (_, blocks) in documents(p).items():
        path = out / name
        if not path.exists():
            problems.append(f"{name}: 文書が無い（facts を回す）")
            continue
        text = path.read_text(encoding="utf-8")
        current = {m.group("id"): m.group(0) for m in BLOCK.finditer(text)}
        for block_id, body, _ in blocks:
            if block_id not in current:
                problems.append(f"{name}: `{block_id}` の節が無い（facts を回す）")
            elif current[block_id] != _block(block_id, body):
                problems.append(f"{name}: `{block_id}` の事実の欄が生成物と違う（生成し直したか、決定・比較が変わった。facts を回し、文章を見直す）")
        for block_id in sorted(set(current) - {b for b, _, _ in blocks}):
            problems.append(f"{name}: `{block_id}` は生成物に無い（消えた routine の節が残っている）")
        holes = text.count(UNWRITTEN)
        unwritten += holes
        if holes:
            problems.append(f"{name}: 未記入が {holes} か所")
        for cited in sorted(set(re.findall(r"\b(\w+\.java)\b", BLOCK.sub("", text)))):
            if cited not in classes:
                problems.append(f"{name}: 文章が引く `{cited}` は生成物に無い")
        for block_id, prose in _sections(text).items():
            if UNWRITTEN in prose:
                continue
            if block_id == "limits":
                for r in p.routines.values():
                    if r.verdict.get("verdict") in ("REVIEW", "REDESIGN") and r.id not in prose:
                        problems.append(f"{name}: 「制限」の文章に、AUTO でない routine `{r.id}` が出てこない")
                    for s in r.scenarios:
                        if (s.get("accepted") or s.get("differences")) and s["name"] not in prose:
                            problems.append(f"{name}: 「制限」の文章に、Oracle と違うシナリオ `{s['name']}` が出てこない")
                if not p.evidence and "比較" not in prose:
                    problems.append(f"{name}: 「制限」の文章に、実 DB で比べていないことが書かれていない")
            elif block_id == "how":
                for kind in sorted({k for k, _, _ in _decisions(p.limits)}):
                    if kind.split(".")[-1] not in prose:
                        problems.append(f"{name}: 「どのように移行したか」の文章に、決定 `{kind}` が出てこない")
            elif block_id in p.routines:
                r = p.routines[block_id]
                for rule in r.open_rules:
                    if rule not in prose:
                        problems.append(f"{name}: `{r.id}` の文章に、判定の理由 `{rule}` が出てこない")
                for s in r.scenarios:
                    if s.get("accepted") and s["name"] not in prose:
                        problems.append(f"{name}: `{r.id}` の文章に、受け入れた差のシナリオ `{s['name']}` が出てこない")
                for kind, _ in r.decisions:
                    if kind.split(".")[-1] not in prose:
                        problems.append(f"{name}: `{r.id}` の文章に、決定 `{kind}` が出てこない")
    return problems, unwritten


def cmd_check(args) -> int:
    p = load(args)
    problems, unwritten = check(p, Path(args.out_dir))
    for problem in problems:
        print(problem)
    print(f"ROUTINES={len(p.routines)} UNWRITTEN={unwritten} PROBLEMS={len(problems)}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (("facts", cmd_facts, "事実の欄を入れた文書の骨組みを作る（文章には触れない）"),
                             ("check", cmd_check, "書き上がった文書を生成物と突き合わせる")):
        command = sub.add_parser(name, help=text)
        command.add_argument("--generated", required=True, help="python -m plsql.generate の --out-dir")
        command.add_argument("--analysis", required=True, help="python -m plsql.cli の --out-dir（生成と同じ --limits で回したもの）")
        command.add_argument("--src", help="PL/SQL のディレクトリ。渡すと、変換前の文を（解析が書き換える前の）原文から引く")
        command.add_argument("--limits", help="生成に渡した limits.yaml")
        command.add_argument("--evidence", help="実 DB の比較（plsql_compare.py の plsql-diff.json）")
        command.add_argument("--record", help="生成コードの外で決めることの記録（decision_items.py の YAML）")
        command.add_argument("--out-dir", required=True, help="文書（Markdown）のディレクトリ")
        command.set_defaults(func=func)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except InputError as e:
        print(f"入力が読めない: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"ファイルが無い: {e.filename}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
