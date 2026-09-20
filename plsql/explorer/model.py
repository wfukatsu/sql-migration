"""One document the page is drawn from: every table, every routine, every SQL statement, joined both ways.

Three sources, none of them a database:

* the analysis directory `python -m plsql.cli` wrote (`program.ir.json`, `decisions.json`, `callgraph.json`);
* the application's SQL files (`appsql`);
* a catalog snapshot (`snapshot`), which may be missing, or partial.

## Only SQL that is in the source

The IR holds statements the analysis added -- a trigger woven into its writer, a paging loop, a split MERGE. They
describe the conversion, not the system being investigated, and they are left out by the same test the
specification skill uses (`FROM_SOURCE`, skills/plsql-spec/scripts/spec_facts.py). The two are kept in step by a
test over the whole corpus, not by sharing code: the skill is a script shipped on its own.

## A name is a table only if it is one

The IR's read set is every `Table` node sqlglot found, which includes `INTO v_total`, a CTE's name and `dual`.
Here the statement is parsed again and those are dropped. What is left is matched to the snapshot by bare
lower-case name (the analysis drops the owner; one snapshot is one schema). `orders@link` stays its own, remote,
entry. A name the snapshot does not know is kept and marked, never dropped.

## What cannot be seen

A statement whose tables cannot be known -- dynamic SQL nobody could enumerate, SQL that does not parse, a PL/SQL
block in a script, a trigger whose source was not given and whose dependencies were not collected -- may touch any
table. It is listed once under `unattributable`, and the page says so on every table, because "this table has no
writers" is only true if nothing is hiding.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import sqlglot
from sqlglot import exp

from ..sqlbridge import read_write_sets
from . import sources
from .appsql import AppStatement, cte_names, tables_of
from .snapshot import Snapshot

FROM_SOURCE = sources.FROM_SOURCE
NOT_TABLES = {"dual"}

NOT_COLLECTED = {"state": "not_collected"}
NO_STATISTICS = {"state": "no_statistics"}
NOT_APPLICABLE = {"state": "not_applicable"}

REASON_DYNAMIC = "動的 SQL（実行時に組み立てる。変種を列挙できない）"
REASON_NOT_PARSED = "解析できない SQL"
REASON_TRIGGER_NO_SOURCE = "原文が渡されていない trigger（中の SQL は解析していない）"


class InputError(ValueError):
    """The analysis directory is not one `python -m plsql.cli --out-dir` wrote."""


def value(v) -> dict:
    return {"state": "value", "value": v}


# --- SQL inside PL/SQL --------------------------------------------------------------------------------------

def _plsql_tables(node: dict) -> tuple[list[str], list[str], bool]:
    """(reads, writes, visible). Parsed again so that an INTO target and a CTE stop being tables."""
    sql = node.get("originalSql") or ""
    try:
        tree = sqlglot.parse_one(sql, read="oracle")
    except sqlglot.errors.SqlglotError:
        tree = None
    if tree is None or isinstance(tree, exp.Command):
        targets = {str(t).split(".")[0].lower() for t in node.get("intoTargets") or []}
        reads = [t for t in node.get("readSet") or [] if t not in targets and t not in NOT_TABLES]
        writes = [t for t in node.get("writeSet") or [] if t not in NOT_TABLES]
        return reads, writes, bool(reads or writes)
    for into in list(tree.find_all(exp.Into)):
        into.pop()
    local = cte_names(tree) | NOT_TABLES
    reads, writes = read_write_sets(tree)
    return [t for t in reads if t not in local], [t for t in writes if t not in local], True


def _line(node: dict) -> int | None:
    return (node.get("sourceRange") or {}).get("startLine")


def _walk(node, found: list[dict]) -> None:
    """Statements in source order. A statement the analysis added is skipped, what it wraps is not."""
    if isinstance(node, list):
        for child in node:
            _walk(child, found)
        return
    if not isinstance(node, dict):
        return
    added = "#" in node.get("id", "") and not FROM_SOURCE.search(node["id"])
    if not added and node.get("kind") in ("SqlOperation", "DynamicSql"):
        found.append(node)
    for key in ("query", "body", "elseBody", "exceptionHandlers"):
        _walk(node.get(key), found)
    for branch in node.get("branches") or []:
        _walk(branch.get("body"), found)


def _routine_statements(routine: dict) -> list[dict]:
    found: list[dict] = []
    _walk(routine.get("body"), found)
    _walk(routine.get("exceptionHandlers"), found)
    return found


def _sql_of_routine(routine: dict, file: str) -> list[dict]:
    out = []
    for node in _routine_statements(routine):
        base = {"id": node["id"], "origin": "plsql", "routine": routine["id"], "file": file, "line": _line(node)}
        if node["kind"] == "SqlOperation":
            reads, writes, visible = _plsql_tables(node)
            out.append({**base, "kind": node.get("sqlKind") or "SQL", "reads": reads, "writes": writes,
                        "lock": node.get("lockingMode"), "text": node.get("originalSql") or "",
                        "visible": visible, "reason": None if visible else REASON_NOT_PARSED})
            continue
        variants = [v for v in node.get("variants") or [] if isinstance(v, str)]
        parsed = [(v, tables_of(v)) for v in variants]
        if parsed and all(found is not None for _, found in parsed):
            for index, (variant, (kind, reads, writes)) in enumerate(parsed):
                out.append({**base, "id": f"{node['id']}~{index}", "kind": kind, "reads": reads, "writes": writes,
                            "lock": None, "text": variant, "visible": True, "reason": None, "dynamic": True})
        else:
            out.append({**base, "kind": "DYNAMIC", "reads": [], "writes": [], "lock": None,
                        "text": node.get("expression") or "", "visible": False, "reason": REASON_DYNAMIC,
                        "dynamic": True})
    return out


# --- reading the analysis directory -------------------------------------------------------------------------

def _json(path: Path, required: bool = True):
    if not path.exists():
        if required:
            raise InputError(f"{path} が無い。先に python -m plsql.cli <src> --out-dir {path.parent} を流す")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _app_sql(statements: list[AppStatement]) -> list[dict]:
    return [{"id": f"{s.file}:{s.line}", "origin": "app", "routine": None, "file": s.file, "line": s.line,
             "kind": s.kind, "reads": [t for t in s.reads if t not in NOT_TABLES],
             "writes": [t for t in s.writes if t not in NOT_TABLES], "lock": None, "text": s.text,
             "visible": s.visible, "reason": s.reason} for s in statements]


def _spec_link(spec_dir: Path | None, module: str, out_dir: Path | None) -> str | None:
    if spec_dir is None:
        return None
    document = spec_dir / f"{module}.md"
    if not document.exists():
        return None
    try:
        return os.path.relpath(document, out_dir) if out_dir else str(document)
    except ValueError:  # another drive on Windows
        return str(document)


def build(analysis_dir: str | Path, snapshot: Snapshot | None = None,
          app_statements: list[AppStatement] | None = None, spec_dir: str | Path | None = None,
          out_dir: str | Path | None = None, src_root: str | Path | None = None,
          app_files: dict[str, Path] | None = None) -> dict:
    analysis_dir = Path(analysis_dir)
    src_root = Path(src_root) if src_root else None
    program = _json(analysis_dir / "program.ir.json")
    decisions = _json(analysis_dir / "decisions.json", required=False) or {}
    call_graph = _json(analysis_dir / "callgraph.json", required=False)
    verdicts = {record["routine"]: record for record in decisions.get("routines", [])}
    diagnostics = sources.diagnostics_of(_json(analysis_dir / "diagnostics.sarif", required=False))
    spec_dir = Path(spec_dir) if spec_dir else None
    out_dir = Path(out_dir) if out_dir else None

    warnings: list[str] = []
    modules = program.get("modules") or []
    sql: list[dict] = []
    routines: list[dict] = []
    source_triggers: dict[str, dict] = {}
    basenames: dict[str, set[str]] = {}

    for module in modules:
        for routine in module.get("routines") or []:
            where = routine.get("sourceRange") or module.get("sourceRange") or {}
            file = where.get("file", "")
            basenames.setdefault(file, set()).add(module["name"])
            statements = _sql_of_routine(routine, file)
            sql += statements
            record = verdicts.get(routine["id"], {})
            start, end = where.get("startLine"), where.get("endLine")
            routines.append({"id": routine["id"], "module": module["name"],
                             "kind": routine.get("routineKind") or "routine", "file": file,
                             "line": start, "endLine": end,
                             "verdict": record.get("verdict"), "reasons": record.get("reasons") or [],
                             "decision": _decision(record),
                             "parameters": sources.typed(routine.get("parameters")),
                             "declarations": sources.typed(routine.get("declarations")),
                             "diagnostics": [d for d in diagnostics if d["file"] == file and start and end
                                             and start <= d["line"] <= end],
                             "_node": routine,
                             "sql": [s["id"] for s in statements], "calls": [], "calledBy": [], "external": [],
                             "spec": _spec_link(spec_dir, module["name"], out_dir)})
        if module.get("moduleKind") == "trigger":
            source_triggers[module["name"].lower()] = {
                "name": module["name"].lower(), "table": (module.get("triggerTable") or "").lower() or None,
                "timing": module.get("triggerTiming"), "event": module.get("triggerEvent"),
                "routine": (module.get("routines") or [{}])[0].get("id")}

    by_id = {r["id"]: r for r in routines}
    if call_graph is None:
        warnings.append("callgraph.json が無い（古い解析結果）。呼び出し関係は出ていない。python -m plsql.cli を流し直す")
    else:
        triggers = {t["routine"] for t in source_triggers.values()}
        for record in call_graph["routines"]:
            caller = by_id.get(record["routine"])
            if caller is None:
                continue
            # a trigger woven into its writer is a call the analysis added, not one the source makes
            caller["calls"] = [c for c in record["calls"] if c not in triggers]
            caller["external"] = record["external"]
            for callee in caller["calls"]:
                if callee in by_id:
                    by_id[callee]["calledBy"].append(caller["id"])

    if any(record.get("redesign") for record in verdicts.values()):
        warnings.append("この解析結果は --limits つきで作られている。--limits は SQL を変換後の形に書き換えることが"
                        "あるので、調査には --limits なしの解析結果を使う")
    if not decisions:
        warnings.append("decisions.json が無い。判定（AUTO / REVIEW / REDESIGN）は出ていない")
    shared = {file for file, names in basenames.items() if file and len(names) > 1}
    for file in sorted(shared):
        warnings.append(f"{file} という名前の原文が複数ある。解析結果はファイル名しか持たないので、行の出どころを取り違えうる")

    sql += _app_sql(app_statements or [])
    sql += _snapshot_sql(snapshot, source_triggers)

    files = _files(modules, routines, sql, source_triggers, diagnostics, src_root, app_files or {}, warnings)
    database_code = _database_code(modules, snapshot, src_root, files)
    # a sequence is as often read in an assignment (`p_id := order_seq.NEXTVAL`) as in SQL: the whole routine is searched
    routine_texts = {routine["id"]: json.dumps(routine["_node"], ensure_ascii=False) for routine in routines}
    for routine in routines:
        routine.pop("_node")
        routine["dbCode"] = database_code["modules"].get(routine["module"], [])

    tables = _tables(sql, snapshot, source_triggers)
    unattributable = [s["id"] for s in sql if not s["visible"] and s.get("reaches") is None]
    notes = ["同じ文の中で読みも書きもするテーブルは、「書く」にだけ数えている（解析の制限）。",
             "テーブル名はスキーマ名を除いた名前で突き合わせている。別のスキーマに同じ名前のテーブルがあると 1 つに見える。"]
    if decisions and not any(r["verdict"] == "AUTO" for r in routines):
        notes.append("AUTO が 1 件も無いのは、検証の結果（--evidence）なしで解析したため。検証するまで AUTO にはならない。")

    return {"meta": {"snapshot": _snapshot_meta(snapshot), "analysis": str(analysis_dir.name),
                     "schemaSnapshot": program.get("schemaSnapshot"),
                     "counts": {"tables": len(tables), "routines": len(routines), "sql": len(sql)}},
            "warnings": warnings, "notes": notes, "unattributable": unattributable,
            "files": files, "dbObjects": database_code["objects"],
            "sequences": _sequences(sql, routine_texts, snapshot),
            "tables": tables, "routines": sorted(routines, key=lambda r: r["id"]), "sql": sql}


def _decision(record: dict) -> dict | None:
    """What the verdict rests on. `None` when there is no decisions.json: then nothing is claimed."""
    if not record:
        return None
    confidence = record.get("confidence") or {}
    return {"ruleVerdict": record.get("ruleVerdict"),
            "rules": [{"id": r.get("id"), "decision": r.get("decision"), "message": r.get("message"),
                       "severity": r.get("severity"), "source": r.get("source")} for r in record.get("rules") or []],
            "whyNotAuto": record.get("whyNotAuto") or [], "remediation": record.get("remediation") or [],
            "requiredTests": record.get("requiredTests") or [],
            "confidence": {key: value for key, value in confidence.items() if key != "zeroFactors"},
            "zeroFactors": confidence.get("zeroFactors") or []}


# --- the code -------------------------------------------------------------------------------------------------

def _files(modules: list[dict], routines: list[dict], sql: list[dict], source_triggers: dict[str, dict],
           diagnostics: list[dict], src_root: Path | None, app_files: dict[str, Path],
           warnings: list[str]) -> dict:
    """File -> its lines and what is on them. Each file's text is carried once, however many routines are in it."""
    if src_root is None and not app_files:
        return {}
    by_node: dict[str, list[dict]] = {}
    for statement in sql:
        if statement["origin"] == "plsql":
            by_node.setdefault(statement["id"].split("~")[0], []).append(statement)
    trigger_routines = {t["routine"] for t in source_triggers.values()}
    out: dict[str, dict] = {}
    if src_root is not None:
        for routine in routines:
            file = routine["file"]
            if not file:
                continue
            if file not in out:
                found = sources.find(src_root, file)
                if found is None:
                    warnings.append(f"{file} が {src_root} の下に無い。この原文のコードは出ていない")
                out[file] = {"origin": "plsql", "found": found is not None,
                             "lines": sources.read_lines(found) if found else [], "marks": []}
            if out[file]["found"]:
                out[file]["marks"] += sources.marks_of(routine["_node"], by_node, trigger_routines)
        for diagnostic in diagnostics:
            if out.get(diagnostic["file"], {}).get("found"):
                out[diagnostic["file"]]["marks"].append(
                    {"line": diagnostic["line"], "endLine": diagnostic["endLine"], "kind": "diagnostic",
                     "label": diagnostic["rule"], "message": diagnostic["message"], "level": diagnostic["level"]})
    for label, path in app_files.items():
        marks = [{"line": s["line"], "endLine": s["line"], "kind": "sql" if s["visible"] else "dynamic",
                  "label": s["kind"], "reads": s["reads"], "writes": s["writes"], "sql": s["id"],
                  "visible": s["visible"]} for s in sql if s["origin"] == "app" and s["file"] == label]
        out[f"app:{label}"] = {"origin": "app", "found": True, "lines": sources.read_lines(path), "marks": marks}
    for record in out.values():
        record["marks"].sort(key=lambda m: (m["line"], m["kind"], m["label"] or ""))
    return out


