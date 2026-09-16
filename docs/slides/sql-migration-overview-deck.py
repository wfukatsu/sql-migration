#!/usr/bin/env python3
"""SQL → ScalarDB SQL 移行ツール 詳細解説 — slide-forge の code-first デッキ。

数値は docs/ のレポート（test-report / oracle-sql-report / transpile-fix-research / dml-benchmark-report /
dml-followup-research / scalardb-backend-comparison）から転記した（2026-09-10〜15 の計測、v0.1.1 時点）。
仕組みの図つき文書版は docs/architecture.md。

    SF=~/.claude/plugins/cache/slide-forge/slide-forge/1.30.0
    cd $SF && .venv/bin/python scripts/validate_layout.py <このファイル> --template templates/blank-16x9.json
    cd $SF && .venv/bin/python scripts/render_deck.py <このファイル> --title "…" --folder <Drive フォルダ ID>
"""
from __future__ import annotations

import json
import os
import sys

SF = os.environ.get("SLIDE_FORGE_ROOT",
                    os.path.expanduser("~/.claude/plugins/cache/slide-forge/slide-forge/1.30.0"))
sys.path.insert(0, os.path.join(SF, "scripts"))

from deckkit import *  # noqa: E402,F403

TITLE = "SQL → ScalarDB SQL 移行ツール 詳細解説"
TEMPLATE = json.load(open(os.path.join(SF, "templates", "blank-16x9.json"), encoding="utf-8"))
ENV = "Apple M3 Pro 上の Docker、ScalarDB Cluster 3.19.1（1 ノード、SERIALIZABLE）、単一クライアント"


# =====================================================================
# 表紙・要約
# =====================================================================

plain(layout="COVER",
      title="SQL → ScalarDB SQL\n移行ツールの仕組みと検証結果",
      subtitle="Oracle・PostgreSQL・MySQL の SQL を ScalarDB に移行する PoC（v0.1.1）\n"
               "2026年9月15日 ／ github.com/wfukatsu/sql-migration（MIT License）",
      notes="SQLGlot で SQL を解析し、ScalarDB SQL への変換、実行計画への分解、実データでの検証までを行うツールの仕組みと、これまでの検証結果をまとめます。")


@slide("既存の SQL を 3 つの経路で ScalarDB に載せて確かめる",
       note="状況・課題・答えの順に 1 枚でまとめています。数値の詳細は第 3 章です。")
def s_exec(d):
    d.exec_summary(
        X0, DY0, W, 3.34,
        "Oracle・PostgreSQL・MySQL の SQL を ScalarDB に移行したいが、ScalarDB SQL の文法は絞られている",
        "副問合せ・結合・式・採番などは ScalarDB SQL にできず、手で書き換えると結果が変わりやすい",
        "変換できる文は変換し、読み取りは取得 + H2 の実行計画にし、残りは理由と対応案を付けて報告する",
        points=["差分テスト: ScalarDB SQL 経路で PostgreSQL 15/15・Oracle 17/17 が一致",
                "DML テスト SQL 51 文: ScalarDB で実行できるのは 31〜33 文",
                "3 表結合: H2 の索引で 26〜28 秒 → 1.7〜1.9 秒"],
        size=9.5)


@slide("変換ツール・実行基盤・スキル・検証基盤の 4 つで構成する",
       note="変換ツールはビルド時、実行基盤は実行時、検証基盤は検証時に使います。スキルは変換ツールを同梱して単体で動きます。")
def s_parts(d):
    layers(d, X0, DY0, W, [
        ("変換ツール", "scalardb_migrate（Python・SQLGlot）\n文ごとの変換と判定、スキーマ変換、アクセスパス分析、実行計画への分解", d.P.primary),
        ("実行基盤", "runtime-java（Java 17・H2・ScalarDB）\n実行計画の実行（取得 → H2）、アプリ側処理の補助クラス、ベンチマーク", d.P.success),
        ("スキル", "skills/sql-transpile（Claude Code）\n32 方言どうし、または ScalarDB SQL への変換。変換ツールを同梱し単体で動く", d.P.info),
        ("検証基盤", "difftest（Docker Compose・Python）\n移行元 DB と ScalarDB で同じ文を実行し、結果と応答時間を比べる", d.P.warning),
    ], row_h=0.74, gap=0.12, label_w=1.6)
    foot(d, None, edition="仕組みの図は docs/architecture.md（Mermaid の図 22 枚）")


# =====================================================================
# 1. 何を解決するか
# =====================================================================

plain(layout="SECTION", title="1. 何を解決するか", body="ScalarDB SQL の制約と、移行の 3 つの経路",
      notes="ScalarDB SQL の文法がなぜ絞られているかと、それに対してこのツールが取る方針です。")


@slide("ScalarDB SQL は、確実に振り分けられる構文だけに絞られている",
       note="ScalarDB は複数のストレージをまたいだトランザクションを実現します。任意の副問合せや結合を許すと、どのストレージにどの処理を振るかが決まらなくなるため、文法が絞られています。")
def s_grammar(d):
    d.comparison(X0, DY0, W, 2.55, [
        ("ScalarDB SQL で書けること", ["主キー・パーティションキー・インデックスで絞る読み書き",
                                    "列と集約関数（COUNT / SUM / AVG / MIN / MAX）の射影",
                                    "キーを覆う結合、UPSERT、LIMIT、DNF / CNF の WHERE"]),
        ("書けないこと", ["射影や WHERE の式・関数、CASE、DISTINCT、OFFSET",
                         "副問合せ、CTE、UNION、ウィンドウ関数",
                         "列を参照する更新（qty = qty - 5）、採番、ビュー・トリガー"]),
    ], highlight=1, size=9.5)
    banner(d, DY0 + 2.8, "移行元の SQL の多くは、そのままでは ScalarDB SQL にならない。どこまで変換でき、残りをどう動かすかが課題", size=9, h=0.5)
    foot(d, None, edition="出典: ScalarDB SQL の文法（scalardb.scalar-labs.com）、skills/sql-transpile/references/scalardb-grammar.md")


