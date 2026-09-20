"""The code itself: the source files, what is on each line, and whether the database runs the same thing.

## Finding a file

The IR knows a file by its name only (`sourceRange.file`), so a file is looked for the way the fingerprint looks
for it (`plsql/fingerprint.py`): under the root, else anywhere below it by name. Looking any other way would let
the page show lines from a file the analysis never read.

## What is on a line

Marks come from the IR's source ranges and from the diagnostics. A statement the analysis added (a trigger woven
into its writer) has a source range too -- the writer's -- and would put a SELECT on a line that holds none, so
the same `FROM_SOURCE` test that keeps added SQL off the list keeps added marks off the code. A declaration the
analysis added is named `#decl-<name>`; one from the source is `#decl-<digits>`.

## The same code, or not

The unit is one database object. For a procedure, a function or a trigger that is one IR module; a package is two
objects, and the analysis lowers only the body when there is one, so the specification is compared from the
sibling `.pks`. `CREATE OR REPLACE`, the `/` that ends a SQL*Plus unit, trailing blanks and blank lines are how a
file differs from USER_SOURCE without differing; anything else is a difference. That normalisation decides *same
or different* and nothing more: the diff a person reads is made from the lines as they are, numbered as the file
numbers them.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

FROM_SOURCE = re.compile(r"#(?:stmt|handler)-\d+(?:#query)?$")
FROM_SOURCE_DECLARATION = re.compile(r"#(?:param|decl)-\d+$")

_CREATE = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?", re.I)
_KIND = re.compile(r"^\s*(PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE\s+BODY|TYPE)\b", re.I)
_HUNK = re.compile(r"^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@")

# database objects the analysis lowers to a module. A TYPE is an object too, and no module: not "source not given"
COMPARABLE = ("PROCEDURE", "FUNCTION", "PACKAGE", "PACKAGE BODY", "TRIGGER")
OUTSIDE_ANALYSIS = ("TYPE", "TYPE BODY")

NOT_COMPARED_NO_CODE = "USER_SOURCE のコードは取っていない（--include-source なしの snapshot）"
NOT_COMPARED_NO_SNAPSHOT = "snapshot が渡されていない"
NOT_COMPARED_WRAPPED = "DB のコードは wrapped（難読化）されている"
NOT_COMPARED_NO_SOURCE = "原文が渡されていない"
NOT_IN_DATABASE = "この名前のオブジェクトは snapshot に無い"


def find(root: Path, file: str) -> Path | None:
    found = root / file
    if found.is_file():
        return found
    return next(iter(sorted(root.rglob(Path(file).name))), None)


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8-sig").splitlines()


# --- what is on a line ------------------------------------------------------------------------------------

def is_added(node: dict) -> bool:
    return "#" in node.get("id", "") and not FROM_SOURCE.search(node["id"])


def _walk(node, found: list[dict]) -> None:
    if isinstance(node, list):
        for child in node:
            _walk(child, found)
        return
    if not isinstance(node, dict):
        return
    if "kind" in node and "sourceRange" in node and not is_added(node):
        found.append(node)
    for key in ("query", "body", "elseBody", "exceptionHandlers"):
        _walk(node.get(key), found)
    for branch in node.get("branches") or []:
        _walk(branch.get("body"), found)


def _mark(node: dict, kind: str, label: str, **extra) -> dict:
    span = node["sourceRange"]
    return {"line": span["startLine"], "endLine": span.get("endLine") or span["startLine"], "kind": kind,
            "label": label, **extra}


def marks_of(routine: dict, sql_by_id: dict[str, list[dict]], trigger_routines: set[str]) -> list[dict]:
    """`sql_by_id`: the explorer's own SQL records by IR node id -- their tables are the cleaned ones."""
    nodes: list[dict] = []
    _walk(routine.get("body"), nodes)
    _walk(routine.get("exceptionHandlers"), nodes)
    out = []
    for node in nodes:
        kind = node["kind"]
        if kind == "SqlOperation":
            for record in sql_by_id.get(node["id"], []):
                out.append(_mark(node, "sql", record["kind"], reads=record["reads"], writes=record["writes"],
                                 sql=record["id"], visible=record["visible"]))
            if node.get("lockingMode"):
                out.append(_mark(node, "lock", node["lockingMode"]))
        elif kind == "DynamicSql":
            out.append(_mark(node, "dynamic", "動的 SQL", sql=(sql_by_id.get(node["id"]) or [{}])[0].get("id")))
        elif kind == "Raise":
            code = node.get("errorCode")
            out.append(_mark(node, "raise", f"RAISE {code}" if code is not None else f"RAISE {node.get('exception') or ''}".strip()))
        elif kind == "ExceptionHandler":
            out.append(_mark(node, "handler", "WHEN " + " OR ".join(node.get("exceptions") or [])))
        elif kind in ("Commit", "Rollback", "Savepoint"):
            out.append(_mark(node, "transaction", kind.upper()))
        elif kind == "Call":
            callee = node.get("resolvedTo") or node.get("callee") or ""
            if callee and callee not in trigger_routines:
                out.append(_mark(node, "call", f"CALL {callee}", routine=node.get("resolvedTo")))
    return out


