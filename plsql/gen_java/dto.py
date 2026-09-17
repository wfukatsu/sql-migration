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


def row_record(name: str, resolved: str, package: str, source: str = "",
               suffix: str = "Row", note: str | None = None) -> Dto | None:
    """A record for a `%ROWTYPE`, one component per column, in DDL order."""
    columns = record_columns(resolved)
    if not columns:
        return None
    file = JavaFile(package=package, name=java_class_name(name) + suffix, source=source)
    components = []
    for column, oracle in columns:
        mapped = java_type(oracle)
        file.add_import(*mapped.imports)
        components.append(f"{mapped.name} {java_name(column)}")
    file.comment(note or f"%ROWTYPE of {name}. Components follow the column names, never their order.")
    file.line(f"public record {file.name}({', '.join(components)}) {{}}")
    return Dto(file=file, kind="row")


def _loops(routine: M.Routine) -> list:
    from ..lower import _walk

    return [s for s in _walk(routine.body) + [x for h in routine.exception_handlers for x in _walk(h.body)]
            if s.kind == "Loop" and getattr(s, "query", None) is not None]


def loop_component_type(oracle: str | None):
    """The Java type of one loop-row component.

    Every number is a BigDecimal, unlike a `%ROWTYPE` record, which takes the column's own width. The record
    here models the PL/SQL loop variable, not the ScalarDB column: in PL/SQL `r.qty` is a NUMBER like every
    other number, and it is passed to routines whose parameters are NUMBER. Giving it the column's narrower
    Java type puts a Long where a BigDecimal is wanted, at a call site the translator cannot coerce because it
    does not know the callee's parameter types.
    """
    mapped = java_type(oracle)
    if mapped.name in ("Long", "Integer"):
        return java_type("NUMBER")
    return mapped


def loop_row_record(routine: M.Routine, loop, package: str, source: str = "") -> Dto | None:
    """One record per cursor FOR loop, named after the columns its query selects.

    The loop body reads `r.qty`, so the record's components have to be the query's columns -- which is the same
    shape `row_record` builds for a `%ROWTYPE`, from a different source for the column list.
    """
    from .repository import loop_record

    query = loop.query
    columns = list(zip(query.into_columns or [], query.into_oracle_types or []))
    if not columns or any(name is None for name, _ in columns):
        return None
    file = JavaFile(package=package, name=loop_record(routine, loop), source=source)
    components = []
    for column, oracle in columns:
        mapped = loop_component_type(oracle)
        file.add_import(*mapped.imports)
        components.append(f"{mapped.name} {java_name(column)}")
    file.comment(f"Rows of the cursor FOR loop at {source}. Components follow the query's select list.")
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
            if declaration.type is None or not declaration.type.resolved:
                continue
            if declaration.type.origin == "rowtype":
                base = declaration.type.oracle.split("%")[0]
                if base.lower() in seen:
                    continue
                seen.add(base.lower())
                record = row_record(base, declaration.type.resolved, package, source)
                if record is not None:
                    out.append(record)
            elif declaration.type.origin == "record":
                # a package-local `TYPE t IS RECORD (...)`: the same thing as a %ROWTYPE, named by the package
                base = declaration.type.oracle.rpartition(".")[2]
                if base.lower() in seen:
                    continue
                seen.add(base.lower())
                record = row_record(base, declaration.type.resolved, package, source, suffix="",
                                    note=f"PL/SQL record type {base}. Components follow the field names.")
                if record is not None:
                    out.append(record)
        for loop in _loops(routine):
            record = loop_row_record(routine, loop, package, source)
            if record is not None:
                out.append(record)
        result = result_record(routine, package, source)
        if result is not None:
            out.append(result)
    return out