@slide("移行する 1 文は、3 つの経路のどれかで ScalarDB 上で動く",
       note="経路 1 は変換後の文をそのまま実行、経路 2 は ScalarDB から取得して H2 で元の SQL を実行、経路 3 はアプリケーションで書き直します。")
def s_paths(d):
    d.flow(X0, DY0, W, 0.62, ["移行元の 1 文", "ScalarDB SQL の\n文法に収まるか", "読み取りか・H2 で\n実行できるか"], size=10)
    d.cards(X0, DY0 + 0.95, W, 2.2, [
        ("経路 1: ScalarDB SQL", "変換後の文を\nそのまま実行する\n判定 OK / WARN\n例: 主キーの読み書き、UPSERT"),
        ("経路 2: 実行計画", "ScalarDB から取得し、H2 で\n元の SQL を実行する\n判定 PLANNED\n例: 結合・副問合せ・集約"),
        ("経路 3: アプリで実装", "理由と対応案を報告し、\nアプリで書き直す\n判定 ERROR\n例: CONNECT BY、列を参照する更新"),
    ], title_size=11, body_size=9.5)
    foot(d, None, edition="経路 2 は読み取り専用の文だけ。H2 が実行できない構文（CONNECT BY、ROLLUP、PIVOT など）は経路 3")


# =====================================================================
# 2. 仕組み
# =====================================================================

plain(layout="SECTION", title="2. 仕組み", body="変換・実行計画・実行基盤・スキル・検証基盤",
      notes="変換ツールが 1 文をどう処理し、実行基盤が実行計画をどう動かすかを説明します。")


@slide("SQL を変換ツールで振り分け、実行基盤が ScalarDB から取得する",
       note="変換ツールは ScalarDB SQL・実行計画・レポートを出します。実行基盤は実行計画に従って ScalarDB から取得し、H2 で元の SQL を実行します。ScalarDB のバックエンドには ScalarDB 以外は接続しません。")
def s_system(d):
    src = d.box(X0, 1.94, 1.5, 0.62, "移行元の SQL\nOracle / PG / MySQL", size=8.5)
    conv = d.solid(2.3, 1.94, 1.6, 0.62, "変換ツール\nscalardb_migrate", size=9, color="#FFFFFF")
    out_sql = d.box(4.3, 0.95, 1.75, 0.62, "ScalarDB SQL\n+ スキーマ JSON", size=8.5)
    out_plan = d.box(4.3, 1.94, 1.75, 0.62, "実行計画\n.plan.json", size=8.5)
    out_rep = d.box(4.3, 2.93, 1.75, 0.62, "レポート\n判定・理由・対応案", size=8.5)
    rt = d.solid(6.45, 1.94, 1.3, 0.62, "residual-runner\n取得 → H2", size=8.5, color="#FFFFFF", fill=d.P.success)
    cl = d.solid(8.1, 1.94, 1.4, 0.62, "ScalarDB\nCluster", size=9, color="#FFFFFF", fill=d.P.info)
    be = d.box(8.1, 3.0, 1.4, 0.7, "バックエンド DB\nPG / Oracle / Cassandra", size=7.5)
    for a, b in ((src, conv), (conv, out_sql), (conv, out_plan), (conv, out_rep), (out_plan, rt), (rt, cl), (cl, be)):
        d.connect(a, b, color=d.P.primary)
    d.connect(out_sql, cl, color=d.P.primary, category="BENT", start_site=3, end_site=0)
    foot(d, None, edition="ScalarDB SQL の経路には ScalarDB Cluster のライセンスが要る。Core API 経路（取得のみ）は不要")


@slide("Python の変換ツールと Java の実行基盤を、計画 JSON でつなぐ",
       note="主要なモジュールの責務です。スキルは変換ツールの 7 モジュールを同梱コピーとして持ち、vendor_sync.py で同期します。")
def s_components(d):
    d.table(X0, DY0, W, ["モジュール", "責務", "入力 → 出力"], [
        ["converter.py", "文の種類ごとの書き換え、制約の検査、アクセスパス分析、判定", "SQL の 1 文 → 判定・変換後 SQL・指摘"],
        ["dialect.py", "ScalarDB SQL の文法だけを生成する SQLGlot 方言", "構文木 → ScalarDB SQL（文法外は例外）"],
        ["types.py / schema.py", "型の対応と精度の警告 / キーとインデックスの管理", "DDL・Schema Loader JSON → 表定義"],
        ["decomposer.py", "読み取り文を取得と H2 で実行する SQL に分ける", "構文木 → 実行計画 JSON"],
        ["appside.py", "アプリ側に移す構文・意味の注意・設計の提案・コスト", "構文木 → 指摘"],
        ["Runner / Fetcher", "計画の実行、ScalarDB からの取得（Core API / JDBC）", "plan.json → 結果 JSON"],
        ["Residual", "H2 への投入、索引（任意）、元の SQL の実行", "行 + SQL → 結果"],
        ["difftest/*.py", "差分テスト、ベンチマーク、スキルの実行検証", "ケース SQL → 比較結果・レポート"],
    ], col_widths=[1.8, 4.2, 3.0], row_h=0.36, header_h=0.34, size=8.5, aligns=["START", "START", "START"])
    foot(d, None, edition="scalardb_migrate/（Python 7 モジュール）、runtime-java/（Java）、difftest/（ハーネス）")


@slide("1 文を解析し、書き換え、厳格に生成してから判定する",
       note="生成側の方言が ScalarDB SQL の文法に無い構文で例外を出すので、変換漏れが「黙って通る SQL」にならず、ERROR として見えます。")
