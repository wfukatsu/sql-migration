#!/usr/bin/env python3
"""SQL / PL/SQL → ScalarDB 移行ツール 開発者向け詳細解説 — slide-forge の spec（AIxDevOps マスター）を書き出す。

数値と出力は docs/guide/tutorial.md（2026-09-20 に samples/tutorial を実 DB で通した記録）と README の「現在地」から
転記した。コードは samples/tutorial/ の原文と、生成物（samples/tutorial/result/、out/migrate/tutorial-points/generated/）の抜粋。

    .venv/bin/python docs/slides/sql-migration-guide-deck.py out/slides/sql-migration-guide/deck.json
    SF=~/.claude/plugins/cache/slide-forge/slide-forge/<version>
    cd $SF && .venv/bin/python scripts/build_deck.py --template templates/aixdevops.json --spec <deck.json> --dry-run --strict
    cd $SF && .venv/bin/python scripts/build_deck.py --template templates/aixdevops.json --spec <deck.json> --title "…" --folder <Drive フォルダ>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIAGRAMS = ROOT / "docs" / "diagrams"
TITLE = "SQL / PL/SQL → ScalarDB 移行ツール 開発者向け詳細解説"
ENV = "Docker 上の Oracle 23ai Free と ScalarDB Cluster 3.19.1（1 ノード、PostgreSQL）"
SRC_TUTORIAL = "docs/guide/tutorial.md（2026-09-20 の実測）。" + ENV
SRC_README = "README と docs/guide/plsql-conversion.md の「現在地」（2026-09-20 の実測、合成 corpus 29 unit / 67 routine）"

X0, Y0, W, YMAX = 0.4, 0.68, 9.2, 5.08     # TITLE_ONLY_PROPOSAL の本文域（下はロゴとページ番号）
slides: list[dict] = []


def page(title: str, figures: list[dict], notes: str = "") -> None:
    slides.append({"layout": "TITLE_ONLY_PROPOSAL", "title": title, "figures": figures, "notes": notes})


def slot(name: str) -> dict:
    """A picture for the master's image placeholder (cover / section pages). No coordinates: the layout's slot decides."""
    return {"type": "image", "source": str(DIAGRAMS / "placeholders" / name), "fit": "contain"}


def section(title: str, subtitle: str, picture: str) -> None:
    slides.append({"layout": "SECTION", "title": title, "subtitle": subtitle, "figures": [slot(picture)]})


def code(x, y, w, text: str, lang: str = "java", size: float = 7.5, h: float | None = None) -> dict:
    lines = text.rstrip("\n").count("\n") + 1
    height = h if h is not None else round(lines * size * 1.04 * 1.45 / 72 + 0.16, 2)
    return {"type": "code_block", "x": x, "y": y, "w": w, "h": height, "code": text.rstrip("\n"), "lang": lang, "size": size}


def table(x, y, w, headers, rows, widths=None, size=9, row_h=0.3) -> dict:
    fig = {"type": "table", "x": x, "y": y, "w": w, "headers": headers, "rows": rows, "size": size,
           "rowH": row_h, "headerH": 0.32, "aligns": ["START"] * len(headers), "textMargin": 0.04}
    if widths:
        fig["colWidths"] = widths
    return fig


def lead(text: str, y: float = Y0) -> dict:
    return {"type": "lead_in", "x": X0, "y": y, "w": W, "text": text, "size": 10.5}


def source(text: str) -> dict:
    return {"type": "source_note", "x": X0, "y": 4.9, "w": W, "source": text}


def terms(y: float, rows: list[str], src: str | None = None) -> dict:
    """用語の注。初めて出てくるページの下に置く。1 行 = 1 行で収まる長さ（全角 85 字まで）にする。"""
    width = lambda t: sum(1.0 if ord(ch) > 0x2E80 else 0.5 for ch in t)
    assert all(width(row) <= 86 for row in rows), [row for row in rows if width(row) > 86]
    marked = [f"※: {row}" for row in rows]
    if src:
        return {"type": "source_note", "x": X0, "y": y, "w": W, "source": src, "notes": marked}
    return {"type": "source_note", "x": X0, "y": y, "w": W, "source": rows[-1], "notes": marked[:-1], "prefix": "※"}


def image(name: str, x, y, w, h) -> dict:
    return {"type": "image", "x": x, "y": y, "w": w, "h": h, "source": str(DIAGRAMS / name), "fit": "contain"}


CROPS = ROOT / "out" / "slides" / "sql-migration-guide" / "figures"


def crop(name: str, box: tuple[int, int, int, int], out: str, x, y, w, h) -> dict:
    """A part of a draw.io PNG (pixels: left, top, right, bottom), so that its text stays readable on a slide.

    The whole diagram on one page came out at 5pt."""
    import os
    import subprocess
    CROPS.mkdir(parents=True, exist_ok=True)
    target = CROPS / out
    left, top, right, bottom = box
    try:
        from PIL import Image
        Image.open(DIAGRAMS / name).crop(box).save(target)
    except ImportError:
        # this repository's venv has no Pillow; slide-forge's does (`sips --cropOffset` did not crop reliably)
        python = os.environ.get("SLIDE_FORGE_PYTHON", os.path.expanduser("~/.claude/venvs/gslides/bin/python"))
        subprocess.run([python, "-c", "import sys; from PIL import Image; "
                        "Image.open(sys.argv[1]).crop(tuple(map(int, sys.argv[3:7]))).save(sys.argv[2])",
                        str(DIAGRAMS / name), str(target), *map(str, box)], check=True)
    return {"type": "image", "x": x, "y": y, "w": w, "h": h, "source": str(target), "fit": "contain"}


# =====================================================================================================
# 表紙・要約
# =====================================================================================================
slides.append({
    "layout": "COVER",
    "title": "SQL / PL/SQL → ScalarDB\n移行ツール 詳細解説",
    "subtitle": "開発者向け: 課題・ソリューション・アーキテクチャ・使い方・具体例",
    "body": ["Oracle などの SQL と PL/SQL を ScalarDB へ移す PoC",
             "2026 年 9 月 20 日",
             "github.com/wfukatsu/sql-migration"],
    "figures": [slot("cover.png")],
    "notes": "変換ツール、PL/SQL 変換、Claude Code / Codex のスキル、実 DB の検証基盤の仕組みと、サンプルを実際に通した結果をまとめます。",
})

page("変換できる所は変換し、変換してよいかは証拠で決め、人の判断は記録して進める", [
    {"type": "exec_summary", "x": X0, "y": Y0, "w": W, "h": 3.5,
     "situation": "Oracle などの SQL と PL/SQL を ScalarDB に移したいが、ScalarDB SQL の文法は絞られており、行ロック・sequence・trigger は無い",
     "complication": "手で書き換えると結果が変わりやすく、コンパイルが通っても同じ意味とは限らない。誰が何を決めたかも残らない",
     "resolution": "SQL は文ごとに判定して変換・実行計画・報告に振り分け、PL/SQL は routine ごとに AUTO / REVIEW / REDESIGN を出す。"
                   "AUTO は実 Oracle と実 ScalarDB で一致した証拠があるときだけ付け、人の判断は承認つきの流れで記録する",
     "points": ["SQL サンプル 20 文: OK 7 / WARN 6 / PLANNED 4 / ERROR 3。実 DB で読み取り 10 文中 9 文が一致（残り 1 文は WARN の警告どおり）",
                "PL/SQL サンプル（routine 4）: 13 シナリオすべてが Oracle と一致。証拠つきで AUTO 3 / REDESIGN 1（決定済み）",
                "合成 corpus（67 routine）: AUTO 対象の意味的同等性 100%（49/49）、決定の適用後 AUTO 40 / REDESIGN 27",
                "Claude Code と Codex の marketplace から入れ、自然文で頼める"],
     "size": 9.5},
    terms(4.25, ["ScalarDB = 複数のデータベースにまたがる ACID トランザクションを提供するミドルウェア。ScalarDB Cluster はそのサーバ版",
                "ScalarDB SQL = ScalarDB Cluster が受ける、文法を絞った SQL（JDBC で使う）。PL/SQL = Oracle のストアドプログラムの言語",
                "routine = procedure・function・trigger の本体 1 つ。corpus = 判定と生成を測るために用意した、合成の PL/SQL 一式"],
          "docs/guide/tutorial.md（2026-09-20 の実測）、README の「現在地」（合成 corpus）"),
], "状況・課題・答えを 1 枚にまとめています。数値はすべて実測で、合成 corpus とサンプル上のものです。実案件耐性の証拠ではありません。")

# =====================================================================================================
# 1. 課題
# =====================================================================================================
section("1. 課題", "SQL と PL/SQL は、どこでつまずくのか", "section-1.png")

page("ScalarDB SQL の文法は絞られており、既存の SQL の多くはそのままでは動かない", [
    table(X0, Y0 + 0.05, W, ["Oracle の書き方", "ScalarDB SQL では", "ツールの判定", "移し方"], [
        ["SET balance = balance + 100", "SET / VALUES はリテラルと bind だけ。列を含む式は書けない", "ERROR RMW", "同じトランザクションで 読む → 計算 → リテラルで書く"],
        ["member_seq.NEXTVAL、CREATE SEQUENCE", "sequence が無い（view・trigger も無い）", "ERROR SEQUENCE / DDL", "採番をアプリで行う"],
        ["NVL(name, '…')、SUM(…) OVER (…)", "SELECT は列と集約だけ。式と OVER は書けない", "PLANNED", "取得 + H2 の実行計画"],
        ["WHERE balance > (SELECT AVG…)", "副問合せが無い", "PLANNED", "内側を先に取得して bind する実行計画"],
        ["m.id = h.id(+)（相手の主キーを覆わない）", "結合は相手の主キー全体か索引列が要る（DB-SQL-10067）", "PLANNED（P8）", "両方を取得して H2 で結合"],
        ["WHERE ROWNUM <= 3 ORDER BY …", "LIMIT は ORDER BY のあとに効く（Oracle は先）", "WARN ROWNUM", "元の意図を確かめる"],
        ["GROUP BY rank（キー以外で絞る）", "パーティションをまたぐ走査になる", "WARN CROSS_PARTITION", "RDBMS のバックエンド限定"],
    ], widths=[2.6, 3.1, 1.5, 2.0], size=8.5, row_h=0.33),
    terms(4.3, ["bind = SQL に値を後から渡す仕組み（:name や ?）。パーティションキー = 行の置き場所を決めるキー。これで絞れない検索は走査になる",
                "H2 = メモリ上で動く Java の RDBMS。実行計画 = 「ScalarDB から取得 → H2 で元の SQL」の手順を書いた JSON（PLANNED の文に出る）",
                "RMW = read-modify-write。読んだ値から計算して書くこと"],
          "samples/tutorial/sql/points.sql の変換レポート（samples/tutorial/result/sql/points.report.md）"),
], "ScalarDB SQL は、キーで行に届く読み書きが基本です。表は、サンプルの Oracle SQL が実際に当たった制約です。制約の一覧は skills/sql-transpile/references/scalardb-grammar.md にあります。")

page("PL/SQL は DB の機能に頼っており、逐語的に Java に写すと意味が変わる", [
    lead("Oracle が黙って保証していたものが、移行先には無い。変換できない構文よりも、この差が移行を止める。"),
    table(X0, 1.2, W, ["PL/SQL が頼っているもの", "Oracle での意味", "移行先（ScalarDB + Java）", "当たる判定ルール"], [
        ["SELECT … FOR UPDATE", "行をロックし、後から来たものを待たせる", "行ロックが無い。衝突は commit のときに弾かれる", "LOCK-001（REDESIGN）"],
        ["routine の中の COMMIT / ROLLBACK", "途中で確定し、続きを別のトランザクションで行う", "境界は呼び出し側。1 反復 = 1 トランザクション", "TX-001（REDESIGN）"],
        ["sequence（NEXTVAL）", "重複しない番号を、トランザクションの外で取る", "counters 表 + 再試行、または hi/lo", "SQL-001"],
        ["trigger", "表へのすべての書き込みに掛かる", "Service を通る書き込みにだけ掛かる（網羅性）", "TRG-*"],
        ["cursor", "トランザクションをまたいで位置を保つ", "先に全部読む。行数の上限を人が決める", "CUR-001 / CUR-002"],
        ["SYSDATE / USER", "DB サーバの時計、DB のユーザ", "アプリの時計、呼び出し側が渡す AuditContext", "SEM-007 / SEM-010"],
        ["'' = NULL、NUMBER の 10 進演算", "空文字は NULL。0.1 + 0.2 = 0.3", "実行時ヘルパ Plsql が意味論を再現する", "SEM-003 ほか"],
    ], widths=[2.1, 2.5, 2.9, 1.7], size=8.5, row_h=0.33),
    terms(4.35, ["sequence = 重複しない番号を発行する DB の機能。trigger = 表への書き込みで自動的に走る処理。cursor = 結果を 1 行ずつたどる仕組み",
                 "LOCK-001 などは判定ルールの ID。REDESIGN = 設計を決め直す、という判定（変換できない、という意味ではない）"],
          "docs/plsql-migration/ の移行パターン、plsql/rules/*.yaml"),
], "判定ルールは plsql/rules/*.yaml にあります。REDESIGN は『設計を決め直す』という意味で、変換できないという意味ではありません。")

page("コンパイルが通っても同じ結果とは限らず、手作業の移行は判断の経緯が残らない", [
    {"type": "cards", "x": X0, "y": Y0 + 0.05, "w": W, "h": 1.75, "items": [
        ["compile が通る ≠ 意味が同じ", "SELECT * INTO の %ROWTYPE が列 1 だけを読んでいた、BULK COLLECT が 2 行目以降を捨てていた——どれもコンパイルは通っていた（corpus の Phase 3 で見つけた 8 件）"],
        ["H2 で通る ≠ ScalarDB で動く", "生成 Repository が BigDecimal をそのまま束縛し、ScalarDB が DB-SQL-10016 で拒否（52 本中 33 本）。H2 は受け取っていた"],
        ["「変換できた」≠「変換してよい」", "行ロックを落として読み書きをそのまま写すと、コードは動くが更新が失われうる。決めるのは人で、ツールが黙って進めてはいけない"],
    ], "titleSize": 11, "bodySize": 9.5},
    {"type": "so_what", "x": X0, "y": 2.75, "w": W, "h": 1.75,
     "text": "だから、このツールの中心は「変換できること」ではなく「変換してよいか」の判定に置いた。",
     "points": ["証拠 = 実 Oracle と実 ScalarDB で同じシナリオを流し、戻り値・例外コード・表の状態が一致したこと",
                "人が決めること = limits.yaml と記録に、決めた人・日付・理由つきで残す。決めていないものは生成器が拒否したままにする",
                "承認 = 現行の仕様 / 人の判断 / 変換後の仕様 の 3 つ。そろうまでテストに進めない"], "size": 10},
    terms(4.62, ["%ROWTYPE = 表の 1 行と同じ形の変数。BULK COLLECT = 複数の行をまとめて配列に読む構文。Repository = SQL を受け持つ Java の class"]),
], "不具合の件数は、合成 corpus の Phase 3（2026-09-17）で見つけたものです。")

# =====================================================================================================
# 2. ソリューション
# =====================================================================================================
section("2. ソリューション", "SQL の変換、PL/SQL の変換、\n承認つきの移行の流れ", "section-2.png")

page("SQL の変換、PL/SQL の変換、移行の流れの 3 つで答える", [
    {"type": "cards", "x": X0, "y": Y0 + 0.05, "w": W, "h": 2.05, "items": [
        ["① SQL 文の変換", "scalardb_migrate/。SQLGlot で構文木にし、ScalarDB 文法へ書き換える。文ごとに OK / WARN / PLANNED / ERROR。ScalarDB SQL にできない読み取りは「取得 + H2」の実行計画に分解し、residual-runner（Java）が動かす"],
        ["② PL/SQL → Java 変換", "plsql/。ANTLR で解析して IR にし、ルールと確信度で AUTO / REVIEW / REDESIGN を出す。SQL 部分は ① をそのまま使い、制御構造・例外・型を Java（Service / Repository / domain）へ落とす"],
        ["③ 移行の流れ", "skills/。Claude Code / Codex のスキル。migrate-flow が 現行の仕様 → 変換と人の判断 → 変換後の仕様 → テスト の順と、3 つの承認を受け持つ。中身は plsql-spec / plsql-migrate / sql-transpile"],
    ], "titleSize": 11, "bodySize": 9.5},
    {"type": "flow", "x": X0, "y": 3.05, "w": W, "h": 0.7, "size": 10,
     "items": ["移行元のコード", "① ② 変換と判定", "③ 人の判断と承認", "実 DB で突き合わせ（difftest/）", "証拠 → AUTO"]},
    {"type": "lead_in", "x": X0, "y": 4.05, "w": W, "rule": False, "size": 10,
     "text": "DB の要らない部分（変換・判定・生成・コンパイル）は CI で回る。実 DB が要るのは、突き合わせと性能だけ。"},
    terms(4.45, ["SQLGlot = SQL を構文木にして方言のあいだで変換する Python のライブラリ。ANTLR = 文法の定義から構文解析器を作るツール",
                 "IR = Intermediate Representation（中間表現）。PL/SQL を解析した結果を、決まった形の JSON にしたもの。判定・生成・文書の元になる",
                 "residual-runner = 実行計画を動かす Java のコマンド。スキル = エージェント（Claude Code / Codex）に手順を教える文書とスクリプトの組"]),
], "3 つの柱です。以降、この順に説明します。")

page("SQL は文ごとに 4 つの判定に分かれ、判定ごとに次の手が決まる", [
    {"type": "flow", "x": X0, "y": Y0 + 0.05, "w": W, "h": 0.6, "size": 10,
     "items": ["SQLGlot で解析", "ScalarDB 文法へ書き換え", "アクセスパス分析", "判定"]},
    table(X0, 1.55, W, ["判定", "意味", "出力", "次にやること", "20 文中"], [
        ["OK", "ScalarDB SQL に変換できた", ".scalardb.sql", "そのまま使う", "7"],
        ["WARN", "変換できたが、意味や性能に注意がある（ROWNUM、MERGE → UPSERT、走査、精度）", ".scalardb.sql + 指摘", "指摘を読む。実 DB で確かめる", "6"],
        ["PLANNED", "ScalarDB SQL にはできないが、実行計画（取得 → H2）で動かせる読み取り", ".plan.json", "行数と応答時間を確かめる", "4"],
        ["ERROR", "自動では移せない（sequence、SET に列を含む式）", "レポートに理由と対応案", "アプリ側で実装する", "3"],
    ], widths=[0.9, 3.4, 1.7, 2.2, 1.0], size=9, row_h=0.4),
    code(X0, 3.85, W, """$ python -m scalardb_migrate.cli samples/tutorial/sql/points.sql --source oracle --out-dir out --plan-dir out/plans
