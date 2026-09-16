"""P1-1: turn a SQL*Plus script into the units the parser sees, keeping every line traceable.

A corpus file is not pure PL/SQL. It carries SQL*Plus directives (`SET`, `@`, `SPOOL`, ...), `/` terminators that
only mean "end of block" at the start of a line, and comments that may contain anything at all. The parser must
receive PL/SQL, and every diagnostic it later raises has to name the line the developer wrote -- so the split and
the position mapping belong together, here, before anything else runs.

    result = preprocess(Path("pkg_order.pkb").read_text(), "pkg_order.pkb")
    for unit in result.units:
        tree = parse(unit.text)                       # P1-2
        loc = unit.origin(line_from_antlr, column)    # back to pkg_order.pkb:42:7

## Two rules keep the mapping exact

* **Only whole lines are removed.** A directive occupies its own line, so a line of output maps to exactly one line
  of input and columns never shift. Nothing has to track offsets inside a line.
* **The input is never case-folded.** The vendored grammar matches keywords case-insensitively (verified in P0-6),
  and upper-casing would destroy the casing of string literals.

## What terminates a unit

SQL*Plus decides this by what the unit starts with, and so does this module:

* a block (`DECLARE` / `BEGIN`, or `CREATE ... PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE`) ends at a line holding
  only `/` -- the `;` inside it belong to the PL/SQL
* anything else ends at the first `;` outside a string or comment

`/` and `;` inside a string literal, a q-quoted literal, a `--` comment or a `/* */` comment are text, not
terminators. That is the whole reason this is a scanner rather than a regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .source import Diagnostics, Issue, SourceLocation, SourceMap, SourceRange

# SQL*Plus directives that occupy a whole line. `@` and `@@` are handled separately: they pull in another file.
DIRECTIVES = re.compile(
    r"^\s*(SET|SHOW|SPOOL|WHENEVER|DEFINE|UNDEFINE|PROMPT|COLUMN|TTITLE|BTITLE|BREAK|COMPUTE|ACCEPT|PAUSE|"
    r"CONNECT|DISCONNECT|EXIT|QUIT|CLEAR|HOST|STARTUP|SHUTDOWN|VARIABLE|PRINT|REM|REMARK)\b",
    re.IGNORECASE)
# EXEC / EXECUTE are SQL*Plus shorthand, but `EXECUTE IMMEDIATE` is PL/SQL. Only the former is a directive.
EXEC_DIRECTIVE = re.compile(r"^\s*EXEC(UTE)?\b(?!\s+IMMEDIATE\b)", re.IGNORECASE)
INCLUDE = re.compile(r"^\s*@@?\s*(\S+)")
SUBSTITUTION = re.compile(r"&&?[A-Za-z][A-Za-z0-9_]*")
BLOCK_START = re.compile(
    r"^\s*(DECLARE|BEGIN|CREATE\s+(OR\s+REPLACE\s+)?(EDITIONABLE\s+|NONEDITIONABLE\s+)?"
    r"(PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE)\b)", re.IGNORECASE)


@dataclass
class Unit:
    """One statement or PL/SQL block, with the map back to the file it came from."""

    text: str
    kind: str  # "block" | "statement"
    range: SourceRange
    map: SourceMap

    def origin(self, line: int, column: int = 1) -> SourceLocation:
        """Position in this unit's text (1-based) -> position in the original file."""
        return self.map.origin(line, column)


@dataclass
class Preprocessed:
    file: str
    units: list[Unit] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)

    @property
    def issues(self) -> list[Issue]:
        return list(self.diagnostics)


def preprocess(text: str, file: str = "<memory>") -> Preprocessed:
    lines = text.splitlines()
    out = Preprocessed(file=file)

    kept, kept_origin = _strip_directives(lines, file, out)
    full_map = SourceMap(file, kept_origin)
    for start, end, kind in _split(kept):
        body = "\n".join(kept[start - 1:end])
        if not body.strip():
            continue
        unit = Unit(
            text=body,
            kind=kind,
            range=SourceRange(file, kept_origin[start - 1], kept_origin[end - 1], 1, len(kept[end - 1]) + 1),
            map=full_map.slice(start, end),
        )
        out.units.append(unit)
        for match in SUBSTITUTION.finditer(body):
            out.diagnostics.add(
                "WARN", "SQLPLUS_SUBSTITUTION",
                f"substitution variable {match.group(0)} is resolved by SQL*Plus at run time, not by this analysis",
                unit.range)

    if not out.units and text.strip():
        out.diagnostics.add("WARN", "EMPTY", "the file has no statement after preprocessing",
                            SourceRange(file, 1, max(1, len(lines))))
    return out