def s_pipeline(d):
    d.flow(X0, DY0, W, 0.66, ["SQLGlot で\n解析", "前処理\n(+) → JOIN など", "文の種類ごとに\n書き換え", "ScalarDB 方言で\n厳格に生成", "アクセスパス\n分析と判定"], size=9, gap=0.26)
    kv_rows(d, X0, DY0 + 1.0, W, [
        ("解析", "移行元の方言で構文木にする。コメントや文字列の中の ; で文を誤って分けない"),
        ("前処理", "Oracle の外部結合 (+) を LEFT JOIN に、REPLACE INTO を UPSERT に、識別子を正規化"),
        ("書き換え", "WHERE の DNF / CNF 正規化、IN の展開、ROWNUM → LIMIT、型の対応、制約の削除"),
        ("厳格な生成", "文法に無い構文（副問合せ・CASE・関数・OFFSET）は例外にして ERROR にする"),
        ("判定", "OK / WARN。ERROR の読み取り文は実行計画に分解し、できれば PLANNED"),
    ], key_w=1.5, row_h=0.4, gap=0.1, size=9)
    foot(d, None, edition="scalardb_migrate/converter.py（書き換えと判定）、dialect.py（生成側の方言）")


@slide("判定は 4 種類で、次にやることが決まる",
       note="変換率は OK と WARN の割合です。PLANNED は変換率に含めず、件数を別に伝えます。")
def s_status(d):
    d.table(X0, DY0, W, ["判定", "意味", "次にやること"], [
        ["OK", "ScalarDB SQL に変換できた。INFO は自動で直したことの記録", "そのまま使う"],
        ["WARN", "変換できたが、意味や性能に注意がある（全表走査・精度の損失など）", "指摘を確認する"],
        ["PLANNED", "ScalarDB SQL にはできないが、実行計画（取得 → H2）で動かせる", "読む行数と応答時間を確認する"],
        ["ERROR", "自動では移行できない。アプリ側に移す処理と対応案を列挙する", "対応案に沿ってアプリで実装する"],
    ], col_widths=[1.3, 5.0, 2.7], row_h=0.56, header_h=0.36, size=9.5, aligns=["CENTER", "START", "START"])
    foot(d, None, edition="出力: <name>.scalardb.sql、<name>.report.md / .json、<name>.schema.json、<plan-dir>/<name>.<n>.plan.json")


@slide("機械的に直せる構文は、構文木の上で書き換える",
       note="文字列置換ではなく構文木で書き換えるので、コメントや文字列リテラル、入れ子の条件を正しく扱えます。")
def s_rules(d):
    d.table(X0, DY0, W, ["移行元の構文", "ScalarDB SQL", "注意"], [
        ["IN (a, b, c) / NOT IN", "col = a OR col = b ... / col <> a AND ...", "—"],
        ["任意の AND / OR のネスト", "DNF か CNF の短いほうに正規化", "—"],
        ["ROWNUM <= n / FETCH FIRST n ROWS", "LIMIT n", "ORDER BY との順序"],
        ["FROM a, b WHERE a.x = b.y / (+)", "INNER JOIN / LEFT JOIN", "(+) の付き方で不可"],
        ["ON CONFLICT / ON DUPLICATE KEY / MERGE", "UPSERT INTO", "意味の差を WARN"],
        ["NUMBER(p) / NUMBER(p, s) / VARCHAR2(n)", "INT・BIGINT / DOUBLE / TEXT", "DOUBLE は精度の WARN"],
        ["複合主キー", "先頭列がパーティションキー、残りがクラスタリングキー", "--keys で変更"],
        ["NOT NULL / DEFAULT / UNIQUE / CHECK", "削除する", "アプリ側で担保"],
    ], col_widths=[3.3, 3.6, 2.1], row_h=0.36, header_h=0.34, size=8.5, aligns=["START", "START", "START"])
    foot(d, None, edition="全ルール: skills/sql-transpile/references/scalardb-grammar.md")


@slide("表定義からアクセスパスを判定し、全表走査を警告する",
       note="入力の CREATE TABLE か既存の Schema Loader JSON から表定義が分かると、各文を 4 つのアクセスパスに分けます。")
def s_access(d):
    d.table(X0, DY0, W, ["アクセスパス", "条件", "判定", "取得コストの目安"], [
        ["GET", "主キーの全列を等値で指定", "問題なし", "キー指定 約 5 ms"],
        ["パーティション SCAN", "パーティションキーを指定（クラスタリングキーの範囲）", "問題なし", "キーあたりの行数に比例"],
        ["インデックス SCAN", "セカンダリインデックスの列を指定", "問題なし", "該当行数に比例"],
        ["クロスパーティション SCAN", "キーを覆う条件が無い", "WARN", "1 行 約 25 µs × 表の行数"],
    ], col_widths=[2.0, 3.6, 1.2, 2.2], row_h=0.5, header_h=0.36, size=9, aligns=["START", "START", "CENTER", "START"])
    banner(d, DY0 + 2.55, "--storage cassandra ではクロスパーティション走査を使わず、「どの表のキーで読むか」を提案する（FULL_SCAN）", size=9, h=0.5)
    foot(d, None, edition="コストの目安は docs/bench-report.md の実測。SERIALIZABLE ではスキャンを約 2 倍にして見積もる")


@slide("読み取りは絞って取得し、H2 で元の SQL を実行する",
       note="取得は表ごとに、ScalarDB で評価できる条件と文が使う列だけを読みます。H2 は移行元の方言の互換モードで、元の SQL をそのまま実行します。")
def s_plan(d):
    orig = d.box(X0, 1.35, 2.6, 1.6, "元の SQL\n\nSELECT c.region, SUM(...)\nFROM customers c\nJOIN orders o ...\nJOIN order_items i ...\nGROUP BY c.region", size=8)
    f1 = d.box(3.6, 1.0, 2.9, 0.62, "customers\nSELECT customer_id, region", size=8)
    f2 = d.box(3.6, 1.83, 2.9, 0.62, "orders（条件を押し下げ）\nWHERE status <> 'CANCELLED'", size=8)
    f3 = d.box(3.6, 2.66, 2.9, 0.62, "order_items\nSELECT order_id, qty, unit_price", size=8)
    h2 = d.solid(7.2, 1.35, 2.3, 1.6, "H2（メモリ上）\n\n行を表として投入\n索引（任意）\n元の SQL を実行", size=9, color="#FFFFFF", fill=d.P.success)
    for f in (f1, f2, f3):
        d.connect(orig, f, color=d.P.primary)
        d.connect(f, h2, color=d.P.primary)
    d.label(3.6, 3.4, 2.9, 0.3, "取得: ScalarDB SQL / Core API", size=8.5, color=d.P.muted)
    foot(d, None, edition="H2 は MODE=Oracle / PostgreSQL / MySQL。H2 に無い Oracle 関数は OracleFunctions で補う")