[  8] ERROR UPDATE   UPDATE members SET balance = balance + 100 WHERE member_id = 1
        ERROR RMW: SET balance = balance + 100: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE ...
[ 14] WARN  SELECT   SELECT member_id, name, balance FROM members WHERE ROWNUM <= 3 ORDER BY balance DESC
        WARN  ROWNUM: 'ROWNUM <= 3' rewritten to LIMIT 3. Note: Oracle applies ROWNUM before ORDER BY, ScalarDB LIMIT applies after""", "bash", 7),
], "判定の決まり方は docs/design/architecture.md の 4.2 にあります。終了コードは、ERROR の文があれば 1 です。")

page("変換の実例: 方言の構文を直し、意味が変わる所は WARN で知らせる", [
    code(X0, Y0 + 0.02, 4.5, """-- Oracle（samples/tutorial/sql/points.sql）
CREATE TABLE members (
  member_id  NUMBER(10)    NOT NULL,
  name       VARCHAR2(100) NOT NULL,
  rank       VARCHAR2(10)  DEFAULT 'REGULAR' NOT NULL,
  balance    NUMBER(10)    NOT NULL,
  updated_at DATE,
  CONSTRAINT pk_members PRIMARY KEY (member_id));
CREATE INDEX idx_members_rank ON members (rank);