_MODULE_KINDS = {"procedure": "PROCEDURE", "function": "FUNCTION", "trigger": "TRIGGER"}


def _units(module: dict, src_root: Path | None, files: dict) -> list[dict]:
    """The database objects one IR module stands for, each with the source lines to compare (or none)."""
    span = module.get("sourceRange") or {}
    file, start, end = span.get("file", ""), span.get("startLine") or 1, span.get("endLine")
    lines = (files.get(file) or {}).get("lines") or []
    mine = lines[start - 1:end] if lines else None
    name, kind = module["name"].lower(), module.get("moduleKind")
    if kind != "package":
        return [{"type": _MODULE_KINDS.get(kind, (kind or "").upper()), "name": name, "lines": mine, "start": start,
                 "file": file}]
    if mine is not None and sources.object_kind(mine) == "PACKAGE":   # a specification nothing here implements
        return [{"type": "PACKAGE", "name": name, "lines": mine, "start": start, "file": file}]
    units = [{"type": "PACKAGE BODY", "name": name, "lines": mine, "start": start, "file": file}]
    if src_root is None:
        return units + [{"type": "PACKAGE", "name": name, "lines": None, "start": 1, "file": None, "unknown": True}]
    # the analysis lowers the body only, so the specification is taken from the sibling file
    spec = sources.find(src_root, str(Path(file).with_suffix(".pks"))) if file else None
    spec_lines = sources.read_lines(spec) if spec else None
    if spec_lines is not None and sources.object_kind(spec_lines) != "PACKAGE":
        spec_lines = None
    return units + [{"type": "PACKAGE", "name": name, "lines": spec_lines, "start": 1,
                     "file": spec.name if spec and spec_lines is not None else None}]


