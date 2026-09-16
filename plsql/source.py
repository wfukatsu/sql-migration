"""Source positions and diagnostics shared by the PL/SQL front end.

Every later phase (P1-2 parser, P1-4 IR, P1-7 report) has to point back at the line the developer wrote, so the
position types live here rather than inside one stage. Diagnostics use the same `severity / code / message` shape as
`scalardb_migrate.converter.Issue`, which is what the plan's §4.2 asks for -- the two are merged in the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceLocation:
    """A 1-based position in an original file."""

    file: str
    line: int
    column: int = 1

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"


@dataclass(frozen=True)
class SourceRange:
    file: str
    start_line: int
    end_line: int
    start_column: int = 1
    end_column: int = 1

    def __str__(self) -> str:
        if self.start_line == self.end_line:
            return f"{self.file}:{self.start_line}"
        return f"{self.file}:{self.start_line}-{self.end_line}"


class SourceMap:
    """Maps a position in preprocessed text back to the original file.

    The preprocessor only ever drops whole lines, so a line in the output corresponds to exactly one line in the
    input and columns are untouched. Keeping that invariant is what makes the inverse exact: there is no need to
    track offsets inside a line, and a caller can always name the line the developer wrote.
    """

    def __init__(self, file: str, original_lines: list[int]) -> None:
        self.file = file
        self._lines = original_lines  # index: 0-based line of the output, value: 1-based line of the input

    def __len__(self) -> int:
        return len(self._lines)

    def origin(self, line: int, column: int = 1) -> SourceLocation:
        """`line` is 1-based in the preprocessed text."""
        if not 1 <= line <= len(self._lines):
            raise IndexError(f"line {line} is outside the preprocessed text (1..{len(self._lines)})")
        return SourceLocation(self.file, self._lines[line - 1], column)

    def slice(self, start: int, end: int) -> "SourceMap":
        """A map for the output lines [start, end], 1-based and inclusive."""
        return SourceMap(self.file, self._lines[start - 1:end])


@dataclass
class Issue:
    """A diagnostic. Same shape as scalardb_migrate.converter.Issue, plus where it happened."""

    severity: str  # INFO | WARN | ERROR
    code: str
    message: str
    range: SourceRange | None = None

    def __str__(self) -> str:
        where = f" [{self.range}]" if self.range else ""
        return f"{self.severity} {self.code}: {self.message}{where}"


@dataclass
class Diagnostics:
    items: list[Issue] = field(default_factory=list)

    def add(self, severity: str, code: str, message: str, range: SourceRange | None = None) -> None:
        self.items.append(Issue(severity, code, message, range))

    def errors(self) -> list[Issue]:
        return [i for i in self.items if i.severity == "ERROR"]

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)
