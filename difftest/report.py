"""Render the Oracle feature survey (JSON written by run.py --json-out and the write-statement classification)
as Markdown tables.

  .venv/bin/python difftest/report.py out/oracle-features.jdbc.json out/oracle-features.write.json > out/oracle-features.tables.md
"""

from __future__ import annotations

import json
import sys
from collections import Counter

# how each non-PASS outcome is explained in the report (matched on the error text)
GAP_RULES = [
    ("CONNECT BY", "H2 未対応構文: 階層問合せ。再帰 WITH に書き換える (文 34 は PASS)"),
    ("SYS_CONNECT_BY_PATH", "H2 未対応構文: 階層問合せ。再帰 WITH に書き換える"),
    ("KEEP", "H2 未対応構文: KEEP。ウィンドウ関数 (ROW_NUMBER で先頭行) に書き換える"),
    ("UNPIVOT", "H2 未対応構文: UNPIVOT。UNION ALL に書き換える"),
    ("PIVOT", "H2 未対応構文: PIVOT。CASE 式による条件付き集約に書き換える"),
    ("CUBE", "H2 未対応構文: CUBE。集約レベルごとの UNION ALL に書き換える"),
    ("GROUPING", "H2 未対応構文: ROLLUP / CUBE / GROUPING SETS。集約レベルごとの UNION ALL に書き換える"),
    ("ROLLUP", "H2 未対応構文: ROLLUP。UNION ALL に書き換える"),
    ("mismatched input '('", "変換ツールの検出漏れを修正済み → H2 未対応構文 (CUBE)。UNION ALL に書き換える"),
    ("mismatched input 'SETS'", "変換ツールの検出漏れを修正済み → H2 未対応構文 (GROUPING SETS)"),
    ("ROWID", "ScalarDB に ROWID は無い。主キーで置き換える"),
    ("'2450.0'", "型対応の差: NUMBER(7,2) → DOUBLE のため CAST(sal AS VARCHAR2) が '2450.0' になる。整数値なら INT/BIGINT、金額はスケール済み BIGINT にする"),
    ("rewrite as LEFT/RIGHT JOIN by hand", "(+) が FROM 側の表に付く形は自動書き換え不可 (SQLGlot の制限)。LEFT/RIGHT JOIN に手で書き換える"),
    ("TUESDAY", "NLS 依存: TO_CHAR の曜日・月名は Oracle の NLS_DATE_LANGUAGE と JVM ロケールに依存"),
]


def explain(rec: dict) -> str:
    if rec.get("source_rejects"):  # Issue #58
        return f"{rec['source_rejects']['label']}: {rec['source_rejects']['reason']}"
    if rec["result"] == "PASS":
        nd = rec.get("nondeterministic")  # Issue #57
        return f"宣言つき（{nd['compare']}）: {nd['reason']}" if nd else ""
    err = rec.get("error") or ""
    for key, text in GAP_RULES:
        if key in err or key in rec["sql"].upper():
            return text
    return err[:120]


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def main(read_json: str, write_json: str) -> None:
    reads = json.load(open(read_json, encoding="utf-8"))
    writes = json.load(open(write_json, encoding="utf-8"))

    print("### 読み取り系 (Oracle 23ai Free と ScalarDB の結果比較)\n")
    print("| # | 機能 | SQL (要約) | 変換 | 実行経路 | 結果 | 備考 |")
    print("|---|---|---|---|---|---|---|")
    for r in reads:
        route = ("ScalarDB SQL" if r["convert_status"] in ("OK", "WARN") else
                 f"計画 {r['pattern']} (fetch {r['fetched']} 行)" if r["result"] == "PASS" and r["pattern"] else
                 f"計画 {r['pattern']}" if r["pattern"] else "-")
        icon = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "⏭ SKIP", "NOT_CONVERTIBLE": "🚫 変換不可", "CASE_ERROR": "⚠️ ケース不備"}[r["result"]]
        print(f"| {r['index']} | {esc(r['feature'])} | `{esc(r['sql'][:80])}{'…' if len(r['sql']) > 80 else ''}` | "
              f"{r['convert_status']} | {route} | {icon} | {esc(explain(r))} |")
    c = Counter(r["result"] for r in reads)
    routes = Counter("ScalarDB SQL" if r["convert_status"] in ("OK", "WARN") else "計画" for r in reads if r["result"] == "PASS")
    print(f"\n合計 {len(reads)} 文: PASS {c.get('PASS', 0)} (ScalarDB SQL {routes.get('ScalarDB SQL', 0)}、計画 {routes.get('計画', 0)})、"
          f"FAIL {c.get('FAIL', 0)}、変換不可 {c.get('NOT_CONVERTIBLE', 0)}、その他 {c.get('CASE_ERROR', 0) + c.get('SKIP', 0)}\n")

    print("### 書き込み / DDL / PL/SQL 系 (変換ツールによる分類のみ)\n")
    print("| # | 機能 | SQL (要約) | 分類 | 変換結果または理由 |")
    print("|---|---|---|---|---|")
    for w in writes:
        reason = "; ".join(w["converted"]) if w["converted"] else "; ".join(f"{code}: {msg}" for sev, code, msg in w["issues"] if sev == "ERROR")
        print(f"| {w['index']} | {esc(w['feature'])} | `{esc(w['sql'][:70])}{'…' if len(w['sql']) > 70 else ''}` | {w['convert_status']} | {esc(reason[:160])} |")
    c = Counter(w["convert_status"] for w in writes)
    print(f"\n合計 {len(writes)} 文: 変換 {c.get('OK', 0) + c.get('WARN', 0)}、変換不可 {c.get('ERROR', 0)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
