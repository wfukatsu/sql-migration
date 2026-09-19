"""Source data type -> ScalarDB data type mapping.

ScalarDB types: BOOLEAN | INT | BIGINT | FLOAT | DOUBLE | TEXT | BLOB | DATE | TIME | TIMESTAMP | TIMESTAMPTZ
Reference: https://scalardb.scalar-labs.com/docs/latest/scalardb-sql/grammar/
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

T = exp.DataType.Type


@dataclass
class TypeMapping:
    scalardb_type: str | None  # None => unsupported
    severity: str  # INFO | WARN | ERROR
    note: str


def _t(*names: str) -> set:
    return {getattr(T, n) for n in names if hasattr(T, n)}


_INT = _t("TINYINT", "SMALLINT", "MEDIUMINT", "INT", "UTINYINT", "USMALLINT", "UMEDIUMINT", "INT128", "INT256")
_BIGINT = _t("BIGINT", "UINT", "UBIGINT")
_FLOAT = _t("FLOAT")
_DOUBLE = _t("DOUBLE")
_DECIMAL = _t("DECIMAL", "BIGDECIMAL", "DECIMAL32", "DECIMAL64", "DECIMAL128", "DECIMAL256", "MONEY", "SMALLMONEY")
_TEXT = _t("CHAR", "NCHAR", "VARCHAR", "NVARCHAR", "TEXT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT", "NAME")
_BLOB = _t("BINARY", "VARBINARY", "BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB", "BYTEA")
_DATE = _t("DATE", "DATE32")
_TIME = _t("TIME", "TIMETZ")
_TIMESTAMP = _t("TIMESTAMP", "DATETIME", "DATETIME64", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "SMALLDATETIME")
_TIMESTAMPTZ = _t("TIMESTAMPTZ", "TIMESTAMPLTZ", "TIMESTAMPNTZ", "DATETIME2")
_SEMI = _t("JSON", "JSONB", "UUID", "ENUM", "ENUM8", "ENUM16", "SET", "INET", "IPADDRESS", "XML")
_BOOL = _t("BOOLEAN", "BIT")
_SERIAL = _t("SERIAL", "BIGSERIAL", "SMALLSERIAL")


def _params(dt: exp.DataType) -> list[int | None]:
    out: list[int | None] = []
    for p in dt.expressions:
        v = p.this if isinstance(p, exp.DataTypeParam) else p
        try:
            out.append(int(v.name))
        except (ValueError, AttributeError):
            out.append(None)
    return out


def map_type(dt: exp.DataType, source_dialect: str) -> TypeMapping:
    """Map a sqlglot DataType (parsed from the source dialect) to a ScalarDB type."""
    t = dt.this
    params = _params(dt)
    raw = dt.sql(dialect=source_dialect)

    if t in _BOOL:
        if t == getattr(T, "BIT", None) and params and params[0] and params[0] > 1:
            return TypeMapping("BLOB", "WARN", f"{raw}: multi-bit BIT has no equivalent; mapped to BLOB")
        return TypeMapping("BOOLEAN", "INFO", "")
    if t in _SERIAL:
        return TypeMapping("BIGINT" if t == T.BIGSERIAL else "INT", "ERROR",
                           f"{raw}: auto-generated sequence values are not supported; generate IDs in the application")
    if source_dialect == "oracle" and (t in _INT or t in _BIGINT):
        # Oracle has no machine integers: INTEGER, INT and SMALLINT are all NUMBER(38). Mapped to a 32-bit INT with
        # no note, a value above 2^31 overflowed on the way in
        return TypeMapping("BIGINT", "WARN", f"{raw}: an Oracle integer type is NUMBER(38); mapped to BIGINT, and "
                                             f"values beyond 64-bit would overflow. Declare NUMBER(p) to map exactly")
    if t in _INT:
        if source_dialect == "mysql" and t == T.TINYINT and params == [1]:
            return TypeMapping("INT", "WARN", "TINYINT(1) is often used as boolean in MySQL; consider BOOLEAN")
        return TypeMapping("INT", "INFO", "")
    if t in _BIGINT:
        if t == getattr(T, "UBIGINT", None):
            return TypeMapping("BIGINT", "WARN", f"{raw}: unsigned range exceeds ScalarDB BIGINT (signed 64-bit)")
        if t != T.BIGINT:
            return TypeMapping("BIGINT", "INFO", f"{raw}: unsigned 32-bit fits ScalarDB BIGINT")
        return TypeMapping("BIGINT", "INFO", "")
    if t in _FLOAT:
        if source_dialect == "oracle":
            # Oracle FLOAT is a NUMBER with binary precision (126 bits by default, about 38 digits), not an IEEE
            # single. As a 32-bit FLOAT it kept 7 digits
            return TypeMapping("DOUBLE", "WARN", f"{raw}: Oracle FLOAT is a decimal NUMBER with up to 38 digits; "
                                                 f"mapped to DOUBLE (about 15 digits)")
        # PostgreSQL FLOAT(25..53) / FLOAT without precision is double precision
        if source_dialect == "postgres" and (not params or (params[0] or 0) > 24):
            return TypeMapping("DOUBLE", "INFO", "")
        return TypeMapping("FLOAT", "INFO", "")
    if t in _DOUBLE:
        return TypeMapping("DOUBLE", "INFO", "")
    if t in _DECIMAL:
        precision = params[0] if params else None
        scale = params[1] if len(params) > 1 else 0
        if precision is None:
            if source_dialect in ("oracle", "postgres"):
                # PostgreSQL's NUMERIC without a precision is unconstrained too -- any number of digits, fractions
                # included. Only MySQL defaults to (10,0). Read as (10,0) it became a BIGINT "exact, fits 64-bit",
                # and every fraction was cut off
                return TypeMapping("DOUBLE", "WARN",
                                   f"{raw}: unconstrained {'NUMBER' if source_dialect == 'oracle' else 'NUMERIC'} "
                                   f"mapped to DOUBLE; exact decimal precision is lost")
            precision, scale = 10, 0  # MySQL's default DECIMAL is (10,0)
        if scale and scale > 0:
            return TypeMapping("DOUBLE", "WARN",
                               f"{raw}: ScalarDB has no DECIMAL type; mapped to DOUBLE (precision loss). "
                               f"For money, store a scaled integer (x10^{scale}) in BIGINT instead")
        if precision <= 9:
            return TypeMapping("INT", "INFO", f"{raw} -> INT (exact, fits 32-bit)")
        if precision <= 18:
            return TypeMapping("BIGINT", "INFO", f"{raw} -> BIGINT (exact, fits 64-bit)")
        return TypeMapping("BIGINT", "WARN", f"{raw}: precision {precision} exceeds 64-bit; values may overflow BIGINT")
    if t in _TEXT:
        if t in _t("CHAR", "NCHAR") and params and (params[0] or 0) > 1:
            # CHAR(n) pads with blanks and compares ignoring them: `code = 'A'` finds 'A  '. As TEXT, the migrated
            # value keeps its padding and the same comparison finds nothing
            return TypeMapping("TEXT", "WARN", f"{raw}: fixed-length CHAR is blank-padded and compared ignoring the "
                                               f"padding; ScalarDB TEXT compares exactly -- trim the data when "
                                               f"migrating, or the same comparisons stop matching")
        if params:
            return TypeMapping("TEXT", "INFO", f"{raw}: length limit is not enforced by ScalarDB TEXT")
        return TypeMapping("TEXT", "INFO", "")
    if t in _BLOB:
        return TypeMapping("BLOB", "INFO", "")
    if t in _DATE:
        if source_dialect == "oracle":
            return TypeMapping("DATE", "WARN",
                               "Oracle DATE carries a time-of-day component; use TIMESTAMP if the time part is used")
        return TypeMapping("DATE", "INFO", "")
    if t in _TIME:
        if t == getattr(T, "TIMETZ", None):
            return TypeMapping("TIME", "WARN", f"{raw}: ScalarDB TIME has no time zone; the offset is dropped")
        return TypeMapping("TIME", "INFO", "TIME precision is microseconds (6 digits)")
    if t in _TIMESTAMP or t in _TIMESTAMPTZ:
        target = "TIMESTAMP" if t in _TIMESTAMP else "TIMESTAMPTZ"
        # with no precision written, Oracle and PostgreSQL keep microseconds (6); MySQL keeps whole seconds (0)
        precision = params[0] if params and params[0] is not None else (0 if source_dialect == "mysql" else 6)
        if precision > 3:
            written = "" if params else " (the default precision is 6)"
            return TypeMapping(target, "WARN", f"{raw}{written}: ScalarDB {target} keeps millisecond precision only"
                               + ("; stored as UTC" if target == "TIMESTAMPTZ" else ""))
        return TypeMapping(target, "INFO", "stored as UTC; millisecond precision" if target == "TIMESTAMPTZ" else "")
    if t in _SEMI:
        return TypeMapping("TEXT", "WARN",
                           f"{raw}: mapped to TEXT; JSON/ENUM/UUID semantics and operators are not available")
    return TypeMapping(None, "ERROR", f"{raw}: no ScalarDB equivalent")
