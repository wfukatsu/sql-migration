"""P1-7: the inventory and the diagnostics, in the forms a reviewer and a tool can each read.

Phase 1 does not convert anything. What it owes is an honest picture: what is in the corpus, what resolved, what
did not, and why. Three outputs, one run:

* `inventory.json` -- the assets, their sizes and their effects, plus the KPI-1/KPI-2 numbers
* `diagnostics.sarif` -- every diagnostic, positioned, so an editor can show it on the line it belongs to
* a Markdown summary -- the same thing for a person, with the unresolved items first

The numbers here are the ones `docs/design/plsql-kpi.md` defines, computed the way it says. They are measured on a
synthetic corpus (§9 of the plan), so the report repeats that caveat rather than letting a reader infer otherwise.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .frontend import ParsedFile, coverage, parse_file
from .ir import model as M, serde
from . import dblinks, merge, paging, rmw, triggers
from .limits import RowLocks
from .lower import _walk, lower_file
from .source import Issue
from .symbols import OracleSchema, SymbolTable, build, public_routines

SARIF_LEVEL = {"ERROR": "error", "WARN": "warning", "INFO": "note"}
BODY_SUFFIXES = {".pkb", ".prc", ".fnc", ".trg", ".pls"}
SPEC_SUFFIX = ".pks"
# a `.sql` file is a body when it creates a PL/SQL unit. The schema DDL is `.sql` too, which is why the suffix
# alone cannot decide -- but skipping every `.sql` left a routine kept in one out of the analysis without a word
_CREATES_UNIT = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?"
                           r"(?:PACKAGE\s+BODY|PROCEDURE|FUNCTION|TRIGGER)\b", re.IGNORECASE | re.MULTILINE)


def _sibling(path: Path, suffix: str) -> Path | None:
    """`pkg_a.PKS` beside `pkg_a.pkb`: the same stem, the suffix in any case."""
    return next((p for p in sorted(path.parent.iterdir())
                 if p.stem == path.stem and p.suffix.lower() == suffix and p != path), None)


def source_bodies(root: Path, schema_ddl: str | Path | None = None) -> list[Path]:
    """The files that hold a routine body, whatever the case of the suffix (`frontend.SOURCE_SUFFIXES` folds the
    case; this used not to, so `PKG_A.PKB` was parsed for KPI-1 and never analysed)."""
    ddl = Path(schema_ddl).resolve() if schema_ddl else None
    bodies = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        suffix = path.suffix.lower()
        if suffix in BODY_SUFFIXES:
            bodies.append(path)
        elif suffix == ".sql" and path.resolve() != ddl:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _CREATES_UNIT.search(text):
                bodies.append(path)
    return bodies


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
            scalardb_schema: str | Path | None = None,
            row_locks: "RowLocks | None" = None, boundaries=None, limits=None, db_links=None,
            package_state=None, constraints=None) -> Analysis:
    """Parse, resolve and lower every source file under `root`. Nothing raises; failures become diagnostics.

    With `scalardb_schema`, every SQL statement is also checked against the target (P2-4) and the answer lands on
    the IR, so the report can say what ScalarDB can run rather than leaving it unasked.
    """
    root = Path(root)
    schema = OracleSchema.from_ddl(schema_ddl) if schema_ddl else None
    program = M.Program(id=program_id, kind="Program",
                        schema_snapshot=schema.snapshot if schema else None)
    analysis = Analysis(program=program, schema=schema)

    for body in source_bodies(root, schema_ddl):
        spec = _sibling(body, SPEC_SUFFIX)
        public: set[str] = set()
        if spec is not None:
            # the specification is parsed for its public names, and counted: KPI-1's denominator is every
            # source file, so leaving it out of `parsed` would quietly shrink the parse rate's denominator
            parsed_spec = parse_file(spec)
            analysis.parsed.append(parsed_spec)
            public = public_routines(parsed_spec)
        parsed = parse_file(body)
        symbols = build(parsed, schema, public, spec=parsed_spec if spec is not None else None)
        analysis.parsed.append(parsed)
        analysis.symbols.append(symbols)
        modules = lower_file(parsed, symbols, schema, public)
        if spec is not None:
            # what the specification declares (a constant, an exception, a type) is the package's too: without
            # it `c_max_raise_pct` was an unknown name in every body that read it (#46, samples/oracle-samples)
            for declared in lower_file(parsed_spec, None, schema, set()):
                for module in modules:
                    if module.name.lower() == declared.name.lower():
                        known = {d.name.lower() for d in module.declarations}
                        module.declarations.extend(d for d in declared.declarations if d.name.lower() not in known)
        program.modules.extend(modules)
        program.unresolved.extend(symbols.unresolved)

    # a specification with no body is still an asset: it declares an interface nothing implements here
    for spec in sorted(p for p in root.rglob("*") if p.suffix.lower() == SPEC_SUFFIX):
        if _sibling(spec, ".pkb") is not None:
            continue
        parsed = parse_file(spec)
        analysis.parsed.append(parsed)
        program.modules.extend(lower_file(parsed, None, schema, set()))

    # #12: 移行先に trigger は無いので、**書き込む側が呼ぶ**。移行先のスキーマが渡っているかに
    # 関わらず行う——「その更新が 1 行に絞れるか」は Oracle の主キーの話である
    # #26 の続き: 記録された routine の MERGE を「読んでから UPDATE か INSERT を選ぶ」へ割る。
    # trigger より前に行う——Oracle の MERGE は UPDATE / INSERT の trigger を行ごとに発火させる
    if package_state is not None and package_state.carried:
        # #46: package variables the caller carries become IN OUT parameters. Calls have to be resolved first
        from .analysis import build_call_graph
        from .package_state import carry

        build_call_graph(program)
        carry(program, package_state)
    # IDENTITY 列を INSERT に足す（採番は Sequences から）。trigger を織り込む前に行う: 織り込まれた trigger の INSERT も同じ
    from . import identity
    identity.rewrite(program, schema)
    merge.rewrite(program, row_locks, schema, analysis.symbol_table())
    # #19: 割った routine の処理対象を、キー順に件数つきで繰り返し読む
    paging.rewrite(program, boundaries, schema, analysis.symbol_table())
    # #9: 記録された routine の RMW（`SET c = c + x`）を、読み + 書きの 2 文へ割る。trigger を織り込む前に行う:
    # 織り込まれた trigger の呼び出しは SET の式を :NEW の値として受け取るので、列を読む式のままだと Java に
    # ならない（samples/oracle-samples raise_salary、2026-09-25）。capability の前でもある
    rmw.rewrite(program, row_locks, schema, analysis.symbol_table())
    # #50: 移行先に無い CHECK / FOREIGN KEY を、決めた表では書く前に評価する。RMW を割ったあと（書く値が
    # 変数になっている）、trigger を織り込む前（guard は書き込みの一部であって trigger ではない）
    from . import constraints as constraint_guards
    constraint_guards.rewrite(program, constraints, schema, analysis.symbol_table())
    triggers.rewrite(program, schema, analysis.symbol_table())
    # a DB link somebody mapped to a ScalarDB namespace: `orders@warehouse_link` -> `warehouse.orders`
    dblinks.rewrite(program, db_links)
    _record_row_limits(program, limits, boundaries)

    if scalardb_schema is not None:
        from .capability import annotate, check
        from scalardb_migrate.schema import SchemaRegistry

        registry = SchemaRegistry.from_schema_loader_json(str(scalardb_schema))
        analysis.capability = check(program, registry, analysis.symbol_table(), schema=schema,
                                    row_locks=row_locks)
        annotate(program, analysis.capability)
    return analysis


def _record_row_limits(program: M.Program, limits, boundaries=None) -> None:
    """Say on the loop that somebody decided how many rows it may read.

    CUR-002 / BULK-003 ask a person to look at the number of rows a loop reads. `limits.yaml` is where that person
    answers -- a value the generated code enforces (RowLimitExceededException), or a reason why a limit is not what
    protects this routine -- and once it is answered the question is no longer open (decided 2026-09-20). The rules
    read this diagnostic; a routine that falls to the default has decided nothing and is still reviewed.

    A routine split into one transaction per iteration (`transactions.perIteration`) answers it another way: its
    loop was rewritten to read its targets in key order, a batch at a time (`paging.rewrite`, diagnostic PAGED), so
    what it holds at once is the batch -- an operational setting -- and not the total, which nobody can decide
    (#19; limits.yaml says so itself, and `--limits-strict` never asked about these). Only a loop that was
    actually paged counts: a refused paging (PAGING_REFUSED) reads everything, and is still reviewed.
    """
    if limits is None:
        return
    from .lower import _walk

    for module in program.modules:
        for routine in module.routines:
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            for statement in statements:
                if statement.kind != "Loop" or any(d.code == "ROW_LIMIT_DECIDED" for d in statement.diagnostics):
                    continue
                query = getattr(statement, "query", None)
                paged = query is not None and any(d.code == "PAGED" for d in query.diagnostics)
                if limits.decided(routine.id):
                    statement.add("INFO", "ROW_LIMIT_DECIDED", limits.explain(routine.id))
                elif paged and boundaries is not None and routine.id in boundaries.per_iteration:
                    statement.add("INFO", "ROW_LIMIT_DECIDED",
                                  f"1 反復 = 1 トランザクションに割り、対象をキー順に件数つきで繰り返し読む形にした"
                                  f"（limits.yaml: transactions.perIteration）。一度に持つ行数は読む単位で決まる")


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
    """The AUTO prohibitions of docs/design/plsql-kpi.md §2 that this routine already trips.

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
        "（実装計画 §9、docs/design/plsql-kpi.md §0）。", "",
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


def call_graph_document(program_analysis) -> dict:
    """Who calls whom, by routine id. `program.ir.json` carries a call only where it is a statement of its own: a
    function called inside an expression (`v := f(x)`) is an edge of the call graph and nothing in the IR, so a
    reader of the files alone saw half the graph. Every routine is listed, a caller of nothing included -- an
    absent routine would read as "not analysed"."""
    graph = program_analysis.call_graph
    routines = sorted(r.id for m in program_analysis.program.modules for r in m.routines)
    return {"schemaVersion": 1,
            "routines": [{"routine": rid, "calls": sorted(graph.calls.get(rid, ())),
                          "external": sorted(graph.external.get(rid, ()))} for rid in routines]}


def write_call_graph(program_analysis, out_dir: str | Path) -> Path:
    path = Path(out_dir) / "callgraph.json"
    path.write_text(json.dumps(call_graph_document(program_analysis), ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def _range(node: M.Node) -> dict | None:
    if node.source_range is None:
        return None
    return {"file": node.source_range.file, "startLine": node.source_range.start_line,
            "endLine": node.source_range.end_line}
