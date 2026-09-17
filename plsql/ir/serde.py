"""P1-4: IR <-> JSON, with the schema as the gate.

The IR is written to disk and read back by other stages and by a later version of the tool, so serialisation is
part of the contract rather than a convenience. Two things follow:

* every document carries `schemaVersion`; a reader that does not understand it refuses rather than guesses
* `to_dict` output is validated against `schema.json` in the tests, so a field added to a dataclass without a
  schema entry fails loudly instead of travelling as an untyped extra

Round-tripping is exact for everything the schema describes: `from_dict(to_dict(x)) == x`.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from ..source import Issue, SourceRange
from . import model as M

SCHEMA_PATH = Path(__file__).with_name("schema.json")

# kind -> dataclass, for reading a document back. `kind` is the discriminator on the wire.
NODE_TYPES: dict[str, type] = {
    "Program": M.Program, "Module": M.Module, "Routine": M.Routine, "Parameter": M.Parameter,
    "Declaration": M.Declaration, "ExceptionHandler": M.ExceptionHandler, "Assignment": M.Assignment,
    "Call": M.Call, "Raise": M.Raise, "Return": M.Return, "Commit": M.TransactionStatement,
    "Rollback": M.TransactionStatement, "Savepoint": M.TransactionStatement, "If": M.If, "Case": M.Case,
    "Loop": M.Loop, "SqlOperation": M.SqlOperation, "DynamicSql": M.DynamicSql, "Statement": M.Statement,
    "OpenCursor": M.CursorStatement, "Fetch": M.CursorStatement, "CloseCursor": M.CursorStatement,
    "Exit": M.ControlStatement, "Continue": M.ControlStatement, "Goto": M.ControlStatement,
    "Null": M.ControlStatement, "Unsupported": M.Unsupported,
}

_CAMEL = {
    "source_range": "sourceRange", "schema_version": "schemaVersion", "declaration_kind": "declarationKind",
    "routine_kind": "routineKind", "module_kind": "moduleKind", "loop_kind": "loopKind",
    "sql_kind": "sqlKind", "original_sql": "originalSql", "into_targets": "intoTargets",
    "read_set": "readSet", "write_set": "writeSet", "target_status": "targetStatus", "target_sql": "targetSql",
    "plan_id": "planId", "locking_mode": "lockingMode", "constant_sql": "constantSql",
    "error_code": "errorCode", "return_type": "returnType", "auth_id": "authId",
    "exception_handlers": "exceptionHandlers", "transaction_effects": "transactionEffects",
    "external_effects": "externalEffects", "trigger_event": "triggerEvent", "trigger_table": "triggerTable",
    "trigger_timing": "triggerTiming", "schema_snapshot": "schemaSnapshot", "else_body": "elseBody",
    "resolved_to": "resolvedTo", "oracle_type": "oracleType", "plsql_variable": "plsqlVariable",
    "db_links": "dbLinks", "dynamic_sql": "dynamicSql", "start_line": "startLine", "end_line": "endLine",
    "start_column": "startColumn", "end_column": "endColumn", "has_package_state": "hasPackageState",
    # P3-1: what each bind and each select item was attributed to
    "selects_star": "selectsStar", "into_columns": "intoColumns", "into_types": "intoTypes",
    "into_oracle_types": "intoOracleTypes", "scalardb_type": "scalardbType",
    "column_oracle_type": "columnOracleType",
    "expression": "expression",
}
_SNAKE = {v: k for k, v in _CAMEL.items()}


def _key(name: str) -> str:
    return _CAMEL.get(name, name)


def to_dict(value: Any) -> Any:
    if isinstance(value, (SourceRange, Issue)) or is_dataclass(value):
        out: dict[str, Any] = {}
        for f in fields(value):
            item = getattr(value, f.name)
            if item is None or (isinstance(item, (list, dict)) and not item):
                continue  # keep documents small; absence and emptiness mean the same thing here
            out[_key(f.name)] = to_dict(item)
        return out
    if isinstance(value, list):
        return [to_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: to_dict(v) for k, v in value.items()}
    return value


def _from(cls: type, data: dict) -> Any:
    kwargs: dict[str, Any] = {}
    by_name = {f.name: f for f in fields(cls)}
    for key, value in data.items():
        name = _SNAKE.get(key, key)
        if name not in by_name:
            continue  # unknown keys are dropped rather than crashing a reader of a newer document
        kwargs[name] = _revive(name, value)
    return cls(**kwargs)


_STATEMENT_LISTS = {"body", "else_body"}


def _revive(name: str, value: Any) -> Any:
    if name == "source_range" and isinstance(value, dict):
        return SourceRange(**{_SNAKE.get(k, k): v for k, v in value.items()})
    if name in ("diagnostics", "unresolved") and isinstance(value, list):
        return [_issue(v) for v in value]
    if name in ("type", "return_type") and isinstance(value, dict):
        return M.TypeRef(**{_SNAKE.get(k, k): v for k, v in value.items()})
    if name == "transaction_effects" and isinstance(value, dict):
        return M.TransactionEffects(**value)
    if name == "external_effects" and isinstance(value, dict):
        return M.ExternalEffects(**{_SNAKE.get(k, k): v for k, v in value.items()})
    if name in ("binds", "using") and isinstance(value, list):
        return [M.BindVariable(**{_SNAKE.get(k, k): v for k, v in item.items()}) for item in value]
    if name == "branches" and isinstance(value, list):
        return [M.Branch(condition=item["condition"], body=[node(s) for s in item.get("body", [])])
                for item in value]
    if name in _STATEMENT_LISTS and isinstance(value, list):
        return [node(v) for v in value]
    if name in ("modules", "routines", "parameters", "declarations", "exception_handlers") \
            and isinstance(value, list):
        return [node(v) for v in value]
    return value


def _issue(data: dict) -> Issue:
    rng = data.get("range")
    return Issue(data["severity"], data["code"], data["message"],
                 SourceRange(**{_SNAKE.get(k, k): v for k, v in rng.items()}) if rng else None)


def node(data: dict) -> Any:
    kind = data.get("kind")
    cls = NODE_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown IR node kind: {kind!r}")
    return _from(cls, data)


def dumps(program: M.Program, indent: int = 1) -> str:
    return json.dumps(to_dict(program), ensure_ascii=False, indent=indent, sort_keys=False) + "\n"


def loads(text: str) -> M.Program:
    data = json.loads(text)
    version = data.get("schemaVersion")
    if version is None:
        raise ValueError("the document has no schemaVersion")
    major = version.split(".")[0]
    if major != M.SCHEMA_VERSION.split(".")[0]:
        raise ValueError(
            f"IR schemaVersion {version} cannot be read by this build ({M.SCHEMA_VERSION}): "
            "major versions differ, so fields may have changed meaning")
    return node(data)


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(program: M.Program) -> None:
    """Raise if the serialised program does not match schema.json."""
    import jsonschema  # imported lazily: only validation needs it

    jsonschema.validate(to_dict(program), schema())
