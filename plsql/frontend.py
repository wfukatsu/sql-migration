"""P1-2: parse the preprocessed units, and report what does not parse as data.

The front end must survive its own corpus. A file the grammar cannot handle is an inventory finding, not a crash:
the whole point of Phase 1 is to show what is there and what is unresolved, so a parse failure becomes an
`Issue(ERROR, "PARSE", ...)` carrying the line of the original file, and the next file is parsed regardless.

    result = parse_file(Path("fixtures/plsql/src/pkg_order.pkb"))
    result.ok          # False when any unit failed
    result.issues      # Issue(ERROR, "PARSE", ...) with a SourceRange into pkg_order.pkb
    result.units[0].tree  # the ANTLR tree, for P1-3 / P1-5

## Parse rate is parser coverage, nothing more

P0-4 found two corpus files that ANTLR accepted and Oracle refused to compile, and P0-5 found one that compiled
and failed at run time. A high parse rate says the grammar reaches the syntax; it says nothing about whether the
unit is valid Oracle, let alone convertible.

## Two-stage parsing

The vendored grammar is ambiguous (its own README says so), and full LL(*) prediction is expensive. Almost every
real unit parses under SLL, which is much cheaper, so each unit is tried with SLL first and re-parsed with the full
strategy only when SLL reports trouble. The fallback is what keeps the result correct: SLL can fail on input that
LL accepts, so its failure is a signal to retry, never a diagnosis. On this corpus 3 of 48 units need it, and the
two stages together are about 5x faster than parsing everything with LL.

## Warm the process, do not fork it

Parsing the whole corpus costs ~14 s in a fresh process and ~0.2 s on the second pass in the same one: ANTLR
deserialises the ATN and builds its decision caches lazily, once per process. Analysis must therefore reuse
workers. Forking a process per routine would pay that ~14 s every time and make parallelism a loss, which is what
the plan's performance risk row is about.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from antlr4 import CommonTokenStream, InputStream, ParserRuleContext
from antlr4.atn.PredictionMode import PredictionMode
from antlr4.error.ErrorListener import ErrorListener
from antlr4.error.Errors import ParseCancellationException
from antlr4.error.ErrorStrategy import BailErrorStrategy

from .grammar import PlSqlLexer, PlSqlParser
from .preprocess import Preprocessed, Unit, preprocess
from .source import Issue, SourceRange


@dataclass
class ParsedUnit:
    unit: Unit
    tree: ParserRuleContext | None
    issues: list[Issue] = field(default_factory=list)
    used_fallback: bool = False

    @property
    def ok(self) -> bool:
        return self.tree is not None and not any(i.severity == "ERROR" for i in self.issues)


@dataclass
class ParsedFile:
    file: str
    units: list[ParsedUnit] = field(default_factory=list)
    preprocessed: Preprocessed | None = None
    issues: list[Issue] = field(default_factory=list)
    parse_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.units) and all(u.ok for u in self.units) and not self._errors(self.issues)

    @staticmethod
    def _errors(issues: list[Issue]) -> list[Issue]:
        return [i for i in issues if i.severity == "ERROR"]

    def all_issues(self) -> list[Issue]:
        return list(self.issues) + [i for u in self.units for i in u.issues]


class _Collector(ErrorListener):
    """Collects syntax errors, mapping each one back to the original file through the unit's source map."""

    def __init__(self, unit: Unit) -> None:
        self.unit = unit
        self.issues: list[Issue] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        # ANTLR lines are 1-based and columns 0-based; SourceLocation is 1-based in both
        where = self.unit.origin(line, column + 1)
        self.issues.append(Issue(
            "ERROR", "PARSE", msg,
            SourceRange(where.file, where.line, where.line, where.column, where.column + 1)))


def _new_parser(text: str, listener: ErrorListener | None) -> PlSqlParser:
    lexer = PlSqlLexer(InputStream(text))
    lexer.removeErrorListeners()
    if listener is not None:
        lexer.addErrorListener(listener)
    parser = PlSqlParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    if listener is not None:
        parser.addErrorListener(listener)
    return parser


def parse_unit(unit: Unit) -> ParsedUnit:
    """Parse one unit. Never raises: a failure comes back as an ERROR issue."""
    # stage 1: SLL, bail on the first problem, no listeners (its errors are not diagnoses)
    parser = _new_parser(unit.text, None)
    parser._interp.predictionMode = PredictionMode.SLL
    parser._errHandler = BailErrorStrategy()
    try:
        return ParsedUnit(unit=unit, tree=parser.sql_script())
    except (ParseCancellationException, RecursionError, Exception):  # noqa: BLE001 - stage 2 decides
        pass

    # stage 2: the full strategy, collecting diagnostics
    collector = _Collector(unit)
    parser = _new_parser(unit.text, collector)
    try:
        tree = parser.sql_script()
    except RecursionError:
        return ParsedUnit(unit=unit, tree=None, used_fallback=True, issues=[Issue(
            "ERROR", "PARSE", "the grammar recursed too deeply on this unit", unit.range)])
    except Exception as e:  # noqa: BLE001 - one unit must not stop the file
        return ParsedUnit(unit=unit, tree=None, used_fallback=True, issues=[Issue(
            "ERROR", "PARSE_INTERNAL", f"{type(e).__name__}: {e}", unit.range)])
    return ParsedUnit(unit=unit, tree=tree, issues=collector.issues, used_fallback=True)


def parse_text(text: str, file: str = "<memory>") -> ParsedFile:
    started = time.perf_counter()
    result = ParsedFile(file=file)
    try:
        pre = preprocess(text, file)
    except Exception as e:  # noqa: BLE001 - preprocessing must not stop the run either
        result.issues.append(Issue("ERROR", "PREPROCESS_INTERNAL", f"{type(e).__name__}: {e}"))
        result.parse_seconds = time.perf_counter() - started
        return result

    result.preprocessed = pre
    result.issues.extend(pre.issues)
    for unit in pre.units:
        result.units.append(parse_unit(unit))
    # No units means the file held only SQL*Plus directives or blank lines. preprocess() already warns
    # (EMPTY); calling that a parse error would turn a legitimate setup script into a failure.
    result.parse_seconds = time.perf_counter() - started
    return result


def parse_file(path: str | Path) -> ParsedFile:
    """Parse one file. A read failure is a diagnostic too, so a run over a directory never stops."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return ParsedFile(file=str(path), issues=[Issue("ERROR", "READ", str(e))])
    return parse_text(text, path.name)


SOURCE_SUFFIXES = {".pks", ".pkb", ".prc", ".fnc", ".trg", ".pls", ".sql"}


def parse_directory(root: str | Path, suffixes: set[str] | None = None) -> list[ParsedFile]:
    """Every source file under `root`. One file's failure never stops the others."""
    root = Path(root)
    wanted = suffixes or SOURCE_SUFFIXES
    return [parse_file(p) for p in sorted(root.rglob("*")) if p.suffix.lower() in wanted]


@dataclass
class Coverage:
    """KPI-1 (docs/design/plsql-kpi.md): parse rate over files, with the failures named."""

    total: int
    parsed: int
    failed: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def rate(self) -> float:
        return 1.0 if not self.total else self.parsed / self.total


def coverage(results: list[ParsedFile]) -> Coverage:
    failed = [r.file for r in results if not r.ok]
    return Coverage(total=len(results), parsed=len(results) - len(failed), failed=failed,
                    seconds=sum(r.parse_seconds for r in results))
