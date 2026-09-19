"""Whether two result sets are the same result -- shared by the harnesses, so they cannot disagree about it.

The comparison this replaces normalised each value on its own and then compared the normal forms. That is how a
harness ends up reporting a match that is not one: `Decimal -> float` dropped the digits a migration is most
likely to get wrong, `round(v, 6)` hid a difference in the seventh place, every string was tried as a date (so
'20240101' equalled '2024-01-01'), the fractional second was cut off, and `True` was `1`.

Here values are compared **in pairs**, because what one side may be read as depends on the other:

* whole numbers exactly, at any size (an 18-digit id is compared digit for digit)
* other numbers at 15 significant digits. ScalarDB has no DECIMAL, so a NUMBER(10,2) legitimately comes back
  through a double; 15 digits is what a double holds, and asking for more would fail every such column
* a string is read as a date or a time only when the other side *is* one (drivers and JSON hand dates back as
  text); two strings are compared as strings
* date-times at millisecond precision, which is what ScalarDB's TIMESTAMP keeps; a midnight date-time equals the
  plain date (Oracle's DATE carries a time)
* a boolean equals a boolean. NULL equals NULL and nothing else -- not ''

Rows are compared in order when the query is ordered, and as a multiset otherwise.
"""

from __future__ import annotations

import datetime
import decimal
import json

_DOUBLE = decimal.Context(prec=15)
_MISSING = object()


def loads(text: str):
    """Runner output, keeping numbers exact. `json.loads` reads 12345678901234567.89 as a float and the digits
    under comparison are gone before the comparison starts."""
    return json.loads(text, parse_float=decimal.Decimal)


def _number(v):
    if isinstance(v, bool) or not isinstance(v, (int, float, decimal.Decimal)):
        return None
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return decimal.Decimal(repr(v))
    return decimal.Decimal(v)


def _moment(v, like):
    """`v` as a date / datetime / time, if it is one -- or is text and `like` is one."""
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v
    if not isinstance(v, str) or not isinstance(like, (datetime.datetime, datetime.date, datetime.time)):
        return None
    text = v.strip().replace("T", " ")
    for parse in (datetime.datetime.fromisoformat, datetime.date.fromisoformat, datetime.time.fromisoformat):
        try:
            return parse(text)
        except ValueError:
            continue
    return None


def _comparable(moment):
    """(date, time) at millisecond precision, zone-aware values in UTC. A date is midnight of that day."""
    if isinstance(moment, datetime.datetime):
        if moment.tzinfo is not None:
            moment = moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return moment.date(), moment.time().replace(microsecond=moment.microsecond // 1000 * 1000)
    if isinstance(moment, datetime.date):
        return moment, datetime.time(0)
    return None, moment.replace(microsecond=moment.microsecond // 1000 * 1000, tzinfo=None)


def same_value(expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    a, b = _number(expected), _number(actual)
    if a is not None or b is not None:
        if a is None or b is None:
            return False
        if a == a.to_integral_value() and b == b.to_integral_value():
            return a == b
        return _DOUBLE.create_decimal(a) == _DOUBLE.create_decimal(b)
    a, b = _moment(expected, actual), _moment(actual, expected)
    if a is not None or b is not None:
        return a is not None and b is not None and _comparable(a) == _comparable(b)
    if isinstance(expected, (bytes, bytearray, memoryview)) or isinstance(actual, (bytes, bytearray, memoryview)):
        return isinstance(expected, (bytes, bytearray, memoryview)) \
            and isinstance(actual, (bytes, bytearray, memoryview)) and bytes(expected) == bytes(actual)
    return type(expected) is type(actual) and expected == actual if isinstance(expected, str) \
        else str(expected) == str(actual)


def same_row(expected, actual) -> bool:
    return len(expected) == len(actual) and all(same_value(e, a) for e, a in zip(expected, actual))


def _sort_key(row) -> tuple:
    """Puts rows that may be equal next to each other. It only has to be consistent between the two sides; the
    pairing below is what decides."""
    out = []
    for v in row:
        number = _number(v)
        if v is None:
            out.append((0, ""))
        elif isinstance(v, bool):
            out.append((1, str(v)))
        elif number is not None:
            out.append((2, format(_DOUBLE.create_decimal(number).normalize(), "f")))
        elif isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
            day, time = _comparable(v)
            out.append((3, f"{day or ''} {time}".strip().replace(" 00:00:00", "")))
        else:
            out.append((3, str(v).strip().replace("T", " ").replace(" 00:00:00", "")))
    return tuple(out)


def difference(expected: list, actual: list, ordered: bool) -> str | None:
    """None when the two are the same result, otherwise the first thing that differs, in words."""
    if len(expected) != len(actual):
        return f"row count: expected {len(expected)}, actual {len(actual)}"
    if ordered:
        for index, (e, a) in enumerate(zip(expected, actual), start=1):
            if not same_row(e, a):
                return f"row {index}: expected {tuple(e)!r}, actual {tuple(a)!r}"
        return None
    # a multiset: sort both alike and walk them; on a miss, look for the row anywhere among what is left, since
    # the sort key is a heuristic (a date as text and as a date sort alike only when the text is ISO)
    left = sorted(actual, key=_sort_key)
    for e in sorted(expected, key=_sort_key):
        at = next((i for i, a in enumerate(left[:1]) if same_row(e, a)), _MISSING)
        if at is _MISSING:
            at = next((i for i, a in enumerate(left) if same_row(e, a)), _MISSING)
        if at is _MISSING:
            return f"no row of the actual result equals expected {tuple(e)!r}"
        left.pop(at)
    return None


def same(expected: list, actual: list, ordered: bool) -> bool:
    return difference(expected, actual, ordered) is None


def is_ordered(sql: str, dialect: str) -> bool:
    """Whether the top-level statement has an ORDER BY, from the syntax tree. The substring test this replaces
    missed `ORDER  BY` and a line break between the words -- and then compared an ordered result as unordered --
    and matched an ORDER BY that only a subquery or a window had."""
    import sqlglot

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001  a statement this cannot parse is compared in order: the stricter reading
        return True
    return tree.args.get("order") is not None


def with_dates(sample: list[list], temporal: list[int]) -> list[list]:
    """The source's sample with its date columns as dates. Both samples arrive as JSON, and rowcompare reads text as
    a date only when the other side is one -- so Oracle's DATE '2023-09-29T00:00' and ScalarDB's DATE '2023-09-29'
    were compared as text, and every statement that returns a date came out FAIL."""
    def parse(v):
        if not isinstance(v, str):
            return v
        for read in (datetime.datetime.fromisoformat, datetime.date.fromisoformat, datetime.time.fromisoformat):
            try:
                return read(v)
            except ValueError:
                pass
        return v
    columns = {int(i) for i in temporal or []}
    return [[parse(v) if i in columns else v for i, v in enumerate(row)] for row in sample]
