"""Generate schema.json from the dataclasses in model.py.

The schema exists so that a document can be rejected before anything reads it, and the dataclasses exist so the
rest of the code is typed. Writing both by hand guarantees they drift, so the schema is derived and a test asserts
the committed file still matches. Adding a field to a node is therefore a one-line change plus `make -C plsql/ir`.

    python -m plsql.ir._schema_gen        # rewrite plsql/ir/schema.json
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from . import model as M
from .serde import SCHEMA_PATH, _key

NODE_CLASSES = [M.Program, M.Module, M.Routine, M.Parameter, M.Declaration, M.ExceptionHandler,
                M.Assignment, M.Call, M.Raise, M.Return, M.TransactionStatement, M.If, M.Case,
                M.Loop, M.Block, M.SqlOperation, M.DynamicSql, M.CursorStatement, M.ControlStatement,
                M.Unsupported, M.Statement]

_DEFS = {
    "sourceRange": {
        "type": "object", "required": ["file", "startLine", "endLine"],
        "properties": {"file": {"type": "string"}, "startLine": {"type": "integer"},
                       "endLine": {"type": "integer"}, "startColumn": {"type": "integer"},
                       "endColumn": {"type": "integer"}},
        "additionalProperties": False},
    "issue": {
        "type": "object", "required": ["severity", "code", "message"],
        "properties": {"severity": {"enum": ["INFO", "WARN", "ERROR"]}, "code": {"type": "string"},
                       "message": {"type": "string"}, "range": {"$ref": "#/$defs/sourceRange"}},
        "additionalProperties": False},
    "typeRef": {
        "type": "object", "required": ["oracle"],
        "properties": {"oracle": {"type": "string"}, "resolved": {"type": ["string", "null"]},
                       "origin": {"enum": ["declared", "rowtype", "record", "collection", "column-type",
                                           "inferred", "unresolved"]},
                       "schemaSnapshot": {"type": ["string", "null"]},
                       "nullable": {"type": ["boolean", "null"]},
                       "range": {"type": ["string", "null"]}},
        "additionalProperties": False},
    "bind": {
        "type": "object", "required": ["name"],
        "properties": {"name": {"type": "string"}, "direction": {"enum": ["IN", "OUT", "IN OUT"]},
                       "oracleType": {"type": ["string", "null"]},
                       "plsqlVariable": {"type": ["string", "null"]}},
        "additionalProperties": False},
    "branch": {
        "type": "object", "required": ["condition"],
        "properties": {"condition": {"type": "string"}, "body": {"$ref": "#/$defs/statements"}},
        "additionalProperties": False},
    "statements": {"type": "array", "items": {"$ref": "#/$defs/node"}},
}

_NODE_LISTS = ("modules", "routines", "parameters", "declarations", "exception_handlers")


SKIP = {"variant_statements"}  # derived, not serialised (P4-7)


def _property(field) -> dict:
    name, annotation = field.name, str(field.type)
    if name == "source_range":
        return {"$ref": "#/$defs/sourceRange"}
    if name in ("diagnostics", "unresolved"):
        return {"type": "array", "items": {"$ref": "#/$defs/issue"}}
    if name in ("type", "return_type"):
        return {"$ref": "#/$defs/typeRef"}
    if name in ("binds", "using"):
        return {"type": "array", "items": {"$ref": "#/$defs/bind"}}
    if name == "branches":
        return {"type": "array", "items": {"$ref": "#/$defs/branch"}}
    if name in ("body", "else_body"):
        return {"$ref": "#/$defs/statements"}
    if name == "variants":
        # P4-7: {"guard": ..., "sql": ...} per statement the dynamic SQL can run
        return {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"guard": {"type": "string"}, "sql": {"type": "string"}}}}
    if name == "query":
        # a cursor FOR loop's query is one statement, not a list (P4-5)
        return {"$ref": "#/$defs/node"}
    if name in _NODE_LISTS:
        return {"type": "array", "items": {"$ref": "#/$defs/node"}}
    if name == "transaction_effects":
        return {"type": "object", "additionalProperties": False,
                "properties": {k: {"type": "boolean" if k == "autonomous" else "integer"}
                               for k in ("commits", "rollbacks", "savepoints", "autonomous")}}
    if name == "external_effects":
        return {"type": "object", "additionalProperties": False,
                "properties": {"dbLinks": {"type": "array", "items": {"type": "string"}},
                               "packages": {"type": "array", "items": {"type": "string"}},
                               "dynamicSql": {"type": "boolean"}}}
    if name == "confidence":
        return {"type": "number", "minimum": 0, "maximum": 1}
    if name == "kind":
        return {"type": "string"}
    if "list[str]" in annotation:
        return {"type": "array", "items": {"type": "string"}}
    if "bool" in annotation:
        return {"type": "boolean"}
    if "int" in annotation:
        return {"type": ["integer", "null"]}
    return {"type": ["string", "null"]}


def _ref(cls: type) -> str:
    return cls.__name__[0].lower() + cls.__name__[1:]


def build() -> dict:
    defs = dict(_DEFS)
    defs["node"] = {"anyOf": [{"$ref": f"#/$defs/{_ref(c)}"} for c in NODE_CLASSES]}
    for cls in NODE_CLASSES:
        defs[_ref(cls)] = {
            "type": "object", "required": ["id", "kind"], "additionalProperties": False,
            "properties": {_key(f.name): _property(f) for f in fields(cls) if f.name not in SKIP},
        }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "PL/SQL Migration IR",
        "description": "P1-4. Generated from plsql/ir/model.py by plsql.ir._schema_gen; do not edit by hand.",
        "allOf": [{"$ref": "#/$defs/program"}],
        "$defs": defs,
    }


def write(path: Path | None = None) -> Path:
    target = path or SCHEMA_PATH
    target.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    print("wrote", write())
