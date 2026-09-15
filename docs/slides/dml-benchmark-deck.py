#!/usr/bin/env python3
"""DML テスト SQL の ScalarDB 変換とベンチマーク（24 枚）— slide-forge の code-first デッキ。

数値は out/dml-bench/<dialect>/bench.json と difftest/work/dml-bench/result.<dialect>-reads.json を生成時に読む
（difftest/bench_dml.py の出力、2026-09-15）。文書版は docs/dml-benchmark-report.md。

    SF=~/.claude/plugins/cache/slide-forge/slide-forge/1.30.0
    cd $SF && .venv/bin/python scripts/validate_layout.py <このファイル> --template templates/blank-16x9.json
    cd $SF && .venv/bin/python scripts/render_deck.py <このファイル> --title "…" --folder <Drive フォルダ ID>
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

SF = os.environ.get("SLIDE_FORGE_ROOT",
                    os.path.expanduser("~/.claude/plugins/cache/slide-forge/slide-forge/1.30.0"))
sys.path.insert(0, os.path.join(SF, "scripts"))

from deckkit import *  # noqa: E402,F403

TITLE = "DML テスト SQL の ScalarDB 変換とベンチマーク"
TEMPLATE = json.load(open(os.path.join(SF, "templates", "blank-16x9.json"), encoding="utf-8"))

REPO = Path(__file__).resolve().parents[2]
DIALECTS = ("oracle", "postgres", "mysql")
NAMES = {"oracle": "Oracle", "postgres": "PostgreSQL", "mysql": "MySQL"}
DATA = {d: json.loads((REPO / "out/dml-bench" / d / "bench.json").read_text(encoding="utf-8")) for d in DIALECTS}
RAW = {d: {q["id"]: q for q in json.loads((REPO / "difftest/work/dml-bench" / f"result.{d}-reads.json")
                                          .read_text(encoding="utf-8"))["queries"]} for d in DIALECTS}
SRC = "出典: 本検証（2026-09-15）。ScalarDB Cluster 3.19.1 + PostgreSQL 16、ウォームアップ 3 回 + 15 回の p50"

SHORT = {
    "I01": "列リスト付き INSERT", "I02": "列リストなし INSERT", "I03": "複数行 INSERT", "I10": "一部の列を上書き upsert",
    "U01": "主キーで UPDATE", "U02": "複合主キーで UPDATE", "U03": "非キー条件の UPDATE", "U08": "NULL を設定",
    "U09": "主キー IN + BETWEEN", "U12": "インデックス列で UPDATE", "D01": "複合主キーで DELETE",
    "D02": "キー範囲で DELETE", "D03": "非キー IN で DELETE", "D04": "全行 DELETE", "D09": "主キーで DELETE",
    "S01": "キー等値 + 範囲", "S02": "キーセットページング", "S03": "OFFSET ページング", "S04": "3 表結合 + 集約",
    "S05": "反結合（LEFT JOIN）", "S06": "相関スカラー副問合せ", "S07": "グループ内の上位 1 件", "S08": "条件付き集約",
    "S09": "LIKE ESCAPE", "S10": "大文字小文字を区別しない検索", "S11": "月ごとの集計", "S12": "NULLS LAST",
    "S13": "UNION ALL", "S14": "EXISTS 半結合", "S15": "FOR UPDATE", "S16": "真偽値の列で絞る",
    "S17": "インデックスで集約",
}


def result(d: str, i: str) -> dict | None:
    return next((r for r in DATA[d]["results"] if r["id"] == i), None)


def p50(r: dict | None, key: str):
    return (r or {}).get(key, {}).get("p50") if r else None


def passed(r: dict | None) -> bool:
    return bool(r) and r["verdict"].startswith("PASS") and p50(r, "scalardb_ms") is not None


def fmt_ms(v: float) -> str:
    return f"{v / 1000:,.1f} s" if v >= 1000 else (f"{v:.0f} ms" if v >= 100 else f"{v:.1f} ms")


def cell(d: str, i: str, path: str | None = None) -> str:
    r = result(d, i)
    if r is None:
        return "変換不可"
    if path and r["path"] != path:
        return "—"
    if not passed(r):
        return "結果が不一致" if r["verdict"] == "FAIL" and "row" in (r.get("detail") or "") else "失敗"
    return f"{fmt_ms(p50(r, 'scalardb_ms'))}（{r['ratio']:g} 倍）"


def ids(kind: str, path: str | None = None) -> list[str]:
    seen = []
    for d in DIALECTS:
        for r in DATA[d]["results"]:
            if (r["write"] if kind == "write" else not r["write"]) and (path is None or r["path"] == path):
                if r["id"] not in seen:
                    seen.append(r["id"])
    return sorted(seen, key=lambda i: ("IUDS".index(i[0]), i))


def median_ratio(d: str, kind: str, path: str | None = None):
    rs = [r["ratio"] for r in DATA[d]["results"] if passed(r) and (r["write"] == (kind == "write"))
          and (path is None or r["path"] == path)]
    return statistics.median(rs) if rs else None


def median_ms(d: str, kind: str, key: str, path: str | None = None):
    rs = [p50(r, key) for r in DATA[d]["results"] if passed(r) and (r["write"] == (kind == "write"))
          and (path is None or r["path"] == path)]
    return statistics.median(rs) if rs else None


def ratio_range(kind: str, path: str | None = None) -> str:
    vals = [v for v in (median_ratio(d, kind, path) for d in DIALECTS) if v is not None]
    return f"{min(vals):.0f}〜{max(vals):.0f} 倍" if vals else "—"


def split(d: str, i: str) -> tuple[float, float]:
    s = RAW[d].get(i, {}).get("scalardb", {})
    return round(s.get("fetch_ms", 0)), round(s.get("residual_ms", 0))


# =====================================================================
# 表紙・要約
# =====================================================================

plain(layout="COVER",
      title="DML テスト SQL の\nScalarDB 変換とベンチマーク",
      subtitle="Oracle・PostgreSQL・MySQL の INSERT / UPDATE / DELETE / SELECT 各 51 文を比較\n"
               "2026年9月15日 ／ ScalarDB Cluster 3.19.1・Oracle 23ai Free・PostgreSQL 16・MySQL 8.4",
      notes="作成したテスト用 SQL を ScalarDB SQL に変換し、変換元のデータベースに直接実行した場合と ScalarDB Cluster で実行した場合の結果と応答時間を比べました。")


@slide("書き込みは半分以上がアプリ側の実装に、読み取りは結合が遅い",
       note="状況・課題・答えの順に 1 枚でまとめています。倍率は変換元 DB に直接実行した場合との p50 の比です。")
def s_exec(d):
    s04 = [p50(result(x, "S04"), "scalardb_ms") for x in DIALECTS if passed(result(x, "S04"))]
    d.exec_summary(
        X0, DY0, W, 3.34,
        "Oracle・PostgreSQL・MySQL の DML 中心のテスト SQL（各 51 文）を ScalarDB SQL に変換し、変換元 DB と性能を比べた",
        "書き込み 33 文のうち 17〜18 文は ScalarDB SQL にできない。変換できた読み取りも、多くは表を読んでアプリ側で処理する",
        "変換できた書き込みは ScalarDB で数 ms。表を結合する読み取りは H2 の処理が遅く、索引を作る改善が要る",
        points=[f"書き込み（COMMIT 込み）: 変換元の {ratio_range('write')}（中央値）",
                f"実行計画の読み取り: 変換元の {ratio_range('read', 'plan')}（中央値）",
                f"3 表結合: ScalarDB 側 {fmt_ms(min(s04))} 以上。ほとんどが H2 の処理" if s04 else "3 表結合: 失敗"],
        size=9.5)


# =====================================================================
# 1. 何を測ったか
# =====================================================================

plain(layout="SECTION", title="1. 何を測ったか", body="テスト SQL・測り方・データ",
      notes="テスト用 SQL の中身と、書き込み・読み取りの測り方を説明します。")


@slide("3 方言で同じ操作をする 51 文を、注文管理の 8 表で用意した",
       note="同じ番号の文は 3 方言で同じ操作をします。方言固有の書き方（MERGE・ON CONFLICT・ON DUPLICATE KEY など）はそれぞれの方言で書いています。")
def s_corpus(d):
    b = d.table(X0, DY0, W, ["種別", "文数", "主なパターン"], [
        ["INSERT", "12", "列リストの有無、複数行、INSERT ... SELECT、DEFAULT、現在時刻、採番、upsert、VALUES の副問合せ"],
        ["UPDATE", "12", "主キー・複合キー・非キー条件、現在の値を使う更新、CASE、相関副問合せ、結合、日付の加算"],
        ["DELETE", "9", "主キー・範囲・全行、EXISTS / NOT EXISTS、結合、先頭 1 行だけ、RETURNING"],
        ["SELECT", "17", "キーセット・OFFSET、3 表結合、反結合、上位 N 件、条件付き集約、NULLS LAST、FOR UPDATE"],
        ["トランザクション", "1", "SAVEPOINT"],
    ], col_widths=[1.4, 0.7, 6.9], row_h=0.44, header_h=0.36, size=9.5, aligns=["START", "CENTER", "START"])
    banner(d, b + 0.25, "表: customers・products・orders・order_items（複合キー）・stock（複合キー）・audit_log（採番）・seq_counter", size=9)
    foot(d, None, edition="テスト用 SQL: skills/sql-transpile/examples/dml/{oracle,postgres,mysql}.sql。各文に変換結果の期待値を付けている")


@slide("書き込みは毎回戻して COMMIT 込み、読み取りは 2 万注文で測定",
       note="書き込みは同じ行に何度も適用するとキーの重複などで失敗するので、毎回の実行前に準備データへ戻しました。戻す時間は計測に含めていません。")
def s_method(d):
    b = d.flow(X0, DY0, W, 0.66, ["変換元の SQL を\nScalarDB SQL に変換", "変換元 DB と\nScalarDB に同じデータ",
                                  "同じ文を交互に\n実行（15 回の p50）", "結果を\n突き合わせ"], size=9.5)
    d.table(X0, b + 0.28, W, ["", "書き込み（INSERT / UPDATE / DELETE / MERGE）", "読み取り（SELECT）"], [
        ["データ", "準備データ 8 表・42 行", "準備データ + 生成した行（注文 2 万・明細 約 5 万）"],
        ["毎回の実行前", "両方を準備データに戻す（時間に含めない）", "戻さない"],
        ["時間に含むもの", "文の実行と COMMIT", "問合せ（実行計画は取得と H2 の処理）"],
        ["突き合わせ", "変更した行数", "行の値（先頭 5,000 行）"],
    ], col_widths=[1.6, 3.7, 3.7], row_h=0.42, header_h=0.36, size=9.5, aligns=["START", "START", "START"])
    foot(d, None, edition="ScalarDB 側だけ audit_log の採番を外して変換した（ScalarDB には採番が無いため）")


@slide("変換元 DB と ScalarDB を、同じ PC の 1 クライアントで測った",
       note="すべてのコンテナが同じホストにあり、ネットワーク遅延はほぼゼロです。本番の値ではなく、経路ごとの差として読んでください。")
def s_env(d):
    cw = (W - 0.44) / 3
    for i, (head, body) in enumerate((("変換元 DB", "Oracle Database 23ai Free\nPostgreSQL 16\nMySQL 8.4"),
                                      ("ScalarDB", "ScalarDB Cluster 3.19.1\nバックエンド PostgreSQL 16\nSERIALIZABLE・scan_fetch_size 10"),
                                      ("計測", "Apple M3 Pro・Docker VM 8 GiB\n単一クライアント・単一スレッド\nウォームアップ 3 回 + 15 回"))):
        d.cards(X0 + i * (cw + 0.22), DY0, cw, 1.5, [(head, body)], title_size=11, body_size=9)
    d.table(X0, DY0 + 1.75, W, ["表（読み取りの計測時）", "customers", "products", "orders", "order_items", "stock", "audit_log"], [
        ["行数", "2,006", "206", "20,008", "約 50,000", "2,008", "10,002"],
    ], col_widths=[2.1, 1.1, 1.1, 1.1, 1.3, 1.1, 1.2], row_h=0.42, header_h=0.36, size=9.5,
        aligns=["START", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER"])
    foot(d, None, edition="実行計画の取得は ScalarDB SQL（JDBC）。変換元 DB には JDBC で直接接続")


# =====================================================================
# 2. 変換結果
# =====================================================================

plain(layout="SECTION", title="2. 変換結果", body="どれだけ ScalarDB SQL にできたか",
      notes="51 文を ScalarDB SQL に変換した結果です。")


@slide("51 文中 18〜19 文は変換できず、読み取りの多くは実行計画になる",
       note="OK は変換できた文、WARN は変換できたが確認が要る文、PLANNED は ScalarDB から取得して H2 で処理する文、ERROR は変換できない文です。")
def s_conv(d):
    d.vbars_stacked(X0, DY0, 5.7, 3.42, ["Oracle", "PostgreSQL", "MySQL"],
                    [("OK", [8, 10, 11]), ("WARN", [14, 11, 11]), ("PLANNED", [10, 11, 11]), ("ERROR", [19, 19, 18])],
                    unit="文", values=True, colors=[d.P.success, d.P.warning, d.P.primary, d.P.danger])
    d.metric(6.55, DY0 + 0.05, 2.95, 1.05, "31〜33 / 51", "ScalarDB で実行できる文", color=d.P.success)
    d.metric(6.55, DY0 + 1.3, 2.95, 1.05, "18〜19 / 51", "変換できない文（アプリ側で実装）", color=d.P.danger)
    d.label(6.55, DY0 + 2.55, 2.95, 0.8, "SELECT はすべて実行できる\nうち 10〜11 文は実行計画", size=9, color=d.P.muted)
    foot(d, None, edition="出典: テスト用 SQL の @expect-scalardb（tests/test_dml_examples.py で確認）")


@slide("書き込みは INSERT の 7〜8 割、UPDATE の半分が変換できない",
       note="DELETE も結合や副問合せを使うものは変換できません。SELECT は変換できないものがありません。")
def s_conv_kind(d):
    d.table(X0, DY0, W, ["種別", "Oracle", "PostgreSQL", "MySQL"], [
        ["INSERT（12 文）", "OK 1・WARN 3・ERROR 8", "OK 3・WARN 2・ERROR 7", "OK 3・WARN 2・ERROR 7"],
        ["UPDATE（12 文）", "OK 2・WARN 4・ERROR 6", "OK 3・WARN 3・ERROR 6", "OK 3・WARN 3・ERROR 6"],
        ["DELETE（9 文）", "OK 3・WARN 2・ERROR 4", "OK 2・WARN 2・ERROR 5", "OK 3・WARN 2・ERROR 4"],
        ["SELECT（17 文）", "OK 2・WARN 5・計画 10", "OK 2・WARN 4・計画 11", "OK 2・WARN 4・計画 11"],
        ["SAVEPOINT（1 文）", "ERROR", "ERROR", "ERROR"],
    ], col_widths=[2.1, 2.3, 2.3, 2.3], row_h=0.46, header_h=0.36, size=9.5, aligns=["START", "CENTER", "CENTER", "CENTER"])
    foot(d, ["・書き込み 33 文のうち、Oracle は 18 文、PostgreSQL は 18 文、MySQL は 17 文が変換できない"],
         edition="計画 = PLANNED（ScalarDB から取得して H2 で処理）")


@slide("変換できないのは、現在の値・別の表・DB の機能に頼る書き込み",
       note="いずれも ScalarDB SQL の文法に無い書き方です。アプリ側で、読んで計算してから書く処理に直します。")
def s_error(d):
    d.comparison(X0, DY0, W, 2.5, [
        ("変換できる書き込み", ["主キー・複合キーを指定した INSERT / UPDATE / DELETE", "非キー条件の UPDATE・DELETE（走査）",
                             "列を上書きするだけの upsert（UPSERT）"]),
        ("変換できない書き込み", ["現在の値を使う更新（qty = qty - 5、日付の加算）",
                               "副問合せ・結合を使う更新と削除、INSERT ... SELECT",
                               "現在時刻・採番・シーケンス・DEFAULT・SAVEPOINT"]),
    ], highlight=1, size=9.5)
    banner(d, DY0 + 2.75, "変換できない書き込みは、1 トランザクションの中で「読む → 計算する → キーを指定して書く」に直す", size=9, h=0.5)
    foot(d, None, edition="出典: 変換レポート（out/dml-bench/convert）")


@slide("方言固有の書き方で、同じ操作でも判定が分かれた文が 8 つある",
       note="同じ操作でも、方言の書き方によって ScalarDB SQL にできるかが変わります。")
def s_diverge(d):
    d.table(X0, DY0, W, ["文", "Oracle", "PostgreSQL", "MySQL", "分かれた理由"], [
        ["I03 複数行 INSERT", "ERROR", "OK", "OK", "Oracle は INSERT ALL"],
        ["D09 消した行を返す", "OK", "ERROR", "OK", "PostgreSQL は RETURNING"],
        ["S02 キーセットページング", "WARN", "PLANNED", "PLANNED", "行値の比較（Oracle は OR）"],
        ["S10 大小文字を区別しない", "PLANNED", "WARN", "WARN", "UPPER() / ILIKE / 照合順序"],
        ["S12 NULLS LAST", "WARN", "WARN", "PLANNED", "MySQL は IS NULL で並べる"],
        ["S16 真偽値の列", "WARN", "PLANNED", "WARN", "PostgreSQL は列だけの条件"],
        ["I07 / U02 採番・時刻", "WARN", "OK", "OK", "TIMESTAMP リテラルの確認"],
    ], col_widths=[2.3, 1.1, 1.3, 1.1, 3.2], row_h=0.4, header_h=0.36, size=9,
        aligns=["START", "CENTER", "CENTER", "CENTER", "START"])
    foot(d, None, edition="出典: テスト用 SQL の @expect-scalardb")


# =====================================================================
# 3. ベンチマーク結果
# =====================================================================

plain(layout="SECTION", title="3. ベンチマーク結果", body="書き込み・キー指定の読み取り・実行計画",
      notes="変換元 DB に直接実行した場合と比べた ScalarDB 側の応答時間です。")


def result_table(d, id_list, head, path=None):
    """Google Slides renders a table row at least ~0.33 in tall at 8.5 pt, so row_h matches that and tables stay
    at 9 rows or fewer per slide."""
    rows = [[f"{i} {SHORT.get(i, '')}"] + [cell(x, i, path) for x in DIALECTS] for i in id_list]
    return d.table(X0, DY0, W, [head, "Oracle", "PostgreSQL", "MySQL"], rows, col_widths=[2.7, 2.1, 2.1, 2.1],
                   row_h=0.34, header_h=0.34, size=8.5, aligns=["START", "CENTER", "CENTER", "CENTER"])


@slide("INSERT と主キーの UPDATE は、COMMIT 込みで数 ms",
       note="各セルは ScalarDB 側の p50 と、変換元 DB に直接実行した場合との比です。変換元 DB は 0.3〜0.9 ms でした。")
def s_writes_insert(d):
    result_table(d, [i for i in ids("write") if i[0] == "I" or i in ("U01", "U02", "U12")], "文（書き込み）")
    foot(d, None, edition=SRC + "。変換不可 = その方言では ERROR")


@slide("DELETE と非キー条件の書き込みも、COMMIT 込みで数 ms",
       note="キー以外の条件で絞る UPDATE・DELETE はクロスパーティション走査になりますが、データが小さいので数 ms でした。")
def s_writes_other(d):
    result_table(d, [i for i in ids("write") if i[0] == "D" or i in ("U03", "U08", "U09")], "文（書き込み）")
    foot(d, None, edition=SRC + "。変換不可 = その方言では ERROR")


@slide("キーで絞る読み取りは ScalarDB でも数 ms、変換元の 6〜13 倍",
       note="ScalarDB SQL として実行した読み取りです。S12 は NULLS LAST が落ちて並び順が変わり、MySQL の S10 は照合順序、S16 は真偽値の型で結果が合いませんでした。")
def s_key_reads(d):
    result_table(d, ids("read", "scalardb_sql"), "文（ScalarDB SQL で実行）", "scalardb_sql")
    foot(d, None, edition="— = その方言では実行計画か変換不可。結果が不一致・失敗の原因は後のページ")


PLAN_JOIN = ("S02", "S03", "S04", "S05", "S06", "S14")


@slide("結合・副問合せ・ページングは表を読み、0.3〜28 秒かかる",
       note="ScalarDB から表を取得して H2 で処理する文です。数万行を取得する結合は数十秒かかりました。")
def s_plan_join(d):
    result_table(d, [i for i in ids("read", "plan") if i in PLAN_JOIN], "文（実行計画で実行）", "plan")
    foot(d, None, edition="— = その方言では ScalarDB SQL か変換不可。15 回の p50")


@slide("集約は 0.6 秒前後、小さい表を読む文は数十 ms 以下",
       note="注文 2 万行を読む集約は 0.6 秒前後、顧客や商品だけを読む文は数十 ms でした。S11 は H2 の予約語で失敗しました。")
def s_plan_other(d):
    result_table(d, [i for i in ids("read", "plan") if i not in PLAN_JOIN], "文（実行計画で実行）", "plan")
    foot(d, None, edition="— = その方言では ScalarDB SQL か変換不可。15 回の p50")


@slide("ScalarDB 側の時間は経路で決まり、方言による差は小さい",
       note="変換元 DB に直接実行した場合との比の中央値と、ScalarDB 側の p50 の中央値です。倍率の差は、主に変換元 DB の速さの差です。")
def s_medians(d):
    def fmt(v):
        return "—" if v is None else f"{v:.0f} 倍"
    d.table(X0, DY0, W, ["経路", "Oracle", "PostgreSQL", "MySQL"], [
        ["書き込み（ScalarDB SQL、COMMIT 込み）"] + [fmt(median_ratio(x, "write")) for x in DIALECTS],
        ["読み取り（ScalarDB SQL）"] + [fmt(median_ratio(x, "read", "scalardb_sql")) for x in DIALECTS],
        ["読み取り（実行計画）"] + [fmt(median_ratio(x, "read", "plan")) for x in DIALECTS],
    ], col_widths=[3.6, 1.8, 1.8, 1.8], row_h=0.5, header_h=0.36, size=10, aligns=["START", "CENTER", "CENTER", "CENTER"])
    cw = (W - 0.44) / 3
    for i, (label, kind, path) in enumerate((("書き込み", "write", None), ("キー指定の読み取り", "read", "scalardb_sql"),
                                             ("実行計画の読み取り", "read", "plan"))):
        vals = [v for v in (median_ms(x, kind, "scalardb_ms", path) for x in DIALECTS) if v is not None]
        value = f"{fmt_ms(min(vals))}〜{fmt_ms(max(vals))}" if vals else "—"
        d.metric(X0 + i * (cw + 0.22), DY0 + 2.2, cw, 1.05, value, f"{label}（ScalarDB 側）",
                 color=(d.P.success, d.P.success, d.P.danger)[i], value_size=18)
    foot(d, None, edition=SRC + "。結果が一致した文だけで集計")


@slide("3 表結合の時間は、取得ではなく H2 の処理がほとんど",
       note="S04 は注文 2 万・明細 5 万行を取得して H2 で結合します。取得は 2 秒前後、H2 の処理は 20 秒以上でした。")
def s_h2(d):
    fetch = [split(x, "S04")[0] for x in DIALECTS]
    resid = [split(x, "S04")[1] for x in DIALECTS]
    d.vbars_grouped(X0, DY0, 5.9, 3.42, [NAMES[x] for x in DIALECTS],
                    [("ScalarDB から取得", fetch), ("H2 で処理", resid)], unit="ms")
    d.metric(6.7, DY0 + 0.05, 2.8, 1.1, f"{max(resid) / 1000:.0f} 秒", "S04 の H2 の処理（最大）", color=d.P.danger)
    d.label(6.7, DY0 + 1.4, 2.8, 1.9,
            "取得した表に主キーも\nインデックスも作っていない\n（Residual.load の CREATE TABLE）\n\n"
            "結合が入れ子ループになり、\n注文 × 明細を総当たりする", size=9, color=d.P.text)
    foot(d, None, edition="出典: 本検証、S04（3 表結合 + 集約）の最終回の内訳。S05（反結合）も同じ傾向")


@slide("失敗の原因は、H2 の予約語と、方言ごとのリテラルと型の差",
       note="S11 は 3 方言とも H2 で構文エラー。ほかは変換ツールが OK / WARN と判定した文が、ScalarDB で失敗するか結果が変わったものです。")
def s_failures(d):
    d.table(X0, DY0, W, ["文（方言）", "経路", "起きたこと", "原因", "対処"], [
        ["S11 月ごとの集計（3 方言）", "実行計画", "H2 で構文エラー", "別名 month が H2 の予約語", "予約語を引用符で囲む"],
        ["S12 NULLS LAST（Oracle・PG）", "ScalarDB SQL", "NULL が先頭に来る", "変換で NULLS LAST が落ちる", "アプリで並べ替える"],
        ["S10 大小文字（MySQL）", "ScalarDB SQL", "0 行になる", "MySQL は大小文字を区別しない", "LOWER() で比べる"],
        ["I01・I02・I10・S16（MySQL）", "ScalarDB SQL", "型の不一致で失敗", "TINYINT(1) が INT、TRUE はそのまま", "1 / 0 に書き換える"],
        ["I01・U02（PostgreSQL）", "ScalarDB SQL", "構文エラー", "型付きの日付リテラルのまま", "文字列リテラルにする"],
    ], col_widths=[2.4, 1.2, 1.5, 2.3, 1.6], row_h=0.4, header_h=0.36, size=8.5,
        aligns=["START", "START", "START", "START", "START"])
    d.shape(X0, DY0 + 2.55, W, 0.75, kind="RECTANGLE", fill=lighten(d.P.warning, 0.8), stroke=None,
            text="計測環境の注意: 1 回目は PostgreSQL・MySQL の書き込みが cached plan must not change result type で全滅した。\n"
                 "直前の方言の計測で同じ表を別の型で作り直したため。ScalarDB Cluster を再起動して測り直した",
            size=8.5, color=darken(d.P.warning, 0.55), align="START")
    foot(d, None, edition="上の 5 件以外の文は、変換元 DB と結果が一致した")


# =====================================================================
# 4. 考察
# =====================================================================

plain(layout="SECTION", title="4. 考察", body="結果から何が言えるか",
      notes="移行の手間、性能、ツールと実行基盤の改善点です。")


@slide("移行の手間は書き込みに集中し、性能の課題は読み取りに集中する",
       note="書き込みは変換できない文が多い一方、変換できれば速い。読み取りは全部実行できる一方、表を読む文が遅い、という対照です。")
def s_contrast(d):
    d.comparison(X0, DY0, W, 2.4, [
        ("書き込み", ["33 文中 17〜18 文が変換できない", "アプリ側で読んで計算して書く実装が要る",
                    f"変換できた文は速い（変換元の {ratio_range('write')}）"]),
        ("読み取り", ["17 文すべて実行できる", "10〜11 文は表を読んで H2 で処理する",
                    f"実行計画は遅い（変換元の {ratio_range('read', 'plan')}）"]),
    ], size=9.5)
    banner(d, DY0 + 2.65, "移行の見積もりは、書き込みは実装の量、読み取りは読む行数で考える", size=9, h=0.5)
    foot(d, None, edition=SRC)


@slide("効くのは H2 の索引と、方言ごとのリテラルの書き換え",
       note="変換ツールと実行基盤で直せる点です。H2 の索引は S04・S05 の数十秒を大きく縮める見込みです（未計測）。")
def s_improve(d):
    d.table(X0, DY0, W, ["改善点", "対象", "効果の見込み"], [
        ["取得した表に主キーと結合列のインデックスを作る", "runtime-java（Residual.load）", "結合の H2 処理が数十秒から大きく縮む"],
        ["H2 の予約語の別名を引用符で囲む", "変換ツール（実行計画の SQL）", "S11 のような構文エラーが無くなる"],
        ["NULLS LAST / FIRST を含む ORDER BY は計画に回す", "変換ツール", "並び順の不一致が無くなる"],
        ["MySQL の照合順序と真偽値リテラルを警告・書き換える", "変換ツール", "S10・S16・I01 など MySQL の失敗が無くなる"],
        ["PostgreSQL の DATE / TIMESTAMP リテラルを文字列にする", "変換ツール", "I01・U02 の構文エラーが無くなる"],
        ["表を作り直したら ScalarDB Cluster を再起動する", "運用・計測手順", "cached plan のエラーを防ぐ"],
        ["読む行数を減らす（集計表、キーでの取得）", "設計", "実行計画の読み取り全般が速くなる"],
    ], col_widths=[3.6, 2.5, 2.9], row_h=0.42, header_h=0.36, size=8.5, aligns=["START", "START", "START"])
    foot(d, None, edition="効果の見込みは本検証の内訳からの推定。改善後の計測はまだ行っていない")


@slide("次は H2 の索引を入れて測り直し、同時実行と本番規模を確かめる",
       note="この計測は単一クライアント・最大 5 万行です。書き込みは小さいデータで測っています。")
def s_next(d):
    pw = (W - 0.3) / 2
    zone(d, X0, DY0, pw, 3.38, "次にやること", fill="#F8FAFC", stroke=lighten(d.P.primary, 0.6))
    checklist(d, X0 + 0.15, DY0 + 0.45, pw - 0.3, [
        ("H2 の索引を入れて S04・S05 を再計測", "todo"),
        ("予約語と NULLS LAST の変換を直す", "todo"),
        ("変換できない書き込みをアプリ側で実装して計測", "todo"),
        ("scan_fetch_size 1000 で読み取りを再計測", "todo"),
    ], row_h=0.58, gap=0.12, size=9)
    rx = X0 + pw + 0.3
    zone(d, rx, DY0, pw, 3.38, "この計測で分からないこと", fill="#FFFBEB", stroke=lighten(d.P.warning, 0.4))
    checklist(d, rx + 0.15, DY0 + 0.45, pw - 0.3, [
        ("単一クライアント。同時実行は未評価", "warn"),
        ("書き込みは 42 行の小さいデータで計測", "warn"),
        ("バックエンドは PostgreSQL のみ", "warn"),
        ("各条件 1 回の計測（ばらつきは未評価）", "warn"),
    ], row_h=0.58, gap=0.12, size=9)