@slide("実行計画は JSON で、取得・H2 の SQL・上限・推奨設定を持つ",
       note="S05（明細の無い注文を探す反結合）の計画の抜粋です。pattern は ScalarDB SQL に収まらなかった理由の分類です。")
def s_plan_json(d):
    d.code_block(X0, DY0, 5.2, 3.4, (
        '{"pattern": "P8",\n'
        ' "fetch": [\n'
        '  {"table": "orders",\n'
        '   "scalardb_sql": "SELECT order_id FROM orders",\n'
        '   "access_path": "CROSS_PARTITION",\n'
        '   "max_rows": 10000,\n'
        '   "index_columns": [["order_id"]]},\n'
        '  {"table": "order_items", ...}],\n'
        ' "residual": {"java": {"engine": "h2",\n'
        '   "mode": "Oracle", "build_indexes": false,\n'
        '   "sql": "SELECT o.order_id FROM orders o\n'
        '     LEFT JOIN order_items i ON ... "}},\n'
        ' "guardrails": {"row_limit": 10000},\n'
        ' "transaction": {"read_only": true}}'), lang="json", size=8)
    kv_rows(d, 5.95, DY0, 3.55, [
        ("fetch", "表ごとの取得の SQL と列"),
        ("index_columns", "H2 に作る索引の列"),
        ("residual", "H2 で実行する元の SQL"),
        ("guardrails", "1 表あたりの行数上限"),
        ("transaction", "読み取り専用で取得"),
        ("recommended_config", "scan_fetch_size など"),
    ], key_w=1.45, row_h=0.46, gap=0.1, size=8.5)
    foot(d, None, edition="pattern: P1 射影の式、P3 DISTINCT、P4 OFFSET、P5 副問合せ、P6 CTE・集合演算、P7 集約、P8 結合 など")


@slide("計画の取得は 1 トランザクションで行い、H2 は要求ごとに捨てる",
       note="1 つの計画の取得をすべて 1 つの読み取り専用トランザクションで行うので、表ごとの結果が同じ時点のデータになります。")
def s_runtime(d):
    pipeline(d, X0, DY0, W, ["H2 を作る\n（互換モード）", "読み取り専用\ntx を開始", "表ごとに\n取得", "H2 に投入", "索引\n（任意）", "元の SQL を\n実行", "COMMIT・\nH2 を破棄"],
             h=0.8, gap=0.2, highlight=(2, 3), highlight_note="1 トランザクションの中", size=8.5)
    d.table(X0, DY0 + 1.5, W, ["取得の実装", "経路", "ライセンス", "用途"], [
        ["CoreFetcher", "ScalarDB Core API（Scan、読み取り専用トランザクション）", "不要", "開発・差分テスト"],
        ["JdbcFetcher", "ScalarDB SQL の JDBC（ScalarDB Cluster）", "要", "本番想定の経路・ベンチマーク"],
    ], col_widths=[1.6, 4.2, 1.2, 2.0], row_h=0.44, header_h=0.34, size=9, aligns=["START", "START", "CENTER", "START"])
    foot(d, None, edition="取得の行数が max_rows を超えると止める（RowLimitExceededException）。ログは標準エラーに WARN 以上")


@slide("H2 の索引は既定でオフにし、大きな表を結合するバッチ処理で使う",
       note="計画には常に索引の列を出し、索引を作るかはオプションで決めます。変換時の --h2-indexes か、実行時の residual-runner run --h2-indexes で有効にします。")
def s_h2index(d):
    compare_panels(d, X0, DY0, W, 3.2,
                   {"title": "索引なし（既定）", "tone": "info", "head": "小さな要求・1 表だけの文",
                    "items": ["構築の時間とメモリがかからない", "結合は入れ子ループの総当たり", "2 万注文の 3 表結合: 26〜28 秒"],
                    "note": "オンライン処理の既定"},
                   {"title": "索引あり（--h2-indexes）", "tone": "good", "head": "数万行以上の表を結合する",
                    "items": ["主キーと結合・相関・IN の列に索引", "結合が索引の参照になる", "同じ 3 表結合: 1.7〜1.9 秒"],
                    "note": "1 表の文では構築の分だけ遅くなる"})
    foot(d, None, edition="出典: docs/dml-benchmark-report.md 3.4、docs/dml-followup-research.md 3 章")


@slide("変換できない読み取り文には、アプリ側に移す処理をすべて挙げる",
       note="最初に見つけた 1 つで止めず、CTE の本体や副問合せの中まで調べます。手で書き換えるときに結果を変えないための注意も付けます。")
def s_appside(d):
    d.table(X0, DY0, W, ["指摘コード", "内容"], [
        ["CTE / SUBQUERY / WINDOW / HIERARCHICAL", "アプリ側で処理する構文。どの CTE・副問合せの中かも示す"],
        ["PROJECTION / AGG / GROUP / PRED / ORDER", "射影・集約・GROUP BY・WHERE・ORDER BY の式や関数"],
        ["RESIDUAL_H2", "H2 でも実行できない構文（CONNECT BY、ROLLUP、PIVOT、KEEP）。計画にしない"],
        ["APP_SEMANTICS", "結果を変えないための注意（0 除算、ROUND の丸め方、NULL の並び順、LAG は暦の前月ではない）"],
        ["DESIGN", "集計表、階層の事前計算、結合列のキー・インデックス、ScalarDB Analytics の提案"],
        ["COST / ROW_LIMIT / COST_DEADLINE", "取得コストの見積もりと、行数上限・gRPC 期限（60 秒）の超過"],
        ["CONFIG / FULL_SCAN", "推奨設定（読み取り専用、scan_fetch_size）/ Cassandra でキーが無い表"],
    ], col_widths=[3.3, 5.7], row_h=0.42, header_h=0.34, size=8.5, aligns=["START", "START"])
    foot(d, None, edition="scalardb_migrate/appside.py。レポートの「アプリ側に移す処理」節に文ごとにまとまる")


