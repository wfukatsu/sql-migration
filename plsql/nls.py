"""Where a routine writes a date or a timestamp as text without saying how (#94).

`TO_CHAR(d)`, `'…' || d` and `DBMS_OUTPUT.PUT_LINE(d)` take their shape from the session: NLS_DATE_FORMAT,
NLS_TIMESTAMP_FORMAT, NLS_DATE_LANGUAGE. The generated code writes what Oracle writes with the defaults (26-SEP-26,
26-SEP-26 09.30.00.500000 AM), and the comparison against Oracle runs with the defaults too, so a match there says
nothing about a source database whose sessions set them otherwise. This layer only states where it happens; the
rule file says what it means (`rules/semantics.yaml` SEM-012). A BOOLEAN is TRUE / FALSE whatever the session says,
so it is not one of them.
"""

from __future__ import annotations

import re

from .ir import model as M
from .lower import _walk

_DATE_LIKE = re.compile(r"^\s*(?:DATE|TIMESTAMP\b.*)\s*$", re.IGNORECASE)
# what the session gives as a date whatever the routine declares
_CLOCKS = ("sysdate", "systimestamp", "current_date", "current_timestamp", "localtimestamp")
_OUTPUT = ("DBMS_OUTPUT.PUT_LINE", "DBMS_OUTPUT.PUT")
_TEXT_FIELDS = ("original_sql", "expression", "condition", "initial", "default", "message")


def annotate(program: M.Program) -> None:
    for module in program.modules:
        for routine in module.routines:
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            holders = list(routine.parameters) + list(routine.declarations) + \
                [d for s in statements for d in getattr(s, "declarations", []) or []]
            names = {h.name.lower() for h in holders
                     if h.type is not None and _DATE_LIKE.match(h.type.resolved or h.type.oracle or "")}
            names.update(_CLOCKS)
            for statement in statements:
                written = _written_as_text(statement, names)
                if written and not any(d.code == "IMPLICIT_DATE_TEXT" for d in statement.diagnostics):
                    statement.add("INFO", "IMPLICIT_DATE_TEXT",
                                  f"{written} is written as text without a format; its shape comes from the "
                                  f"session's NLS settings")


def _written_as_text(statement: M.Statement, names: set[str]) -> str | None:
    arguments = [str(a) for a in getattr(statement, "arguments", []) or []]
    if (getattr(statement, "callee", None) or "").upper() in _OUTPUT and len(arguments) == 1 \
            and arguments[0].strip().lower() in names:
        return arguments[0].strip()
    text = " ".join(str(getattr(statement, f, "") or "") for f in _TEXT_FIELDS) + " " + " ".join(arguments)
    for name in sorted(names):
        word = re.escape(name)
        # TO_CHAR(d) with no format, and d on either side of || -- not d.field, d(i), or TO_CHAR(d, 'fmt')
        if re.search(rf"\bTO_CHAR\s*\(\s*{word}\s*\)", text, re.IGNORECASE) \
                or re.search(rf"\|\|\s*{word}\b(?!\s*[.(])", text, re.IGNORECASE) \
                or re.search(rf"(?<![.\w]){word}\s*\|\|", text, re.IGNORECASE):
            return name
    return None
