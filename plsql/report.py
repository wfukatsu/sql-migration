"""P1-7: the inventory and the diagnostics, in the forms a reviewer and a tool can each read.

Phase 1 does not convert anything. What it owes is an honest picture: what is in the corpus, what resolved, what
did not, and why. Three outputs, one run:

* `inventory.json` -- the assets, their sizes and their effects, plus the KPI-1/KPI-2 numbers
* `diagnostics.sarif` -- every diagnostic, positioned, so an editor can show it on the line it belongs to
* a Markdown summary -- the same thing for a person, with the unresolved items first

The numbers here are the ones `docs/plsql-kpi.md` defines, computed the way it says. They are measured on a
synthetic corpus (§9 of the plan), so the report repeats that caveat rather than letting a reader infer otherwise.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .frontend import ParsedFile, coverage, parse_file
from .ir import model as M, serde
from .lower import _walk, lower_file
from .source import Issue
from .symbols import OracleSchema, SymbolTable, build, public_routines

SARIF_LEVEL = {"ERROR": "error", "WARN": "warning", "INFO": "note"}
BODY_SUFFIXES = {".pkb", ".prc", ".fnc", ".trg"}


@dataclass
class Analysis:
    """One run over a directory: the IR, the symbols and every diagnostic, kept together."""

    program: M.Program
    parsed: list[ParsedFile] = field(default_factory=list)
    symbols: list[SymbolTable] = field(default_factory=list)
    schema: OracleSchema | None = None
    capability: "object | None" = None   # CapabilityReport when P2-4 has run

    def issues(self) -> list[Issue]:
        out: list[Issue] = []
        for parsed in self.parsed:
            out.extend(parsed.all_issues())
        for table in self.symbols:
            out.extend(table.unresolved)
        for module in self.program.modules:
            out.extend(module.diagnostics)
            for routine in module.routines:
                out.extend(routine.diagnostics)
                for statement in _walk(routine.body):
                    out.extend(statement.diagnostics)
                for handler in routine.exception_handlers:
                    for statement in _walk(handler.body):
                        out.extend(statement.diagnostics)
        return out

    def symbol_table(self) -> SymbolTable:
        """Every scope of every file in one table.

        The SQL bridge resolves a name against the routine it is in, so it needs the scopes of the file that
        routine came from. Handing it one file's table -- which is what a list invites -- silently turns every
        variable in the other files into an unknown column.
        """
        merged = SymbolTable(schema_snapshot=self.schema.snapshot if self.schema else None,
                             oracle_schema=self.schema)
        for table in self.symbols:
            merged.scopes.update(table.scopes)
            merged.overloads.update(table.overloads)
            merged.unresolved.extend(table.unresolved)
        return merged

    def routines(self) -> list[tuple[M.Module, M.Routine]]:
        return [(m, r) for m in self.program.modules for r in m.routines]


def analyse(root: str | Path, schema_ddl: str | Path | None = None, program_id: str = "corpus",
            scalardb_schema: str | Path | None = None) -> Analysis:
    """Parse, resolve and lower every source file under `root`. Nothing raises; failures become diagnostics.

    With `scalardb_schema`, every SQL statement is also checked against the target (P2-4) and the answer lands on
    the IR, so the report can say what ScalarDB can run rather than leaving it unasked.
    """
    root = Path(root)
    schema = OracleSchema.from_ddl(schema_ddl) if schema_ddl else None
    program = M.Program(id=program_id, kind="Program",
                        schema_snapshot=schema.snapshot if schema else None)
    analysis = Analysis(program=program, schema=schema)

    for body in sorted(p for p in root.rglob("*") if p.suffix in BODY_SUFFIXES):
        spec = body.with_suffix(".pks")
        public: set[str] = set()
        if spec.exists():
            # the specification is parsed for its public names, and counted: KPI-1's denominator is every
            # source file, so leaving it out of `parsed` would quietly shrink the parse rate's denominator
            parsed_spec = parse_file(spec)
            analysis.parsed.append(parsed_spec)
            public = public_routines(parsed_spec)
        parsed = parse_file(body)
        symbols = build(parsed, schema, public)
        analysis.parsed.append(parsed)
        analysis.symbols.append(symbols)
        program.modules.extend(lower_file(parsed, symbols, schema, public))
        program.unresolved.extend(symbols.unresolved)

    # a specification with no body is still an asset: it declares an interface nothing implements here
    for spec in sorted(root.rglob("*.pks")):
        if spec.with_suffix(".pkb").exists():
            continue
        parsed = parse_file(spec)
        analysis.parsed.append(parsed)
        program.modules.extend(lower_file(parsed, None, schema, set()))

    if scalardb_schema is not None:
        from .capability import annotate, check
        from scalardb_migrate.schema import SchemaRegistry

        registry = SchemaRegistry.from_schema_loader_json(str(scalardb_schema))
        analysis.capability = check(program, registry, analysis.symbol_table())
        annotate(program, analysis.capability)
    return analysis


# --- inventory ----------------------------------------------------------------------------------------

def inventory(analysis: Analysis) -> dict:
    parse = coverage(analysis.parsed)
    typed = [s for table in analysis.symbols for s in table.all_symbols() if s.type is not None]
    resolved = [s for s in typed if s.type.is_resolved()]
    statements = Counter()
    modules = []

    for module in analysis.program.modules:
        routines = []
        for routine in module.routines:
            body = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            statements.update(s.kind for s in body)
            routines.append({
                "id": routine.id, "name": routine.name, "kind": routine.routine_kind,
                "visibility": routine.visibility, "statements": len(body),
                "parameters": len(routine.parameters), "declarations": len(routine.declarations),
                "handlers": len(routine.exception_handlers),
                "transactionEffects": {
                    "commits": routine.transaction_effects.commits,
                    "rollbacks": routine.transaction_effects.rollbacks,
                    "savepoints": routine.transaction_effects.savepoints,
                    "autonomous": routine.transaction_effects.autonomous},
                "externalEffects": {
                    "dbLinks": routine.external_effects.db_links,
                    "packages": routine.external_effects.packages,
                    "dynamicSql": routine.external_effects.dynamic_sql},
                "autoBlockers": auto_blockers(module, routine),
                "sourceRange": _range(routine),
            })
        modules.append({"id": module.id, "name": module.name, "kind": module.module_kind,
                        "packageState": module.has_package_state, "routines": routines,
                        "sourceRange": _range(module)})

    issues = analysis.issues()
    return {
        "schemaVersion": M.SCHEMA_VERSION,
        "corpus": {"origin": "synthetic",
                   "note": "合成 corpus 上の値であり、実案件耐性の証拠にはならない（実装計画 §9）"},
        "kpi": {
            "parseRate": round(parse.rate, 4),
            "parsedFiles": parse.parsed, "totalFiles": parse.total, "failedFiles": parse.failed,
            "typeResolutionRate": round(len(resolved) / len(typed), 4) if typed else 1.0,
            "typedSymbols": len(typed), "resolvedSymbols": len(resolved),
        },
        "totals": {"modules": len(modules), "routines": sum(len(m["routines"]) for m in modules),
                   "statements": sum(statements.values()), "issues": len(issues),
                   "errors": sum(1 for i in issues if i.severity == "ERROR")},
        "statementKinds": dict(statements.most_common()),
        "targetCapability": ({"statuses": analysis.capability.counts(),
                              "runnableRate": round(analysis.capability.rate(), 4)}
                             if analysis.capability is not None else None),
        "modules": modules,
        "schemaSnapshot": analysis.program.schema_snapshot,
    }


def auto_blockers(module: M.Module, routine: M.Routine) -> list[str]:
    """The AUTO prohibitions of docs/plsql-kpi.md §2 that this routine already trips.

    Phase 1 does not decide anything -- the rule engine (P2-2) does -- but the evidence is in the IR now, and a
    reader of the inventory should not have to re-derive it. The names match the document so the two can be
    compared directly.
    """
    blockers: list[str] = []
    effects = routine.transaction_effects
    if effects.commits or effects.rollbacks or effects.savepoints:
        blockers.append("transaction-control-in-routine")
    if effects.autonomous:
        blockers.append("autonomous-transaction")
    if module.has_package_state:
        blockers.append("package-state")
    if module.module_kind == "trigger":
        blockers.append("trigger")
    if routine.auth_id == "CURRENT_USER":
        blockers.append("authid-current-user")
    if routine.external_effects.db_links:
        blockers.append("db-link")
    body = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
    if any(s.kind == "DynamicSql" and s.constant_sql is None for s in body):
        blockers.append("dynamic-sql")
    # row locking hides in two places: a statement, and a cursor declaration whose query carries FOR UPDATE
    locked = any(s.kind == "SqlOperation" and s.locking_mode for s in body) or any(
        d.declaration_kind == "cursor" and d.initial and "FOR UPDATE" in d.initial.upper()
        for d in routine.declarations)
    if locked:
        blockers.append("row-lock")
    if any(s.kind == "Unsupported" for s in body):
        blockers.append("unmodelled-construct")
    return blockers


# --- SARIF --------------------------------------------------------------------------------------------

def sarif(analysis: Analysis, tool_version: str = M.SCHEMA_VERSION) -> dict:
    """SARIF 2.1.0, so the diagnostics open in an editor on the line they belong to."""
    rules: dict[str, dict] = {}
    results = []
    for issue in analysis.issues():
        rules.setdefault(issue.code, {"id": issue.code, "shortDescription": {"text": issue.code}})
        result = {"ruleId": issue.code, "level": SARIF_LEVEL.get(issue.severity, "note"),
                  "message": {"text": issue.message}}
        if issue.range is not None:
            result["locations"] = [{"physicalLocation": {
                "artifactLocation": {"uri": issue.range.file},
                "region": {"startLine": issue.range.start_line, "endLine": issue.range.end_line,
                           "startColumn": max(1, issue.range.start_column)}}}]
        results.append(result)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "plsql-migration-analyzer", "version": tool_version,
                                      "informationUri": "https://github.com/wfukatsu/sql-migration",
                                      "rules": list(rules.values())}},
                  "results": results}],
    }


# --- Markdown -----------------------------------------------------------------------------------------

def markdown(analysis: Analysis) -> str:
    data = inventory(analysis)
    kpi, totals = data["kpi"], data["totals"]
    lines = [
        "# PL/SQL 移行 インベントリ（Phase 1）", "",
        f"- 解析対象: {totals['modules']} モジュール / {totals['routines']} routine / "
        f"{totals['statements']} 文",
        f"- DDL スナップショット: `{data['schemaSnapshot'] or '(なし)'}`",
        f"- 診断: {totals['issues']} 件（うち ERROR {totals['errors']} 件）", "",
        "> この数値は**合成 corpus 上の値**であり、実案件の PL/SQL に対する耐性を示すものではない"
        "（実装計画 §9、docs/plsql-kpi.md §0）。", "",
        "## KPI", "", "| KPI | 値 | 目標 |", "|---|---|---|",
        f"| parse 率 | {kpi['parseRate']:.1%}（{kpi['parsedFiles']}/{kpi['totalFiles']}） | Phase 1 で 90% 以上 |",
        f"| 型解決率 | {kpi['typeResolutionRate']:.1%}"
        f"（{kpi['resolvedSymbols']}/{kpi['typedSymbols']}） | Phase 1 で 95% 以上 |", "",
    ]
    if kpi["failedFiles"]:
        lines += ["**parse できなかったファイル**", ""] + [f"- `{f}`" for f in kpi["failedFiles"]] + [""]

    blocked = [(m, r) for m in data["modules"] for r in m["routines"] if r["autoBlockers"]]
    lines += ["## AUTO を妨げる条件が既に見えている routine", "",
              f"{len(blocked)} / {totals['routines']} routine。判定そのものは P2-2 のルールが行う。"
              "ここは IR に既にある証拠を並べただけである。", "",
              "| routine | 条件 |", "|---|---|"]
    for module, routine in blocked:
        lines.append(f"| `{routine['id']}` | {', '.join(routine['autoBlockers'])} |")

    unresolved = [i for i in analysis.issues() if i.severity == "ERROR"] + \
                 [i for i in analysis.issues() if i.code == "UNRESOLVED_TYPE"]
    lines += ["", "## 未解決", ""]
    if not unresolved:
        lines.append("未解決の項目はない。")
    else:
        lines += [f"- **{i.severity}** `{i.code}` {i.message}" + (f"（{i.range}）" if i.range else "")
                  for i in unresolved]

    lines += ["", "## 資産", "", "| モジュール | 種別 | routine | 文 | package 状態 |", "|---|---|---|---|---|"]
    for module in data["modules"]:
        statements = sum(r["statements"] for r in module["routines"])
        lines.append(f"| `{module['name']}` | {module['kind']} | {len(module['routines'])} | "
                     f"{statements} | {'あり' if module['packageState'] else '—'} |")

    lines += ["", "## 文の内訳", "", "| 種別 | 件数 |", "|---|---|"]
    lines += [f"| {kind} | {count} |" for kind, count in data["statementKinds"].items()]
    return "\n".join(lines) + "\n"


def write(analysis: Analysis, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = {
        "inventory": out / "inventory.json",
        "diagnostics": out / "diagnostics.sarif",
        "summary": out / "summary.md",
        "ir": out / "program.ir.json",
    }
    written["inventory"].write_text(json.dumps(inventory(analysis), ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    written["diagnostics"].write_text(json.dumps(sarif(analysis), ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
    written["summary"].write_text(markdown(analysis), encoding="utf-8")
    written["ir"].write_text(serde.dumps(analysis.program), encoding="utf-8")
    return written


def _range(node: M.Node) -> dict | None:
    if node.source_range is None:
        return None
    return {"file": node.source_range.file, "startLine": node.source_range.start_line,
            "endLine": node.source_range.end_line}
