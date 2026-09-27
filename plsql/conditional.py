"""#118: conditional compilation -- keep the text Oracle would compile, blank out the rest.

    $IF $$logger_debug $THEN dbms_output.put_line('x'); $ELSE null; $END

Oracle decides which side to compile when the unit is created, from `PLSQL_CCFLAGS` (the inquiry directives
`$$name`) and the database version (`DBMS_DB_VERSION`). The grammar has no rule for the directives, so a file with
one did not parse at all: Logger 3.1.1 has 87 (samples of public PL/SQL, #15). The synthetic corpus had none.

Only the selected side is kept. Everything else -- the directives, the conditions, the sides not taken -- becomes
blanks, newlines kept, so every line and column still points where it did (the same rule as `preprocess`).

The flags are the source database's settings, which the migration does not see. An inquiry directive nobody
declared is NULL, as in Oracle, and a NULL condition is not true. What was assumed is reported, so that someone who
knows the real `PLSQL_CCFLAGS` can say otherwise (`conditionalCompilation` in limits.yaml). A condition this does not
understand is not guessed: the text is left as it is and fails to parse, as before.
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass, field

DIRECTIVE = re.compile(r"\$(if|elsif|else|end|then|error)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Settings:
    """What the source database compiled with. `version` is DBMS_DB_VERSION.VERSION / RELEASE."""

    flags: dict = field(default_factory=dict)
    version: tuple[int, int] = (19, 0)


_SETTINGS: "contextvars.ContextVar[Settings]" = contextvars.ContextVar("conditional_compilation", default=Settings())


def configure(settings: Settings) -> None:
    _SETTINGS.set(settings)


def settings() -> Settings:
    return _SETTINGS.get()


class Unresolved(Exception):
    """A directive or a condition this does not understand."""


@dataclass
class Selected:
    text: str
    directives: int = 0                                      # how many $IF were resolved
    flags_read: set[str] = field(default_factory=set)        # the inquiry directives the conditions asked about
    errors: list[tuple[int, str]] = field(default_factory=list)   # $ERROR on a selected side: (line, message)
    unresolved: str | None = None                            # why the text was left as it was


def select(text: str) -> Selected:
    if "$" not in text or not DIRECTIVE.search(text):
        return Selected(text)
    code = _code_only(text)
    tokens = [(m.start(), m.end(), m.group(1).lower()) for m in DIRECTIVE.finditer(code)]
    if not any(kind == "if" for _, _, kind in tokens) and not any(kind == "error" for _, _, kind in tokens):
        return Selected(text)
    out = list(text)
    result = Selected(text)
    try:
        position = _block(tokens, 0, text, code, out, result, top=True)
        if position != len(tokens):
            raise Unresolved(f"unexpected ${tokens[position][2]}")
    except Unresolved as problem:
        return Selected(text, unresolved=str(problem))
    result.text = "".join(out)
    return result


def _block(tokens, i, text, code, out, result, top=False, keep=True) -> int:
    """Walk tokens from `i` until a token that closes the enclosing construct (or the end at the top level)."""
    while i < len(tokens):
        start, end, kind = tokens[i]
        if kind == "if":
            i = _if(tokens, i, text, code, out, result, keep)
        elif kind == "error":
            close = _find(tokens, i + 1, "end")
            if keep:
                line = text.count("\n", 0, start) + 1
                result.errors.append((line, " ".join(code[end:tokens[close][0]].split())))
            _blank(out, start, tokens[close][1])
            i = close + 1
        elif top:
            raise Unresolved(f"${kind} outside $IF")
        else:
            return i
    if not top:
        raise Unresolved("$IF without $END")
    return i


def _if(tokens, i, text, code, out, result, keep) -> int:
    """`$IF c $THEN a [$ELSIF c $THEN b]... [$ELSE e] $END`: keep the first side whose condition is true."""
    chosen = False
    result.directives += 1
    kind = "if"
    while True:
        start = tokens[i][0]
        if kind in ("if", "elsif"):
            then = _find(tokens, i + 1, "then")
            condition = code[tokens[i][1]:tokens[then][0]]
            take = keep and not chosen and _evaluate(condition, result) is True
            _blank(out, start, tokens[then][1])
            body_start = tokens[then][1]
            i = _block(tokens, then + 1, text, code, out, result, keep=take)
        elif kind == "else":
            take = keep and not chosen
            _blank(out, start, tokens[i][1])
            body_start = tokens[i][1]
            i = _block(tokens, i + 1, text, code, out, result, keep=take)
        else:
            raise Unresolved(f"unexpected ${kind}")
        if i >= len(tokens):
            raise Unresolved("$IF without $END")
        if not take:
            _blank(out, body_start, tokens[i][0])
        chosen = chosen or take
        kind = tokens[i][2]
        if kind == "end":
            _blank(out, tokens[i][0], tokens[i][1])
            return i + 1
        if kind not in ("elsif", "else"):
            raise Unresolved(f"unexpected ${kind} in $IF")


def _find(tokens, i, wanted) -> int:
    depth = 0
    while i < len(tokens):
        kind = tokens[i][2]
        if kind == "if":
            depth += 1
        elif kind == "end" and depth:
            depth -= 1
        elif kind == wanted and depth == 0:
            return i
        i += 1
    raise Unresolved(f"no ${wanted}")


def _blank(out: list[str], start: int, end: int) -> None:
    for k in range(start, end):
        if out[k] != "\n":
            out[k] = " "


def _code_only(text: str) -> str:
    """The text with comments and literals blanked: a `$if` inside them is not a directive."""
    from .preprocess import _strip_comments

    lines, open_ = [], None
    for line in text.split("\n"):
        code, open_ = _strip_comments(line, open_)
        lines.append(code)
    return "\n".join(lines)


# --- the condition --------------------------------------------------------------------------------------------

TOKEN = re.compile(r"\s*(?:(?P<flag>\$\$[A-Za-z_][\w$#]*)|(?P<version>dbms_db_version\s*\.\s*[A-Za-z_]\w*)"
                   r"|(?P<number>\d+(?:\.\d+)?)|(?P<op><>|!=|<=|>=|=|<|>|\(|\))"
                   r"|(?P<word>[A-Za-z_]\w*))", re.IGNORECASE)


def _evaluate(condition: str, result: Selected):
    tokens = []
    position = 0
    stripped = condition.strip()
    while position < len(stripped):
        match = TOKEN.match(stripped, position)
        if not match or match.end() == position:
            raise Unresolved(f"condition not understood: {stripped!r}")
        position = match.end()
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
    parser = _Condition(tokens, result)
    value = parser.disjunction()
    if parser.i != len(tokens):
        raise Unresolved(f"condition not understood: {stripped!r}")
    return value


class _Condition:
    """AND / OR / NOT with SQL's three values, comparisons, IS [NOT] NULL, and the atoms Oracle allows here."""

    def __init__(self, tokens, result: Selected):
        self.tokens, self.i, self.result = tokens, 0, result

    def peek(self, word: str | None = None):
        if self.i >= len(self.tokens):
            return None
        kind, value = self.tokens[self.i]
        if word is None:
            return value
        return value if value.upper() == word else None

    def take(self):
        self.i += 1
        return self.tokens[self.i - 1]

    def disjunction(self):
        value = self.conjunction()
        while self.peek("OR"):
            self.take()
            other = self.conjunction()
            value = True if value is True or other is True else (None if value is None or other is None else False)
        return value

    def conjunction(self):
        value = self.negation()
        while self.peek("AND"):
            self.take()
            other = self.negation()
            value = False if value is False or other is False else (None if value is None or other is None else True)
        return value

    def negation(self):
        if self.peek("NOT"):
            self.take()
            value = self.negation()
            return None if value is None else not value
        return self.comparison()

    def comparison(self):
        left = self.atom()
        if self.peek("IS"):
            self.take()
            negate = bool(self.peek("NOT")) and bool(self.take())
            if not self.peek("NULL"):
                raise Unresolved("IS without NULL")
            self.take()
            return (left is not None) if negate else (left is None)
        operator = self.peek()
        if operator in ("=", "<>", "!=", "<", ">", "<=", ">="):
            self.take()
            right = self.atom()
            if left is None or right is None:
                return None
            if isinstance(left, bool) != isinstance(right, bool):
                raise Unresolved("a comparison of a BOOLEAN with a number")
            return {"=": left == right, "<>": left != right, "!=": left != right, "<": left < right,
                    ">": left > right, "<=": left <= right, ">=": left >= right}[operator]
        return left

    def atom(self):
        if self.i >= len(self.tokens):
            raise Unresolved("condition ends early")
        kind, value = self.take()
        if value == "(":
            inner = self.disjunction()
            if self.peek() != ")":
                raise Unresolved("unbalanced parentheses")
            self.take()
            return inner
        if kind == "number":
            return float(value)
        if kind == "flag":
            name = value[2:].lower()
            self.result.flags_read.add(name)
            return settings().flags.get(name)
        if kind == "version":
            return _db_version(value.split(".")[-1].strip().lower())
        if kind == "word" and value.upper() in ("TRUE", "FALSE", "NULL"):
            return {"TRUE": True, "FALSE": False, "NULL": None}[value.upper()]
        raise Unresolved(f"{value!r} in a condition")


def _db_version(member: str):
    major, minor = settings().version
    if member == "version":
        return float(major)
    if member == "release":
        return float(minor)
    match = re.fullmatch(r"ver_le_(\d+)(?:_(\d+))?", member)
    if not match:
        raise Unresolved(f"DBMS_DB_VERSION.{member}")
    at_most = (int(match.group(1)), int(match.group(2)) if match.group(2) else None)
    if at_most[1] is None:
        return major <= at_most[0]
    return (major, minor) <= at_most