def _database_code(modules: list[dict], snapshot: Snapshot | None, src_root: Path | None, files: dict) -> dict:
    objects = snapshot.objects() if snapshot else None
    known = {(o["type"], o["name"]): o for o in objects or []}
    matched: set[tuple[str, str]] = set()
    by_module: dict[str, list[dict]] = {}
    for module in modules:
        results = []
        for unit in _units(module, src_root, files):
            key = (unit["type"], unit["name"])
            present = known.get(key)
            if unit["lines"] is not None or unit.get("unknown") or src_root is None:
                matched.add(key)   # without --src nobody can say a source is missing, so nothing is called DB-only
            result = {"type": unit["type"], "name": unit["name"], "file": unit["file"],
                      "status": present.get("status") if present else None,
                      "lastDdl": present.get("lastDdl") if present else None}
            code = snapshot.source_of(*key) if snapshot else None
            if snapshot is None:
                result.update(state="not_compared", reason=sources.NOT_COMPARED_NO_SNAPSHOT)
            elif objects is not None and present is None:
                result.update(state="not_in_database", reason=sources.NOT_IN_DATABASE)
            elif not snapshot.contains_source_code:
                result.update(state="not_compared", reason=sources.NOT_COMPARED_NO_CODE)
            elif code is not None and code.get("wrapped"):
                result.update(state="not_compared", reason=sources.NOT_COMPARED_WRAPPED)
            elif unit["lines"] is None:
                result.update(state="not_compared", reason=sources.NOT_COMPARED_NO_SOURCE)
            elif code is None:
                result.update(state="not_in_database", reason=sources.NOT_IN_DATABASE)
            else:
                result.update(sources.compare(unit["lines"], code["text"], unit["start"], unit["file"] or "原文"))
            results.append(result)
        by_module[module["name"]] = results

    if objects is None:
        return {"modules": by_module, "objects": {"state": "not_collected", "dbOnly": [], "outsideAnalysis": []}}

    def entry(item: dict) -> dict:
        code = snapshot.source_of(item["type"], item["name"])
        return {"type": item["type"], "name": item["name"], "status": item.get("status"),
                "lastDdl": item.get("lastDdl"), "wrapped": bool(code and code.get("wrapped")),
                "text": code.get("text") if code else None}

    db_only = [entry(o) for o in objects if o["type"] in sources.COMPARABLE and (o["type"], o["name"]) not in matched]
    outside = [entry(o) for o in objects if o["type"] in sources.OUTSIDE_ANALYSIS]
    return {"modules": by_module,
            "objects": {"state": "collected", "containsSourceCode": snapshot.contains_source_code,
                        "sourceGiven": src_root is not None,
                        "dbOnly": sorted(db_only, key=lambda o: (o["type"], o["name"])),
                        "outsideAnalysis": sorted(outside, key=lambda o: (o["type"], o["name"])),
                        "invalid": sorted(f"{o['type']} {o['name']}" for o in objects if o.get("status") == "INVALID")}}


