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
from .appsql import AppStatement, cte_names, tables_of
from .snapshot import Snapshot

FROM_SOURCE = re.compile(r"#(?:stmt|handler)-\d+(?:#query)?$")
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
          out_dir: str | Path | None = None) -> dict:
    analysis_dir = Path(analysis_dir)
    program = _json(analysis_dir / "program.ir.json")
    decisions = _json(analysis_dir / "decisions.json", required=False) or {}
    call_graph = _json(analysis_dir / "callgraph.json", required=False)
    verdicts = {record["routine"]: record for record in decisions.get("routines", [])}
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
            routines.append({"id": routine["id"], "module": module["name"],
                             "kind": routine.get("routineKind") or "routine", "file": file,
                             "line": where.get("startLine"), "endLine": where.get("endLine"),
                             "verdict": record.get("verdict"), "reasons": record.get("reasons") or [],
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
            "tables": tables, "routines": sorted(routines, key=lambda r: r["id"]), "sql": sql}


def _snapshot_meta(snapshot: Snapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return {"ident": snapshot.ident, "collectedAt": snapshot.collected_at, "database": snapshot.database,
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

    return {"presence": "snapshot", "rows": rows, "size": NOT_COLLECTED if size is None else value(size),
            "foreignKeysOut": snapshot.foreign_keys_out(name), "foreignKeysIn": snapshot.foreign_keys_in(name),
            "columns": snapshot.columns(name), "constraints": snapshot.constraints(name),
            "indexes": snapshot.indexes(name), "columnStatistics": _column_statistics(snapshot, name),
            "attached": attached, "triggerCount": trigger_count, "baseTables": None}


def _column_statistics(snapshot: Snapshot, table: str) -> list[dict] | None:
    statistics = snapshot.column_statistics(table)
    if statistics is None:
        return None
    for record in statistics:
        record["frequent"] = snapshot.histogram(table, record["column"]) if snapshot.contains_data_values else None
    return statistics


def unseen_for(data: dict, table: str) -> list[str]:
    """Every statement that may touch `table` without anybody being able to say so: the ones known to reach it,
    and the ones that may reach anything."""
    record = next(t for t in data["tables"] if t["name"] == table)
    return record["unseen"] + data["unattributable"]
