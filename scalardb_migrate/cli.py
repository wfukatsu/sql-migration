"""CLI: python -m scalardb_migrate.cli <file.sql> --dialect oracle|postgres|mysql [options]"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .converter import convert_script
from .schema import SchemaRegistry


def _parse_keys(items: list[str]) -> dict[str, tuple[list[str], list[str]]]:
    """--keys table=p1,p2/c1,c2  (partition keys / clustering keys)"""
    out = {}
    for item in items or []:
        table, _, spec = item.partition("=")
        p, _, c = spec.partition("/")
        out[table.strip().lower()] = ([x.strip() for x in p.split(",") if x.strip()],
                                      [x.strip() for x in c.split(",") if x.strip()])
    return out


def render_markdown(results, dialect: str, source: str) -> str:
    counts = Counter(r.status for r in results)
    lines = [f"# ScalarDB SQL migration report", "",
             f"- source: `{source}` (dialect: {dialect})",
             f"- statements: {len(results)}  |  OK: {counts.get('OK', 0)}  |  "
             f"converted with warnings: {counts.get('WARN', 0)}  |  app-side plan: {counts.get('PLANNED', 0)}  |  "
             f"not convertible: {counts.get('ERROR', 0)}", ""]
    icon = {"OK": "✅", "WARN": "⚠️", "ERROR": "❌", "PLANNED": "🧩"}
    lines += ["| # | kind | status | source | ScalarDB SQL | issues |", "|---|---|---|---|---|---|"]
    for r in results:
        src = r.source_sql.replace("\n", " ").replace("|", "\\|")
        src = src[:90] + ("…" if len(src) > 90 else "")
        out = "; ".join(r.converted).replace("\n", " ").replace("|", "\\|")
        out = out[:90] + ("…" if len(out) > 90 else "")
        iss = "<br>".join(f"**{i.severity}** {i.code}: {i.message}".replace("|", "\\|") for i in r.issues)
        lines.append(f"| {r.index} | {r.kind} | {icon[r.status]} {r.status} | `{src}` | `{out}` | {iss} |")
    code_counts = Counter((i.severity, i.code) for r in results for i in r.issues)
    lines += ["", "## Issue summary", "", "| severity | code | count |", "|---|---|---|"]
    for (sev, code), n in sorted(code_counts.items(), key=lambda kv: (-{'ERROR': 2, 'WARN': 1, 'INFO': 0}[kv[0][0]], -kv[1])):
        lines.append(f"| {sev} | {code} | {n} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analyse and convert SQL to ScalarDB SQL (PoC)")
    ap.add_argument("file")
    ap.add_argument("--dialect", required=True, choices=["oracle", "postgres", "mysql"])
    ap.add_argument("--schema", help="ScalarDB Schema Loader JSON with existing table definitions")
    ap.add_argument("--keys", action="append", help="partition/clustering key hint: table=p1,p2/c1,c2")
    ap.add_argument("--out-dir", default=None, help="write <name>.scalardb.sql, <name>.report.md, "
                                                     "<name>.report.json, <name>.schema.json here")
    ap.add_argument("--plan-dir", default=None, help="write one <name>.<n>.plan.json per PLANNED statement here")
    ap.add_argument("--no-plan", action="store_true", help="do not build app-side plans for unconvertible SELECTs")
    args = ap.parse_args(argv)

    text = Path(args.file).read_text(encoding="utf-8")
    registry = SchemaRegistry.from_schema_loader_json(args.schema) if args.schema else SchemaRegistry()
    results, registry = convert_script(text, args.dialect, registry, _parse_keys(args.keys), decompose=not args.no_plan)

    for r in results:
        print(f"[{r.index:>3}] {r.status:<5} {r.kind:<12} {r.source_sql.splitlines()[0][:70]}")
        for i in r.issues:
            print(f"        {i.severity:<5} {i.code}: {i.message}")
        for c in r.converted:
            print("        => " + c.replace("\n", "\n           "))
    counts = Counter(r.status for r in results)
    print(f"\n{len(results)} statements: OK={counts.get('OK', 0)} WARN={counts.get('WARN', 0)} "
          f"PLANNED={counts.get('PLANNED', 0)} ERROR={counts.get('ERROR', 0)}")
    if args.plan_dir:
        pd = Path(args.plan_dir)
        pd.mkdir(parents=True, exist_ok=True)
        stem = Path(args.file).stem
        for r in results:
            if r.plan:
                (pd / f"{stem}.{r.index}.plan.json").write_text(json.dumps(r.plan, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"plans written to {pd}/")

    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = Path(args.file).stem
        sql_lines = []
        for r in results:
            if r.converted:
                sql_lines += [c + ";" for c in r.converted]
            elif r.plan:
                sql_lines.append("-- [APP-SIDE PLAN #%d] %s" % (r.index, r.source_sql.replace("\n", " ")[:200]))
            else:
                sql_lines.append("-- [NOT CONVERTED #%d] %s" % (r.index, r.source_sql.replace("\n", " ")[:200]))
        (out / f"{stem}.scalardb.sql").write_text("\n".join(sql_lines) + "\n", encoding="utf-8")
        (out / f"{stem}.report.md").write_text(render_markdown(results, args.dialect, args.file), encoding="utf-8")
        (out / f"{stem}.report.json").write_text(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
        (out / f"{stem}.schema.json").write_text(registry.to_schema_loader_json(), encoding="utf-8")
        print(f"written to {out}/")
    return 0 if not counts.get("ERROR") else 1


if __name__ == "__main__":
    sys.exit(main())