@slide("アプリ側で書き直す処理は、Oracle の正解と DB なしで比べる",
       note="Oracle の動きを再現する補助クラスで実装し、Oracle で一度だけ取った正解（入力の表と結果）と、DB なしで比べます。")
def s_golden(d):
    kv_rows(d, X0, DY0, 4.2, [
        ("1. capture", "Oracle で準備と問合せを実行し、\n入力の表と結果を golden.json に保存"),
        ("2. 実装", "AppSideQuery を実装し、\n補助クラスで Oracle の動きを再現"),
        ("3. check", "golden.json の表を実装に渡し、\n結果を比べる（DB 不要）"),
    ], key_w=1.1, row_h=0.8, gap=0.2, size=9)
    d.table(5.0, DY0, 4.5, ["補助クラス", "再現する Oracle の動き"], [
        ["Hierarchy", "CONNECT BY、LEVEL、経路、循環"],
        ["Windows", "LAG / LEAD、移動平均、RANK"],
        ["OracleNumbers", "0 除算、0 から遠いほうへ丸め"],
        ["OracleOrdering", "NULL の位置、BINARY の文字列順"],
        ["OracleDates", "ADD_MONTHS の月末、TO_CHAR"],
    ], col_widths=[1.5, 3.0], row_h=0.46, header_h=0.34, size=8.5, aligns=["START", "START"])
    foot(d, None, edition="difftest/golden.py、runtime-java の com.scalar.migrate.appside。例: examples/AreaSalesReport")


@slide("変換できない書き込みは、読む → 計算 → キーで書く に直す",
       note="書き込みは読み取りと違って実行計画にしていません。原因ごとに、1 トランザクションの中での直し方が決まります。")
def s_writes(d):
    d.table(X0, DY0, W, ["原因", "例", "指摘", "直し方"], [
        ["列を参照する式", "SET qty = qty - 5、日付の加算", "RMW", "読む → 計算 → リテラルで書く"],
        ["副問合せ・結合", "UPDATE ... FROM、INSERT ... SELECT", "SUBQUERY", "対象のキーを読んでからキーで書く"],
        ["値をアプリで作る", "採番、シーケンス、現在時刻、DEFAULT", "SEQUENCE / NOW", "アプリで値を作ってバインド"],
        ["存在で分岐する", "ON CONFLICT DO NOTHING、INSERT IGNORE", "DO_NOTHING", "主キーで読み、無ければ INSERT"],
        ["結果を返す", "DELETE ... RETURNING", "RETURNING", "同じ tx で SELECT → DELETE"],
        ["部分的な取り消し", "SAVEPOINT", "STATEMENT", "トランザクションの組み立てを変える"],
    ], col_widths=[1.7, 3.2, 1.5, 2.6], row_h=0.44, header_h=0.34, size=8.5, aligns=["START", "START", "START", "START"])
    foot(d, None, edition="読んだ行を書くと Consensus Commit が競合を検出するので、更新は失われない")


@slide("sql-transpile スキルは 32 方言に対応し、Sonnet で動く",
       note="素の sqlglot.transpile が黙って通す構文を直すか報告します。変換はスクリプトが行い、モデルは実行と報告だけなので、スキルのターンは Sonnet で動かします。")
def s_skill(d):
    d.flow(X0, DY0, W, 0.62, ["解析", "変換元の\n検査", "前処理", "生成", "変換先の\n検査", "往復検証"], size=9, gap=0.22)
    kv_rows(d, X0, DY0 + 0.95, W, [
        ("直す構文", "ROWNUM、Oracle の外部結合 (+)、再帰 CTE、FROM dual、整数除算、日付の引き算、INTERVAL"),
        ("関数の判定", "PostgreSQL・Oracle・MySQL・DuckDB の実際の組み込み関数一覧（catalogs/）と照合"),
        ("判定の正しさ", "実データでの検証 321 件: OK 判定の正しさ 80.4% → 100%、見逃し 48 → 0"),
        ("モデル", "model: sonnet、effort: medium。書き換えや Java 実装の依頼はセッションのモデルで受ける"),
        ("単体で動く", "変換ツールの 7 モジュールを同梱。vendor_sync.py --check で本体との差分を確認"),
    ], key_w=1.5, row_h=0.42, gap=0.1, size=9)
    foot(d, None, edition="skills/sql-transpile/SKILL.md。判定の正しさは docs/transpile-fix-research.md 8 章")


@slide("移行元 DB と ScalarDB で同じ文を実行し、結果を突き合わせる",
       note="ハーネスだけが移行元 DB に接続し、ScalarDB のバックエンド DB には ScalarDB 以外は接続しません。")
def s_difftest(d):
    d.table(X0, DY0, W, ["ハーネス", "何を確かめるか", "結果の文書"], [
        ["run.py", "移行元 DB と ScalarDB（変換後 SQL / 実行計画）の結果集合の差分", "test-report.md"],
        ["bench.py", "Oracle 直接実行と ScalarDB の互換性・応答時間", "bench-report.md"],
        ["bench_dml.py", "DML テスト SQL（3 方言 × 51 文）の変換とベンチマーク", "dml-benchmark-report.md"],
        ["transpile_verify.py", "スキルの判定が、実際の DB での動作と合うか", "transpile-fix-research.md"],
        ["backend_compare.sh", "バックエンドを PostgreSQL / Oracle / Cassandra にしたときの比較", "scalardb-backend-comparison.md"],
        ["golden.py", "アプリ側で書き直した問合せと、Oracle の正解の比較", "—"],
        ["experiments/run.sh", "並列取得・H2 の索引・書き込み計画のコスト", "dml-followup-research.md"],
    ], col_widths=[1.9, 4.8, 2.3], row_h=0.4, header_h=0.34, size=8.5, aligns=["START", "START", "START"])
    foot(d, None, edition="Docker Compose: 移行元 PostgreSQL 16 / Oracle 23ai Free、ScalarDB Cluster 3.19.1、バックエンド PostgreSQL / Cassandra 5.0 / Oracle")


