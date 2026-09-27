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
    "Map": "java.util.Map",
    "List": "java.util.List",
    "BigDecimal": "java.math.BigDecimal",
    "LocalDateTime": "java.time.LocalDateTime",
    "LocalDate": "java.time.LocalDate",
    "LocalTime": "java.time.LocalTime",
    "OffsetDateTime": "java.time.OffsetDateTime",
}


import contextvars

# Schema object types (`CREATE TYPE t AS OBJECT`, #54): {name: "RECORD(...)"}, and the package their records are
# generated in. Set by the analysis (the DDL knows them) and by the generator (it knows the package)
_OBJECT_TYPES: "contextvars.ContextVar[dict[str, str]]" = contextvars.ContextVar("object_types", default={})
_OBJECT_PACKAGE: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("object_package", default=None)


def set_object_types(types: dict[str, str]) -> None:
    _OBJECT_TYPES.set({k.lower(): v for k, v in types.items()})


def object_types() -> dict[str, str]:
    return _OBJECT_TYPES.get()


# `TYPE name_rec IS RECORD (...)` by the shape the symbol table resolves it to (#82). A field or an element typed
# with one is resolved to `RECORD(first VARCHAR2(20), ...)`, which has lost the name -- without this the component
# was an Object and `friend.name.first` could be neither read nor assigned. Two types of the same shape are left
# out: which one a field means is not written anywhere
_RECORD_CLASSES: "contextvars.ContextVar[dict[str, str]]" = contextvars.ContextVar("record_classes", default={})


def _shape(resolved: str) -> str:
    return re.sub(r"\s+", "", resolved or "").lower()


def set_record_types(types: dict[str, str], elements: dict[str, str] | None = None) -> None:
    """{type name: its resolved `RECORD(...)`}, and {collection type name: the `RECORD(...)` it holds}.

    A collection of `c1%ROWTYPE` (12-23) holds a record no TYPE names: its class is named after the collection
    (`NameSet` -> `NamesetRow`) unless a named RECORD type has the same shape (#67)."""
    by_shape: dict[str, str] = {}
    clashing: set[str] = set()
    for name, resolved in types.items():
        key, cls = _shape(resolved), java_class_name(name.rpartition(".")[2])
        if key in by_shape and by_shape[key] != cls:
            clashing.add(key)
        by_shape.setdefault(key, cls)
    for name, resolved in (elements or {}).items():
        key = _shape(resolved)
        if key not in by_shape and key not in clashing:
            by_shape[key] = java_class_name(name.rpartition(".")[2]) + "Row"
    _RECORD_CLASSES.set({k: v for k, v in by_shape.items() if k not in clashing})


def record_types() -> dict[str, str]:
    """{shape: class} of the record types a field or an element can be typed with."""
    return _RECORD_CLASSES.get()


def set_object_package(package: str | None) -> None:
    _OBJECT_PACKAGE.set(package)


def object_class(name: str | None) -> str | None:
    """The Java record for a schema object type, or None when `name` is not one."""
    return java_class_name(name.strip()) if name and name.strip().lower() in _OBJECT_TYPES.get() else None


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
        """`List<BigDecimal>` は 2 つ要る。名前をそのまま引くだけだと、総称型のときに 0 個になる。"""
        out = {IMPORTS[part] for part in re.findall(r"\w+", self.name) if part in IMPORTS}
        package = _OBJECT_PACKAGE.get()
        if package:
            classes = {java_class_name(n) for n in _OBJECT_TYPES.get()} | set(_RECORD_CLASSES.get().values())
            out |= {f"{package}.{part}" for part in re.findall(r"\w+", self.name) if part in classes}
        return out

    @property
    def is_scaled(self) -> bool:
        return self.scale is not None


UNKNOWN = JavaType("Object", "TEXT", note="type not resolved; the generator must not guess")

# `TABLE OF <type>`: symbol table がコレクション型をこの形に解決する（`RECORD(...)` と同じ考え方）
COLLECTION = re.compile(r"^TABLE\s+OF\s+(?P<element>.+?)(?:\s+INDEX\s+BY\s+(?P<key>.+?))?(?:\s+LIMIT\s+(?P<limit>\d+))?$",
                        re.IGNORECASE | re.DOTALL)