MERGE INTO members m
USING (SELECT 2 AS member_id, 'Tanaka' AS name
         FROM dual) s
   ON (m.member_id = s.member_id)
 WHEN MATCHED THEN UPDATE SET m.name = s.name
 WHEN NOT MATCHED THEN INSERT (member_id, name, rank,
   balance, last_seq) VALUES (s.member_id, s.name,
   'REGULAR', 0, 0);

SELECT member_id, name, balance FROM members
 WHERE ROWNUM <= 3 ORDER BY balance DESC;""", "sql", 7),
    code(5.1, Y0 + 0.02, 4.5, """-- ScalarDB SQL（points.scalardb.sql）
CREATE TABLE members (
  member_id BIGINT PRIMARY KEY,
  name TEXT,
  rank TEXT,           -- WARN DEFAULT: 既定値はアプリが入れる
  balance BIGINT,
  updated_at DATE      -- WARN TYPE: Oracle の DATE は時刻を持つ
);
CREATE INDEX ON members (rank);   -- INFO: 索引名は落ちる

-- WARN MERGE: UPSERT に書き換えた。既存行では
--   WHEN MATCHED が触れない rank / balance / last_seq も
--   上書きする
UPSERT INTO members (member_id, name, rank, balance,
  last_seq) VALUES (2, 'Tanaka', 'REGULAR', 0, 0);



