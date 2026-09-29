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

## Two decisions are answered without looking ahead to the closing parenthesis

`NVL(a, b)` matches two alternatives of `unary_expression_core` token for token: `standard_function` (the grammar
names NVL, SUBSTR, GREATEST, ...) and `atom` (a call of anything named NVL). ANTLR cannot tell them apart before
the closing parenthesis, so it looks ahead that far -- and inside, each nested built-in forks the same way again.
The first parse of a shape doubled to tripled with every level: `NVL(SUBSTR(TRIM(UPPER(...))))` took 13 s, NVL six
deep 65 s, and the public Logger package about 50 s (#152, review H2). A second parse of the same shape is instant
(the DFA cache), but that cache does not outlive the process. The `function_argument*` loop of
`general_element_part` (`upper(x)`: is the `(` another argument list?) costs seconds the same way on first sight.

So the fast stages take `standard_function` for `<built-in name> (`, and enter the loop at `(`, without the
lookahead (`_Shortcuts`). Each is the alternative ANTLR itself picks whenever it fits: an ambiguity goes to the
lower-numbered alternative, and a loop is greedy. A parse that succeeds this way is therefore the parse the plain
prediction gives. Some names take a narrower form there than a call does (`CHR` wants `USING NCHAR_CS`,
`COALESCE` a column): when a parse fails inside a shortcut, that name (or that `(`) is predicted the plain way from
then on in the unit, and the stage is run again. A failure no shortcut explains falls through to the next stage,
ending with the plain LL parse that has always decided (and reported) what does not parse.

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
from antlr4.atn.ATNState import StarLoopEntryState
from antlr4.atn.ParserATNSimulator import ParserATNSimulator
from antlr4.atn.PredictionMode import PredictionMode
from antlr4.atn.Transition import RuleTransition
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


class _Shortcuts(ParserATNSimulator):
    """Two decisions answered without the lookahead, where the plain prediction explodes (see the module doc).

    * `unary_expression_core` at `<built-in name> (`: `standard_function`. Only for the names `standard_function`
      spells out itself (not the ones it reaches through `regular_id`), and only when no lower alternative (CASE,
      EXISTS, ...) can start with the same token.
    * the `function_argument*` loop of `general_element_part` at `(`: another argument list.

    Each is the alternative the plain prediction picks whenever it is viable (the lowest-numbered one, and a loop
    is greedy). `excluded` holds what a failed run blamed on a shortcut -- a name, or the position of a `(` --
    which is predicted the plain way from then on.
    """

    _ready = False
    _function_decision = _argument_decision = -1
    _function_alternative = _argument_alternative = 0
    _names: frozenset[int] = frozenset()

    @classmethod
    def prepare(cls) -> None:
        if cls._ready:
            return
        atn, rules = PlSqlParser.atn, PlSqlParser
        state = next(s for s in atn.decisionToState if s.ruleIndex == rules.RULE_unary_expression_core)
        entered = [_rules_entered(t.target) for t in state.transitions]
        alternative = next(i for i, r in enumerate(entered) if rules.RULE_standard_function in r)
        lower: set[int] = set()
        for transition in state.transitions[:alternative]:
            lower |= set(atn.nextTokens(transition.target))
        functions = {rules.RULE_standard_function, rules.RULE_string_function, rules.RULE_numeric_function_wrapper,
                     rules.RULE_numeric_function, rules.RULE_json_function, rules.RULE_other_function}
        cls._names = frozenset(_leading_terminals(atn.ruleToStartState[rules.RULE_standard_function], functions)
                               - lower)
        cls._function_alternative = alternative + 1   # ANTLR numbers alternatives from 1
        cls._function_decision = state.decision
        loop = next(s for s in atn.decisionToState
                    if s.ruleIndex == rules.RULE_general_element_part and isinstance(s, StarLoopEntryState))
        assert rules.RULE_function_argument in _rules_entered(loop.transitions[0].target) and not loop.nonGreedy
        cls._argument_decision, cls._argument_alternative = loop.decision, 1   # 1 enters the loop, 2 leaves it
        cls._ready = True

    def __init__(self, *args, excluded: "set[tuple[str, int]] | None" = None) -> None:
        super().__init__(*args)
        self.excluded = excluded if excluded is not None else set()
        self.names: dict[int, int] = {}   # token index of each name taken as standard_function -> its token type
        self.arguments: set[int] = set()  # token index of each `(` taken as an argument list

    def adaptivePredict(self, input, decision, outerContext):  # noqa: N802 - ANTLR's name
        if decision == self._function_decision and input.LA(2) == PlSqlLexer.LEFT_PAREN:
            name = input.LA(1)
            if name in self._names and ("name", name) not in self.excluded:
                self.names[input.index] = name
                return self._function_alternative
        elif decision == self._argument_decision and input.LA(1) == PlSqlLexer.LEFT_PAREN \
                and ("argument", input.index) not in self.excluded:
            self.arguments.add(input.index)
            return self._argument_alternative
        return super().adaptivePredict(input, decision, outerContext)

    def culprit(self, error: BaseException) -> "tuple[str, int] | None":
        """The innermost shortcut the failure happened inside, if any."""
        # BailErrorStrategy raises ParseCancellationException(the RecognitionException)
        cause = error.args[0] if isinstance(error, ParseCancellationException) and error.args else error
        context = getattr(cause, "ctx", None)
        while context is not None:
            start = context.start.tokenIndex if context.start is not None else None
            if isinstance(context, PlSqlParser.Unary_expression_coreContext) and start in self.names:
                return ("name", self.names[start])
            if isinstance(context, PlSqlParser.Function_argumentContext) and start in self.arguments:
                return ("argument", start)
            context = context.parentCtx
        return None


def _rules_entered(state) -> set[int]:
    """The rules an alternative enters before its first token."""
    out, seen, stack = set(), set(), [state]
    while stack:
        current = stack.pop()
        if current.stateNumber in seen:
            continue
        seen.add(current.stateNumber)
        for transition in current.transitions:
            if isinstance(transition, RuleTransition):
                out.add(transition.target.ruleIndex)
            elif transition.isEpsilon:
                stack.append(transition.target)
    return out


def _leading_terminals(state, rules: set[int]) -> set[int]:
    """The tokens written literally at the start of an alternative of `rules` (entered only through `rules`)."""
    out, seen, stack = set(), set(), [state]
    while stack:
        current = stack.pop()
        if current.stateNumber in seen:
            continue
        seen.add(current.stateNumber)
        for transition in current.transitions:
            if isinstance(transition, RuleTransition):
                if transition.target.ruleIndex in rules:
                    stack.append(transition.target)
            elif transition.isEpsilon:
                stack.append(transition.target)
            elif transition.label is not None:
                out |= set(transition.label)
    return out


def _new_parser(text: str, listener: ErrorListener | None,
                shortcuts: "set[tuple[str, int]] | None" = None) -> PlSqlParser:
    """`shortcuts`: None for the plain prediction, or what not to take without the lookahead (`_Shortcuts`)."""
    lexer = PlSqlLexer(InputStream(text))
    lexer.removeErrorListeners()
    if listener is not None:
        lexer.addErrorListener(listener)
    parser = PlSqlParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    if listener is not None:
        parser.addErrorListener(listener)
    if shortcuts is not None:
        _Shortcuts.prepare()
        parser._interp = _Shortcuts(parser, parser.atn, parser.decisionsToDFA, parser.sharedContextCache,
                                    excluded=shortcuts)
    return parser


# a run that keeps failing inside shortcuts is given up to the next stage: each retry parses the unit again
_RETRIES = 8


def _bail(unit: Unit, mode: int, excluded: "set[tuple[str, int]]"):
    """A fast stage: no listeners (its errors are not diagnoses), bail on the first problem, None when it fails.

    A failure inside a shortcut adds it to `excluded` and runs again."""
    for _ in range(_RETRIES + 1):
        parser = _new_parser(unit.text, None, shortcuts=excluded)
        parser._interp.predictionMode = mode
        parser._errHandler = BailErrorStrategy()
        try:
            return parser.sql_script()
        except (ParseCancellationException, RecursionError, Exception) as error:  # noqa: BLE001 - next stage decides
            blamed = parser._interp.culprit(error)
            if blamed is None or blamed in excluded:
                return None
            excluded.add(blamed)
    return None


def parse_unit(unit: Unit) -> ParsedUnit:
    """Parse one unit. Never raises: a failure comes back as an ERROR issue."""
    excluded: set[tuple[str, int]] = set()   # shortcuts this unit does not take (`_Shortcuts`)
    # stage 1: SLL
    tree = _bail(unit, PredictionMode.SLL, excluded)
    if tree is not None:
        return ParsedUnit(unit=unit, tree=tree)
    # stage 2: the full strategy, still bailing: a unit SLL cannot take, without the plain lookahead of stage 3
    tree = _bail(unit, PredictionMode.LL, excluded)
    if tree is not None:
        return ParsedUnit(unit=unit, tree=tree, used_fallback=True)

    # stage 3: the full strategy with the plain prediction, collecting diagnostics
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


# `.spc` / `.bdy` / `.pck` / `.plb` as well: the names Toad and PL/SQL Developer give them (report.BODY_SUFFIXES)
SOURCE_SUFFIXES = {".pks", ".spc", ".pkb", ".bdy", ".pck", ".plb", ".prc", ".fnc", ".trg", ".pls", ".sql"}


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