@slide("接続情報は環境変数で受け取り、本番の DB には書き込まない",
       note="v0.1.1 で追加しました。プロファイルには値ではなく環境変数の名前を書くので、コミットできます。environment は必須です。")
def s_profiles(d):
    d.code_block(X0, DY0, 4.3, 2.3, (
        '{"product": "oracle",\n'
        ' "environment": "production",\n'
        ' "host_env": "PROD_ORACLE_HOST",\n'
        ' "port_env": "PROD_ORACLE_PORT",\n'
        ' "user_env": "PROD_ORACLE_USER",\n'
        ' "password_env": "PROD_ORACLE_PASSWORD",\n'
        ' "database_env": "PROD_ORACLE_SERVICE"}'), lang="json", size=8.5)
    d.table(5.1, DY0, 4.4, ["environment", "表の作成・投入", "読み取りだけ"], [
        ["local / dev / test / ci", "実行する", "実行する"],
        ["production など", "接続前に拒否", "--allow-production"],
        ["未指定", "拒否", "拒否"],
    ], col_widths=[1.7, 1.35, 1.35], row_h=0.5, header_h=0.34, size=8.5, aligns=["START", "CENTER", "CENTER"])
    banner(d, DY0 + 2.6, "本番の移行元から正解を一度だけ取る: golden.py capture --no-setup --allow-production（読み取り専用トランザクション）", size=9, h=0.5)
    foot(d, None, edition="difftest/sources.py、difftest/conf/sources/。ベンチマークの spec ファイルにはパスワードを書かない")


# =====================================================================
# 3. 検証結果
# =====================================================================

plain(layout="SECTION", title="3. 検証結果", body="互換性・性能・索引・並列取得・バックエンド",
      notes="2026年9月10日〜15日に、同じ PC の Docker 上で計測した結果です。")


@slide("変換後の SQL と実行計画は、移行元と同じ結果を返した",
       note="差分テストは結果集合を値まで比べます。Oracle 固有 SQL の 11 文は、H2 で実行できない構文などで一致しませんでした。")
def s_compat(d):
    stats(d, X0, DY0, W, [
        ("15 / 15", "差分テスト PostgreSQL\n（ScalarDB SQL 経路）", "good"),
        ("17 / 17", "差分テスト Oracle\n（ScalarDB SQL 経路）", "good"),
        ("51 / 62", "Oracle 固有 SQL の読み取り\n（変換 7・計画 44）", "info"),
        ("100%", "スキルの OK 判定の正しさ\n（258 / 258）", "good"),
    ], h=1.25, value_size=22)
    d.table(X0, DY0 + 1.6, W, ["検証", "対象", "結果"], [
        ["差分テスト", "PostgreSQL 15 文・Oracle 17 文", "Core API 経路でも実行計画は全件一致（PASS 12 / 10）"],
        ["Oracle 固有 SQL", "読み取り 62 文・書き込み 17 文", "読み取り 51 文が一致。書き込みは分類のみ"],
        ["スキルの実行検証", "3 方言 × 変換先 3 = 321 件", "見逃し 48 → 0、変換率 85.7%"],
    ], col_widths=[1.8, 3.0, 4.2], row_h=0.44, header_h=0.34, size=9, aligns=["START", "START", "START"])
    foot(d, None, edition="出典: docs/test-report.md、oracle-sql-report.md、transpile-fix-research.md")


@slide("DML 51 文のうち、ScalarDB で実行できるのは 31〜33 文",
       note="INSERT・UPDATE・DELETE・SELECT を中心にした 3 方言のテスト SQL です。SELECT はすべて実行でき、10〜11 文は実行計画です。")
def s_dml_conv(d):
    d.vbars_stacked(X0, DY0, 5.7, 3.42, ["Oracle", "PostgreSQL", "MySQL"],
                    [("OK", [8, 10, 11]), ("WARN", [14, 11, 11]), ("PLANNED", [10, 11, 11]), ("ERROR", [19, 19, 18])],
                    unit="文", values=True, colors=[d.P.success, d.P.warning, d.P.primary, d.P.danger])
    d.metric(6.55, DY0 + 0.05, 2.95, 1.05, "17〜18 / 33", "変換できない書き込み", color=d.P.danger)
    d.metric(6.55, DY0 + 1.3, 2.95, 1.05, "17 / 17", "実行できる SELECT", color=d.P.success)
    d.label(6.55, DY0 + 2.55, 2.95, 0.8, "変換できない書き込みは\n現在の値・別の表・採番に頼る文", size=9, color=d.P.muted)
    foot(d, None, edition="出典: docs/dml-benchmark-report.md 2 章（skills/sql-transpile/examples/dml/）")


@slide("時間は経路で決まり、実行計画は読む行数に比例する",
       note="ScalarDB 側の p50 の中央値です。倍率は変換元 DB に直接実行した場合との比で、方言による差は主に変換元 DB の速さの差です。")
def s_perf(d):
    stats(d, X0, DY0, W, [
        ("3〜6 ms", "書き込み（COMMIT 込み）\n変換元の 7〜20 倍", "good"),
        ("4〜6 ms", "キーで絞る読み取り\n変換元の 6〜13 倍", "good"),
        ("0.6 s 前後", "実行計画の読み取り（中央値）\n注文 2 万行を読む", "warn"),
    ], h=1.2, value_size=22)
    d.table(X0, DY0 + 1.55, W, ["バックエンド（40,000 行）", "点読み", "1 行の書き込み", "表全体を読む処理"], [
        ["ScalarDB + Oracle", "4 ms", "3 ms", "実行できる（1.0〜1.6 秒）"],
        ["ScalarDB + Cassandra", "17 ms", "24 ms", "実行できない（キー設計が要る）"],
    ], col_widths=[2.6, 1.4, 1.8, 3.2], row_h=0.46, header_h=0.34, size=9, aligns=["START", "CENTER", "CENTER", "START"])
    foot(d, None, edition="出典: dml-benchmark-report.md、scalardb-backend-comparison.md（1 ノード・単一クライアント）")


