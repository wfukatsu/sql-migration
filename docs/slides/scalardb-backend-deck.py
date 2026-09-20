#!/usr/bin/env python3
"""Oracle → ScalarDB + Oracle / ScalarDB + Cassandra 検証結果（27 枚）— slide-forge の code-first デッキ。

数値の出典は docs/reports/scalardb-backend-comparison.md と docs/reports/cassandra-verification-report.md
（計測データ: out/cassandra-verify/*/bench.json、2026-09-11）。

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

TITLE = "Oracle → ScalarDB 移行検証: Oracle 版と Cassandra 版"
TEMPLATE = json.load(open(os.path.join(SF, "templates", "blank-16x9.json"), encoding="utf-8"))

SRC = "出典: 本検証（2026-09-11）。ScalarDB Cluster 3.19.1、Oracle Database 23ai Free、Apache Cassandra 5.0.9"
SRC40 = "出典: 本検証、nosql-patterns.sql・注文 40,000 行・15 回の p50（中央値）"


def verdict_colors(d):
    return {"向く": (lighten(d.P.success, 0.78), darken(d.P.success, 0.45)),
            "条件付き": (lighten(d.P.warning, 0.70), darken(d.P.warning, 0.55)),
            "全件走査": (lighten(d.P.danger, 0.84), darken(d.P.danger, 0.25)),
            "取得不可": ("#E5E7EB", d.P.text)}


def cyl(d, x, y, w, h, name, sub=None):
    """DB cylinder that connectors can attach to (deckkit.db() returns no object id)."""
    s = d.shape(x, y, w, h, kind="CAN", fill="#FFFFFF", stroke=d.P.muted, stroke_weight=1.0)
    d.label(x - 0.3, y + h + 0.03, w + 0.6, 0.2, name, size=8.5, align="CENTER", color=d.P.text)
    if sub:
        d.label(x - 0.3, y + h + 0.22, w + 0.6, 0.2, sub, size=7.5, align="CENTER", color=d.P.muted)
    return s


# =====================================================================
# 表紙・要約
# =====================================================================

plain(layout="COVER",
      title="Oracle → ScalarDB 移行検証",
      subtitle="ScalarDB のバックエンドを Oracle と Cassandra にしたときの互換性・性能・NoSQL 適性\n"
               "2026年9月11日 ／ ScalarDB Cluster 3.19.1・Oracle Database 23ai Free・Apache Cassandra 5.0.9",
      notes="同じ Oracle SQL と同じデータで、ScalarDB の背後を Oracle のままにした構成（Oracle 版）と Cassandra にした構成（Cassandra 版）を実測した結果です。")


@slide("キーで取得できる SQL は両方で動く。分かれ目は表全体を読む処理",
       note="1 枚で判断できるように、状況・課題・答えの順にまとめています。")
def s_exec(d):
    d.exec_summary(
        X0, DY0, W, 3.34,
        "Oracle SQL を ScalarDB に移す。背後を Oracle のまま（Oracle 版）と Cassandra（Cassandra 版）で実測した",
        "Cassandra ではクロスパーティション走査を使わない。キーで取得できない SQL は動かない",
        "キーで取得する SQL は両版で動き、結果も一致する。表全体を読む SQL は Oracle 版では遅く、Cassandra 版では動かない",
        points=["互換性: Oracle 版は 62 文中 51 文一致（PostgreSQL 版と同じ）",
                "30 パターン: 両版とも「向く」17・「条件付き」3",
                "読み書き: Oracle 版 3〜8 ms、Cassandra 版 11〜36 ms"],
        size=9.5)


@slide("Oracle 版は小さく移せる。Cassandra 版はキー設計から作り直す",
       note="2 つの構成の違いを一覧にしたものです。以降のページで根拠を示します。")
def s_compare(d):
    d.table(X0, DY0, W, ["観点", "Oracle 版（ScalarDB + Oracle）", "Cassandra 版（ScalarDB + Cassandra）"], [
        ["互換性", "Oracle 固有機能 62 文中 51 文が一致", "動いた文はすべて一致。62 文中 47 文は取得不可"],
        ["キーでの読み書き", "3〜8 ms（Oracle 直接の 3〜4 倍）", "11〜36 ms（Oracle 直接の約 20 倍）"],
        ["表全体を読む処理", "動く。15 ms〜1.6 秒", "動かない（設計変更が必要）"],
        ["移行の手間", "小さい。キー設計はそのまま", "大きい。問いごとにキー設計を作り直す"],
        ["向く使い方", "既存 SQL を活かした段階的な移行", "キーで引く処理が中心で、拡張性が要る系"],
    ], col_widths=[1.3, 2, 2], row_h=0.5, header_h=0.4, size=10, aligns=["START", "START", "START"])
    foot(d, ["・キーで取得する SQL はどちらでも同じように動く。違いは表全体を読む SQL と、1 回あたりの応答時間"],
         edition=SRC)


# =====================================================================
# 1. 検証の方法
# =====================================================================

plain(layout="SECTION", title="1. 検証の方法", body="何を、どの構成で、どう測ったか",
      notes="変換の仕組み、比べた構成、測り方の順に説明します。")


@slide("同じ Oracle SQL を変換し、3 つのバックエンドで実行した",
       note="ScalarDB SQL の文法に収まる文はそのまま、収まらない SELECT は実行計画（キーで取得して H2 で処理）にします。")
def s_arch(d):
    a = d.box(X0, 2.02, 1.45, 0.74, "移行元の\nOracle SQL", size=10, bold=True)
    b = d.solid(2.2, 2.02, 1.55, 0.74, "変換ツール\nscalardb_migrate", size=10)
    c1 = d.box(4.05, 1.22, 1.75, 0.7, "ScalarDB SQL\n（文法に収まる文）", size=9.5)
    c2 = d.box(4.05, 2.86, 1.75, 0.7, "実行計画\nキーで取得 → H2 で処理", size=9.5)
    k = d.solid(6.1, 1.22, 1.0, 2.34, "ScalarDB\nCluster\n3.19.1", size=10, fill=d.P.primaryDark)
    d.connect(a, b)
    d.connect(b, c1, category="BENT")
    d.connect(b, c2, category="BENT")
    d.connect(c1, k)
    d.connect(c2, k)
    for name, sub, yy in (("Oracle 23ai Free", "Oracle 版", 1.05),
                          ("Cassandra 5.0.9", "Cassandra 版", 2.07),
                          ("PostgreSQL 16", "参考", 3.09)):
        c = d.shape(7.45, yy, 0.7, 0.5, kind="CAN", fill="#FFFFFF", stroke=d.P.muted, stroke_weight=1.0)
        d.label(8.25, yy + 0.03, 1.25, 0.22, name, size=8.5, align="START", color=d.P.text)
        d.label(8.25, yy + 0.26, 1.25, 0.2, sub, size=7.5, align="START", color=d.P.muted)
        d.link(k, c)
    base = d.box(X0, 3.62, 3.25, 0.44, "正解: 同じ SQL を Oracle 23ai に直接実行", size=9, dash="DASH")
    d.connect(a, base)
    foot(d, ["・ScalarDB SQL に収まらない SELECT は、ScalarDB から行を取得して H2（インメモリ SQL、Oracle 互換モード）で処理する"],
         edition="ScalarDB Cluster は standalone・トライアルライセンス、Consensus Commit / SERIALIZABLE。Cassandra 版はクロスパーティション走査なし")


@slide("比べたのは 2 つの構成と、参考の 2 つの構成",
       note="以降、ScalarDB + Oracle を「Oracle 版」、ScalarDB + Cassandra を「Cassandra 版」と呼びます。")
def s_configs(d):
    b = d.cards(X0, DY0, W, 1.62, [
        ("Oracle 版", "ScalarDB + Oracle\n移行元と同じ DB の\n別スキーマ・走査あり"),
        ("Cassandra 版", "ScalarDB + Cassandra\n1 ノード・走査なし\n残りはアプリで処理"),
        ("参考: PostgreSQL", "ScalarDB + PostgreSQL\n前回と同じ構成を\n同じ日に再計測"),
        ("参考: 走査あり", "Cassandra で\nクロスパーティション\n走査を許した場合"),
    ], title_size=11, body_size=9)
    banner(d, b + 0.25, "変換ツールの指定: Oracle 版・PostgreSQL 版は --storage jdbc、Cassandra 版は --storage cassandra", size=9)
    banner(d, b + 0.73, "方針: クロスパーティション走査は RDBMS バックエンドでのみ使う。Cassandra にはキー指定のアクセスだけをさせる",
           tone="warn", size=9)
    foot(d, None, edition="ScalarDB のドキュメントでも、非 JDBC のストレージのクロスパーティション走査は SERIALIZABLE でも直列化可能にならないとされる")


@slide("Oracle 直接の結果を正解に、4 ケース × 3 規模を交互に計測",
       note="同じ 1 つの JVM から Oracle 直接と ScalarDB 経由を交互に実行し、毎回結果を突き合わせています。")
def s_method(d):
    b = d.flow(X0, DY0, W, 0.62, ["同じ SQL を\nOracle に直接実行", "ScalarDB 経由で\n実行",
                                  "結果を値まで比較\n（互換性）", "応答時間を比較\n（15 回の p50）"], size=9.5)
    d.table(X0, b + 0.28, W, ["ケース", "内容", "規模"], [
        ["oracle.sql", "変換ツールの基本ケース 17 文", "SCOTT 相当"],
        ["oracle-features.sql", "Oracle 固有機能の読み取り 62 文", "SCOTT 相当"],
        ["bench.sql", "前回のベンチ 15 文（点アクセス〜全表走査）", "5,000 / 20,000 / 40,000 行"],
        ["nosql-patterns.sql", "NoSQL 適性 30 文（RDB 型と NoSQL 型の 2 つの表）", "5,000 / 20,000 / 40,000 行"],
    ], col_widths=[1.5, 3.1, 1.6], row_h=0.4, size=9.5, aligns=["START", "START", "CENTER"])
    foot(d, None, edition="計測: ウォームアップ 3 回 + 15 回。Apple M3 Pro、Docker の VM 8 GiB、すべて同一ホスト（ネットワーク遅延ほぼゼロ）")


# =====================================================================
# 2. 互換性
# =====================================================================

plain(layout="SECTION", title="2. 互換性", body="Oracle と同じ結果を返したか",
      notes="Oracle に直接投げた結果を正解として、値まで比べました。")


@slide("Oracle 版の互換性は PostgreSQL 版と完全に同じ",
       note="Oracle 版と PostgreSQL 版は 51 / 9 / 2 で同じ文が同じ結果でした。Cassandra 版は 47 文がキーで取得できず実行できません。")
def s_compat(d):
    d.vbars_stacked(X0, DY0, 5.6, 3.42, ["Oracle 版", "Cassandra 版", "参考: PostgreSQL 版"],
                    [("一致", [51, 11, 51]), ("不一致", [9, 2, 9]), ("取得不可", [0, 47, 0]), ("変換不可", [2, 2, 2])],
                    unit="文", values=True, colors=[d.P.success, d.P.danger, "#9CA3AF", "#D1D5DB"])
    d.metric(6.45, DY0 + 0.05, 3.05, 1.05, "51 / 62", "Oracle 版で Oracle と一致した文", color=d.P.success)
    d.metric(6.45, DY0 + 1.3, 3.05, 1.05, "47 / 62", "Cassandra 版でキーで取得できなかった文", color=d.P.danger)
    d.label(6.45, DY0 + 2.55, 3.05, 0.8, "oracle.sql（17 文）では\nOracle 版 17 一致、\nCassandra 版 5 一致・12 取得不可",
            size=9, color=d.P.muted)
    foot(d, ["・Cassandra 版で実行できた 13 文のうち不一致の 2 文は、Oracle 版でも不一致（H2 と型対応が原因）"],
         edition="出典: 本検証、oracle-features.sql（読み取り 62 文）。取得不可 = キーで行を取得できない文")


@slide("不一致 9 文の原因はバックエンドではなく H2 と型対応",
       note="ScalarDB SQL に収まらない文を処理するアプリ側のエンジン H2 に無い構文と、NUMBER → DOUBLE の型対応が原因です。")
def s_mismatch(d):
    b = d.table(X0, DY0, W, ["不一致の文（両版で同じ）", "原因", "対処"], [
        ["#32、#33 階層問合せ（CONNECT BY）", "H2 に無い構文", "再帰 WITH に書き換える"],
        ["#39 KEEP（DENSE_RANK FIRST）", "H2 に無い構文", "ROW_NUMBER() で書き換える"],
        ["#42〜#44 ROLLUP / CUBE / GROUPING SETS", "H2 に無い構文", "集約レベルごとの UNION ALL"],
        ["#45、#46 PIVOT / UNPIVOT", "H2 に無い構文", "条件付き集約 / UNION ALL"],
        ["#21 CAST(sal AS VARCHAR2)", "NUMBER(7,2) → DOUBLE で '2450.0'", "金額は整数化か TO_CHAR で書式指定"],
    ], col_widths=[2.4, 2.1, 2.0], row_h=0.44, size=9.5, aligns=["START", "START", "START"])
    banner(d, b + 0.22, "Oracle 版・PostgreSQL 版・Cassandra 版のどれでも同じ文が不一致になる。バックエンドを替えても解決しない",
           size=9)
    foot(d, None, edition="出典: 本検証、oracle-features.sql。H2 = 実行計画で使うインメモリ SQL エンジン（Oracle 互換モード）")


@slide("Cassandra で動くかは「キーで取得できるか」で決まる",
       note="Oracle 固有の関数や構文は、取得した行に H2 がかけるので障害になりません。障害になるのは、キーで行を取れない書き方です。")
def s_keyfetch(d):
    d.comparison(X0, DY0, 5.75, 2.35, [
        ("障害にならない: H2 で処理できる", ["NVL・DECODE・TO_CHAR", "分析関数・再帰 WITH・副問合せ",
                                           "UNION・式の射影・ROWNUM"]),
        ("障害になる: キーで取得できない", ["WHERE が無い（表全体を読む）", "キー以外の条件だけで絞る",
                                          "表全体を並べ替えて上位 N 件"]),
    ], highlight=1, size=9.5)
    banner(d, DY0 + 2.6, "関数・構文は取得した行に H2 がかける。\n障害は ScalarDB にキーで行を取らせられないこと",
           size=9, h=0.6, w=5.75)
    d.metric(6.55, DY0 + 0.1, 2.95, 1.45, "13 / 62", "Cassandra 版で実行できた文\n（インデックスや主キーで絞る文）",
             color=d.P.success)
    d.metric(6.55, DY0 + 1.8, 2.95, 1.45, "47 / 62", "キーで取得できない文\n（WHERE が無い・キー以外の条件）",
             color=d.P.danger)
    foot(d, ["・移行の障害は Oracle 固有の文法より、表全体を読む書き方にある"],
         edition="出典: 本検証、oracle-features.sql（読み取り 62 文、残り 2 文は元から変換不可）")


# =====================================================================
# 3. 性能
# =====================================================================

plain(layout="SECTION", title="3. 性能", body="どれだけ速いか、表が大きくなるとどうなるか",
      notes="NoSQL 適性ケースと前回のベンチの実測値です。")


@slide("キーでの読み書きは Oracle 版 3〜8 ms、Cassandra 版 11〜36 ms",
       note="どちらも表サイズに依らず一定です。Cassandra 版の上乗せは、Consensus Commit が Cassandra の軽量トランザクションを使う分と考えられます。")
def s_keyperf(d):
    d.vbars_grouped(X0, DY0, W, 3.42, ["主キー読み取り", "範囲読み取り", "キー駆動 JOIN", "1 行書き込み", "キー 5 個の IN"],
                    [("Oracle 直接", [0.9, 1.4, 1.2, 1.1, 1.2]),
                     ("Oracle 版", [4.0, 4.0, 5.3, 3.1, 8.3]),
                     ("Cassandra 版", [16.9, 12.4, 18.6, 23.8, 35.9])], unit="ms")
    foot(d, ["・ScalarDB が Oracle 直接に上乗せするのは 2〜4 ms。Cassandra 版はさらに軽量トランザクション（Paxos）の分が乗る"],
         edition=SRC40)


@slide("キー指定は表が 8 倍でも一定、全表集約は行数に比例",
       note="左はキー指定の点読み、右は全表の GROUP BY。右の Cassandra は走査を許した参考計測です（Cassandra 版では取得不可）。")
def s_scaling(d):
    cols = d.P.series(3)
    legend(d, X0, DY0, W, [(cols[0], "Oracle 直接"), (cols[1], "Oracle 版"),
                           (cols[2], "Cassandra 版（右は走査を許した参考計測）")], size=9)
    cw = 4.3
    rx = X0 + cw + 0.4
    for x, head, series, note in (
            (X0, "主キー 1 件の読み取り（N1a）",
             [("Oracle 直接", [1.2, 0.9, 1.0]), ("Oracle 版", [7.5, 4.5, 4.5]), ("Cassandra 版", [24.1, 15.2, 14.7])],
             "40,000 行: Oracle 直接 1.0、Oracle 版 4.5、Cassandra 版 14.7 ms"),
            (rx, "全表の GROUP BY（N8a）",
             [("Oracle 直接", [0.7, 1.1, 1.8]), ("Oracle 版", [134, 640, 992]), ("Cassandra 走査あり", [118, 600, 1028])],
             "40,000 行: Oracle 直接 1.8、Oracle 版 992、Cassandra 走査あり 1,028 ms")):
        d.label(x, DY0 + 0.32, cw, 0.28, head, size=10.5, bold=True)
        d.linechart(x, DY0 + 0.64, cw, 2.55, ["5,000 行", "20,000 行", "40,000 行"], series,
                    unit="ms", legend=False, end_values=False)
        d.label(x, DY0 + 3.22, cw, 0.22, note, size=8, color=d.P.muted)
    foot(d, None, edition="出典: 本検証、nosql-patterns.sql・15 回の p50。Cassandra 版では全表の GROUP BY は取得不可")


@slide("全表を読む処理は、絞り込みを DB に任せられるかで数十倍違う",
       note="上の 3 つは条件や並べ替えを Oracle が SQL で処理するので軽い。下の 5 つは全行を ScalarDB が読み込むので 1 秒を超えます。Cassandra 版ではどれも取得不可です。")
def s_fullscan(d):
    ok, ng = d.P.success, d.P.danger
    b = d.hbars(X0, DY0, W, [
        ("無索引列のフィルタ（N6a）", 15.5, "15.5 ms"),
        ("全表の上位 10 件（N7a）", 19.2, "19.2 ms"),
        ("非キー条件の一括更新（N15）", 36.1, "36.1 ms"),
        ("全表 COUNT(*)（N8b）", 983, "983 ms"),
        ("全表 GROUP BY（N8a）", 992, "992 ms"),
        ("OFFSET ページング（N11a）", 1077, "1,077 ms"),
        ("全表 DISTINCT（N8c）", 1206, "1,206 ms"),
        ("全表 JOIN + 集約（N10b）", 1615, "1,615 ms"),
    ], row_h=0.28, gap=0.1, label_w=2.7, value_w=1.35, colors=[ok, ok, ok, ng, ng, ng, ng, ng])
    legend(d, X0, b + 0.08, W, [(ok, "条件・並べ替えを Oracle が SQL で処理"), (ng, "全行を ScalarDB が読み込んで処理")])
    foot(d, ["・Oracle 直接ではどれも 1〜4 ms。Cassandra 版ではどれも取得不可"], edition="出典: 本検証、Oracle 版・注文 40,000 行・15 回の p50")


@slide("1 行の書き込みは Cassandra 版が Oracle 版の約 8 倍",
       note="Consensus Commit の条件付き書き込みが Cassandra では軽量トランザクションになるためと考えられます。paxos_variant v2 でも変わりませんでした。")
def s_write(d):
    d.vbars(X0, DY0, 5.7, 3.42, [("Oracle 直接", 1.1, "1.1 ms"), ("Oracle 版", 3.1, "3.1 ms"),
                                 ("PostgreSQL 版", 5.1, "5.1 ms"), ("Cassandra 版", 23.8, "23.8 ms")],
            colors=[d.P.muted, d.P.primary, d.P.primary, d.P.danger])
    d.metric(6.55, DY0 + 0.1, 2.95, 1.35, "約 8 倍", "Cassandra 版 ÷ Oracle 版\n（1 行の書き込み）", color=d.P.danger)
    d.label(6.55, DY0 + 1.7, 2.95, 1.6,
            "条件付き書き込みなどが、Cassandra では\n軽量トランザクション（Paxos）になる\nためと考えられる\n\nPaxos v2 に変えても速くならなかった",
            size=9, color=d.P.text)
    foot(d, None, edition="出典: 本検証、N13a〜d・N14（INSERT / UPDATE / upsert / DELETE）の中央値、注文 40,000 行")


# =====================================================================
# 4. NoSQL 適性
# =====================================================================

plain(layout="SECTION", title="4. NoSQL 適性", body="RDBMS の SQL のうち、どれが NoSQL に向くか",
      notes="同じ業務データを RDB 型と NoSQL 型の 2 つのキー設計で持ち、30 の問いを投げました。")


@slide("30 パターンの判定は 17 / 3 / 10 で、両構成とも同じ集合",
       note="「向く」は表を 8 倍にしても応答時間の伸びが 30% 以内のもの。条件付きは返す行に比例するもの。残り 10 は Oracle 版では全件走査、Cassandra 版では取得不可です。")
def s_verdicts(d):
    vc = verdict_colors(d)
    rows = [
        ("主キーの読み取り（N1a、N1b）", ("向く", "4 ms"), ("向く", "17 ms")),
        ("パーティション内の範囲・キーセット（N2、N11b）", ("向く", "4 ms"), ("向く", "12 ms")),
        ("RDB 型の表で同じ問い（N3）", ("向く", "4 ms"), ("向く", "17 ms")),
        ("1 顧客の件数・合計（N4a、N4b）", ("向く", "5 ms"), ("向く", "19 ms")),
        ("キーで駆動する JOIN（N9a、N9b）", ("向く", "5 ms"), ("向く", "19 ms")),
        ("キー 5 個の IN（N12a、N12b）", ("向く", "8 ms"), ("向く", "36 ms")),
        ("1 行の書き込み（N13a〜d、N14）", ("向く", "3 ms"), ("向く", "24 ms")),
        ("小さい表のインデックス（N5c）", ("向く", "5 ms"), ("向く", "12 ms")),
        ("返す行が多いインデックス・JOIN（N5a、N5b、N10a）", ("条件付き", "103〜214 ms"), ("条件付き", "180〜476 ms")),
        ("DB で絞れる全件走査（N6a、N6b、N7a、N7b、N15）", ("全件走査", "15〜36 ms"), ("取得不可", "")),
        ("全行を読む集計・並べ替え（N8a〜c、N10b、N11a）", ("全件走査", "983〜1,615 ms"), ("取得不可", "")),
    ]
    lw, cw, rh, step = 4.1, 2.35, 0.245, 0.272
    head = dict(kind="RECTANGLE", fill=d.P.primary, stroke=None, color="#FFFFFF", size=9, bold=True)
    d.shape(X0, DY0, lw, 0.3, text="パターン（nosql-patterns.sql）", **head)
    d.shape(X0 + lw + 0.1, DY0, cw, 0.3, text="Oracle 版", **head)
    d.shape(X0 + lw + 0.2 + cw, DY0, cw, 0.3, text="Cassandra 版", **head)
    y = DY0 + 0.35
    for label, *cells in rows:
        d.shape(X0, y, lw, rh, kind="RECTANGLE", fill=d.P.surface, stroke=None, text=label, size=8.5,
                align="START", color=d.P.text)
        for i, (v, txt) in enumerate(cells):
            fill, col = vc[v]
            d.shape(X0 + lw + 0.1 + i * (cw + 0.1), y, cw, rh, kind="RECTANGLE", fill=fill, stroke=None,
                    text=f"{v}　{txt}" if txt else v, size=8.5, color=col, bold=True)
        y += step
    foot(d, ["・「向く」17 と「条件付き」3 は両版で同じ文。残り 10 は Oracle 版では全件走査、Cassandra 版では取得不可"],
         edition=SRC40)


@slide("NoSQL に向くのはキーで絞れる問い、向かないのは全表を読む問い",
       note="判定は Cassandra 版（クロスパーティション走査なし）の実測によります。")
def s_suited(d):
    d.comparison(X0, DY0, W, 2.25, [
        ("向く: キーで絞れる問い", ["主キーでの読み書き", "顧客ごとの最新 N 件", "キーで駆動する JOIN",
                                  "キーの IN（キーごとに取得）"]),
        ("条件付き: 返す行が多い問い", ["状態区分などのインデックス", "インデックスで駆動する JOIN",
                                      "返す行に比例して遅くなる"]),
        ("向かない: 全表を読む問い", ["全表の集計・COUNT・DISTINCT", "無索引列だけの検索", "全表の上位 N 件、OFFSET",
                                    "非キー条件の一括更新"]),
    ], highlight=0, size=9.5)
    cw = (W - 0.22 * 2) / 3
    for i, (val, cap, col) in enumerate((("11〜36 ms", "Cassandra 版・表サイズで一定", d.P.success),
                                         ("180〜476 ms", "Cassandra 版・返す行に比例", d.P.warning),
                                         ("動かない", "Cassandra 版（Oracle 版では遅い）", d.P.danger))):
        d.metric(X0 + i * (cw + 0.22), DY0 + 2.45, cw, 0.95, val, cap, color=col)
    foot(d, ["・Oracle 版では「向かない」問いも動くが、表に比例して遅くなる（15 ms〜1.6 秒）"], edition=SRC40)


@slide("「顧客ごとの最新 N 件」はキー設計で Cassandra に任せられる",
       note="NoSQL 型は 1 つのパーティションを並び順どおりに読んで止まる。RDB 型はインデックスで 40 行を取り、アプリ側で並べ替えます。")
def s_keydesign(d):
    zw = 4.85
    zone(d, X0, DY0, zw, 1.62, "NoSQL 型: パーティション = 顧客、並び順 = 注文日",
         fill="#F6FCF4", stroke=lighten(d.P.success, 0.5))
    for i, t in enumerate(["顧客 101 ｜ 2025-06-10", "顧客 101 ｜ 2025-05-28", "顧客 101 ｜ 2025-05-02", "顧客 101 ｜ …（40 件）"]):
        d.shape(X0 + 0.2, DY0 + 0.42 + i * 0.28, 2.3, 0.24, kind="RECTANGLE", fill="#FFFFFF",
                stroke=lighten(d.P.success, 0.5), text=t, size=8, color=d.P.text)
    d.label(X0 + 2.7, DY0 + 0.5, 2.05, 0.95, "1 つのパーティションを\n新しい順に読み、\n10 件で止まる", size=9,
            color=darken(d.P.success, 0.45))
    zy = DY0 + 1.78
    zone(d, X0, zy, zw, 1.62, "RDB 型: 主キー = 注文 ID、顧客はインデックス",
         fill="#F8FAFC", stroke=lighten(d.P.muted, 0.5))
    for i, t in enumerate(["注文 4041 ｜ 顧客 101", "注文 4046 ｜ 顧客 101", "注文 4051 ｜ 顧客 101", "…（40 件、順不同）"]):
        d.shape(X0 + 0.2, zy + 0.42 + i * 0.28, 2.3, 0.24, kind="RECTANGLE", fill="#FFFFFF",
                stroke=lighten(d.P.muted, 0.5), text=t, size=8, color=d.P.text)
    d.label(X0 + 2.7, zy + 0.5, 2.05, 0.95, "インデックスで 40 件取り、\nアプリ（H2）で\n並べ替える", size=9, color=d.P.text)
    rx = X0 + zw + 0.35
    d.vbars_grouped(rx, DY0 + 0.1, XE - rx, 2.95, ["Oracle 版", "Cassandra 版"],
                    [("NoSQL 型（N2）", [4.1, 13.9]), ("RDB 型（N3）", [4.2, 17.1])], unit="ms")
    d.label(rx, DY0 + 3.1, XE - rx, 0.3, "改修前の変換では RDB 型は Cassandra でエラー", size=8.5, color=d.P.muted)
    foot(d, None, edition=SRC40)


# =====================================================================
# 5. 変換ツールと設定
# =====================================================================

plain(layout="SECTION", title="5. 変換ツールと設定の注意点", body="Cassandra で動かない形の振り分けと、設定の落とし穴",
      notes="変換ツールの改修内容と、計測中に見つかった設定の問題です。")


@slide("変換ツールが Cassandra で動かない形を自動で振り分ける",
       note="--storage cassandra を付けたときの判定です。既定の jdbc では従来と同じ変換結果になります。")
def s_converter(d):
    d.mece_tree(X0, DY0, 5.95, 3.36, ("Oracle SQL を\n--storage cassandra\nで変換", [
        "キーで絞れる\n→ ScalarDB SQL のまま",
        "キー順以外の ORDER BY\n→ キーで取得 + H2 で並べ替え",
        "キーの IN / OR\n→ キーごとに取得して合成",
        "キーで取得できない\n→ 変換不可（FULL_SCAN）",
    ]), size=9)
    d.metric(6.75, DY0, 2.75, 1.05, "995 → 36 ms", "キー 5 個の IN（40,000 行）", color=d.P.success)
    d.metric(6.75, DY0 + 1.2, 2.75, 1.05, "47 / 62", "設計変更の対象として一覧化", color=d.P.danger)
    d.label(6.75, DY0 + 2.45, 2.75, 0.9, "JDBC 向けの変換結果は改修前と同じ\n単体テスト 160 件が通過",
            size=9, color=d.P.muted)
    foot(d, None, edition="995 ms は分割しない場合（全パーティション走査）、36 ms はキーごとの取得に分けた場合")


@slide("Cassandra は設定次第で起動せず、既定値のままだと走査が遅い",
       note="並べ替えの設定は起動エラー、scan_fetch_size の既定値 10 は走査を 10 行ごとの往復にします。Paxos v2 は効果がありませんでした。")
def s_cassconf(d):
    b = d.cards(X0, DY0, W, 1.12, [
        ("並べ替え設定で起動しない", "ordering を有効にすると\n起動しない（DB-CORE-10128）"),
        ("取得単位の既定値が小さい", "既定 10 は 10 行ごとに往復\n1000 で走査が 4〜19 倍速い"),
        ("Paxos v2 では速くならない", "点アクセスの約 25 ms は\nv2 でも変わらない"),
    ], title_size=10.5, body_size=8.5)
    d.vbars_grouped(X0, b + 0.18, W, DY1 - (b + 0.18), ["無索引列のフィルタ", "全表 COUNT(*)", "インデックス 1/5", "インデックス列の IN"],
                    [("既定値 10", [1693, 1724, 450, 3381]), ("1000", [148, 115, 98, 179])], unit="ms")
    foot(d, None, edition="出典: 本検証、Cassandra で走査を許した構成・注文 5,000 行・2 反復の p50。本計測は 1000 で実施")


@slide("Oracle では ScalarDB の既定の接続プールがプロセス上限を超える",
       note="既定では min 20 / max 200、並列コミットは最大 128 並列。Oracle Database Free のプロセス上限 200 を使い切り、ORA-12516 になりました。")
def s_oraconf(d):
    n = d.box(X0, DY0 + 0.1, 3.0, 0.9, "ScalarDB Cluster ノード\nプール 既定 min 20 / max 200\n並列コミット 最大 128", size=9)
    c = d.box(X0, DY0 + 1.25, 3.0, 0.72, "ScalarDB Core クライアント\n（データ投入・計測、各 min 20）", size=9)
    h = d.box(X0, DY0 + 2.22, 3.0, 0.55, "移行元への接続（正解の計測）", size=9)
    o = d.shape(4.25, DY0 + 0.75, 1.5, 1.35, kind="CAN", fill=lighten(d.P.danger, 0.9), stroke=d.P.danger,
                text="Oracle Free\nプロセス上限\n200", size=9, bold=True, color=d.P.text)
    for s in (n, c, h):
        d.link(s, o)
    d.label(4.0, DY0 + 2.2, 2.0, 0.3, "ORA-12516 で接続不可", size=9, bold=True, align="CENTER", color=d.P.danger)
    d.arrow_shape(5.95, DY0 + 1.2, 0.45, 0.45)
    d.shape(6.55, DY0 + 0.45, 2.95, 1.95, kind="ROUND_RECTANGLE", fill=lighten(d.P.success, 0.82), stroke=None,
            text="対処: プールを縮小\nノード min 5 / max 50\nクライアント min 1 / max 10\n\n→ 全ケースを完走", size=9.5,
            color=darken(d.P.success, 0.45))
    foot(d, ["・本番でも、ScalarDB のプール上限 × ノード数を Oracle の processes / sessions に収まるように決める"],
         edition="PostgreSQL 版は既定のまま。JDBC 系でも scan_fetch_size=1000 で全表走査が 2〜3 倍速い（PostgreSQL で確認）")


# =====================================================================
# まとめ
# =====================================================================

plain(layout="SECTION", title="6. まとめ", body="どちらを選び、どう進めるか", notes="判断の基準と進め方です。")


@slide("既存 SQL を活かすなら Oracle 版、拡張性なら Cassandra 版",
       note="3 つ目の ScalarDB Analytics は本検証では測っていません。全表を読む処理の逃がし先の候補です。")
def s_choose(d):
    b = decision(d, X0, DY0, W, "何を優先するか？", [
        ("既存 SQL を活かす", "Oracle 版\nデータは Oracle のまま", "good"),
        ("拡張性・可用性", "Cassandra 版\nSQL をキーで取得する形に", "info"),
        ("キー設計を直せない", "Oracle 版のまま\n重い集計は別経路へ", "warn"),
    ], dia_w=3.2, dia_h=0.7)
    d.cards(X0, b + 0.2, W, DY1 - (b + 0.2), [
        ("Oracle 版の実測", "互換性は PostgreSQL 版と同じ\n読み書き 3〜8 ms\n全表集計は 1〜1.6 秒"),
        ("Cassandra 版の実測", "キーで取れる文は全て一致\n読み書き 11〜36 ms\n全表を読む文は動かない"),
        ("別経路の候補", "集計値を持つ表を用意する\nScalarDB Analytics に回す\n（本検証の範囲外）"),
    ], gap=0.3, title_size=10, body_size=9)


@slide("Oracle 版の上で SQL をキー取得に直してから Cassandra に移す",
       note="両版で「向く」の文の集合が同じだったので、Oracle 版の上でキー取得の形に直した SQL は、そのまま Cassandra 版でも動くと見込めます。")
def s_path(d):
    d.steps(X0, DY0 + 0.05, W, 3.3, ["1. Oracle 版に切り替え", "2. 取得不可を洗い出す",
                                     "3. SQL をキー取得に直す", "4. Cassandra 版に移す"],
            captions=["データはそのまま", "FULL_SCAN を一覧化", "キー設計・集計表", "「向く」17 は同じ SQL"],
            size=10)
    foot(d, ["・今回のケースでは、両版で「向く」になった文の集合が同じだった"], edition=SRC40)


@slide("スケールアウトと同時実行はまだ測っていない",
       note="Cassandra を選ぶ主な理由であるノード追加時のスループットは、今回の範囲外です。")
def s_limits(d):
    pw = (W - 0.3) / 2
    zone(d, X0, DY0, pw, 3.38, "この計測で分からないこと", fill="#FFFBEB", stroke=lighten(d.P.warning, 0.4))
    checklist(d, X0 + 0.15, DY0 + 0.45, pw - 0.3, [
        ("Cassandra は 1 ノード。スケールアウトは未評価", "warn"),
        ("単一クライアント。同時実行と再試行は未評価", "warn"),
        ("Oracle 版は移行元と同じインスタンス", "warn"),
        ("JDBC 系は scan_fetch_size が既定値", "warn"),
    ], row_h=0.58, gap=0.12, size=9)
    rx = X0 + pw + 0.3
    zone(d, rx, DY0, pw, 3.38, "次に測るもの", fill="#F8FAFC", stroke=lighten(d.P.primary, 0.6))
    checklist(d, rx + 0.15, DY0 + 0.45, pw - 0.3, [
        ("複数ノード・同時実行でのスループット", "todo"),
        ("100 万行以上の本番規模", "todo"),
        ("読んで書く更新などの書き込みテンプレート", "todo"),
        ("取得不可の問いを直した設計での再計測", "todo"),
    ], row_h=0.58, gap=0.12, size=9)
