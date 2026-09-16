"""P2-5: records for `%ROWTYPE` and for the values a routine hands back.

Two shapes come out of here:

* a **row record** per `%ROWTYPE`, with one component per column. The design document (§5.3) is explicit that the
  mapping is by column *name*, not position -- a record built positionally silently swaps two columns of the same
  type the first time the DDL changes.
* a **result record** per routine that has `OUT` parameters. PL/SQL writes through its arguments; Java should not.
  Returning one value keeps the caller from seeing a half-updated set when an exception interrupts the routine
  (design document §6.2, `NOCOPY`).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ir import model as M
from .emit import JavaFile
from .types import java_class_name, java_name, java_type, record_columns


@dataclass
class Dto:
    file: JavaFile
    kind: str          # row | result


def row_record(name: str, resolved: str, package: str, source: str = "") -> Dto | None:
    """A record for a `%ROWTYPE`, one component per column, in DDL order."""
    columns = record_columns(resolved)
    if not columns:
        return None
    file = JavaFile(package=package, name=java_class_name(name) + "Row", source=source)
    components = []
    for column, oracle in columns:
        mapped = java_type(oracle)
        file.add_import(*mapped.imports)
        components.append(f"{mapped.name} {java_name(column)}")
    file.comment(f"%ROWTYPE of {name}. Components follow the column names, never their order.")
    file.line(f"public record {file.name}({', '.join(components)}) {{}}")
    return Dto(file=file, kind="row")


def result_record(routine: M.Routine, package: str, source: str = "") -> Dto | None:
    """A record carrying what the routine produces: its return value and every OUT parameter."""
    outs = [p for p in routine.parameters if p.direction in ("OUT", "IN OUT")]
    if not outs and routine.return_type is None:
        return None
    if not outs:
        return None  # a plain return value needs no record

    file = JavaFile(package=package, name=java_class_name(routine.name) + "Result", source=source)
    components = []
    if routine.return_type is not None:
        mapped = java_type(routine.return_type.resolved or routine.return_type.oracle)
        file.add_import(*mapped.imports)
        components.append(f"{mapped.name} returned")
    for parameter in outs:
        mapped = java_type(parameter.type.resolved if parameter.type else None)
        file.add_import(*mapped.imports)
        components.append(f"{mapped.name} {java_name(parameter.name)}")
    file.comment(
        f"What {routine.name} produces. PL/SQL writes through its OUT arguments; returning one value instead "
        "keeps a caller from seeing a half-updated set when an exception interrupts the routine.")
    file.line(f"public record {file.name}({', '.join(components)}) {{}}")
    return Dto(file=file, kind="result")


def dtos_for(module: M.Module, package: str) -> list[Dto]:
    out: list[Dto] = []
    seen: set[str] = set()
    for routine in module.routines:
        source = f"{module.name}.{routine.name}"
        for declaration in routine.declarations:
            if declaration.type and declaration.type.origin == "rowtype" and declaration.type.resolved:
                base = declaration.type.oracle.split("%")[0]
                if base.lower() in seen:
                    continue
                seen.add(base.lower())
                record = row_record(base, declaration.type.resolved, package, source)
                if record is not None:
                    out.append(record)
        result = result_record(routine, package, source)
        if result is not None:
            out.append(result)
    return out
