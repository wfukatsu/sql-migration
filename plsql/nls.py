"""Where a routine's text depends on the session's NLS settings, and whether the project decided them (#94, #157).

`TO_CHAR(d)`, `'…' || d` and `DBMS_OUTPUT.PUT_LINE(d)` take their shape from the session: NLS_DATE_FORMAT,
NLS_TIMESTAMP_FORMAT, NLS_DATE_LANGUAGE. A format with names in it (`'DAY'`, `'MON'`, `'AM'`) takes them from
NLS_DATE_LANGUAGE, and `G` / `D` / `L` from NLS_NUMERIC_CHARACTERS and NLS_CURRENCY. The generated code writes what
Oracle writes with the settings the project decided in limits.yaml (`nls:`), or with Oracle's defaults (26-SEP-26,
AMERICAN) when it decided none -- and the comparison against Oracle runs with the defaults too, so a match there
says nothing about a source database whose sessions set them otherwise.

This layer states where it happens (IMPLICIT_DATE_TEXT), which literal formats the runtime refuses
(FORMAT_UNSUPPORTED), and, once somebody decided the settings, that the decision covers it (NLS_DECIDED). The rule
file says what each means (`rules/semantics.yaml` SEM-008, SEM-012, SEM-015). A BOOLEAN is TRUE / FALSE whatever the
session says, so it is not one of them.
"""

from __future__ import annotations

import re

from .formats import FRACTION_ELEMENTS, check_date, check_number
from .ir import model as M
from .lower import _walk

_DATE_LIKE = re.compile(r"^\s*(?:DATE|TIMESTAMP\b.*)\s*$", re.IGNORECASE)
_TIMESTAMP = re.compile(r"^\s*TIMESTAMP\b", re.IGNORECASE)
# what the session gives as a date whatever the routine declares
_CLOCKS = ("sysdate", "systimestamp", "current_date", "current_timestamp", "localtimestamp")
# clocks the runtime holds as a value with a zone: FF and TZR on them never need the declared type
_ZONED_CLOCKS = {"systimestamp", "current_timestamp"}
_OUTPUT = ("DBMS_OUTPUT.PUT_LINE", "DBMS_OUTPUT.PUT")
_TEXT_FIELDS = ("original_sql", "expression", "condition", "initial", "default", "message")
# a conversion the runtime makes under the session's settings
_CONVERSION = re.compile(r"\bTO_(?:CHAR|DATE|NUMBER|TIMESTAMP)\s*\(", re.IGNORECASE)


def annotate(program: M.Program) -> None:
    """IMPLICIT_DATE_TEXT where a date is written without a format, FORMAT_UNSUPPORTED where a literal format has
    what the runtime refuses. Neither depends on the decision; `mark_decided` runs after the capability check."""
    for module in program.modules:
        for routine in module.routines:
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            holders = list(routine.parameters) + list(routine.declarations) + \
                [d for s in statements for d in getattr(s, "declarations", []) or []]
            declared = {h.name.lower(): (h.type.resolved or h.type.oracle or "") for h in holders
                        if h.type is not None}
            names = {n for n, t in declared.items() if _DATE_LIKE.match(t)}
            timestamps = {n for n, t in declared.items() if _TIMESTAMP.match(t)}
            names.update(_CLOCKS)
            for statement in statements:
                written = _written_as_text(statement, names)
                if written and not any(d.code == "IMPLICIT_DATE_TEXT" for d in statement.diagnostics):
                    statement.add("INFO", "IMPLICIT_DATE_TEXT",
                                  f"{written} is written as text without a format; its shape comes from the "
                                  f"session's NLS settings")
                for problem in _unsupported_formats(_statement_text(statement), declared, timestamps):
                    if not any(d.code == "FORMAT_UNSUPPORTED" and d.message == problem for d in statement.diagnostics):
                        statement.add("WARN", "FORMAT_UNSUPPORTED", problem)


