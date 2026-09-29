"""Oracle's format models as the runtime reads them (#157), for checking a model before any code runs.

The runtime (`runtime-java` `OracleFormat`) writes TO_CHAR / TO_NUMBER and the NLS date formats. This module reads a
model the same way and answers one question about it: does Oracle accept it, and does the runtime implement it?

    check_number("9G999D99")  -> Check("ok")
    check_number("99.99.99")  -> Check("invalid", "ORA-01481 ...")        Oracle refuses it too: nothing to decide
    check_number("FMB999")    -> Check("unsupported", "B with FM")        the runtime refuses it by name
    check_date("DD-MON-RR")   -> Check("ok", elements={"DD", "MON", "RR"})

The two readers are kept in step by `tests/test_plsql_formats.py`, which runs this one over every model recorded
from Oracle in `fixtures/plsql/formats.json` and compares its answer with Oracle's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Check:
    state: str                      # ok / invalid / unsupported
    detail: str | None = None
    elements: frozenset[str] = field(default_factory=frozenset)


OK = Check("ok")

# --- numbers ------------------------------------------------------------------------------------------------------

def check_number(model: str) -> Check:
    """Whether a number format model reads, as `OracleFormat.parseNumberModel` reads it."""
    if len(model) > 63:
        return Check("invalid", "ORA-01481: longer than 63 characters")
    f = model
    fm = f[:2].upper() == "FM"
    if fm:
        f = f[2:]
    upper = f.upper()
    if upper.startswith("TM"):
        return OK if upper in ("TM", "TM9", "TME") else Check("invalid", "ORA-01481: TM takes nothing after it but 9 or E")
    if upper == "RN":
        return OK
    if "X" in upper:
        return OK if re.fullmatch(r"[09]*X+", upper) else Check("invalid", "ORA-01481: X takes only 0 before it")
    invalid = Check("invalid", "ORA-01481: invalid number format model")
    integer: list[str] = []
    fraction = 0
    decimal = sign = currency = ""
    after_decimal = digits = dollar = blank = eeee = leading_currency = False
    i = 0
    while i < len(upper):
        c = upper[i]
        last = i == len(upper) - 1
        if sign in ("s", "M", "P"):
            return invalid
        if c in "90":
            if eeee or (currency and not leading_currency and decimal != currency):
                return invalid
            if after_decimal:
                fraction += 1
            else:
                integer.append(c)
            digits = True
        elif c in ",G":
            if not digits or after_decimal or eeee or ("G" if c == "," else ",") in integer:
                return invalid
            integer.append(c)
        elif c in ".DV":
            if decimal or eeee:
                return invalid
            decimal, after_decimal = c, True
        elif c == "$":
            if dollar or currency:
                return invalid
            dollar = True
        elif c in "LCU":
            if currency or dollar:
                return invalid
            currency = c
            rest = upper[i + 1:]
            tail = rest in ("", "S", "MI", "PR")
            if not digits and not after_decimal:
                leading_currency = True
            elif not after_decimal and not tail:
                decimal, after_decimal = c, True
            elif not tail:
                return invalid
        elif c == "B":
            if blank or (not last and upper[i + 1] in "LCUS"):
                return invalid
            blank = True
        elif c == "S":
            if sign or (i != 0 and not last):
                return invalid
            sign = "S" if i == 0 else "s"
        elif c in "MP":
            if upper[i:i + 2] not in ("MI", "PR") or i + 2 != len(upper) or sign:
                return invalid
            sign = c
            i += 1
        elif c == "E":
            if not upper.startswith("EEEE", i) or eeee or not any(d in "90" for d in integer) or decimal == "V" \
                    or "," in integer or "G" in integer:
                return invalid
            eeee = True
            i += 3
        else:
            return Check("invalid", f"ORA-01481: {model[i + (2 if fm else 0)]!r} is not a number format element")
        i += 1
    if eeee and currency and not leading_currency:
        return invalid
    if not digits and not decimal:
        return Check("unsupported", "a number format with no digit")
    if blank and fm:
        return Check("unsupported", "B with FM")
    return OK


# --- dates --------------------------------------------------------------------------------------------------------

# longest first, as the runtime's tokenizer tries them
DATE_ELEMENTS = [
    "SYYYY", "SYEAR", "Y,YYY", "YYYY", "YEAR", "YYY", "YY", "Y", "IYYY", "IYY", "IY", "IW", "I", "RRRR", "RR",
    "SCC", "CC", "Q", "MONTH", "MON", "MM", "MI", "RM", "WW", "W", "DDD", "DD", "DAY", "DY", "DS", "DL", "D", "J",
    "HH24", "HH12", "HH", "SSSSS", "SS", "A.M.", "P.M.", "AM", "PM", "A.D.", "B.C.", "AD", "BC", "FF", "TZH", "TZM",
    "TZR", "TZD", "TS", "FM", "FX", "X"]
NUMERIC_DATE_ELEMENTS = {"SYYYY", "YYYY", "YYY", "YY", "Y", "Y,YYY", "IYYY", "IYY", "IY", "I", "RRRR", "RR", "SCC",
                         "CC", "Q", "MM", "WW", "W", "IW", "DDD", "DD", "D", "J", "HH24", "HH12", "HH", "MI", "SSSSS",
                         "SS"}
# what the NLS language decides (SEM-008): names, and the English of SP / TH
LANGUAGE_ELEMENTS = {"MONTH", "MON", "DAY", "DY", "AM", "PM", "A.M.", "P.M.", "AD", "BC", "A.D.", "B.C.", "DL", "DS",
                     "TS", "YEAR", "SYEAR", "SP", "TH"}
# what only a TIMESTAMP has: a DATE refuses them (ORA-01821)
FRACTION_ELEMENTS = {"FF", "X", "TZH", "TZM", "TZR", "TZD"}


def check_date(model: str) -> Check:
    """Whether a date format model reads, as `OracleFormat.parseDateModel` reads it; the elements it uses."""
    upper = model.upper()
    elements: set[str] = set()
    cost = 0
    literal: int | None = None
    i = 0
    while i < len(model):
        c = model[i]
        if c == '"':
            end = model.find('"', i + 1)
            text = model[i + 1:] if end < 0 else model[i + 1:end]
            literal = (literal or 0) + len(text)
            i = len(model) if end < 0 else end + 1
            continue
        if ord(c) < 128 and not c.isalnum():
            literal = (literal or 0) + (0 if c == "|" else 1)
            i += 1
            continue
        if literal is not None:
            cost += literal + 2
            literal = None
        if upper[i] == "E":
            return Check("invalid", "ORA-01822: era format code is not valid with this calendar")
        match = next((e for e in DATE_ELEMENTS if upper.startswith(e, i)), None)
        if match is None:
            return Check("invalid", f"ORA-01821: {model[i:i + 5]!r} is not a date format element")
        elements.add(match)
        i += len(match)
        if match == "FF" and i < len(model) and model[i] in "123456789":
            i += 1
        if match in NUMERIC_DATE_ELEMENTS:
            for suffix in ("SPTH", "THSP", "SP", "TH"):
                if upper.startswith(suffix, i):
                    elements.update({"SP"} if "SP" in suffix else set())
                    elements.update({"TH"} if "TH" in suffix else set())
                    i += len(suffix)
                    break
        cost += 2
    if literal is not None:
        cost += literal + 2
    if cost > 73:
        return Check("invalid", "ORA-01801: date format is too long for internal buffer")
    return Check("ok", elements=frozenset(elements))