def _strip_directives(lines: list[str], file: str, out: "Preprocessed") -> tuple[list[str], list[int]]:
    """Drop SQL*Plus lines, but only where SQL*Plus would read them: between statements.

    Inside a statement the same words are ordinary SQL. A continuation line of a multi-line UPDATE starts with
    `SET`, and a PL/SQL body contains `EXECUTE IMMEDIATE`; dropping either would silently corrupt the unit.
    """
    kept: list[str] = []
    kept_origin: list[int] = []
    between_units = True
    open_: Open = None
    kind: str | None = None

    for number, line in enumerate(lines, start=1):
        if between_units:
            include = INCLUDE.match(line)
            if include:
                out.includes.append(include.group(1))
                out.diagnostics.add(
                    "WARN", "SQLPLUS_INCLUDE",
                    f"@{include.group(1)} is not inlined; analyse that file separately",
                    SourceRange(file, number, number))
                continue
            if DIRECTIVES.match(line) or EXEC_DIRECTIVE.match(line):
                continue

        kept.append(line)
        kept_origin.append(number)

        # track whether the next line starts a new unit, using the same rules as _split
        code, open_ = _strip_comments(line, open_)
        if kind is None:
            if code.strip():
                kind = "block" if BLOCK_START.match(code) else "statement"
        if kind == "block" and code.strip() == "/":
            kind = None
        elif kind == "statement" and ";" in code:
            kind = None
        between_units = kind is None

    return kept, kept_origin


def _split(lines: list[str]) -> list[tuple[int, int, str]]:
    """Unit boundaries as (start, end, kind), 1-based and inclusive over `lines`."""
    units: list[tuple[int, int, str]] = []
    start = 1
    kind: str | None = None
    open_: Open = None

    line_number = 0
    while line_number < len(lines):
        line_number += 1
        raw = lines[line_number - 1]
        code, open_ = _strip_comments(raw, open_)

        if kind is None:
            if not code.strip():
                if not raw.strip() and start == line_number:
                    start = line_number + 1  # skip leading blank lines
                continue
            kind = "block" if BLOCK_START.match(code) else "statement"

        if kind == "block":
            if code.strip() == "/":
                units.append((start, line_number, kind))
                start, kind = line_number + 1, None
            continue

        semicolon = code.find(";")
        if semicolon >= 0:
            units.append((start, line_number, kind))
            start, kind = line_number + 1, None

    if kind is not None and start <= len(lines):
        units.append((start, len(lines), kind))
    return units


def _strip_comments(line: str, open_: "Open" = None) -> tuple[str, "Open"]:
    """Blank out comments and literals so a `/` or `;` inside them is not read as a terminator.

    Characters are replaced by spaces rather than removed, so column positions stay put. The returned state carries
    an unterminated comment or literal to the next line: both may legally span lines in PL/SQL, and a `/` sitting
    inside one is text, not a terminator.
    """
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        if open_ is not None:
            kind, closer = open_
            end = line.find("*/", i) if kind == "comment" else \
                line.find("'", i) if kind == "string" else line.find(closer + "'", i)
            width = 2 if kind != "string" else 1
            if end < 0:
                out.append(" " * (n - i))
                return "".join(out), open_
            if kind == "string" and line.startswith("''", end):
                out.append(" " * (end + 2 - i))
                i = end + 2
                continue
            out.append(" " * (end + width - i))
            i = end + width
            open_ = None
            continue
        ch = line[i]
        if line.startswith("--", i):
            out.append(" " * (n - i))
            break
        if line.startswith("/*", i):
            out.append("  ")
            i += 2
            open_ = ("comment", None)
            continue
        if ch in "qQ" and line.startswith("'", i + 1):
            if i + 2 >= n:
                out.append(" " * (n - i))
                return "".join(out), ("q", "'")
            opener = line[i + 2]
            out.append("   ")
            i += 3
            open_ = ("q", _Q_CLOSE.get(opener, opener))
            continue
        if ch == "'":
            out.append(" ")
            i += 1
            open_ = ("string", None)
            continue
        out.append(ch)
        i += 1
    return "".join(out), open_


_Q_CLOSE = {"[": "]", "(": ")", "{": "}", "<": ">"}

# Scanner state carried from one line to the next: a literal or a comment may span lines.
#   None                      nothing open
#   ("comment", None)         inside /* ... */
#   ("string", None)          inside '...'
#   ("q", closer)             inside q'X ... Xq' with this closing character
Open = tuple[str, str | None] | None