@slide("H2 に索引を作ると、3 表結合は 14〜15 倍速くなった",
       note="S04 は注文 2 万・明細 5 万行を取得して H2 で結合します。索引なしでは H2 の総当たりが 23〜28 秒を占めていました。")
def s_h2_effect(d):
    d.vbars_grouped(X0, DY0, 5.9, 3.42, ["Oracle", "PostgreSQL", "MySQL"],
                    [("索引なし", [26300, 26091, 27211]), ("索引あり", [1858, 1725, 1821])], unit="ms")
    d.metric(6.7, DY0 + 0.05, 2.8, 1.1, "26–27→1.7–1.9 秒", "S04 の p50（3 方言の範囲）", color=d.P.success, value_size=13)
    d.label(6.7, DY0 + 1.4, 2.8, 1.9, "索引ありの内訳（取得 + H2）\nOracle: 1,520 + 153 ms\nPostgreSQL: 1,514 + 181 ms\n"
            "MySQL: 1,558 + 239 ms\n\n残る時間のほとんどは\nScalarDB からの取得", size=9, color=d.P.text)
    foot(d, None, edition="出典: docs/dml-benchmark-report.md 3.4。S05（反結合）は 16〜17 倍、1 表だけ読む文は変わらない")


@slide("並列取得は 1.2〜1.3 倍、scan_fetch_size の変更は 1.5〜2.4 倍",
       note="ScalarDB のトランザクションはスレッドセーフでないので、並列にすると取得ごとに別トランザクションになり、1 つのスナップショットで読めなくなります。")
def s_parallel(d):
    d.vbars_grouped(X0, DY0, 6.0, 3.42, ["明細だけ", "3 表を順に", "3 表を並列", "8 分割で並列"],
                    [("fetch size 10", [1264, 1207, 1024, 823]), ("fetch size 1000", [649, 801, 647, 795])], unit="ms")
    d.label(6.8, DY0 + 0.1, 2.7, 3.2,
            "キーごとの取得（100 キー）\n順に 108 ms → 8 並列 49 ms\n（2.2 倍）\n\n"
            "表の並列取得は伸びない\n1 ノードの Cluster が律速\n\n"
            "推奨: まず scan_fetch_size を\n1000 にする（整合性は変わらない）", size=9, color=d.P.text)
    foot(d, None, edition="出典: docs/dml-followup-research.md 2 章（注文 1.5 万・明細 5 万行、1 ノード）")


@slide("H2 の索引は、1 表の文では構築時間とメモリの分だけ遅くなる",
       note="取得を含まない、H2 だけの計測です。結合では総当たりを避ける効果がはるかに大きい一方、1 表の文には純粋な追加になります。")
def s_h2_cost(d):
    d.table(X0, DY0, W, ["注文 / 明細", "1 表の集約（索引なし → あり）", "3 表結合（索引あり）", "H2 のメモリ（行 + 索引）"], [
        ["5,000 / 9,767", "29 → 34 ms", "39 ms（なしは 1,086 ms）", "6.4 MB + 1.2 MB"],
        ["20,000 / 40,378", "29 → 35 ms", "147 ms", "8.8 MB + 4.8 MB"],
        ["100,000 / 201,050", "166 → 182 ms（+10%）", "877 ms", "42 MB + 24 MB"],
        ["500,000 / 1,000,842", "893 → 1,064 ms（+19%）", "5,426 ms（構築が 1/3）", "208 MB + 118 MB"],
    ], col_widths=[2.0, 2.6, 2.3, 2.1], row_h=0.5, header_h=0.36, size=9, aligns=["START", "CENTER", "CENTER", "CENTER"])
    banner(d, DY0 + 2.65, "対策: 結合の無い計画では作らない、小さな表には作らない、作れなかった索引を stats に出す（索引は v0.1.1 で既定オフ）", size=9, h=0.5)
    foot(d, None, edition="出典: docs/dml-followup-research.md 3 章（difftest/experiments/H2Index.java）")


@slide("Oracle なら移行は小さく、Cassandra ではキー設計が要る",
       note="NoSQL 適性ケース 30 文のうち、どちらの構成でも速く表サイズに依らないのは同じ 17 文でした。分かれるのは表全体を読む文です。")
def s_backend(d):
    d.table(X0, DY0, W, ["観点", "ScalarDB + Oracle", "ScalarDB + Cassandra"], [
        ["互換性", "oracle.sql 17 / 17、固有機能 51 / 62（PostgreSQL と同じ）", "実行できた文は一致。固有機能 62 文中 47 文は実行できない"],
        ["キーで絞る読み書き", "点読み 4 ms・書き込み 3 ms（Oracle 直接の 3〜4 倍）", "点読み 17 ms・書き込み 24 ms（約 20 倍）"],
        ["表全体を読む処理", "実行できる。全行を読む集計は 1.0〜1.6 秒", "実行できない（設計変更が必要）"],
        ["移行の手間", "小さい。実行計画で吸収、キー設計はそのまま", "大きい。キー設計・集計表・Analytics が要る"],
    ], col_widths=[1.7, 3.65, 3.65], row_h=0.56, header_h=0.36, size=8.5, aligns=["START", "START", "START"])
    foot(d, None, edition="出典: docs/scalardb-backend-comparison.md（40,000 行、単一クライアント・単一ノード）")


# =====================================================================
# 4. 課題と今後
# =====================================================================