_SEQUENCE_USE = re.compile(r"\b([A-Za-z_][\w$#]*)\.(?:NEXTVAL|CURRVAL)\b", re.I)


def _sequences(sql: list[dict], routine_texts: dict[str, str], snapshot: Snapshot | None) -> dict:
    """Every sequence the database has or the code uses, with who uses it. ScalarDB has no sequence."""
    used: dict[str, set[str]] = {}
    for routine, text in routine_texts.items():
        for name in _SEQUENCE_USE.findall(text):
            used.setdefault(name.lower(), set()).add(routine)
    for statement in sql:
        if statement["routine"]:
            continue
        for name in _SEQUENCE_USE.findall(statement["text"] or ""):
            used.setdefault(name.lower(), set()).add(statement["id"])
    known = snapshot.sequences() if snapshot else None
    by_name = {row["name"]: row for row in known or []}
    items = []
    for name in sorted(set(by_name) | set(used)):
        row = by_name.get(name)
        items.append({"name": name, "inSnapshot": None if known is None else row is not None,
                      "incrementBy": row.get("incrementBy") if row else None,
                      "cacheSize": row.get("cacheSize") if row else None,
                      "lastNumber": row.get("lastNumber") if row else None,
                      "users": sorted(used.get(name, ()))})
    return {"state": "not_collected" if known is None else "collected", "items": items}