def diagnostics_of(sarif: dict | None) -> list[dict]:
    out = []
    for run in (sarif or {}).get("runs", []):
        for result in run.get("results", []):
            for location in result.get("locations", []):
                physical = location.get("physicalLocation", {})
                region = physical.get("region", {})
                if "startLine" not in region:
                    continue
                out.append({"file": physical.get("artifactLocation", {}).get("uri", ""), "line": region["startLine"],
                            "endLine": region.get("endLine") or region["startLine"], "rule": result.get("ruleId"),
                            "level": result.get("level"), "message": result.get("message", {}).get("text", "")})
    return out


def typed(nodes: list[dict]) -> list[dict]:
    """Parameters or declarations that are in the source, with the type as written and as resolved."""
    out = []
    for node in nodes or []:
        if not FROM_SOURCE_DECLARATION.search(node.get("id", "")):
            continue
        kind = node.get("type") or {}
        out.append({"name": node.get("name"), "mode": node.get("mode"), "written": kind.get("oracle"),
                    "resolved": kind.get("resolved"), "origin": kind.get("origin"),
                    "line": (node.get("sourceRange") or {}).get("startLine")})
    return out


# --- the same code, or not --------------------------------------------------------------------------------

def object_kind(lines: list[str]) -> str | None:
    """PROCEDURE / PACKAGE BODY / ... as the text declares it, or `None` when it declares nothing we know."""
    for line in lines:
        if not line.strip() or line.lstrip().startswith("--"):
            continue
        match = _KIND.match(_CREATE.sub("", line, count=1))
        return re.sub(r"\s+", " ", match.group(1)).upper() if match else None
    return None


def _without_create(lines: list[str]) -> list[str]:
    out, done = [], False
    for line in lines:
        if not done and line.strip():
            line, done = _CREATE.sub("", line, count=1), True
        out.append(line.rstrip())
    while out and out[-1].strip() in ("", "/"):
        out.pop()
    return out


def normalise(lines: list[str]) -> list[str]:
    return [line for line in _without_create(lines) if line.strip()]


def compare(source_lines: list[str], database_text: str, start_line: int = 1, source_name: str = "原文") -> dict:
    database_lines = database_text.splitlines()
    if normalise(source_lines) == normalise(database_lines):
        return {"state": "same"}
    # the leading blank lines stay in, so that line n of the slice is still line n
    mine, theirs = _without_create(source_lines), _without_create(database_lines)
    diff = []
    for line in difflib.unified_diff(mine, theirs, fromfile=source_name, tofile="DB（USER_SOURCE）", lineterm="", n=2):
        match = _HUNK.match(line)
        if match:  # the source side is numbered as the file numbers it
            line = (f"@@ -{int(match.group(1)) + start_line - 1}{match.group(2) or ''} "
                    f"+{match.group(3)}{match.group(4) or ''} @@")
        diff.append(line)
    return {"state": "different", "diff": "\n".join(diff)}
