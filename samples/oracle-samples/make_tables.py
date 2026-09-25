"""README.md に貼る表を、結果ファイルから機械的に作る（手で写して間違えないため）。

- result/sql/<file>.report.json     : 文ごとの判定
- result/sql/queries-check.json     : 実 DB（Oracle vs ScalarDB）の比較
- result/plsql/analysis/decisions.json : routine ごとの判定
- plsql-run/work/plsql-diff.json    : PL/SQL の実 DB 比較

python3 samples/oracle-samples/make_tables.py > samples/oracle-samples/result/tables.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = HERE / "result"

STATUS = {"OK": "OK", "WARN": "WARN", "PLANNED": "PLANNED", "ERROR": "ERROR"}


def label_of(sql: str) -> str:
    """`-- A-2. 絞り込み…` のような節の印。無ければ空。"""
    m = re.search(r"--\s*([A-H]-\d+'?|\d+(?:-\d+)?)\.\s*([^\n]*)", sql)
    return f"{m.group(1)} {m.group(2).strip()[:28]}" if m else ""


def body(sql: str, n: int = 70) -> str:
    s = re.sub(r"--[^\n]*", "", sql)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[: n - 1] + "…") if len(s) > n else s


def codes(x: dict) -> str:
    return ", ".join(sorted({i["code"] for i in x["issues"] if i["severity"] in ("ERROR", "WARN")}))


def sql_tables() -> str:
    out = []
    check = {x["index"]: x for x in json.loads((R / "sql" / "queries-check.json").read_text())} \
        if (R / "sql" / "queries-check.json").exists() else {}
    for f in ["00_setup", "01_sql_ddl", "02_sql_query", "03_sql_dml", "05_plsql_units", "06_plsql_advanced"]:
        rep = json.loads((R / "sql" / f"{f}.report.json").read_text())
        s = rep["summary"]
        out.append(f"\n#### `{f}.sql` — {s['total']} 文 / OK {s['ok']} / WARN {s['warn']} / PLANNED {s['planned']} / ERROR {s['error']}（変換率 {s['rate']}%）\n")
        out.append("| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |")
        out.append("|---|---|---|---|---|---|")
        for x in rep["results"]:
            pat = (x.get("plan") or {}).get("pattern") or ""
            out.append(f"| {x['index']} | {label_of(x['source_sql'])} | `{body(x['source_sql'])}` | {STATUS[x['status']]} | {codes(x)} | {pat} |")
    return "\n".join(out)


def check_table() -> str:
    p = R / "sql" / "queries-check.json"
    if not p.exists():
        return ""
    out = ["| # | 元の SQL | 変換 | 実行 | 結果 | 差の内容 |", "|---|---|---|---|---|---|"]
    for x in json.loads(p.read_text()):
        how = "ScalarDB SQL" if x["convert_status"] in ("OK", "WARN") else (f"実行計画 {x['pattern']}" if x["convert_status"] == "PLANNED" else "—")
        err = (x.get("error") or "").replace("|", "\\|").replace("\n", " ")
        err = re.sub(r"\s+", " ", err)[:110]
        out.append(f"| {x['index']} | `{body(x['sql'], 60)}` | {x['convert_status']} | {how} | {x['result']} | {err} |")
    return "\n".join(out)


def plsql_table() -> str:
    d = json.loads((R / "plsql" / "analysis" / "decisions.json").read_text())
    items = d["routines"] if "routines" in d else next(v for v in d.values() if isinstance(v, list))
    out = ["| routine | 判定 | ルール | 理由（先頭） |", "|---|---|---|---|"]
    for x in items:
        rules = ", ".join(dict.fromkeys(r if isinstance(r, str) else r.get("id", "") for r in x["rules"]))
        reason = ("; ".join(x["reasons"]) or "").replace("|", "\\|")
        reason = re.sub(r"\s+", " ", reason)[:80]
        out.append(f"| `{x['routine']}` | {x['verdict']} | {rules} | {reason} |")
    return "\n".join(out)


def diff_table() -> str:
    p = R / "plsql" / "plsql-diff.json"
    if not p.exists():
        return "(plsql-diff.json がまだ無い)"
    d = json.loads(p.read_text())
    variant = d.get("double") or next(iter(d.values()))
    out = ["| シナリオ | routine | 結果 | 差の内容 |", "|---|---|---|---|"]
    for name, x in variant["scenarios"].items():
        diffs = x.get("differences") or []
        status = "一致" if not diffs and not x.get("not_compared") else ("比較できず" if x.get("not_compared") else "相違")
        text = "; ".join(diffs)[:170].replace("|", "\\|") if diffs else (x.get("not_compared") or "")
        out.append(f"| `{name}` | `{x['routine']}` | {status} | {text} |")
    return "\n".join(out)


if __name__ == "__main__":
    print("### SQL 変換（文ごと）\n" + sql_tables())
    print("\n### 実 DB 比較（SQL）\n" + check_table())
    print("\n### PL/SQL 判定（routine ごと）\n" + plsql_table())
    print("\n### PL/SQL 実 DB 比較\n" + diff_table())