def _snapshot_meta(snapshot: Snapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return {"ident": snapshot.ident, "formatVersion": snapshot.format_version,
            "containsSourceCode": snapshot.contains_source_code, "collectedAt": snapshot.collected_at, "database": snapshot.database,
            "schema": snapshot.schema_name, "containsDataValues": snapshot.contains_data_values,
            "skipped": snapshot.skipped}


def _snapshot_sql(snapshot: Snapshot | None, source_triggers: dict[str, dict]) -> list[dict]:
    """Views, and the triggers nobody gave the source of. Both exist only in the snapshot."""
    if snapshot is None:
        return []
    out = []
    for view in snapshot.views() or []:
        reads = snapshot.base_tables(view["name"])
        direct = _direct_references(snapshot, view["name"])
        parsed = tables_of(view["text"]) if view.get("text") else None
        if direct is None and parsed is not None:
            direct = parsed[1]
        out.append({"id": f"view:{view['name']}", "origin": "view", "routine": None, "file": None, "line": None,
                    "kind": "VIEW", "object": view["name"], "reads": direct or [], "writes": [], "lock": None,
                    "text": view.get("text") or "", "visible": direct is not None,
                    "reason": None if direct is not None else "view の定義も依存関係も取れていない",
                    "baseTables": reads})
    for trigger in snapshot.triggers() or []:
        if trigger["name"] in source_triggers:
            continue
        reaches = _direct_references(snapshot, trigger["name"], "TRIGGER")
        out.append({"id": f"trigger:{trigger['name']}", "origin": "trigger", "routine": None, "file": None,
                    "line": None, "kind": "TRIGGER", "object": trigger["name"], "reads": [], "writes": [],
                    "lock": None, "text": trigger.get("body") or "", "visible": False,
                    "reason": REASON_TRIGGER_NO_SOURCE,
                    # the catalog knows which tables the body names, though not what it does to them
                    "reaches": None if reaches is None else sorted(set(reaches) - {trigger.get("table")})})
    return out


def _direct_references(snapshot: Snapshot, name: str, kind: str = "VIEW") -> list[str] | None:
    rows = snapshot.sections.get("dependencies")
    if rows is None:
        return None
    return sorted({row["refName"] for row in rows if row["name"] == name and row["type"] == kind
                   and row["refType"] in ("TABLE", "VIEW", "MATERIALIZED VIEW")})


# --- tables ---------------------------------------------------------------------------------------------------

def _touch(statement: dict, how: str, via: str | None = None) -> dict:
    return {"sql": statement["id"], "how": how, "via": via}


def _count(touches: list[dict], by_id: dict[str, dict], hows: set[str]) -> dict:
    statements = [by_id[t["sql"]] for t in touches if t["how"] in hows]
    return {"routines": len({s["routine"] for s in statements if s["routine"]}),
            "statements": len({s["id"] for s in statements if not s["routine"]})}


def _tables(sql: list[dict], snapshot: Snapshot | None, source_triggers: dict[str, dict]) -> list[dict]:
    by_id = {s["id"]: s for s in sql}
    views = (snapshot.view_names() if snapshot else None) or set()
    touches: dict[str, list[dict]] = {}
    for statement in sql:
        for table in statement["writes"]:
            touches.setdefault(table, []).append(_touch(statement, "write"))
        for table in statement["reads"]:
            touches.setdefault(table, []).append(_touch(statement, "read"))
            if table in views and statement["origin"] != "view":
                for base in snapshot.base_tables(table) or []:
                    touches.setdefault(base, []).append(_touch(statement, "read", via=table))
        for table in statement.get("reaches") or []:
            touches.setdefault(table, []).append(_touch(statement, "unseen"))

    names = set(touches) | set((snapshot.table_names() if snapshot else None) or []) | views
    names |= {t["table"] for t in source_triggers.values() if t["table"]}
    out = []
    for name in sorted(names):
        mine = touches.get(name, [])
        kind = "remote" if "@" in name else "view" if name in views else "table"
        record = {"name": name, "kind": kind, "touches": mine,
                  "writers": _count(mine, by_id, {"write"}), "readers": _count(mine, by_id, {"read"}),
                  "unseen": [t["sql"] for t in mine if t["how"] == "unseen"]}
        record.update(_from_snapshot(name, kind, snapshot, source_triggers))
        out.append(record)
    return out


def _from_snapshot(name: str, kind: str, snapshot: Snapshot | None, source_triggers: dict[str, dict]) -> dict:
    known_triggers = [t for t in source_triggers.values() if t["table"] == name]
    attached = [{"type": "trigger", "name": t["name"], "timing": t["timing"], "event": t["event"],
                 "inSource": True, "inSnapshot": None, "enabled": None, "routine": t["routine"]}
                for t in known_triggers]
    nothing = {"presence": "not_collected", "rows": NOT_COLLECTED, "size": NOT_COLLECTED,
               "foreignKeysOut": None, "foreignKeysIn": None, "columns": None, "constraints": None,
               "indexes": None, "columnStatistics": None, "attached": attached,
               "modifications": NOT_COLLECTED, "partitioning": None, "comment": None, "lobColumns": None,
               "temporary": None,
               "triggerCount": {"state": "not_collected", "known": len(known_triggers)}, "baseTables": None}
    if snapshot is None:
        return nothing
    if kind == "remote":
        return {**nothing, "presence": "remote"}
    if kind == "view":
        return {**nothing, "presence": "snapshot", "rows": NOT_APPLICABLE, "size": NOT_APPLICABLE,
                "baseTables": snapshot.base_tables(name), "triggerCount": NOT_APPLICABLE}
    present = snapshot.has_table(name)
    if present is None:
        return nothing
    if not present:
        return {**nothing, "presence": "missing"}

    statistics = snapshot.table_statistics(name)
    if statistics is None:
        rows = NOT_COLLECTED
    elif statistics["numRows"] is None:
        rows = NO_STATISTICS
    else:
        rows = {**value(statistics["numRows"]), "analyzedAt": statistics.get("lastAnalyzed"),
                "stale": statistics.get("stale"), "avgRowLen": statistics.get("avgRowLen")}
    size = snapshot.size_bytes(name)

    snapshot_triggers = snapshot.triggers(name)
    if snapshot_triggers is not None:
        attached = []
        for trigger in snapshot_triggers:
            source = source_triggers.get(trigger["name"])
            attached.append({"type": "trigger", "name": trigger["name"], "timing": trigger["timing"],
                             "event": trigger["event"], "inSource": source is not None, "inSnapshot": True,
                             "enabled": trigger.get("enabled"), "routine": source["routine"] if source else None,
                             "sql": None if source else f"trigger:{trigger['name']}"})
        in_snapshot = {t["name"] for t in snapshot_triggers}
        attached += [{"type": "trigger", "name": t["name"], "timing": t["timing"], "event": t["event"],
                      "inSource": True, "inSnapshot": False, "enabled": None, "routine": t["routine"]}
                     for t in known_triggers if t["name"] not in in_snapshot]
        trigger_count = value(len(attached))
    else:
        trigger_count = {"state": "not_collected", "known": len(known_triggers)}
    for view in snapshot.views_on(name) or []:
        attached.append({"type": "view", "name": view, "inSource": False, "inSnapshot": True})

    modifications = snapshot.modifications(name)
    info = snapshot.table_info(name) or {}
    comments = snapshot.column_comments(name)
    columns = snapshot.columns(name)
    if columns is not None and comments is not None:
        columns = [dict(column, comment=comments.get(column["name"])) for column in columns]
    return {"presence": "snapshot", "rows": rows, "size": NOT_COLLECTED if size is None else value(size),
            "modifications": NOT_COLLECTED if modifications is None else
            {"state": "value", "value": modifications["inserts"] + modifications["updates"] + modifications["deletes"],
             "inserts": modifications["inserts"], "updates": modifications["updates"],
             "deletes": modifications["deletes"], "truncated": modifications.get("truncated"),
             "timestamp": modifications.get("timestamp")},
            "partitioning": snapshot.partitioning(name), "comment": info.get("comment"),
            "lobColumns": snapshot.lob_columns(name), "temporary": info.get("temporary"),
            "foreignKeysOut": snapshot.foreign_keys_out(name), "foreignKeysIn": snapshot.foreign_keys_in(name),
            "columns": columns, "constraints": snapshot.constraints(name),
            "indexes": snapshot.indexes(name), "columnStatistics": _column_statistics(snapshot, name),
            "attached": attached, "triggerCount": trigger_count, "baseTables": None}


def _column_statistics(snapshot: Snapshot, table: str) -> list[dict] | None:
    statistics = snapshot.column_statistics(table)
    if statistics is None:
        return None
    table_statistics = snapshot.table_statistics(table) or {}
    rows = table_statistics.get("numRows")
    for record in statistics:
        # of the rows: how many are NULL here, and how many distinct values the rest hold. Nothing is computed
        # from a table nobody analysed, or an empty one
        usable = bool(rows) and record.get("numNulls") is not None and record.get("numDistinct") is not None
        record["nullRatio"] = record["numNulls"] / rows if usable else None
        record["selectivity"] = record["numDistinct"] / rows if usable else None
        record["frequent"] = snapshot.histogram(table, record["column"]) if snapshot.contains_data_values else None
    return statistics


def unseen_for(data: dict, table: str) -> list[str]:
    """Every statement that may touch `table` without anybody being able to say so: the ones known to reach it,
    and the ones that may reach anything."""
    record = next(t for t in data["tables"] if t["name"] == table)
    return record["unseen"] + data["unattributable"]
