"""P2-5: Oracle types -> Java types, and the storage form ScalarDB needs.

Two mappings, deliberately separate (design document §5.3). `OracleType -> TargetType` is what the generated Java
sees; `TargetType -> ScalarDB` is how it is stored. Collapsing them is how precision quietly disappears.

The rule that does the most work here is the one about `NUMBER`: without a scale, a value can be anything, so it
becomes `BigDecimal` rather than a `long` that happens to fit today's data. The design document is explicit that
an unresolved precision must stay visible instead of being guessed.

Money is the case where the two mappings disagree on purpose. P0-3 chose a scaled integer in ScalarDB because
ScalarDB has no DECIMAL and DOUBLE loses Oracle's half-up rounding; the Java side keeps `BigDecimal`, and the
conversion between them lives in the generated repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER = re.compile(r"^\s*NUMBER\s*(?:\(\s*(\*|\d+)\s*(?:,\s*(-?\d+)\s*)?\))?\s*$", re.IGNORECASE)
CHAR_TYPE = re.compile(r"^\s*(N?VARCHAR2|N?CHAR|VARCHAR|STRING)\s*(\(.*\))?\s*$", re.IGNORECASE)
TIMESTAMP_TZ = re.compile(r"^\s*TIMESTAMP\s*(\(\s*\d+\s*\))?\s*WITH\s+(LOCAL\s+)?TIME\s+ZONE\s*$", re.IGNORECASE)
TIMESTAMP = re.compile(r"^\s*TIMESTAMP\s*(\(\s*\d+\s*\))?\s*$", re.IGNORECASE)
RECORD = re.compile(r"^RECORD\((.*)\)$", re.IGNORECASE | re.DOTALL)

# Java types that need an import
IMPORTS = {
    "BigDecimal": "java.math.BigDecimal",
    "LocalDateTime": "java.time.LocalDateTime",
    "LocalDate": "java.time.LocalDate",
    "LocalTime": "java.time.LocalTime",
    "OffsetDateTime": "java.time.OffsetDateTime",
}


@dataclass(frozen=True)
class JavaType:
    """A Java type, plus how it is stored and why that was chosen."""

    name: str
    storage: str = "TEXT"          # the ScalarDB column type this maps to
    scale: int | None = None       # set when the value is stored as a scaled integer
    note: str = ""
    nullable: bool = True

    @property
    def imports(self) -> set[str]:
        return {IMPORTS[self.name]} if self.name in IMPORTS else set()

    @property
    def is_scaled(self) -> bool:
        return self.scale is not None


UNKNOWN = JavaType("Object", "TEXT", note="type not resolved; the generator must not guess")


def java_type(oracle: str | None, *, money: bool = False) -> JavaType:
    """Map one Oracle type. `money=True` selects the scaled-integer storage P0-3 chose for amounts."""
    if not oracle:
        return UNKNOWN
    written = oracle.strip()

    number = NUMBER.match(written)
    if number:
        precision, scale = number.group(1), number.group(2)
        if precision is None or precision == "*":
            # NUMBER without precision can hold anything; a long that fits today is a guess
            return JavaType("BigDecimal", "TEXT",
                            note="NUMBER without precision: kept exact, stored as text to avoid silent loss")
        digits = int(precision)
        decimals = int(scale) if scale is not None else 0
        if decimals == 0:
            if digits <= 9:
                return JavaType("Integer", "INT")
            if digits <= 18:
                return JavaType("Long", "BIGINT")
            # Beyond 18 digits the value no longer fits a long. The Java side keeps it exact; the storage stays
            # BIGINT so that the generator and the schema P0-3 loaded agree -- `scalardb_migrate.types` makes the
            # same choice and warns about the overflow rather than silently changing the column type.
            return JavaType("BigDecimal", "BIGINT",
                            note=f"NUMBER({digits}) exceeds 64 bits; values beyond BIGINT overflow")
        if money or decimals > 0:
            return JavaType("BigDecimal", "BIGINT", scale=decimals,
                            note=f"stored as a scaled integer (x10^{decimals}): ScalarDB has no DECIMAL and "
                                 "DOUBLE loses Oracle's half-up rounding")

    if CHAR_TYPE.match(written):
        return JavaType("String", "TEXT",
                        note="Oracle treats '' as NULL; the application has to keep that distinction")
    if TIMESTAMP_TZ.match(written):
        return JavaType("OffsetDateTime", "TIMESTAMPTZ", note="stored as UTC, millisecond precision")
    if TIMESTAMP.match(written):
        return JavaType("LocalDateTime", "TIMESTAMP", note="ScalarDB TIMESTAMP keeps milliseconds")

    upper = written.upper()
    if upper.startswith("DATE"):
        return JavaType("LocalDateTime", "TIMESTAMP",
                        note="Oracle DATE carries a time of day, so it is not LocalDate")
    if upper.startswith(("BINARY_FLOAT",)):
        return JavaType("Float", "FLOAT")
    if upper.startswith(("BINARY_DOUBLE",)):
        return JavaType("Double", "DOUBLE")
    if upper.startswith(("RAW", "LONG RAW", "BLOB")):
        return JavaType("byte[]", "BLOB", note="never round-trip through String")
    if upper.startswith(("CLOB", "NCLOB", "LONG")):
        return JavaType("String", "TEXT", note="size and streaming need a decision for large values")
    if upper.startswith("BOOLEAN"):
        return JavaType("Boolean", "BOOLEAN", note="PL/SQL BOOLEAN can be NULL, so not the primitive")
    if upper.startswith(("PLS_INTEGER", "BINARY_INTEGER", "SIMPLE_INTEGER", "INTEGER", "INT", "SMALLINT")):
        return JavaType("Integer", "INT")
    if upper.startswith(("FLOAT", "REAL")):
        return JavaType("Double", "DOUBLE")
    if RECORD.match(written):
        return JavaType("Object", "TEXT", note="%ROWTYPE: generated as a record, see dto.py")
    return JavaType("Object", "TEXT", note=f"no mapping for {written!r}")


def record_columns(resolved: str) -> list[tuple[str, str]]:
    """Split the `RECORD(name TYPE, ...)` form the symbol table produces for `%ROWTYPE`."""
    match = RECORD.match(resolved or "")
    if not match:
        return []
    columns: list[tuple[str, str]] = []
    depth = 0
    current = ""
    for character in match.group(1):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "," and depth == 0:
            columns.append(_split_column(current))
            current = ""
        else:
            current += character
    if current.strip():
        columns.append(_split_column(current))
    return [c for c in columns if c[0]]


def _split_column(text: str) -> tuple[str, str]:
    name, _, type_ = text.strip().partition(" ")
    return name.strip(), type_.strip()


def java_name(identifier: str) -> str:
    """`v_order_id` -> `vOrderId`. Names come from PL/SQL, so they are snake_case and sometimes prefixed."""
    parts = [p for p in re.split(r"[_$#]+", identifier.strip()) if p]
    if not parts:
        return "value"
    head, *rest = parts
    return head.lower() + "".join(p[:1].upper() + p[1:].lower() for p in rest)


def java_class_name(identifier: str) -> str:
    parts = [p for p in re.split(r"[_$#.]+", identifier.strip()) if p]
    return "".join(p[:1].upper() + p[1:].lower() for p in parts) or "Generated"