def java_type(oracle: str | None, *, money: bool = False) -> JavaType:
    """Map one Oracle type. `money=True` selects the scaled-integer storage P0-3 chose for amounts."""
    if not oracle:
        return UNKNOWN
    written = oracle.strip()
    record = object_class(written)
    if record is not None:
        return JavaType(record, "TEXT", note="Oracle のオブジェクト型。record として生成する（#54）")
    if RECORD.match(written) and _shape(written) in _RECORD_CLASSES.get():
        return JavaType(_RECORD_CLASSES.get()[_shape(written)], "TEXT", note="PL/SQL の RECORD 型（#82）")

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
    if upper.startswith(("BINARY_FLOAT", "SIMPLE_FLOAT")):
        return JavaType("Float", "FLOAT")
    if upper.startswith(("BINARY_DOUBLE", "SIMPLE_DOUBLE", "DOUBLE PRECISION")):
        return JavaType("Double", "DOUBLE")
    decimal = re.match(r"^\s*(?:DEC|DECIMAL|NUMERIC)\b\s*(\(.*\))?\s*$", written, re.IGNORECASE)
    if decimal:
        # ANSI names of NUMBER (#75: `DEC(5,2)` came out Object)
        return java_type(f"NUMBER{decimal.group(1) or ''}", money=money)
    if upper.startswith(("RAW", "LONG RAW", "BLOB")):
        return JavaType("byte[]", "BLOB", note="never round-trip through String")
    if upper.startswith(("CLOB", "NCLOB", "LONG")):
        return JavaType("String", "TEXT", note="size and streaming need a decision for large values")
    if upper.startswith("BOOLEAN"):
        return JavaType("Boolean", "BOOLEAN", note="PL/SQL BOOLEAN can be NULL, so not the primitive")
    if re.match(r"(PLS_INTEGER|BINARY_INTEGER|SIMPLE_INTEGER|INTEGER|INT|SMALLINT|NATURALN?|POSITIVEN?|SIGNTYPE)\b",
                upper):
        # NATURAL(N) / POSITIVE(N) / SIGNTYPE are PLS_INTEGER with a range (#59); they were Object (#75)
        return JavaType("Integer", "INT")
    if upper.startswith(("FLOAT", "REAL")):
        return JavaType("Double", "DOUBLE")
    collection = COLLECTION.match(written)
    if collection:
        # `TYPE t IS TABLE OF NUMBER(19)` は Java では要素の List である。要素の型が分からなければ
        # `List<Object>` にはせず Object のままにする——`List` と書けることと、中身が何か分かって
        # いることは別である
        element = java_type(collection.group("element"), money=money)
        if element is UNKNOWN or element.name == "Object":
            return JavaType("Object", "TEXT", note=f"collection of an unmapped type: {written!r}")
        key = java_type(collection.group("key").strip(), money=False) if collection.group("key") else None
        if key is not None and key.name in ("String", "Integer"):
            # an associative array is a sorted Map: keyed by text (`INDEX BY VARCHAR2(30)`, #45) or by integer
            # (`INDEX BY PLS_INTEGER`, #93). Oracle walks it in key order (FIRST / NEXT), which a TreeMap gives for
            # free. The integer one was a List, which cannot hold `v(0)` or `v(-10)` (SUBSCRIPT_OUTSIDE_LIMIT,
            # oracle-plsql-docs 5-3, 7-4) and grows to the largest key (`v(emp_id)`); the harness still binds the
            # scenario's list, numbered from 1 as python-oracledb numbers it
            return JavaType(f"Map<{key.name}, {element.name}>", element.storage, scale=element.scale,
                            note="PL/SQL の連想配列。キー順に回る TreeMap で持つ")
        return JavaType(f"List<{element.name}>", element.storage, scale=element.scale,
                        note="PL/SQL のコレクション。呼び出し側が渡す")
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


# PL/SQL happily names a procedure `import`; Java does not. Renaming is the only option, and doing it in one
# place keeps the service and the repository agreeing on the name.
JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const", "continue",
    "default", "do", "double", "else", "enum", "extends", "final", "finally", "float", "for", "goto", "if",
    "implements", "import", "instanceof", "int", "interface", "long", "native", "new", "package", "private",
    "protected", "public", "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
    "throw", "throws", "transient", "try", "void", "volatile", "while", "true", "false", "null", "var", "record",
}


_PLAIN_UPPER = re.compile(r"[A-Z][A-Z0-9_$#]*")


def plsql_identity(identifier: str) -> str:
    """What makes two PL/SQL names the same name, lower-cased for lookups: `Hello`, `HELLO` and `"HELLO"` are one
    name; `"Hello"` and `"hello"` are two others (a quoted name keeps its case). A quoted name that is not plain
    upper case gets a key no unquoted name can have (samples/oracle-plsql-docs 2-1〜2-5, #72)."""
    name = identifier.strip()
    if len(name) > 1 and name.startswith('"') and name.endswith('"'):
        inner = name[1:-1]
        if _PLAIN_UPPER.fullmatch(inner):
            return inner.lower()
        return "q:" + "".join(f"^{c.lower()}" if c.isupper() else c for c in inner)
    return name.lower()