def mark_decided(program: M.Program, settings) -> None:
    """NLS_DECIDED on every conversion the runtime makes under the settings the project decided (limits.yaml `nls`).

    Only what the runtime computes is covered. A TO_CHAR (or a `||` with a date) left in SQL that ScalarDB or the
    execution plan's engine evaluates is written by that engine, not under the decided settings, so it keeps its
    REVIEW -- the same split `evaluatedByTarget` makes for SEM-003. Runs after the capability check, which is what
    says what reaches the target.
    """
    if settings is None or not getattr(settings, "decided", False):
        return
    for module in program.modules:
        for routine in module.routines:
            for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
                implicit = any(d.code == "IMPLICIT_DATE_TEXT" for d in statement.diagnostics)
                if not implicit and not _CONVERSION.search(_statement_text(statement)):
                    continue
                if any(d.code == "NLS_DECIDED" for d in statement.diagnostics):
                    continue
                if statement.kind == "SqlOperation" and _target_converts(statement, implicit):
                    continue
                statement.add("INFO", "NLS_DECIDED",
                              f"the project decided the source sessions' NLS settings (limits.yaml nls: "
                              f"{settings.date_language} / {settings.territory}; {settings.reason})")


def _target_converts(statement: M.Statement, implicit: bool) -> bool:
    """Whether the SQL the target evaluates still holds the conversion (as `evaluatedByTarget` reads it)."""
    status = getattr(statement, "target_status", None)
    if status == "ERROR":
        return False
    texts = list(getattr(statement, "target_sql", None) or []) if status in ("OK", "WARN") \
        else [getattr(statement, "original_sql", None) or ""]
    return any(_CONVERSION.search(text) or (implicit and "||" in text) for text in texts)


def _statement_text(statement: M.Statement) -> str:
    arguments = [str(a) for a in getattr(statement, "arguments", []) or []]
    return " ".join(str(getattr(statement, f, "") or "") for f in _TEXT_FIELDS) + " " + " ".join(arguments)


# `TO_CHAR(<first>, '<format>'` and `TO_NUMBER(<first>, '<format>'`: the first argument without a top-level comma
_LITERAL_FORMAT = re.compile(
    r"\bTO_(?P<function>CHAR|NUMBER)\s*\(\s*(?P<first>(?:[^,()']|'(?:[^']|'')*'|\((?:[^()']|'(?:[^']|'')*')*\))+?)"
    r"\s*,\s*'(?P<format>(?:[^']|'')*)'\s*[,)]", re.IGNORECASE)


def _unsupported_formats(text: str, declared: dict[str, str], timestamps: set[str]) -> list[str]:
    """What the runtime refuses in the literal formats of this text, each with the element named."""
    out = []
    for match in _LITERAL_FORMAT.finditer(text):
        model = match.group("format").replace("''", "'")
        first = match.group("first").strip().lower()
        if match.group("function").upper() == "NUMBER":
            check = check_number(model)
            if check.state == "unsupported":
                out.append(f"TO_NUMBER format '{model}': {check.detail}; the runtime refuses it")
            continue
        number, date = check_number(model), check_date(model)
        if number.state == "unsupported" and date.state != "ok":
            out.append(f"TO_CHAR format '{model}': {number.detail}; the runtime refuses it")
            continue
        if date.state != "ok" or number.state == "ok":
            continue
        # FF, X and TZR / TZD: ORA-01821 on a DATE, written on a TIMESTAMP. The generated code tells them apart by
        # the declared type; a value it cannot type (a record field, a function's result) is refused at run time
        # unless it carries a fraction of a second that says TIMESTAMP
        needs_type = date.elements & (FRACTION_ELEMENTS - {"TZH", "TZM"})
        if needs_type and first not in timestamps and first not in _ZONED_CLOCKS \
                and not (first in declared and not _TIMESTAMP.match(declared[first])):
            out.append(f"TO_CHAR({match.group('first').strip()}, '{model}'): {', '.join(sorted(needs_type))} on a "
                       f"value whose type (DATE or TIMESTAMP) the generator does not know; the runtime refuses it "
                       f"unless the value has a fraction of a second")
    return out


def _written_as_text(statement: M.Statement, names: set[str]) -> str | None:
    arguments = [str(a) for a in getattr(statement, "arguments", []) or []]
    if (getattr(statement, "callee", None) or "").upper() in _OUTPUT and len(arguments) == 1 \
            and arguments[0].strip().lower() in names:
        return arguments[0].strip()
    text = _statement_text(statement)
    for name in sorted(names):
        word = re.escape(name)
        # TO_CHAR(d) with no format, and d on either side of || -- not d.field, d(i), or TO_CHAR(d, 'fmt')
        if re.search(rf"\bTO_CHAR\s*\(\s*{word}\s*\)", text, re.IGNORECASE) \
                or re.search(rf"\|\|\s*{word}\b(?!\s*[.(])", text, re.IGNORECASE) \
                or re.search(rf"(?<![.\w]){word}\s*\|\|", text, re.IGNORECASE):
            return name
    return None
