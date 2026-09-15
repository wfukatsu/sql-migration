"""変換結果から、変換後 SQL・診断レポート・変換率を作る。

汎用パスと ScalarDB パスはどちらも同じ ``Result`` の列を返すので、ここは 1 本で済む。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from _scalardb.appside import group_issues

ICON = {"OK": "✅", "WARN": "⚠️", "ERROR": "❌", "PLANNED": "🧩"}
SEVERITY_ORDER = {"ERROR": 2, "WARN": 1, "INFO": 0}
# 文ごとの表には出さず、「アプリ側に移す処理」の節にまとめる指摘
GROUPED_CODES = {"APP_SEMANTICS", "DESIGN", "COST", "ROW_LIMIT", "COST_DEADLINE", "CONFIG"}
SECTIONS = (("アプリ側で処理する構文", "app_side"), ("結果を変えないための注意（意味の差）", "semantics"),
            ("設計の提案", "design"), ("取得コストの見積もり", "cost"), ("推奨設定", "config"))


def summarize(results) -> dict:
    """変換率のサマリ。変換できた = Target の SQL を出力できた文 (OK + WARN)。"""
    counts = Counter(r.status for r in results)
    total = len(results)
    converted = counts.get("OK", 0) + counts.get("WARN", 0)
    return {
        "total": total,
        "ok": counts.get("OK", 0),
        "warn": counts.get("WARN", 0),
        "error": counts.get("ERROR", 0),
        "planned": counts.get("PLANNED", 0),
        "converted": converted,
        "rate": round(converted / total * 100, 1) if total else 0.0,
    }


def _cell(text: str, limit: int = 90) -> str:
    text = text.replace("\n", " ").replace("|", "\\|")
    return text[:limit] + ("…" if len(text) > limit else "")


def render_markdown(results, source: str, target: str, source_path: str) -> str:
    s = summarize(results)
    lines = [
        f"# SQL 変換レポート: {source} → {target}", "",
        f"- 入力: `{source_path}`",
        f"- 文数: {s['total']}　|　OK: {s['ok']}　|　WARN: {s['warn']}　|　"
        + (f"PLANNED: {s['planned']}　|　" if s["planned"] else "") + f"ERROR: {s['error']}",
        f"- **変換率: {s['rate']}%**（{s['converted']} / {s['total']} 文が {target} の SQL を出力できた）",
        "",
        "| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        iss = "<br>".join(f"**{i.severity}** {i.code}: {i.message}".replace("|", "\\|")
                          for i in r.issues if i.code not in GROUPED_CODES)
        out = _cell("; ".join(r.converted)) if r.converted else ""
        lines.append(f"| {r.index} | {r.kind} | {ICON.get(r.status, '')} {r.status} "
                     f"| `{_cell(r.source_sql)}` | {('`' + out + '`') if out else '—'} | {iss} |")

    codes = Counter((i.severity, i.code) for r in results for i in r.issues)
    if codes:
        lines += ["", "## 指摘の集計", "", "| 重要度 | コード | 件数 |", "|---|---|---|"]
        for (sev, code), n in sorted(codes.items(), key=lambda kv: (-SEVERITY_ORDER.get(kv[0][0], 0), -kv[1])):
            lines.append(f"| {sev} | {code} | {n} |")

    work = [(r, group_issues(r.issues)) for r in results]
    work = [(r, g) for r, g in work if r.status in ("ERROR", "PLANNED") or g["cost"]]
    if work:
        lines += ["", "## アプリ側に移す処理", ""]
        for r, g in work:
            lines += [f"### #{r.index} {ICON.get(r.status, '')} {r.status} `{_cell(r.source_sql, 70)}`", ""]
            if r.status == "PLANNED":
                lines += ["実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。", ""]
            for title, key in SECTIONS:
                if g[key]:
                    lines += [f"**{title}**", ""] + [f"- `{i.code}` {i.message}" for i in g[key]] + [""]
    return "\n".join(lines) + "\n"


def render_sql(results, target: str) -> str:
    """変換後 SQL。変換できなかった文は原文をコメントで残し、位置を追えるようにする。"""
    parts = []
    for r in results:
        if r.converted:
            parts.append(";\n".join(r.converted) + ";")
        else:
            body = "\n".join("-- " + ln for ln in r.source_sql.splitlines())
            if r.status == "PLANNED":
                fetches = "\n".join(f"--   {f['scalardb_sql']};" for f in r.plan["fetch"])
                parts.append(f"-- [APP-SIDE PLAN #{r.index}] ScalarDB から取得して H2 で実行する\n{fetches}\n{body}")
                continue
            reason = "; ".join(i.message for i in r.issues if i.severity == "ERROR")
            parts.append(f"-- [NOT CONVERTED #{r.index}] {reason}\n{body}")
    return f"-- converted to {target}\n" + "\n\n".join(parts) + "\n"


def write_plans(results, plan_dir: Path, stem: str) -> list[Path]:
    plan_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for r in results:
        if getattr(r, "plan", None):
            path = plan_dir / f"{stem}.{r.index}.plan.json"
            path.write_text(json.dumps(r.plan, indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(path)
    return written


def write_outputs(results, out_dir: Path, stem: str, source: str, target: str, source_path: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        out_dir / f"{stem}.{target}.sql": render_sql(results, target),
        out_dir / f"{stem}.report.md": render_markdown(results, source, target, source_path),
        out_dir / f"{stem}.report.json": json.dumps(
            {"source": source, "target": target, "summary": summarize(results),
             "results": [asdict(r) for r in results]}, indent=2, ensure_ascii=False, default=str),
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return list(files)