def java_name(identifier: str) -> str:
    """`v_order_id` -> `vOrderId`. Names come from PL/SQL, so they are snake_case and sometimes prefixed.

    A quoted name (#72): `"HELLO"` is the plain name HELLO; `"Begin"` / `"begin"` / `"my col"` keep apart by a
    suffix that spells their case, since Java would otherwise see one name (`begin_Ulllll`, `begin_lllll`)."""
    stripped = identifier.strip()
    if len(stripped) > 1 and stripped.startswith('"') and stripped.endswith('"'):
        inner = stripped[1:-1]
        if not _PLAIN_UPPER.fullmatch(inner):
            base = java_name(re.sub(r"[^\w$#]+", "_", inner))
            return f"{base}_{''.join('U' if c.isupper() else 'l' if c.islower() else 'x' for c in inner)}"
        identifier = inner
    parts = [p for p in re.split(r"[_$#]+", identifier.strip()) if p]
    if not parts:
        return "value"
    head, *rest = parts
    name = head.lower() + "".join(p[:1].upper() + p[1:].lower() for p in rest)
    if not name[:1].isalpha():
        # a bind named by the literal it carries (`USING 110` in dynamic SQL: 7-20, #76) is not a Java name
        name = "v" + re.sub(r"\W", "_", name)
    return f"{name}_" if name in JAVA_KEYWORDS else name


def signature_type(type_ref) -> JavaType:
    """A parameter's or a function's return type. `INTEGER` / `INT` / `SMALLINT` there are subtypes of NUMBER
    whose precision a formal parameter or a RETURN does not inherit: `test(p INTEGER)` called with 0.66 prints
    .66 in Oracle. As Integer the value was rounded to 1 (samples/oracle-plsql-docs 8-11); it is a BigDecimal.
    PLS_INTEGER is a type of its own and stays Integer."""
    if type_ref is None:
        return java_type(None)
    if re.fullmatch(r"\s*(?:INTEGER|INT|SMALLINT)\s*", type_ref.oracle or "", re.IGNORECASE):
        return java_type("NUMBER")
    record = record_class(type_ref)
    if record is not None:
        # a function returning `My_Types.My_Rec` returns the generated record, not Object (5-33, #74)
        return JavaType(record, "TEXT")
    return java_type(type_ref.resolved or type_ref.oracle)


def record_class(type_ref) -> str | None:
    """The generated class of a record-typed holder: `EmployeesRow` for a %ROWTYPE, the type's own name for a
    `TYPE ... IS RECORD` (`r_types.r_type_1` -> `RType1`). None for anything else (#74)."""
    if type_ref is None:
        return None
    if type_ref.origin == "rowtype":
        return java_class_name(type_ref.oracle.split("%")[0]) + "Row"
    if type_ref.origin == "record" and not object_class(type_ref.oracle or ""):
        return java_class_name(type_ref.oracle.rpartition(".")[2])
    return None


def java_class_name(identifier: str) -> str:
    parts = [p for p in re.split(r"[_$#.]+", identifier.strip()) if p]
    return "".join(p[:1].upper() + p[1:].lower() for p in parts) or "Generated"


def routine_stem(routine) -> str:
    """The PL/SQL-side name every Java name of a routine is made from: `put`, and `put1` / `put2` for overloads.

    Every overload gets the number, not only the ones Java could not tell apart: NUMBER and INTEGER parameters are
    the same Java type, a `<Name>Result` record and the repository's `<name>Stmt<N>` methods have no parameters to
    differ by, and one rule is easier to find a method by (the evidence harness, `verify._attribute`).
    """
    from ..lower import overload_of

    ordinal = overload_of(routine)
    return routine.name if ordinal is None else f"{routine.name}{ordinal}"


def result_record_name(routine) -> str:
    """The `<Name>Result` record a routine with OUT / IN OUT arguments returns. A subprogram lifted out of another
    routine (#80) carries its module's name too: the lifted `p` of ex_8_21 and a standalone `p` beside it both made
    `PResult` in the one domain package, and javac took the other's (#90)."""
    stem = java_class_name(routine_stem(routine))
    if getattr(routine, "enclosing", None):
        return java_class_name(routine.id.rsplit(".", 1)[0]) + stem + "Result"
    return stem + "Result"