-- WARN ROWNUM: Oracle は ROWNUM を ORDER BY より先に適用する
SELECT member_id, name, balance FROM members
 ORDER BY balance DESC LIMIT 3;""", "sql", 7),
    {"type": "so_what", "x": X0, "y": 4.0, "w": W, "h": 0.85, "size": 10,
     "text": "WARN は「変換できた」であって「同じ結果になる」ではない。ROWNUM の文は、実 DB で実際に結果が変わった（5 章）。"},
], "左が原文、右が変換後です。右のコメントは、レポートに出る指摘を書き添えたものです。")

page("ScalarDB SQL にできない読み取りは、取得 + H2 の実行計画で動かす", [
    {"type": "flow", "x": X0, "y": Y0 + 0.05, "w": W, "h": 0.58, "size": 9.5,
     "items": ["元の SQL（PLANNED）", "decomposer で分解", "表ごとに ScalarDB SQL で取得", "メモリ上の H2 に投入", "元の SQL を実行"]},
    code(X0, 1.5, 5.6, """// out/plans/points.17.plan.json（外部結合 (+) の文、抜粋）
{ "pattern": "P8",
  "fetch": [
    { "table": "members", "access_path": "GET",
      "scalardb_sql": "SELECT member_id, name FROM members
                       WHERE member_id = 1" },
    { "table": "point_history", "access_path": "CROSS_PARTITION",
      "scalardb_sql": "SELECT member_id, seq_no, points
                       FROM point_history", "max_rows": 10000 } ],
  "residual": { "java": { "engine": "h2", "mode": "Oracle",
      "sql": "SELECT m.member_id, m.name, h.seq_no, h.points
              FROM members m LEFT JOIN point_history h
                ON m.member_id = h.member_id
              WHERE m.member_id = 1" } },
  "guardrails": { "requires_cross_partition_scan": true,
                  "row_limit": 10000 },
  "transaction": { "read_only": true } }""", "json", 7.5, h=2.75),
    {"type": "cards", "x": 6.15, "y": 1.5, "w": 3.45, "h": 2.75, "items": [
        ["守っていること", "取得はすべて 1 つの ScalarDB トランザクション（読み取り専用）。\n\nH2 は一時領域で、永続化しない。\n\nバックエンド DB には直接つながない。\n\n行数の上限（既定 10,000）を超えたら止める"],
    ], "titleSize": 10.5, "bodySize": 9.5},
    code(X0, 4.3, W, """$ residual-runner validate --plan out/plans/points.17.plan.json          # H2 でコンパイルだけ（DB 不要）
$ residual-runner run --plan out/plans/points.17.plan.json --properties scalardb-sql-jdbc.properties --fetcher jdbc""", "bash", 7),
    terms(4.84, ["P8 = 計画のパターン（両方の表を取得して H2 で結合）。GET = 主キーで 1 行を読む。CROSS_PARTITION = すべてのパーティションを走査する"]),
], "実行計画のパターン（P1〜P8）は docs/design/app-side-processing-plan.md にあります。応答時間はほぼ、ScalarDB から読む行数で決まります。")

page("PL/SQL は routine ごとに判定し、AUTO は証拠があって初めて付く", [
    table(X0, Y0 + 0.02, 5.0, ["判定", "意味"], [
        ["AUTO", "無人で生成してよい。当たった禁止条件が無く、確信度の 5 因子がすべて満ちている"],
        ["REVIEW", "人が確認する。決まると生成コードが変わりうる"],
        ["REDESIGN", "設計を決め直す（行ロック、routine 内 COMMIT、trigger など）。決定のあとも REDESIGN のまま"],
    ], widths=[1.0, 4.0], size=9, row_h=0.42),
    table(5.6, Y0 + 0.02, 4.0, ["確信度の因子", "何を見るか"], [
        ["ruleCoverage", "文がルールで覆われているか"],
        ["symbolResolution", "名前が解決できたか"],
        ["typeResolution", "%TYPE / %ROWTYPE が解けたか"],
        ["targetCapability", "ScalarDB で実行できるか"],
        ["testEvidence", "実 Oracle と一致したか"],
    ], widths=[1.6, 2.4], size=9, row_h=0.3),
    {"type": "flow", "x": X0, "y": 2.95, "w": W, "h": 0.55, "size": 9.5,
     "items": ["生成 Java を Cluster で実行", "PL/SQL を Oracle で実行", "capture を比較", "plsql-diff.json", "--evidence で判定へ"]},
    code(X0, 3.65, W, """# 証拠なし: 当たったルールが無くても AUTO にはならない
verdicts   {'REDESIGN': 1, 'REVIEW': 3}   whyNotAuto: 確信度が 0: testEvidence（まだ誰も Oracle と突き合わせていない）
# 実 DB の比較を --evidence で渡したあと
verdicts   {'AUTO': 3, 'REDESIGN': 1}
# 原文か生成器が変わると、指紋が合わなくなり「古い証拠」として REVIEW に戻る（decisions.json の staleEvidence）""", "bash", 7),
    terms(4.7, ["確信度 = 5 因子の積。1 つでも 0 なら AUTO にならない。capture = 1 シナリオの実行結果（戻り値・例外・表の状態）の記録",
                "指紋（fingerprint）= 原文と、生成器・SQL 変換器・実行時ヘルパのハッシュ。何を測った証拠かを示す"]),
], "確信度と AUTO 禁止条件の定義は docs/design/plsql-kpi.md にあります。")

page("人が決めることは、決めた人・日付・理由つきで limits.yaml と記録に残す", [
    code(X0, Y0 + 0.02, 4.7, """# samples/tutorial/plsql/limits.yaml
# 行ロックを落として、楽観制御 + 呼び出し側の再試行へ
# 移すと決めた routine（2026-09-20、移行責任者）。
# 決めていない routine の書き込みは、生成器が拒否したまま
rowLocks:
  optimistic:
    pkg_points.use_points: >-
      残高は同じトランザクションの中で読んだ値から
      計算して書く。同時の利用は commit で衝突として
      弾かれるので、残高はマイナスにならない。呼び出し側は
      衝突だけを再試行する（-20103 は再試行しても変わらない）

# ほかに書けるもの
# scanRows.routines / notLimited   走査行数の上限
# transactions.perIteration        1 反復 = 1 トランザクション
# dynamicTables                    動的 SQL が触れる表
# dbLinks                          DB link → namespace""", "json", 7),
    table(5.3, Y0 + 0.02, 4.3, ["生成コードの外で決めること", "答える人"], [
        ["OPS: 照合ジョブ、採番表の運用、監視", "運用担当"],
        ["CALL: 境界、再試行、AuditContext", "呼び出し側"],
        ["BIZ: 移行で変わる振る舞いの受け入れ", "業務担当"],
    ], widths=[3.0, 1.3], size=9, row_h=0.4),
    code(5.3, 2.55, 4.3, """# decisions-outside-generator.yaml（抜粋）
CALL-5:
  状態: 決定
  出た: [診断 OPTIMISTIC — pkg_points.use_points]
  決定: 衝突だけを最大 3 回、50 ms から倍々の
        指数バックオフで再試行する。…
  決めた人: 移行責任者
  日付: '2026-09-20'
  記録先: limits.yaml の rowLocks.optimistic、…
  決定時の根拠: [上と同じ。変わると「要再確認」]""", "json", 7),
    {"type": "lead_in", "x": X0, "y": 4.35, "w": W, "rule": False, "size": 10,
     "text": "理由の無い決定は決定として扱わない。--limits-strict は、行数の上限を誰も決めていない routine を名指しして止める。"},
    terms(4.68, ["楽観制御 = ロックせずに進め、衝突したら片方をやり直す方式。AuditContext = 「誰が・いつ」を呼び出し側から渡すための入れ物",
                 "OPS / CALL / BIZ = 生成コードの外で決めることの分類（運用 / 呼び出し側 / 業務ロジックとの整合）"]),
], "項目の定義は docs/plsql-migration/plsql-decisions-outside-generator.md にあります。")

# =====================================================================================================
# 3. アーキテクチャ
# =====================================================================================================
section("3. アーキテクチャ", "変換ツール、PL/SQL 変換、実行基盤、\nスキル、検証基盤", "section-3.png")

page("構成の変化: 業務ロジックは DB の中から、アプリの中の生成した Java へ移る", [
    image("transformation-1.png", X0, Y0, W, 3.95),
    terms(4.72, ["Consensus Commit = ScalarDB のトランザクション方式。同じ行を読み書きした別のトランザクションとの衝突を、commit のときに検出する",
                 "JDBC = Java から SQL を実行する標準の API。namespace = ScalarDB の表のまとまり（RDBMS のスキーマに当たる）"]),
], "左が現行、右が移行後。赤い箱は、Oracle が黙って保証していたもの（行ロック、sequence、trigger、SYSDATE / USER）で、移行先には無い。"
   "移行後は、呼び出し側（人が書く）がトランザクションの境界と再試行を持ち、生成した Service と Repository が PL/SQL の本体と SQL を受け持つ。"
   "データへは必ず ScalarDB を通って届く。図の元データは docs/diagrams/architecture-transformation.drawio（生成元 docs/diagrams/src/transformation.py）。")

page("呼び出しの変化: 境界は呼び出し側に移り、衝突は「待つ」から「commit で弾かれる」に変わる", [
    image("transformation-2.png", X0, Y0, W, 4.3),
], "use_points の 1 回の呼び出し。現行は SELECT … FOR UPDATE で行をロックし、ほかの利用を待たせる。移行後はロックなしで読み、"
   "同じトランザクションの中で書く。同じ行を読んで書いた別の commit があれば、commit のときに DB-CORE-20013 で弾かれ、呼び出し側が最初から再試行する。")

page("要素の対応: PL/SQL の何が、Java と ScalarDB の何になるか", [
    image("transformation-3.png", X0, Y0, W, 4.3),
], "白はそのまま写るもの、橙は出所が変わるもの、赤は意味が変わり、人の決定（limits.yaml）が要るもの。sequence・trigger・routine の中の COMMIT は、"
   "このサンプルには無く、合成 corpus で扱っている形。")

page("データ構造の変化: 主キーはパーティションキーとクラスタリングキーになり、SQL の動き方を決める", [
    image("transformation-4.png", X0, Y0, W, 3.95),
    terms(4.72, ["クラスタリングキー = 同じパーティションの中の並び順を決めるキー。パーティションキーと合わせて主キーになる",
                 "副次索引（secondary index）= キー以外の列で行を探すための索引。SCAN = キーの範囲で複数の行を読むこと"]),
], "members の主キーはパーティションキーに、point_history の複合主キーはパーティションキー + クラスタリングキーになる。索引は副次索引に、sequence は無い。"
   "下段は、キーの設計から決まるアクセスパス（変換ツールのアクセスパス分析が判定に入れる）。")

page("5 つの構成要素は、ビルド時・実行時・検証時に分かれて働く", [
    table(X0, Y0 + 0.02, W, ["構成要素", "場所", "言語", "役割", "DB"], [
        ["変換ツール", "scalardb_migrate/", "Python（SQLGlot）", "文ごとの変換、スキーマ変換、アクセスパス分析、実行計画への分解、アプリ側に移す処理の分析", "不要"],
        ["PL/SQL 変換", "plsql/", "Python（ANTLR）", "解析 → IR → 判定（ルール + 確信度）→ Java 生成 → レポートとトレーサビリティ", "不要"],
        ["実行基盤", "runtime-java/", "Java 17（Gradle）", "実行計画の実行（取得 → H2）、生成コードの実行時ヘルパ（Oracle の式の意味論）、ベンチマーク", "実行時"],
        ["スキル", "skills/", "Markdown + Python", "migrate-flow / plsql-spec / plsql-migrate / sql-transpile。Claude Code と Codex から使う", "段階 4"],
        ["検証基盤", "difftest/", "Python + Docker", "移行元 DB と ScalarDB Cluster での差分テスト、ベンチマーク、PL/SQL の capture と比較", "要る"],
    ], widths=[1.2, 1.5, 1.5, 4.2, 0.8], size=8.5, row_h=0.42),
    {"type": "flow", "x": X0, "y": 3.55, "w": W, "h": 0.62, "size": 9.5,
     "items": ["ビルド時: 変換・判定・生成（Python）", "実行時: 生成 Java + 実行計画（Java、ScalarDB 経由）", "検証時: ハーネスが両側で流して比べる"]},
    {"type": "lead_in", "x": X0, "y": 4.35, "w": W, "rule": False, "size": 10,
     "text": "CI（GitHub Actions と GitLab CI、同じ内容）が回すのは DB の要らない部分: pytest 2,200 件、同梱コピーの一致、Java の単体テスト。"},
], "sql-transpile スキルは scalardb_migrate のコピーを同梱しています（vendor_sync.py が一致を確かめます）。")

page("SQL の 1 文は 3 つの経路のどれかを通り、実行時は必ず ScalarDB を経由する", [
    {"type": "comparison", "x": X0, "y": Y0 + 0.02, "w": W, "h": 2.55, "size": 9.5, "columns": [
        ["経路 1: ScalarDB SQL（OK / WARN）", ["converter.py が書き換え、dialect.py が ScalarDB 文法だけを出す", "実行: ScalarDB SQL の JDBC（ScalarDB Cluster。ライセンスが要る）", "schema.py がキーを持ち、アクセスパス（GET / SCAN / 走査）を判定に入れる"]],
        ["経路 2: 実行計画（PLANNED）", ["decomposer.py が「取得 + 残りの SQL」に分解する（パターン P1〜P8）", "実行: residual-runner。Fetcher（Core API または JDBC）→ H2 → 結果", "TxRunner が競合の再試行、行数の上限、走査の許可を見る"]],
        ["経路 3: アプリ側（ERROR）", ["appside.py が、アプリで処理する構文・結果を変えないための注意・設計の提案・取得コストを出す", "実装の補助: runtime-java の appside（階層、ウィンドウ関数、数値、並び順、日付）"]],
    ]},
    table(X0, 3.4, W, ["決まり", "理由"], [
        ["アプリはバックエンド DB の接続情報を持たない。取得も書き込みも ScalarDB（SQL / JDBC または Core API）を通す", "ScalarDB のトランザクションの外で読むと、一貫したスナップショットにならない"],
        ["1 つの実行計画の取得は、すべて同じ ScalarDB トランザクションの中で行う", "複数の表を取得しても、同じ時点の状態になる"],
        ["パーティションをまたぐ走査は、RDBMS のバックエンドでだけ使う", "Cassandra ではキーで取得し、残りはアプリ側で処理する（--storage cassandra）"],
    ], widths=[5.2, 4.0], size=8.5, row_h=0.4),
], "全体の構成図（draw.io）は docs/diagrams/architecture.drawio にあります。細部は docs/design/architecture.md の Mermaid の図を見てください。")

page("PL/SQL 変換の構成 (1): ANTLR で解析して IR にし、ルールと確信度で判定する", [
    crop("plsql-conversion.png", (0, 0, 1770, 1020), "plsql-1-analyse-judge.png", X0, Y0, 6.75, 3.88),
    {"type": "cards", "x": 7.3, "y": Y0 + 0.02, "w": 2.3, "h": 3.85, "items": [
        ["読み方", "左は解析。frontend、symbols、lower の順に進み、IR になる。\n\n右は判定。SQL は sqlbridge が変換ツールへ渡し、capability が実行できるかを見る。構造は rules/*.yaml が見る。\n\nlimits.yaml（人の決定）と fingerprint（証拠が古くないか）が確信度に入る。\n\n緑の線: IR は Java 生成にも渡る"],
    ], "titleSize": 10.5, "bodySize": 8.5},
    terms(4.64, ["IR = Intermediate Representation（中間表現）。構文木から、型・制御フロー・SQL・例外・トランザクションを取り出した JSON",
                 "SLL → LL = ANTLR の速い解析を先に試し、だめなら完全な解析に切り替えること。lowering = 構文木を、より単純な IR の文に落とすこと"]),
], "図は docs/diagrams/plsql-conversion.drawio の左側（解析と判定）です。図の中の注記: IR が、判定・Java 生成・仕様書の「事実の欄」の元になる。AUTO = 無人で生成してよい。実 DB で一致した証拠がある routine にだけ付く。")

page("PL/SQL 変換の構成 (2): IR から Java を生成し、判定はレポートとトレーサビリティになる", [
    crop("plsql-conversion.png", (1860, 0, 2796, 1020), "plsql-2-generate-report.png", X0, Y0, 3.75, 4.05),
    table(4.55, Y0 + 0.02, 5.05, ["モジュール", "守っていること"], [
        ["gen_java/", "変換できない文は、例外を投げる method にする。--verify-compile が javac まで確かめ、落ちた routine を名指しする"],
        ["runtime-java/…/plsql", "Oracle の式の意味論（3 値の比較、NUMBER、'' = NULL、日付）を再現する。実機 Oracle の答え 2,015 件で検査している"],
        ["review.py / report.py", "判定の理由を、どのルールがどのファイルで当てたかまで辿れる。Java の member から原文の file:line へ辿れる"],
        ["kpi.py", "変換率は KPI にしない。AUTO 対象の意味的同等性を測る"],
        ["remediate.py / propose.py", "自分では効力を持たない。助言は常に REVIEW で、判定エンジンは読まない"],
    ], widths=[1.7, 3.35], size=8.5, row_h=0.55),
    terms(4.84, ["トレーサビリティ = 生成した Java のどの member が、元の PL/SQL のどの行から来たかを辿れること。KPI = 達成を測る指標"]),
], "図は docs/diagrams/plsql-conversion.drawio の右側（生成と報告）です。")

page("PL/SQL 変換の構成 (3): 同じシナリオを Oracle と ScalarDB で流し、一致を証拠にする", [
    crop("plsql-conversion.png", (0, 1066, 1720, 1602), "plsql-3-verify-capture.png", X0, Y0, 7.6, 2.37),
    {"type": "cards", "x": 8.1, "y": Y0 + 0.02, "w": 1.5, "h": 2.3, "items": [
        ["両側で同じシナリオ", "Oracle は FIXED_DATE で SYSDATE を固定。ScalarDB は決定つきで生成した Java を流す"],
    ], "titleSize": 9.5, "bodySize": 8.5},
    crop("plsql-conversion.png", (1700, 1072, 2796, 1602), "plsql-3-verify-compare.png", X0, 3.12, 4.0, 1.95),
    table(4.6, 3.15, 5.0, ["比べるもの", "決まり"], [
        ["戻り値・例外", "型つき、例外はコードで比べる"],
        ["表の状態", "capture_tables の全行。固定できない時計の列は mask"],
        ["受け入れた差", "シナリオに理由と決定日つきで書いた 1 組だけ"],
        ["指紋", "原文と生成器に合うときだけ、証拠として数える"],
    ], widths=[1.3, 3.7], size=8.5, row_h=0.36),
], "図は docs/diagrams/plsql-conversion.drawio の下側（実 DB での検証）です。")

page("IR が単一の事実の源で、判定・Java・仕様書の「事実の欄」はすべて IR から出る", [
    {"type": "flow", "x": X0, "y": Y0 + 0.05, "w": W, "h": 0.6, "size": 9.5,
     "items": ["frontend.py: ANTLR（SLL → LL の 2 段構え）", "symbols.py: %TYPE / %ROWTYPE の解決", "lower.py: 構文木 → IR", "IR（JSON Schema 固定）"]},
    table(X0, 1.5, 5.3, ["IR から出るもの", "モジュール", "出力"], [
        ["ScalarDB で実行できるか", "sqlbridge.py → scalardb_migrate、capability.py", "文ごとの OK / WARN / ERROR"],
        ["判定", "rules/*.yaml、rules/engine.py", "decisions.json、unresolved.md"],
        ["Java", "gen_java/", "generated/、traceability.csv"],
        ["現行の仕様（事実の欄と図）", "skills/plsql-spec/scripts/spec_facts.py", "spec/*.md（Mermaid）"],
        ["変換後の文書（事実の欄）", "skills/plsql-migrate/scripts/migration_doc.py", "docs/*.md"],
    ], widths=[1.7, 2.2, 1.4], size=8, row_h=0.38),
    code(5.85, 1.5, 3.75, """// program.ir.json（use_points の SELECT、抜粋）
{ "id": "pkg_points.use_points#stmt-3",
  "kind": "SqlOperation", "sqlKind": "SELECT",
  "sourceRange": { "file": "pkg_points.pkb",
                   "startLine": 69, "endLine": 72 },
  "readSet": ["members"],
  "diagnostics": [
    { "severity": "WARN", "code": "ROW_LOCK", … },
    { "severity": "WARN", "code": "OPTIMISTIC", … },
    { "severity": "WARN", "code": "LOCK", … },
    { "severity": "INFO", "code": "ACCESS",
      "message": "full primary key specified
                  -> GET (single record)" } ] }""", "json", 6.5),
    {"type": "lead_in", "x": X0, "y": 4.2, "w": W, "rule": False, "size": 10,
     "text": "文章（動作・業務ルールなど）はモデルが書き、check が事実の欄と突き合わせる。事実の欄は作り直しても文章に触れない。"},
    terms(4.7, ["JSON Schema = JSON の形（項目と型）を定義する規格。事実の欄 = 文書のうち、IR と生成物から機械的に出す部分（手で書き換えない）",
                "Mermaid = テキストから図を描く記法。Markdown の中に書ける"]),
], "IR は実物の抜粋です（diagnostics の message と range を省略）。スキーマは plsql/ir/ にあります。")

page("生成する Java は Service → Repository の一方向で、トランザクション境界は呼び出し側にある", [
    code(X0, Y0 + 0.02, 5.2, """// Generated by the PL/SQL migration tool. Do not edit.
// The transaction boundary belongs to the caller:
// no method here begins, commits or rolls back.
public class PkgPointsService {
  private final PkgPointsRepository repository;

  // pkg_points.pkb:57
  public void usePoints(Long pMemberId, BigDecimal pPoints,
                        String pReason) throws Exception {
    Long vBalance = null; Integer vSeq = null;
    LocalDateTime vNow = Plsql.sysdate();
    // pkg_points.pkb:64
    if (Plsql.le(pPoints, 0)) {
      throw new MigratedException(-20102, "使うポイントは…");
    }
    // pkg_points.pkb:69  SELECT INTO v_balance, v_seq
    var row = repository.usePointsStmt3(pMemberId);
    vBalance = Plsql.fitLong((Long) row[0], 10);
    vSeq = Plsql.fitInt((Integer) row[1], 6);
    // pkg_points.pkb:74
    if (Plsql.lt(vBalance, pPoints)) {
      throw new MigratedException(-20103, "ポイントが不足…");
    }
    vSeq = Plsql.fitInt(Plsql.add(vSeq, 1), 6);
    rowCount = repository.usePointsStmt7(pMemberId, vSeq, …);
    rowCount = repository.usePointsStmt8(vSeq, vNow, …);
  }
}""", "java", 6.5),
    {"type": "cards", "x": 5.8, "y": Y0 + 0.02, "w": 3.8, "h": 4.15, "gap": 0.12, "items": [
        ["読み方", "Service は PL/SQL の本体を文の順のまま写す。各文の前に原文の位置（// pkg_points.pkb:69）が残り、traceability.csv が member → file:line を持つ。\n\n比較と算術は Plsql.le / add / fitLong を通る。NULL を含む 3 値の比較と、NUMBER(10) の桁あふれを Oracle と同じにするため。\n\nRepository は SQL 1 文 = 1 method。変換できなかった文は UnsupportedOperationException を投げる method になり、黙って少なく動くコードにはならない。\n\nRAISE_APPLICATION_ERROR は MigratedException(code)。NO_DATA_FOUND などは domain の例外 class。"],
    ], "titleSize": 10.5, "bodySize": 8.5},
], "コードは out/migrate/tutorial-points/generated/ の PkgPointsService.java を、幅に合わせて一部省略したものです。")

page("Repository は SQL 1 文 = 1 method。SQL に書けない式は、method の中で計算してから bind する", [
    code(X0, Y0 + 0.02, 4.55, """-- PL/SQL（pkg_points.pkb:80-88）
INSERT INTO point_history
  (member_id, seq_no, points, reason, created_at)
VALUES (p_member_id, v_seq, -p_points,
        p_reason, v_now);

-- 使ってもランクは下げない（業務ルール）
UPDATE members
   SET balance = v_balance - p_points,
       last_seq = v_seq,
       updated_at = v_now
 WHERE member_id = p_member_id;""", "sql", 7, h=1.95),
    code(5.05, Y0 + 0.02, 4.55, """// PkgPointsRepository.java
public int usePointsStmt8(Object vSeq, LocalDateTime vNow,
    Object vBalance, BigDecimal pPoints,
    Object pMemberId) throws SQLException {
  String sql = "UPDATE members SET balance = :expr6, "
      + "last_seq = :v_seq, updated_at = :v_now "
      + "WHERE member_id = :p_member_id";
  // :expr6 = v_balance - p_points を Java で求めて bind
  …
}""", "java", 7, h=1.95),
    table(X0, 2.85, W, ["式の持ち上げ（sqlbridge.lift_expressions）の決まり", "理由"], [
        ["bind だけでできた式（-:p_points、:v_balance - :p_points、ROUND(:v * :r, 2)）は Java で計算して 1 つの bind にする", "ScalarDB SQL の SET / VALUES はリテラルと bind しか取らない"],
        ["列を含む式（SET stock = stock + :n）は持ち上げない", "保存されている値が要る。読んでから書く 2 文に割る（RMW_SPLIT）のは、決定があるときだけ"],
        ["行ロックが落ちた routine では、決定（rowLocks.optimistic）があるまで持ち上げない", "ロックが読み書きを安全にしていた。黙って書き換えを進めない"],
        ["実行時ヘルパが実装していない関数（package の関数など）は持ち上げない", "はっきりした拒否を、実行時のあいまいな失敗に変えないため"],
    ], widths=[5.4, 3.8], size=8.5, row_h=0.4),
], "use_points の 2 文は、行ロックの決定を limits.yaml に書くまで、この method が UnsupportedOperationException を投げていました。")

page("証拠は指紋つきで記録し、原文か生成器が変わると「古い」になって判定が戻る", [
    {"type": "flow", "x": X0, "y": Y0 + 0.05, "w": W, "h": 0.62, "size": 9,
     "items": ["plsql_run.py: Oracle で capture（SYSDATE は FIXED_DATE で固定）", "plsql_capture.py: 生成 Java を Cluster で capture + fingerprint.json", "plsql_compare.py: 突き合わせ → plsql-diff.json", "plsql.cli --evidence: 指紋が合えば数える"]},
    table(X0, 1.55, 4.6, ["比べるもの", "決まり"], [
        ["戻り値・OUT 引数", "型つきで比べる"],
        ["例外", "コードで比べる（-20103、-1403）"],
        ["表の状態", "capture_tables の全行。固定できない時計の列は mask"],
        ["受け入れた差", "シナリオに理由と決定日つきで書いた 1 組だけ"],
    ], widths=[1.4, 3.2], size=8.5, row_h=0.36),
    code(5.2, 1.55, 4.4, """// golden/use_points_short.json（Oracle の capture、抜粋）
{ "scenario": "use_points_short", "source": "oracle",
  "pinned": { "sysdate": "2026-01-15 09:30:00" },
  "exception": { "code": -20103,
    "message": "ORA-20103: ポイントが不足しています" },
  "tables": { "members": { "columns": ["member_id", …],
      "rows": [[{"$dec": "1"}, "Sato", "SILVER",
                {"$dec": "450"}, {"$dec": "3"}, …]] },
    "point_history": { "rows": [] } } }

// work/plsql-scalardb-double/fingerprint.json
{ "toolchain": "1a429944…",
  "sources": { "pkg_points.use_points": "c4cdc038…", … } }""", "json", 6.5),
    {"type": "so_what", "x": X0, "y": 3.85, "w": W, "h": 0.95, "size": 10,
     "text": "比較が観るのは 1 回の呼び出しの結果。同時実行、途中で止まったとき、PL/SQL の外からの書き込みは観ていない——文書の「制限」に必ずそう書く（check が見る）。"},
], "capture と指紋は実物の抜粋です（ハッシュは先頭 8 桁）。指紋は plsql/fingerprint.py が、原文と、生成器・SQL 変換器・実行時ヘルパのハッシュから作ります。")

page("検証環境: ハーネスだけが移行元に接続し、ScalarDB のバックエンドには誰も直接つながない", [
    table(X0, Y0 + 0.02, W, ["コンテナ（difftest/docker-compose.yml）", "ポート", "役割", "誰が接続するか"], [
        ["source-oracle（Oracle 23ai Free）", "1521", "移行元。正解を取る。PL/SQL を配備する", "ハーネスだけ"],
        ["source-postgres / MySQL（使い捨て）", "15432 / 13306", "移行元（PostgreSQL / MySQL の方言）", "ハーネスだけ"],
        ["scalardb-cluster", "60053", "ScalarDB SQL（JDBC）。ライセンスが要る", "residual-runner、生成 Java"],
        ["backend-postgres / cassandra / oracle", "—", "ScalarDB のバックエンド", "ScalarDB だけ"],
        ["schema-loader", "—", "namespace と表を作る", "—"],
    ], widths=[3.1, 1.2, 3.4, 1.5], size=8.5, row_h=0.36),
    code(X0, 3.15, W, """$ cd difftest && ./make-cluster-conf.sh && docker compose --profile cluster --profile oracle up -d
$ python difftest/run.py samples/tutorial/sql/points-check.sql --dialect oracle --fetcher jdbc --restart-cluster   # SQL の差分テスト
$ python difftest/sources.py oracle --profile oracle=my-profile.json      # 接続せずに、使われるプロファイルと書き込みの可否を確かめる""", "bash", 7),
    {"type": "lead_in", "x": X0, "y": 4.05, "w": W, "rule": False, "size": 9.5,
     "text": "接続情報は環境変数の名前で渡す。書き込みは environment が local / dev / test / ci のときだけで、解決したホストとも照らす。"},
    terms(4.55, ["ハーネス = テストを動かして結果を集める仕組み（difftest/ のスクリプト）。バックエンド = ScalarDB がデータを置く先のデータベース",
                 "Schema Loader = ScalarDB の表を作るツール。profile = Docker Compose で、起動するコンテナの組を選ぶ指定"]),
], "本番の移行元から正解を一度だけ取るときは golden.py capture --no-setup --allow-production（読み取り専用）を使います。")

# =====================================================================================================
# 4. 使い方
# =====================================================================================================
section("4. 使い方", "インストール、4 つのスキル、コマンド、出力", "section-4.png")

page("Claude Code と Codex の marketplace から入れ、自然文で頼む", [
    code(X0, Y0 + 0.02, 4.55, """# Claude Code
/plugin marketplace add wfukatsu/sql-migration
/plugin install sql-migration@sql-migration

# 呼ぶ
/sql-migration:migrate-flow
「samples/tutorial/plsql の PL/SQL を
  ScalarDB に移行して」""", "bash", 7.5),
    code(5.05, Y0 + 0.02, 4.55, """# Codex
codex plugin marketplace add wfukatsu/sql-migration
codex plugin add sql-migration@sql-migration

# このリポジトリを開発するとき（チェックアウトから）
for n in migrate-flow plsql-spec plsql-migrate \\
         sql-transpile; do
  ln -s "$PWD/skills/$n" ~/.claude/skills/$n; done""", "bash", 7.5),
    table(X0, 2.2, W, ["", "Claude Code", "Codex"], [
        ["スキルの選択", "description と when_to_use", "description だけ（きっかけと対象外は description の末尾にも書いてある）"],
        ["コマンドの許可", "allowed-tools の範囲は聞かれずに動く（プラグインでは聞かれる）", "サンドボックスと承認の設定に従う"],
        ["利用者への確認", "AskUserQuestion（推奨を先頭に、選択肢ごとの影響つき）", "同じ内容を本文で聞く"],
        ["要るもの", "Python 3.10 以上（bin/python が初回に仮想環境を作る）。コンパイルの確認に Java 17", "同じ"],
    ], widths=[1.4, 4.0, 3.8], size=8.5, row_h=0.36),
    terms(4.84, ["marketplace = プラグインの配布元として登録する Git リポジトリ。サンドボックス = エージェントが触れる範囲を制限する実行環境"]),
    {"type": "lead_in", "x": X0, "y": 4.5, "w": W, "rule": False, "size": 9.5,
     "text": "プラグイン = リポジトリのルート（約 18 MB）。作業ディレクトリは利用者のプロジェクトのままで、入力と出力はそちらに置く。"},
], "marketplace のマニフェストは .claude-plugin/ と .codex-plugin/、.agents/plugins/ にあります。")

page("4 つのスキルが段階を分担し、migrate-flow が順番と承認を受け持つ", [
    table(X0, Y0 + 0.02, W, ["スキル", "頼み方の例", "すること", "主なスクリプト"], [
        ["migrate-flow", "「この PL/SQL を ScalarDB に移行して」", "4 段階を順に進める。承認（人・日付・指紋）とテストの関門", "scripts/flow.py（init / status / approve / gate / tested）"],
        ["plsql-spec", "「このパッケージが何をしているか文書にして」", "現行の仕様。事実の欄と図は IR から、動作と業務ルールは原文の位置つきで書く", "scripts/spec_facts.py（facts / check）"],
        ["plsql-migrate", "「PL/SQL を Java にして」", "Java 生成、コンパイル確認、生成コードの外で決めることの確認と記録、変換後の文書", "scripts/decision_items.py、scripts/migration_doc.py"],
        ["sql-transpile", "「この SQL を ScalarDB 用に」", "32 方言どうし、または ScalarDB SQL への変換。変換率、指摘、実行計画", "scripts/transpile.py（終了コード 0 / 1 / 2）"],
    ], widths=[1.2, 2.6, 3.2, 2.2], size=8.5, row_h=0.5),
    {"type": "cards", "x": X0, "y": 3.25, "w": W, "h": 1.5, "items": [
        ["事実の欄 / 文章", "事実の欄は機械的に出し、文章はモデルが書く。check が確かめるのは、文章が事実から離れていないことまで"],
        ["判断を求める形", "何を決めるか・推奨・理由・影響・決めないとどうなるか・誰が答えるか、を示してから聞く"],
        ["承認するのは利用者", "スキルは問いを出して答えを記録するだけ。承認の無いまま approve を打たない"],
    ], "titleSize": 10.5, "bodySize": 9},
], "手順の本体は skills/<名前>/SKILL.md と references/ にあります。")

page("移行は 4 段階で、3 つの承認がそろうまでテストに進めない", [
    {"type": "flow", "x": X0, "y": Y0 + 0.05, "w": W, "h": 0.75, "size": 10, "gap": 0.26,
     "items": ["1. 現行\nの仕様", "承認\nspec", "2. 変換と\n人の判断", "承認\ndecisions", "3. 変換後\nの仕様", "承認\nconverted", "4. テスト"]},
    table(X0, 1.7, W, ["段階", "スキル", "作るもの", "承認の前の検査", "承認する人が引き受けること"], [
        ["1. 現行の仕様", "plsql-spec", "spec/*.md（事実の欄 + 図 + 動作・業務ルール・確かめたいこと）", "未記入が無い、事実が古くない、引用が routine の範囲内", "「現行はこう動いている」を、判断とテストの正解に使う"],
        ["2. 変換と人の判断", "plsql-migrate / sql-transpile", "generated/、limits.yaml、決定の記録", "決めた人のいない「決定」が無い。未決を残すなら理由", "この判断で生成されたコードを、移行後の姿とする"],
        ["3. 変換後の仕様", "plsql-migrate", "docs/*.md（何がどう変わったか、使い方、制限）", "AUTO でない routine・決定・受け入れた差を落としていない", "変わる振る舞いを、業務と呼び出し側が受け入れる"],
        ["4. テスト", "difftest/", "plsql-diff.json（指紋つき）", "flow.py gate が GATE=open", "—"],
    ], widths=[1.3, 1.4, 2.5, 2.2, 1.8], size=8, row_h=0.5),
    {"type": "so_what", "x": X0, "y": 4.15, "w": W, "h": 0.8, "size": 9.5,
     "text": "承認は中身に付く。承認した人・日付と、そのときのファイルの指紋（spec は原文も）を控える。あとで中身が変わると承認は古くなり、関門が閉じる。"},
], "承認つきの流れの図（draw.io）は docs/diagrams/plsql-conversion.drawio の 2 ページ目にあります。")

page("コマンドだけでも同じ結果になる: 段階ごとに打つもの", [
    code(X0, Y0 + 0.02, W, """P=samples/tutorial/plsql; O=out/migrate/tutorial-points; FLOW=skills/migrate-flow/scripts/flow.py
IN="--scalardb-schema $P/scalardb-schema.json --limits $P/limits.yaml"; REC="--record $P/decisions-outside-generator.yaml"
python $FLOW init --out $O --kind plsql --src $P/src $IN $REC

# 1. 現行の仕様（文章を書いたら check）
python -m plsql.cli $P/src --out-dir $O/spec-analysis --quiet
python skills/plsql-spec/scripts/spec_facts.py facts --analysis $O/spec-analysis --out-dir $O/spec
python $FLOW approve spec --out $O --by 移行責任者 --date 2026-09-20

# 2. 変換と人の判断
python -m plsql.generate $P/src $IN --out-dir $O/generated --verify-compile --limits-strict
python -m plsql.cli $P/src $IN --out-dir $O/generated/analysis --quiet
python skills/plsql-migrate/scripts/decision_items.py scan --generated $O/generated $IN $REC --write --out $O/generated/decision-items.md
python $FLOW approve decisions --out $O --by 移行責任者 --date 2026-09-20 --with-open "<未決を残して進める理由>"

# 3. 変換後の仕様
python skills/plsql-migrate/scripts/migration_doc.py facts --src $P/src --generated $O/generated --analysis $O/generated/analysis … --out-dir $O/docs
python $FLOW approve converted --out $O --by 移行責任者 --date 2026-09-20

# 4. テスト（python $FLOW gate が GATE=open を返してから）
python difftest/plsql_run.py deploy --project $P && python difftest/plsql_run.py run --project $P     # Oracle
python difftest/plsql_capture.py --project $P --namespace points --variant double                    # ScalarDB Cluster
python difftest/plsql_compare.py --project $P --variant double --json $P/work/plsql-diff.json
python $FLOW tested --out $O --result pass --report $P/work/plsql-diff.json""", "bash", 7.2),
], "スキルは、この中で文章を書き、判断を問い、承認を求めます。python は .venv/bin/python（プラグインでは <root>/bin/python）です。")

page("出力は、判定・生成物・文書・記録に分かれ、どれも原文の位置まで辿れる", [
    table(X0, Y0 + 0.02, W, ["ファイル", "誰が作る", "中身"], [
        ["<name>.scalardb.sql / .report.md / .schema.json / plans/*.plan.json", "scalardb_migrate.cli、sql-transpile", "変換後の SQL、判定と指摘、スキーマ、実行計画"],
        ["decisions.json", "plsql.cli", "routine ごとの判定・確信度 5 因子・どのルールがどのファイルで判定したか・whyNotAuto・staleEvidence"],
        ["unresolved.md", "plsql.cli", "REVIEW / REDESIGN を REDESIGN 先頭で並べ、根拠、代替案、受け入れに必要なテストを付ける"],
        ["generated/（Java）+ traceability.csv", "plsql.generate", "Service / Repository / domain。生成 Java の member → 元 PL/SQL の file:line"],
        ["spec/*.md", "plsql-spec", "現行の仕様（事実の欄 + Mermaid の図 + 動作・業務ルール・エラー・確かめたいこと）"],
        ["docs/*.md", "plsql-migrate", "変換後の仕様（アーキテクチャ・使い方・制限・どのように移行したか・何がどう変わったか）"],
        ["limits.yaml / decisions-outside-generator.yaml", "人（スキルが記録する）", "決定と、決めた人・日付・理由。原文のそばに置く（作業ディレクトリは消してよい）"],
        ["flow.yaml", "migrate-flow", "入力、3 つの承認（人・日付・指紋・未決のまま進めた理由）、テストの結果"],
    ], widths=[3.3, 1.9, 4.0], size=8.2, row_h=0.4),
], "通した結果の実物は samples/tutorial/result/ にあります。")

# =====================================================================================================
# 5. 具体例
# =====================================================================================================
section("5. 具体例", "ポイントカードのサンプルを、\nスキルで最後まで通した記録", "section-5.png")

page("題材: 会員のポイント残高と履歴。利用だけが行ロックで直列にしている", [
    code(X0, Y0 + 0.02, 5.1, """-- samples/tutorial/plsql/src/pkg_points.pkb（use_points）
PROCEDURE use_points(p_member_id IN members.member_id%TYPE,
                     p_points    IN NUMBER,
                     p_reason    IN VARCHAR2) IS
  v_balance members.balance%TYPE;
  v_seq     members.last_seq%TYPE;
  v_now     DATE := SYSDATE;
BEGIN
  IF p_points <= 0 THEN
    RAISE_APPLICATION_ERROR(-20102, '使うポイントは1以上で…');
  END IF;
  -- 同時に使われても残高がマイナスにならないよう、行をロックする
  SELECT balance, last_seq INTO v_balance, v_seq
    FROM members WHERE member_id = p_member_id FOR UPDATE;
  IF v_balance < p_points THEN
    RAISE_APPLICATION_ERROR(-20103, 'ポイントが不足しています');
  END IF;
  v_seq := v_seq + 1;
  INSERT INTO point_history (member_id, seq_no, points, reason,
    created_at) VALUES (p_member_id, v_seq, -p_points, p_reason, v_now);
  -- 使ってもランクは下げない（業務ルール）
  UPDATE members SET balance = v_balance - p_points,
         last_seq = v_seq, updated_at = v_now
   WHERE member_id = p_member_id;
END use_points;""", "sql", 6.5),
    table(5.7, Y0 + 0.02, 3.9, ["routine", "すること"], [
        ["get_balance", "残高を返す。会員なしは -20101"],
        ["add_points", "付与、履歴、ランクの付け直し（300 以上 SILVER、1000 以上 GOLD）"],
        ["use_points", "利用。不足は -20103。FOR UPDATE"],
        ["rank_of", "残高 → ランク（package 内だけ）"],
    ], widths=[1.2, 2.7], size=8.5, row_h=0.36),
    table(5.7, 2.75, 3.9, ["表", "キー（ScalarDB）"], [
        ["members", "partition: member_id、索引: rank"],
        ["point_history", "partition: member_id、clustering: seq_no"],
    ], widths=[1.3, 2.6], size=8.5, row_h=0.34),
    {"type": "lead_in", "x": 5.7, "y": 3.95, "w": 3.9, "rule": False, "size": 9.5,
     "text": "履歴の番号は sequence ではなく、会員の行の last_seq から進める。SQL のサンプルは同じ 2 表への 20 文。"},
], "サンプルは samples/tutorial/ にあります。")

page("SQL: 20 文中 13 文が変換でき、実 DB では読み取り 10 文中 9 文が一致した", [
    code(X0, Y0 + 0.02, W, """$ python difftest/run.py samples/tutorial/sql/points-check.sql --dialect oracle --fetcher jdbc --restart-cluster
PASS [4]  scalardb-sql            SELECT member_id, name, balance FROM members WHERE member_id = 1
PASS [5]  scalardb-sql            SELECT seq_no, points, reason FROM point_history WHERE member_id = 1 AND seq_no >= 10 ORDER BY seq_no DESC
PASS [6]  scalardb-sql            SELECT member_id, name FROM members WHERE rank = 'GOLD'
FAIL [7]  scalardb-sql            SELECT member_id, name, balance FROM members WHERE ROWNUM <= 3 ORDER BY balance DESC
PASS [8]  scalardb-sql            SELECT member_id, name, balance FROM members ORDER BY balance DESC FETCH FIRST 3 ROWS ONLY
PASS [9]  plan P1  fetched=1      SELECT member_id, NVL(name, '(no name)') AS name, balance FROM members WHERE member_id = 1
PASS [10] plan P8  fetched=7      SELECT m.member_id, m.name, h.seq_no, h.points FROM members m, point_history h WHERE m.member_id = h.member_id(+) …
PASS [11] scalardb-sql            SELECT rank, COUNT(*) AS members, SUM(balance) AS total FROM members GROUP BY rank
PASS [12] plan P1  fetched=4      SELECT member_id, points, SUM(points) OVER (PARTITION BY member_id ORDER BY seq_no) AS running_total …
PASS [13] plan P5  fetched=4      SELECT member_id, name, balance FROM members WHERE balance > (SELECT AVG(balance) FROM members)
PASS=9 FAIL=1 SKIP=0 CASE_ERROR=0""", "bash", 6.5),
    {"type": "cards", "x": X0, "y": 2.85, "w": W, "h": 1.95, "items": [
        ["FAIL [7] は WARN のとおり", "Oracle は「任意の 3 行を取ってから並べる」、変換後は「並べてから 3 行取る」。「残高の多い 3 人」のつもりなら、Oracle でも [8] のように書く（こちらは一致）。教材として直さずに残した"],
        ["[10] はツールの不具合を直した結果", "当初は「ScalarDB が断る」と言いながら WARN で、Cluster は DB-SQL-10067 で断った。JOIN_KEY を ERROR にし、実行計画 P8（両方を取得して H2 で結合）に回した"],
        ["ERROR の 3 文", "CREATE SEQUENCE、member_seq.NEXTVAL、SET balance = balance + 100。どれも理由と対応案つきで報告される（採番はアプリで、読む → 計算 → 書く）"],
    ], "titleSize": 10.5, "bodySize": 9},
], SRC_TUTORIAL)

page("最初の生成でツールは 3 文を断った。原因は「書き方」と「人が決めていないこと」の 2 種類", [
    code(X0, Y0 + 0.02, 4.55, """-- 最初の版（add_points）
UPDATE members
   SET balance = v_balance,
       last_seq = v_seq,
       rank = rank_of(v_balance),   -- 断られた
       updated_at = SYSDATE         -- SEM-007（2 回目）
 WHERE member_id = p_member_id;

$ python -m plsql.generate … --verify-compile
routines: 4  AUTO 2  REVIEW 1  REDESIGN 1
untranslated statements 0  SQL ScalarDB refuses 3""", "sql", 7, h=1.75),
    code(5.05, Y0 + 0.02, 4.55, """-- 直した版: ランクと現在日時を先に変数へ
v_now  DATE := SYSDATE;             -- 宣言部で 1 度だけ
…
v_rank := rank_of(v_balance);

UPDATE members
   SET balance = v_balance, last_seq = v_seq,
       rank = v_rank, updated_at = v_now
 WHERE member_id = p_member_id;
-- Oracle 上の結果は変わらない""", "sql", 7, h=1.75),
    table(X0, 2.6, W, ["断られた文", "原因", "どうしたか"], [
        ["SET rank = rank_of(v_balance)", "書き方: SQL の中で package の関数を呼んでいる。持ち上げられるのは実行時ヘルパが実装している関数だけ", "原文を直す（上）"],
        ["VALUES (…, -p_points, …)、SET balance = v_balance - p_points（use_points）", "人が決めていない: 行ロックの決定が無いあいだ、この routine の書き込みは拒否したまま", "人が決めて limits.yaml に書く（次のページ）"],
    ], widths=[3.2, 4.2, 1.8], size=8.5, row_h=0.5),
    {"type": "so_what", "x": X0, "y": 4.1, "w": W, "h": 0.85, "size": 10,
     "text": "原文を直したので、承認済みの spec は「承認が古い」になった。事実の欄を作り直し、変えた所を示して承認を取り直した（flow.py は原文の指紋も控えている）。"},
], "生成物を手で直さないのが決まりです。直すのは原文か、決定か、生成器です。")

page("行ロックは「楽観制御 + 衝突だけ再試行」に決めた。変わるのは待ち方で、残高は守られる", [
    {"type": "comparison", "x": X0, "y": Y0 + 0.02, "w": 5.0, "h": 2.0, "arrows": True, "highlight": 1, "size": 8.5, "columns": [
        ["現行（Oracle）", ["FOR UPDATE で行をロック", "後から来た利用は待たされる", "待ちに上限が無い", "残高はマイナスにならない"]],
        ["移行後（ScalarDB）", ["同じトランザクションで読んで書く", "どちらも待たずに進む", "後の commit が弾かれる（DB-CORE-20013）", "残高はマイナスにならない"]],
    ]},
    code(5.6, Y0 + 0.02, 4.0, """// 呼び出し側（CALL-5: 最大 3 回、50 ms から倍々）
int attempt = 0;
while (true) {
  try (Connection c = DriverManager.getConnection(url)) {
    c.setAutoCommit(false);
    var svc = new PkgPointsService(
        new PkgPointsRepository(c));
    try {
      svc.usePoints(memberId, points, "coupon");
      c.commit();            // 衝突はここで分かる
      return;
    } catch (MigratedException e) {   // -20102 / -20103
      c.rollback(); throw e;          // 再試行しない
    } catch (SQLTransactionRollbackException e) {
      c.rollback();
      if (++attempt >= 3) throw e;
      Thread.sleep(50L << (attempt - 1));
    }
  }
}""", "java", 6.5),
    table(X0, 2.95, 5.0, ["項目", "決定（移行責任者、2026-09-20）"], [
        ["LOCK-001", "楽観制御。limits.yaml の rowLocks.optimistic に理由つき"],
        ["CALL-5", "衝突だけ最大 3 回。番号は読み直されるので二重実行にならない"],
        ["BIZ-4", "受け入れる。現行は NOWAIT を使っておらず、業務上の意味は無い"],
        ["BIZ-5", "該当しない（MERGE は無い）。決定として記録する"],
    ], widths=[0.9, 4.1], size=8.2, row_h=0.36),
], "決定のあと、生成器の不具合を 1 件直しました。rank_of(v_balance) の引数が Long で、BigDecimal を取る method に渡せず、コンパイルできませんでした。")

page("承認済みの仕様から 13 シナリオを起こし、すべてが Oracle と一致した", [
    table(X0, Y0 + 0.02, 4.9, ["routine", "シナリオ（境界は両側）", "Oracle の結果"], [
        ["get_balance", "正常 / 会員なし", "450 / -20101"],
        ["add_points", "正常 / 残高 299 / ちょうど 300 / ちょうど 1000（理由 NULL）", "REGULAR / REGULAR / SILVER / GOLD"],
        ["add_points", "0 ポイント / 会員なし", "-20102 / -20101"],
        ["use_points", "正常 / 残高ちょうど / 残高 + 1", "ランクは SILVER のまま / 残高 0 / -20103"],
        ["use_points", "負の数 / 会員なし", "-20102 / -1403（言い換えない。現行どおり）"],
    ], widths=[1.1, 2.3, 1.5], size=8, row_h=0.42),
    code(5.5, Y0 + 0.02, 4.1, """# samples/tutorial/plsql/scenarios/add_points_to_gold.yaml
name: add_points_to_gold
unit: pkg_points
routine: add_points
pinned: { sysdate: '2026-01-15 09:30:00' }
setup:
- INSERT INTO members (…) VALUES (1, 'Sato', 'SILVER',
    450, 0, DATE '2026-01-01')
call:
  kind: procedure
  name: pkg_points.add_points
  args: { p_member_id: 1, p_points: 550, p_reason: null }
capture_tables: [members, point_history]
note: '境界: 残高ちょうど 1000 で GOLD。理由は NULL'""", "json", 6.5),
    code(X0, 3.42, W, """$ python difftest/plsql_compare.py --project samples/tutorial/plsql --variant double --json …/plsql-diff.json
13 compared: 13 identical, 0 differing; 0 not compared
$ python -m plsql.cli … --evidence …/plsql-diff.json --variant double --generated …/work/generated
verdicts        {'AUTO': 3, 'REDESIGN': 1}            # テストの前は {'REDESIGN': 1, 'REVIEW': 3}""", "bash", 7),
    terms(4.55, ["シナリオ = 固定した状態で 1 routine を呼び、観測できる結果をすべて記録する単位。golden = Oracle で取った正解の capture"],
          SRC_TUTORIAL),
], "シナリオは、承認済みの現行の仕様の『業務ルール』と『エラーと例外』の 1 行が 1 本になるように起こします。")

page("承認は 5 回。中身が変わるたびに古くなり、変えた所を示して取り直した", [
    {"type": "timeline", "x": X0, "y": Y0 + 0.1, "w": W, "items": [
        ["承認 spec", "現行の仕様。確かめたいこと 4 点"],
        ["spec が古くなる", "断られた文を直すために原文を変えた"],
        ["承認 spec（2 回目）", "変えた所を示して取り直し"],
        ["承認 decisions", "REVIEW 3 件は証拠待ちと控えて"],
        ["承認 converted", "意味が変わるのは行ロックだけ"],
        ["テスト 13 / 13", "converted が古くなる → 取り直し"],
    ]},
    code(X0, 2.3, W, """$ python skills/migrate-flow/scripts/flow.py status --out out/migrate/tutorial-points
spec        approved   by 移行責任者  2026-09-20   # 現行の仕様
decisions   approved   by 移行責任者  2026-09-20   # 人の判断
converted   approved   by 移行責任者  2026-09-20   # 変換後の仕様
test        pass                      2026-09-20
$ python skills/migrate-flow/scripts/flow.py gate --out out/migrate/tutorial-points
GATE=open""", "bash", 7.5, h=1.4),
    {"type": "cards", "x": X0, "y": 3.85, "w": 4.5, "h": 1.15, "items": [
        ["取り直しの決まり", "converted だけを取り直すかぎり、テストの結果は残る。spec か decisions を取り直すと、テストは消える（確かめた相手が変わるため）"],
    ], "titleSize": 10, "bodySize": 9},
    {"type": "so_what", "x": 5.05, "y": 3.85, "w": 4.55, "h": 1.15, "size": 9,
     "text": "承認の問いは「これを前提に次へ進んでよいですか」。見る所を 3〜7 点に絞り、推奨・理由・影響を示してから聞く。"},
], "status の表示は、幅に合わせて英語の見出しに置き換えています。")

page("実 DB で 1 回通しただけで、ツールの側の不具合が 4 件見つかった", [
    table(X0, Y0 + 0.02, W, ["見つかったもの", "どう見つかったか", "直した所"], [
        ["相手の主キーを覆わない結合を、「ScalarDB が断る」と言いながら WARN にしていた", "差分テストで Cluster が DB-SQL-10067 を返した", "scalardb_migrate/converter.py: JOIN_KEY を ERROR に。読み取りは実行計画 P8 へ（実 DB で一致）"],
        ["package 内の関数に、宣言と違う数値型の変数を渡すと、コンパイルできない Java が出た", "--verify-compile が routine を名指しして止めた（判定は AUTO だった）", "plsql/gen_java/: 兄弟の routine の NUMBER 引数には Plsql.dec(…) で渡す"],
        ["corpus の外のプロジェクトの capture に指紋が付かず、一致しても AUTO にならなかった", "13 / 13 一致なのに判定が REVIEW のまま（staleEvidence）", "difftest/plsql_capture.py --project を足した"],
        ["テストのあとも、証拠待ちだった REVIEW を未決の判断として出し続けた", "flow.py status の「未決の判断」", "skills/migrate-flow/scripts/flow.py: 証拠つきの解析が AUTO なら未決から外す"],
    ], widths=[3.4, 2.6, 3.2], size=8.2, row_h=0.55),
    {"type": "so_what", "x": X0, "y": 3.55, "w": W, "h": 1.3, "size": 9.5,
     "text": "4 件とも回帰テストつきで直し、corpus を実 DB で取り直した。",
     "points": ["corpus: AUTO 40 / REDESIGN 27、AUTO 対象は 49/49 が両規約で Oracle と一致",
                "チュートリアルの数値のうち DB の要らない分は tests/test_tutorial_sample.py が固定"]},
    source("docs/guide/tutorial.md（2026-09-20 の実測）、README の「現在地」（合成 corpus）"),
], "corpus が一度も踏んでいない形を、外から来たコードが踏む、という実例です。")

# =====================================================================================================
# まとめ
# =====================================================================================================
page("分かったことと、まだ確かめていないこと", [
    {"type": "comparison", "x": X0, "y": Y0 + 0.02, "w": W, "h": 2.95, "size": 9.5, "columns": [
        ["分かったこと", [
            "SQL は文ごとの判定で、変換・実行計画・報告に振り分けられる。WARN は読むもので、実 DB で確かめる価値がある",
            "PL/SQL を止めるのは、変換できない構文より、人が決めていないこと（行ロック、境界、採番、行数の上限）",
            "AUTO を証拠でしか付けないと、テストの前は当たったルールが無くても REVIEW になる。これは仕様",
            "承認を中身に付けると、原文や文書の変更が承認の取り直しとして必ず見える",
        ]],
        ["まだ確かめていないこと", [
            "同時実行（use_points の衝突と再試行、add_points の同時の付与）。比較が観るのは 1 回の呼び出しの結果",
            "途中で止まったとき、PL/SQL の外からの書き込み（trigger の網羅性）",
            "実案件のコード。数値は合成 corpus とサンプル上のもの",
            "移行工数。KPI-6 は計測しないと決めた——AUTO 率が上がると移行が速くなるかは分からない",
        ]],
    ]},
    table(X0, 3.95, W, ["次に読むもの", "場所"], [
        ["はじめに / チュートリアル / スキル", "docs/guide/getting-started.md、tutorial.md、skills.md"],
        ["仕組み / KPI と AUTO 禁止条件 / 人が決めること", "docs/design/architecture.md、plsql-kpi.md、docs/plsql-migration/"],
    ], widths=[3.6, 5.6], size=8.5, row_h=0.3),
], "リポジトリ: github.com/wfukatsu/sql-migration。文書の入口は docs/README.md です。")

slides.append({"layout": "CLOSING"})


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "out" / "slides" / "sql-migration-guide" / "deck.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = {"title": TITLE, "density": "print", "defaults": {"textFit": "shrink", "group": True}, "slides": slides}
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(slides)} slides -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
