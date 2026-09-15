#!/usr/bin/env python3
"""変換ツールの新旧比較（アプリ側分析の導入前後、21 枚）— slide-forge の code-first デッキ。

数値の出典は docs/app-side-benchmark-comparison.md（計測データ: out/bench-compare/*/bench.json、2026-09-15）。

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

TITLE = "変換ツールの新旧比較: アプリ側分析の導入前後"
TEMPLATE = json.load(open(os.path.join(SF, "templates", "blank-16x9.json"), encoding="utf-8"))

SRC = "出典: 本検証（2026-09-15）。ScalarDB Cluster 3.19.1 + PostgreSQL 16、Oracle Database 23ai Free、15 回の p50"
SRC_EMP = "出典: 本検証、emp 20,000 行・ウォームアップ 3 回 + 15 回の p50。旧方式 = 2825858、新方式 = main"


# =====================================================================
# 表紙・要約
# =====================================================================

plain(layout="COVER",
      title="変換ツールの新旧比較",
      subtitle="アプリ側分析の導入前後で、変換結果・性能・正しさがどう変わったか\n"
               "2026年9月15日 ／ ScalarDB Cluster 3.19.1・Oracle Database 23ai Free",
      notes="SQL を ScalarDB 向けに変換するツールを改修しました。改修前（旧方式）と改修後（新方式）を、同じ環境・同じデータで比べた結果です。")


@slide("日付条件の押し下げで 8〜12 倍速くなり、失敗は変換時に分かる",
       note="状況・課題・答えの順に 1 枚でまとめています。数値は jdbc 取得の p50 です。")
def s_exec(d):
    d.exec_summary(
        X0, DY0, W, 3.34,
        "ScalarDB SQL に収まらない SELECT を、旧方式は「ScalarDB から取得 + H2 で実行」の計画に回していた",
        "WITH の中の条件を取得に渡せず表全体を読み、H2 に無い構文（CONNECT BY など）は実行時に失敗していた",
        "新方式は日付条件を取得に押し下げ、H2 で動かない文は変換時に判定してアプリ側 Java に回す",
        points=["日付範囲の文: 566 ms → 47 ms、567 ms → 69 ms",
                "失敗していた 2 文: 変換時に判定。うち 1 文は Java で Oracle と一致",
                "既存の 15 文: 変換結果も性能も新旧で同じ"],
        size=9.5)


# =====================================================================
# 1. 方式の違い
# =====================================================================

plain(layout="SECTION", title="1. 方式の違い", body="ScalarDB SQL に収まらない SELECT をどう扱うか",
      notes="旧方式と新方式の処理の流れと、新方式で加わったものを説明します。")


@slide("旧方式は、収まらない SELECT をすべて実行計画にした",
       note="実行計画は、ScalarDB から行を取得し、インメモリの H2（Oracle 互換モード）で元の SQL を実行する方式です。")
def s_old(d):
    a = d.box(X0, 1.75, 1.45, 0.8, "移行元の\nOracle SQL", size=10, bold=True)
    b = d.solid(2.25, 1.75, 1.6, 0.8, "変換ツール\n（旧方式）", size=10)
    c1 = d.box(4.25, 0.95, 2.05, 0.7, "ScalarDB SQL\n文法に収まる文", size=9.5)
    c2 = d.box(4.25, 2.6, 2.05, 0.7, "実行計画\n収まらない SELECT すべて", size=9.5)
    k1 = d.solid(6.7, 0.95, 2.8, 0.7, "ScalarDB Cluster で実行", size=9.5, fill=d.P.primaryDark)
    k2 = d.box(6.7, 2.6, 2.8, 0.7, "ScalarDB から取得\n→ H2 で元の SQL を実行", size=9.5)
    d.connect(a, b)
    d.connect(b, c1, category="BENT")
    d.connect(b, c2, category="BENT")
    d.connect(c1, k1)
    d.connect(c2, k2)
    pw = (W - 0.3) / 2
    for i, text in enumerate(("問題 1: WITH の中の DATE '…' を取得に渡せず、表全体を読む",
                              "問題 2: H2 に無い構文（CONNECT BY、ROLLUP など）でも計画を作り、実行時に失敗")):
        d.shape(X0 + i * (pw + 0.3), 3.5, pw, 0.62, kind="RECTANGLE", fill=lighten(d.P.danger, 0.86), stroke=None,
                text=text, size=9, color=darken(d.P.danger, 0.3), align="START")
    foot(d, None, edition="旧方式: コミット 2825858。H2 = 実行計画で使うインメモリ SQL エンジン（Oracle 互換モード）")


@slide("新方式は文全体を調べ、計画・Java・設計変更に振り分ける",
       note="どの経路になっても、アプリ側で守るべき意味の差、設計の提案、取得コストの見積もりを文ごとに報告します。")
def s_new(d):
    a = d.box(X0, 2.0, 1.35, 0.8, "移行元の\nOracle SQL", size=10, bold=True)
    b = d.solid(2.1, 2.0, 1.75, 0.8, "変換ツール（新方式）\n文全体を分析", size=9.5)
    rows = (("ScalarDB SQL\n文法に収まる文", "ScalarDB Cluster で実行", None),
            ("実行計画\n取得 → H2", "WITH の中の日付条件も取得に渡す", None),
            ("アプリ側 Java\nH2 で動かない構文", "補助クラスで Oracle の動きを再現し、\nOracle の結果と突き合わせる", "hi"),
            ("設計の見直し\nキーで取得できない", "集計表・参照用の表を提案する\n（Cassandra の FULL_SCAN）", None))
    for i, (box_text, desc, tone) in enumerate(rows):
        y = 0.92 + i * 0.84
        if tone:
            o = d.solid(4.25, y, 2.2, 0.66, box_text, size=9, fill=d.P.success)
        else:
            o = d.box(4.25, y, 2.2, 0.66, box_text, size=9)
        d.connect(b, o, category="BENT")
        d.label(6.7, y, 2.8, 0.66, desc, size=9, color=d.P.text, valign="MIDDLE")
    foot(d, None, edition="新方式: main（7f28da6）。scalardb_migrate/appside.py と runtime-java の com.scalar.migrate.appside")


@slide("新方式では判定の変更 4 つと、アプリ側の道具 3 つが加わった",
       note="上の 4 行が変換の判定の変更、下の 3 行がアプリ側で実装するときの支援です。")
def s_features(d):
    d.table(X0, DY0, W, ["観点", "旧方式", "新方式"], [
        ["変換できない理由", "最初の 1 つだけ", "CTE・サブクエリの中まで全部挙げる"],
        ["H2 に無い構文", "計画を作り、実行時に失敗", "計画を作らず RESIDUAL_H2 で報告"],
        ["WITH の中の DATE リテラル", "取得に渡さず表全体を読む", "取得する SQL に押し下げる"],
        ["Cassandra でキーが無い表", "最初の 1 表で打ち切り", "全表を挙げ、キーで読む方法を提案"],
        ["アプリ側の注意", "なし", "0 除算・丸め・NULL と文字列の並び順などを警告"],
        ["設計とコスト", "なし", "集計表の提案、取得コストの見積もり、推奨設定"],
        ["実装と検証", "なし", "Java の補助クラス、Oracle との golden 比較"],
    ], col_widths=[2.0, 2.5, 4.5], row_h=0.4, header_h=0.36, size=9.5, aligns=["START", "START", "START"])
    foot(d, None, edition="新方式の分析は、変換ツールの CLI とスキル（sql-transpile）の両方のレポートに出る")


@slide("WITH の中の日付条件を、取得する SQL に押し下げた",
       note="旧方式は DATE '2020-06-01' をリテラルとして扱えず、条件の無い取得になっていました。H2 は元の SQL を実行するので、結果はどちらも同じです。")
def s_pushdown(d):
    d.code_block(X0, DY0, W, 0.86,
                 "WITH hired AS (SELECT empno, deptno, sal FROM emp\n"
                 "  WHERE hiredate >= DATE '2020-06-01' AND hiredate < DATE '2021-01-01')\n"
                 "SELECT deptno, empno, sal, RANK() OVER (PARTITION BY deptno ORDER BY sal DESC) AS rnk\n"
                 "  FROM hired ORDER BY deptno, rnk, empno",
                 lang="bash", size=8)
    cw = (W - 0.3) / 2
    rx = X0 + cw + 0.3
    y = DY0 + 1.0
    d.label(X0, y, cw, 0.26, "旧方式の取得: 表全体 20,018 行", size=10, bold=True, color=d.P.danger)
    d.label(rx, y, cw, 0.26, "新方式の取得: 範囲内の 1,409 行", size=10, bold=True, color=darken(d.P.success, 0.3))
    y += 0.32
    d.code_block(X0, y, cw, 0.9, "SELECT empno, sal, deptno, hiredate\n  FROM emp", lang="bash", size=8)
    d.code_block(rx, y, cw, 0.9,
                 "SELECT empno, sal, deptno, hiredate\n  FROM emp\n WHERE hiredate >= '2020-06-01'\n"
                 "   AND hiredate <  '2021-01-01'", lang="bash", size=8)
    y += 1.02
    d.metric(X0, y, cw, 0.84, "566 ms", "旧方式の ScalarDB 側 p50（jdbc）", color=d.P.danger)
    d.metric(rx, y, cw, 0.84, "47 ms", "新方式の ScalarDB 側 p50（jdbc）", color=d.P.success)
    foot(d, None, edition=SRC_EMP)


@slide("H2 で動かない構文の文は、計画を作らずアプリ側に回す",
       note="いずれも以前の調査（docs/oracle-sql-report.md）で H2 に無いと確認した構文です。新方式はアプリ側での対応も併せて報告します。")
def s_h2(d):
    b = d.table(X0, DY0, W, ["構文", "旧方式", "新方式の判定", "アプリ側での対応"], [
        ["CONNECT BY / SYS_CONNECT_BY_PATH", "計画 → 実行時に失敗", "RESIDUAL_H2・HIERARCHICAL", "木をたどる（Hierarchy）"],
        ["ROLLUP / CUBE / GROUPING SETS", "計画 → 実行時に失敗", "RESIDUAL_H2・GROUP", "集約レベルごとに UNION ALL"],
        ["PIVOT / UNPIVOT", "計画 → 実行時に失敗", "RESIDUAL_H2・PIVOT", "条件付き集約 / UNION ALL"],
        ["KEEP (DENSE_RANK FIRST)", "計画 → 実行時に失敗", "RESIDUAL_H2・KEEP", "ROW_NUMBER() = 1 の行"],
    ], col_widths=[2.6, 1.8, 2.2, 2.4], row_h=0.5, header_h=0.36, size=9.5,
        aligns=["START", "START", "START", "START"])
    banner(d, b + 0.25, "H2 で動く構文（分析関数・再帰 WITH・NVL・副問合せなど）は、従来どおり実行計画で動かす", size=9)
    foot(d, None, edition="Oracle 固有機能の読み取り 62 文のうち 8 文がこれらの構文で、旧方式では実行時に失敗していた")


# =====================================================================
# 2. 比較の方法と変換結果
# =====================================================================

plain(layout="SECTION", title="2. 比較の方法と変換結果", body="同じ条件で新旧を並べる",
      notes="測り方と、変換の段階での違いです。")


@slide("同じ環境とデータで、旧方式 → 新方式の順に 3 ケースを測った",
       note="ケースごとにデータを 1 回だけ投入し、旧方式、新方式の順に同じ条件で測りました。毎回 Oracle の結果と突き合わせています。")
def s_method(d):
    b = d.flow(X0, DY0, W, 0.66, ["同じ SQL を\n新旧で変換", "データを\n1 回投入", "旧方式で計測\n（15 回の p50）",
                                  "新方式で計測\n（15 回の p50）", "Oracle と結果を\n突き合わせ"], size=9)
    b = d.table(X0, b + 0.28, W, ["ケース", "データ", "内容"], [
        ["bench.sql", "emp 20,000 行", "既存の 15 文（点アクセス〜全表走査）"],
        ["bench-appside.sql", "emp 20,000 行", "扱いが変わる 3 文（WITH の日付範囲 2・ROLLUP 1）"],
        ["bench-area-sales.sql", "売上 40,000 行（2026 年分 29,426 行）", "エリア別月次売上分析（CONNECT BY ほか）"],
    ], col_widths=[1.9, 2.8, 4.3], row_h=0.44, header_h=0.36, size=9.5, aligns=["START", "START", "START"])
    banner(d, b + 0.25, "実行計画の取得は jdbc（ScalarDB SQL）と core（Core API）の両方で測った", size=9)
    foot(d, None,
         edition="Apple M3 Pro、Docker（VM 8 GiB）、ScalarDB Cluster 3.19.1（SERIALIZABLE、scan_fetch_size 10）+ PostgreSQL 16")


@slide("変換結果が変わったのは 4 文で、既存の 15 文は新旧で同じだった",
       note="既存のベンチマーク 15 文は、状態も取得 SQL も新旧で同じでした。")
def s_convert(d):
    b = d.table(X0, DY0, W, ["文", "旧方式", "新方式", "変わった点"], [
        ["既存の 15 文（bench.sql）", "OK 8・WARN 6・PLANNED 5", "同じ", "なし"],
        ["WITH の日付範囲 + ウィンドウ関数", "PLANNED（条件なしで取得）", "PLANNED（範囲で取得）", "取得に日付条件"],
        ["WITH の日付範囲 + 月次集計", "PLANNED（条件なしで取得）", "PLANNED（範囲で取得）", "取得に日付条件"],
        ["GROUP BY ROLLUP", "PLANNED", "ERROR（RESIDUAL_H2）", "計画を作らない"],
        ["エリア別月次売上分析", "PLANNED（2 表を全件取得）", "ERROR（6 種類を列挙）", "アプリ側 Java へ"],
    ], col_widths=[2.6, 2.3, 2.2, 1.9], row_h=0.46, header_h=0.36, size=9.5,
        aligns=["START", "START", "START", "START"])
    banner(d, b + 0.25, "エリア別月次売上分析では CTE・HIERARCHICAL・WINDOW・PROJECTION・GROUP・RESIDUAL_H2 を一度に報告した",
           size=9)
    foot(d, None, edition="出典: 新旧の変換ツールで同じケースファイルを変換（out/bench-compare/convert-old・convert-new）")


# =====================================================================
# 3. ベンチマーク結果
# =====================================================================

plain(layout="SECTION", title="3. ベンチマーク結果", body="速さ・失敗の有無・正しさ",
      notes="扱いが変わった文、失敗していた文、既存の文の順に見ます。")


@slide("日付範囲の文は取得行数が 1/8〜1/14 になり、6〜12 倍速い",
       note="時間のほとんどは ScalarDB からの取得で、H2 での残りの処理は 2〜15 ms でした。Oracle 直接は 2〜5 ms です。")
def s_speed(d):
    d.vbars_grouped(X0, DY0, 5.75, 3.42, ["ウィンドウ jdbc", "月次集計 jdbc", "ウィンドウ core", "月次集計 core"],
                    [("旧方式", [566.4, 566.9, 641.9, 625.3]), ("新方式", [46.9, 69.2, 62.4, 98.1])], unit="ms")
    d.metric(6.55, DY0 + 0.05, 2.95, 1.05, "20,018 → 1,409", "取得行数（ウィンドウ関数の文）", color=d.P.success,
             value_size=18)
    d.metric(6.55, DY0 + 1.3, 2.95, 1.05, "6〜12 倍", "ScalarDB 側 p50（jdbc・core）", color=d.P.success)
    d.label(6.55, DY0 + 2.55, 2.95, 0.8, "4 つとも Oracle と一致\n月次集計の文は 20,018 → 2,430 行", size=9,
            color=d.P.muted)
    foot(d, None, edition=SRC_EMP)


@slide("旧方式で実行時に失敗した 2 文を、新方式は変換時に判定した",
       note="旧方式の失敗は、ScalarDB から取得したあと H2 で実行して初めて分かります。新方式は変換レポートの段階で分かります。")
def s_fail(d):
    d.comparison(X0, DY0, W, 1.9, [
        ("旧方式: 計画を作り、実行時に失敗", ["ROLLUP: Function \"ROLLUP\" not found",
                                            "エリア別分析: Function \"SYS_CONNECT_BY_PATH\" not found",
                                            "失敗は取得のあと H2 で初めて分かる"]),
        ("新方式: 変換時に理由つきで判定", ["ROLLUP: RESIDUAL_H2（集約レベルごとに UNION ALL）",
                                          "エリア別分析: 6 種類の構文を列挙し Java に回す",
                                          "計画を作らないので、実行時の失敗は起きない"]),
    ], highlight=1, size=9.5)
    mw = (W - 0.3) / 2
    d.metric(X0, DY0 + 2.1, mw, 0.9, "失敗 2 文", "旧方式（jdbc・core とも）", color=d.P.danger)
    d.metric(X0 + mw + 0.3, DY0 + 2.1, mw, 0.9, "失敗 0 文", "新方式で実行時に失敗した文", color=d.P.success)
    foot(d, None, edition="出典: 本検証、bench-appside.sql の ROLLUP と bench-area-sales.sql")


@slide("エリア別分析は Java で Oracle と 2,400 行すべて一致した",
       note="Oracle の実データの結果と、値まで 1 行ずつ比べました。単体テストの手計算の期待値に加えて、実結果で正しさを確かめたのはこれが初めてです。")
def s_area(d):
    cw = (W - 0.44) / 3
    for i, (val, cap, col) in enumerate((("2,400 / 2,400", "Oracle と値まで一致した行", d.P.success),
                                         ("814 ms", "新方式の p50（p95 1,001 ms）", d.P.primary),
                                         ("14 ms", "Oracle 直接の p50（58 倍の差）", d.P.muted))):
        d.metric(X0 + i * (cw + 0.22), DY0, cw, 1.05, val, cap, color=col)
    b = d.flow(X0, DY0 + 1.3, W, 0.66, ["ScalarDB SQL で\n2 表を取得", "Hierarchy で\n階層と経路",
                                        "店舗×月の\n合計", "Windows で\nLAG・移動平均", "DENSE_RANK と\n並べ替え"], size=9)
    banner(d, b + 0.3, "旧方式では実行できなかった SQL を、新方式の補助クラスで書き、Oracle と同じ結果を返せることを確認した",
           size=9, h=0.5)
    foot(d, None, edition="出典: 本検証、bench-area-sales.sql（組織 214 行・売上 40,000 行）、com.scalar.migrate.examples.AreaSalesReport")


@slide("814 ms の大半は取得で、Java の処理は 36 ms だった",
       note="内訳は最終回の計測です。p50 の 814 ms には、取得・コミット・Java の処理がすべて入っています。")
def s_breakdown(d):
    b = d.hbars(X0, DY0, W, [
        ("ScalarDB からの取得とコミット（29,640 行）", 637.1, "637 ms"),
        ("Java での処理（階層・集計・分析・並べ替え）", 36.5, "36.5 ms"),
        ("参考: Oracle 直接の p50", 14.0, "14.0 ms"),
    ], row_h=0.46, gap=0.22, label_w=3.7, value_w=1.2, colors=[d.P.danger, d.P.success, d.P.muted])
    d.cards(X0, b + 0.3, W, DY1 - (b + 0.3), [
        ("取得 1 行あたり", "約 22 µs\n（29,640 行で 637 ms）"),
        ("Java の処理", "40 ms 未満\n表が大きくなっても小さい"),
        ("速くするには", "読む行数を減らす\n（月次集計表など）"),
    ], title_size=10.5, body_size=9)


@slide("既存 15 文は新旧で差がなく、読み取り専用の効果も見えない",
       note="既存の 15 文はコードが同じなので、差は測定のばらつきです。Core API 経由の実行計画は一貫して 1〜6 % 短かったものの、同じコードの文でも ±20 % ばらついています。")
def s_same(d):
    b = d.table(X0, DY0, W, ["経路（取得方式）", "文の数", "新/旧（p50 の比）", "読み方"], [
        ["ScalarDB SQL（jdbc）", "10", "0.44〜1.17", "コードは同じ。先に測った旧方式の暖まり不足"],
        ["実行計画（jdbc）", "5", "0.88〜1.06", "差なし"],
        ["ScalarDB SQL（core の回）", "10", "0.78〜1.20", "同じコードでも ±20 % ばらつく"],
        ["実行計画（core、読み取り専用）", "5", "0.94〜0.99", "1〜6 % 短いが、ばらつきの範囲"],
    ], col_widths=[2.6, 0.9, 1.7, 3.8], row_h=0.5, header_h=0.36, size=9.5,
        aligns=["START", "CENTER", "CENTER", "START"])
    banner(d, b + 0.25, "30 組すべて Oracle と一致。読み取り専用トランザクションの効果は、順序を入れ替えて複数回測らないと分からない",
           tone="warn", size=9, h=0.5)
    foot(d, None, edition=SRC_EMP)


# =====================================================================
# 4. 考察
# =====================================================================

plain(layout="SECTION", title="4. 考察", body="結果から何が言えるか",
      notes="速さを決めるもの、見積もりの確からしさ、次にやることです。")


@slide("速さを決めるのは経路ではなく、ScalarDB から読む行数",
       note="ScalarDB SQL・実行計画・アプリ側 Java のどれでも、取得行数が 1 万行を超えると 0.5 秒を超えました。")
def s_rows(d):
    plan, java = d.P.primary, d.P.success
    b = d.hbars(X0, DY0, W, [
        ("計画: インデックス 500 行", 17.5, "18 ms"),
        ("計画: 日付範囲 1,409 行（新方式）", 46.9, "47 ms"),
        ("計画: 日付範囲 2,430 行（新方式）", 69.2, "69 ms"),
        ("計画: 全表 DISTINCT 20,018 行", 528.1, "528 ms"),
        ("計画: 全表 20,018 行（旧方式）", 566.4, "566 ms"),
        ("Java: エリア別分析 29,640 行", 813.6, "814 ms"),
    ], row_h=0.3, gap=0.1, label_w=3.9, value_w=1.0, colors=[plan, plan, plan, plan, plan, java])
    legend(d, X0, b + 0.06, W, [(plan, "実行計画（取得 + H2）"), (java, "アプリ側 Java（取得 + Java）")], size=8.5)
    banner(d, b + 0.42, "経路を替えても速くならない。取得を 1 万行未満に抑える設計（条件の押し下げ、集計表）が効く", size=9)
    foot(d, None, edition="出典: 本検証、jdbc 取得の p50。行数は ScalarDB から取得した行数")


@slide("コストの見積もりは実測の 1.5〜1.8 倍で、上限側の目安になる",
       note="変換ツールの見積もりは 1 行 25 µs、SERIALIZABLE では再読み込みを見込んで 2 倍にしています。実測は 1 行 20〜28 µs で、再読み込みの負担は 2 倍より小さいようです。")
def s_estimate(d):
    d.vbars_grouped(X0, DY0, 5.9, 3.42, ["1,409 行", "2,430 行", "20,018 行", "29,640 行"],
                    [("見積もり", [70, 122, 1001, 1482]), ("実測 p50", [47, 69, 566, 814])], unit="ms")
    d.metric(6.7, DY0 + 0.05, 2.8, 1.1, "1.5〜1.8 倍", "見積もり ÷ 実測（p50）", color=d.P.warning)
    d.label(6.7, DY0 + 1.4, 2.8, 1.9,
            "見積もり = 取得行数 × 25 µs × 2\n--expected-rows で出す値\n\n本番の性能を約束する値ではなく、\n行数上限や 60 秒の期限に\n近づくかの判断に使う",
            size=9, color=d.P.text)
    foot(d, None, edition=SRC)


@slide("正しさは確保できた。次は読む行数を減らす設計と追加計測",
       note="新方式でアプリ側 Java に回した文も Oracle と一致しました。性能は読む行数で決まるので、設計の見直しと、今回測れていない条件の計測に進みます。")
def s_next(d):
    pw = (W - 0.3) / 2
    zone(d, X0, DY0, pw, 3.38, "次にやること", fill="#F8FAFC", stroke=lighten(d.P.primary, 0.6))
    checklist(d, X0 + 0.15, DY0 + 0.45, pw - 0.3, [
        ("月次集計表で読む行数を減らし、エリア別分析を再計測", "todo"),
        ("読み取り専用を、順序を替えて複数回計測", "todo"),
        ("scan_fetch_size 1000・SNAPSHOT で再計測", "todo"),
        ("見積もりの係数を実測に合わせて見直す", "todo"),
    ], row_h=0.58, gap=0.12, size=9)
    rx = X0 + pw + 0.3
    zone(d, rx, DY0, pw, 3.38, "この計測で分からないこと", fill="#FFFBEB", stroke=lighten(d.P.warning, 0.4))
    checklist(d, rx + 0.15, DY0 + 0.45, pw - 0.3, [
        ("各条件 1 回、旧方式 → 新方式の順で計測", "warn"),
        ("単一クライアント。同時実行は未評価", "warn"),
        ("最大 4 万行。本番規模は未評価", "warn"),
        ("バックエンドは PostgreSQL のみ", "warn"),
    ], row_h=0.58, gap=0.12, size=9)
