"""変換結果から、変換後 SQL・診断レポート・変換率を作る。

汎用パスと ScalarDB パスはどちらも同じ ``Result`` の列を返すので、ここは 1 本で済む。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ICON = {"OK": "✅", "WARN": "⚠️", "ERROR": "❌", "PLANNED": "🧩"}
SEVERITY_ORDER = {"ERROR": 2, "WARN": 1, "INFO": 0}


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
        f"- 文数: {s['total']}　|　OK: {s['ok']}　|　WARN: {s['warn']}　|　ERROR: {s['error']}",
        f"- **変換率: {s['rate']}%**（{s['converted']} / {s['total']} 文が {target} の SQL を出力できた）",
        "",
        "| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        iss = "<br>".join(f"**{i.severity}** {i.code}: {i.message}".replace("|", "\\|") for i in r.issues)
        out = _cell("; ".join(r.converted)) if r.converted else ""
        lines.append(f"| {r.index} | {r.kind} | {ICON.get(r.status, '')} {r.status} "
                     f"| `{_cell(r.source_sql)}` | {('`' + out + '`') if out else '—'} | {iss} |")

    codes = Counter((i.severity, i.code) for r in results for i in r.issues)
    if codes:
        lines += ["", "## 指摘の集計", "", "| 重要度 | コード | 件数 |", "|---|---|---|"]
        for (sev, code), n in sorted(codes.items(), key=lambda kv: (-SEVERITY_ORDER.get(kv[0][0], 0), -kv[1])):
            lines.append(f"| {sev} | {code} | {n} |")

    errors = [r for r in results if r.status == "ERROR"]
    if errors:
        lines += ["", "## 手作業が必要な文", ""]
        for r in errors:
            reason = "; ".join(i.message for i in r.issues if i.severity == "ERROR")
            lines.append(f"- **#{r.index}** `{_cell(r.source_sql, 70)}` — {reason}")
    return "\n".join(lines) + "\n"


def render_sql(results, target: str) -> str:
    """変換後 SQL。変換できなかった文は原文をコメントで残し、位置を追えるようにする。"""
    parts = []
    for r in results:
        if r.converted:
            parts.append(";\n".join(r.converted) + ";")
        else:
            reason = "; ".join(i.message for i in r.issues if i.severity == "ERROR")
            body = "\n".join("-- " + ln for ln in r.source_sql.splitlines())
            parts.append(f"-- [NOT CONVERTED #{r.index}] {reason}\n{body}")
    return f"-- converted to {target}\n" + "\n\n".join(parts) + "\n"


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