plain(layout="SECTION", title="4. 課題と今後", body="変換できない文・既知の問題・次の一手",
      notes="変換できない文への対応案、残っている既知の問題、次にやることです。")


@slide("変換できない書き込みの大半は、書き込み計画で自動化できる",
       note="読み取りの実行計画を書き込みに広げる案です。対象の行を読み、H2 で主キーと新しい値を計算し、キー指定で書きます。まだ実装していません。")
def s_write_plan(d):
    d.table(X0, DY0, W, ["分類", "文", "対応", "自動化"], [
        ["A. 変換ツールで直す", "INSERT ALL・DEFAULT・現在時刻・RETURNING（4 文）", "ScalarDB SQL の文に書き換える", "可能"],
        ["B. 書き込み計画", "列を参照する更新、副問合せ・結合など（14 文）", "読む → H2 で計算 → キーで書く", "実行基盤の拡張"],
        ["C. 設計が要る", "IDENTITY・シーケンス、SAVEPOINT", "UUID・ブロック採番、tx の組み立て", "不可"],
    ], col_widths=[1.9, 3.6, 2.4, 1.1], row_h=0.56, header_h=0.36, size=8.5, aligns=["START", "START", "START", "CENTER"])
    stats(d, X0, DY0 + 2.3, W, [
        ("16 → 36 ms", "1 行の更新（読み取りが加わる）", "good"),
        ("406 ms", "100 行を読んでキーで更新", "warn"),
        ("1.7 s", "約 1,000 行（分割が要る）", "bad"),
    ], h=0.95, value_size=18)
    foot(d, None, edition="出典: docs/dml-followup-research.md 1 章（ScalarDB Cluster で実測）")


@slide("残る既知の問題は、変換ツールで直せるものが多い",
       note="いずれも実データでの検証で見つかった問題です。変換ツールの判定が OK / WARN なのに、ScalarDB や H2 で失敗するか結果が変わります。")
def s_known(d):
    d.table(X0, DY0, W, ["問題", "起きること", "対処"], [
        ["別名 month が H2 の予約語", "S11 の実行計画が 3 方言とも構文エラー", "予約語の別名を引用符で囲む"],
        ["NULLS LAST が変換で落ちる", "ScalarDB で NULL が先頭に来る", "NULLS を含む ORDER BY は計画に回す"],
        ["PostgreSQL の DATE '...' / TIMESTAMP '...'", "ScalarDB で構文エラー（OK 判定）", "文字列リテラルに書き換える"],
        ["MySQL の TRUE / FALSE（TINYINT(1)）", "INT 列への真偽値で型エラー", "1 / 0 に書き換える"],
        ["MySQL の照合順序", "大文字小文字の違いで 0 行になる", "警告し、LOWER() で比べる"],
        ["PostgreSQL の INTERVAL '1 day'", "H2 が解析できない", "H2 向けに書き換える"],
    ], col_widths=[3.1, 3.1, 2.8], row_h=0.44, header_h=0.34, size=8.5, aligns=["START", "START", "START"])
    foot(d, None, edition="出典: docs/dml-benchmark-report.md 4 章、issue #3 の検証（H2 の更新前後で同じ）")


@slide("次は scan_fetch_size の再計測と、書き込み計画の試作",
       note="優先順位は docs/dml-followup-research.md 4 章に基づきます。計測の限界も合わせて示します。")
def s_next(d):
    pw = (W - 0.3) / 2
    zone(d, X0, DY0, pw, 3.38, "次にやること", fill="#F8FAFC", stroke=lighten(d.P.primary, 0.6))
    checklist(d, X0 + 0.15, DY0 + 0.45, pw - 0.3, [
        ("scan_fetch_size 1000 でベンチマークを再計測", "todo"),
        ("1 表の計画では索引を作らない・結果を出す", "todo"),
        ("既知の問題の変換を直す（予約語・リテラル）", "todo"),
        ("書き込み計画を U04・U06・D06 で試作", "todo"),
        ("キーごとの取得の並列化を計画で指定", "todo"),
    ], row_h=0.44, gap=0.08, size=9)
    rx = X0 + pw + 0.3
    zone(d, rx, DY0, pw, 3.38, "この検証で分からないこと", fill="#FFFBEB", stroke=lighten(d.P.warning, 0.4))
    checklist(d, rx + 0.15, DY0 + 0.45, pw - 0.3, [
        ("単一クライアント。同時実行は未評価", "warn"),
        ("ScalarDB Cluster は 1 ノードだけ", "warn"),
        ("書き込みは小さいデータで計測", "warn"),
        ("本番規模（数百万行）は未計測", "warn"),
        ("各条件 1 回の計測", "warn"),
    ], row_h=0.44, gap=0.08, size=9)


@slide("v0.1.1 を GitHub（MIT License）と GitLab で公開している",
       note="GitHub の issue で報告された 4 件を v0.1.1 で修正しました。依存するソフトウェアのライセンスは README の「ライセンス」節にあります。")
def s_release(d):
    d.table(X0, DY0, W, ["版", "内容"], [
        ["v0.1.0", "最初のリリース。変換ツール、実行基盤、sql-transpile スキル、検証基盤、H2 の索引オプション、文書"],
        ["v0.1.1", "#1 集約の指摘の文言、#2 接続情報のプロファイルと本番の拒否、#3 H2 2.5.250・Gson 2.14.0、#4 SLF4J のログ"],
    ], col_widths=[1.2, 7.8], row_h=0.62, header_h=0.36, size=9, aligns=["CENTER", "START"])
    d.cards(X0, DY0 + 1.75, W, 1.5, [
        ("リポジトリ", "github.com/wfukatsu/sql-migration\nMIT License（Wataru Fukatsu）"),
        ("文書", "README.md（使い方）\ndocs/architecture.md（仕組みの図）"),
        ("前提", "ScalarDB SQL の経路は\nCluster のライセンスが要る\nバックエンドには直接接続しない"),
    ], title_size=10.5, body_size=8.5)
    foot(d, None, edition="テスト: pytest 343 件、runtime-java 39 件")
