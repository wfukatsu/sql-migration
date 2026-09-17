"""P2-6: PL/SQL expressions -> Java, through the semantics helper.

A PL/SQL expression does not mean in Java what it looks like it means. `=` is three-valued, `||` treats NULL as
empty, `''` is NULL, and `ROUND` is half-up. Emitting the Java operator and hoping is how a migration ends up
with code that passes review and fails on the first NULL.

So comparisons and the Oracle built-ins become calls into `com.scalar.migrate.plsql.Plsql`, where the behaviour
is written down and tested once. Identifiers are renamed through the scope, and anything the translator does not
recognise is reported rather than guessed -- an unknown function reaching the output would either not compile or,
worse, resolve to something with different semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .types import java_name

HELPER = "Plsql"
HELPER_IMPORT = "com.scalar.migrate.plsql.Plsql"
SEQUENCES_IMPORT = "com.scalar.migrate.plsql.Sequences"

TOKEN = re.compile(r"""
    (?P<string>'(?:[^']|'')*')
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<bind>:[A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)
  | (?P<attribute>[A-Za-z][\w$#]*\s*%\s*[A-Za-z][\w$#]*)
  | (?P<name>[A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)*)
  | (?P<op><=|>=|<>|!=|\|\||:=|[-+*/(),=<>%])
  | (?P<space>\s+)
""", re.VERBOSE)

COMPARISONS = {"=": "eq", "<>": "ne", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
# Oracle built-ins the helper covers. Anything else is reported, not invented.
FUNCTIONS = {
    "NVL": f"{HELPER}.nvl", "ROUND": f"{HELPER}.round", "TRUNC": f"{HELPER}.trunc",
    "TO_CHAR": f"{HELPER}.text", "RTRIM": f"{HELPER}.rtrim", "LTRIM": f"{HELPER}.ltrim",
    "MOD": f"{HELPER}.mod", "ABS": f"{HELPER}.abs",
}
# Values, not calls. SYSDATE is the database clock, which is not the JVM clock -- the helper takes it from the
# caller so that a generated routine is testable and the difference stays visible.
VALUES = {"SYSDATE": f"{HELPER}.sysdate()", "SYSTIMESTAMP": f"{HELPER}.systimestamp()"}
KEYWORDS = {"AND", "OR", "NOT", "NULL", "IS", "TRUE", "FALSE", "MOD", "BETWEEN", "IN", "LIKE"}


@dataclass
class Expression:
    java: str
    imports: set[str] = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)   # names the translator could not place
    # 式が採る sequence。Repository が Sequences を受け取る必要があるかを、生成側が知るため
    sequences: set[str] = field(default_factory=set)

    @property
    def translatable(self) -> bool:
        return not self.unknown


def translate(text: str | None, names: dict[str, str] | None = None) -> Expression:
    """Render one PL/SQL expression as Java. `names` maps PL/SQL identifiers to the Java ones in scope."""
    if text is None or not text.strip():
        return Expression("")
    scope = {k.lower(): v for k, v in (names or {}).items()}
    tokens = _tokens(text.strip())
    result = Expression("")
    rendered = _render(tokens, scope, result)
    result.java = rendered
    if HELPER + "." in rendered:
        result.imports.add(HELPER_IMPORT)
    return result


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if match is None:
            out.append(("other", text[position]))
            position += 1
            continue
        kind = match.lastgroup
        if kind != "space":
            out.append((kind, match.group()))
        position = match.end()
    return out


def _render(tokens: list[tuple[str, str]], scope: dict[str, str], result: Expression) -> str:
    """Recursive descent over PL/SQL's precedence.

    A first attempt rewrote operators by marker substitution on the rendered string; it mis-split
    `a > 1 AND b = 'x'` because a marker cannot tell how far its operands reach. Parsing by precedence is both
    shorter and correct.
    """
    parser = _Parser(tokens, scope, result)
    rendered = parser.parse_or()
    rest = parser.rest()
    return (rendered + " " + rest).strip() if rest else rendered


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], scope: dict[str, str], result: Expression) -> None:
        self.tokens = tokens
        self.scope = scope
        self.result = result
        self.position = 0

    # -- helpers ---------------------------------------------------------------------------------------
    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def at_word(self, *words: str) -> bool:
        token = self.peek()
        return token is not None and token[0] == "name" and token[1].upper() in words

    def rest(self) -> str:
        remaining = " ".join(v for _, v in self.tokens[self.position:])
        self.position = len(self.tokens)
        return remaining

    # -- grammar ---------------------------------------------------------------------------------------
    def parse_or(self) -> str:
        left = self.parse_and()
        while self.at_word("OR"):
            self.take()
            left = f"{left} || {self.parse_and()}"
        return left

    def parse_and(self) -> str:
        left = self.parse_not()
        while self.at_word("AND"):
            self.take()
            left = f"{left} && {self.parse_not()}"
        return left

    def parse_not(self) -> str:
        if self.at_word("NOT"):
            self.take()
            return f"!({self.parse_not()})"
        return self.parse_comparison()

    def parse_comparison(self) -> str:
        left = self.parse_concat()
        if self.at_word("IS"):
            self.take()
            negated = self.at_word("NOT")
            if negated:
                self.take()
            if self.at_word("NULL"):
                self.take()
                self.result.imports.add(HELPER_IMPORT)
                return f"{HELPER}.{'isNotNull' if negated else 'isNull'}({left})"
            return f"{left} is{'Not' if negated else ''}"
        negated = False
        if self.at_word("NOT"):
            # `x NOT IN (...)` / `x NOT LIKE ...`: the NOT belongs to the operator, not to a fresh operand
            self.take()
            negated = True
        if self.at_word("IN"):
            self.take()
            return self._wrap(negated, f"{HELPER}.in({left}, {', '.join(self._arguments())})")
        if self.at_word("BETWEEN"):
            self.take()
            low = self.parse_concat()
            if self.at_word("AND"):
                self.take()
            high = self.parse_concat()
            return self._wrap(negated, f"{HELPER}.between({left}, {low}, {high})")
        if self.at_word("LIKE"):
            self.take()
            return self._wrap(negated, f"{HELPER}.like({left}, {self.parse_concat()})")
        if negated:
            self.result.unknown.append("NOT")
            return f"!({left})"
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] in COMPARISONS:
            operator = self.take()[1]
            right = self.parse_concat()
            self.result.imports.add(HELPER_IMPORT)
            return f"{HELPER}.{COMPARISONS[operator]}({left}, {right})"
        return left

    def _wrap(self, negated: bool, call: str) -> str:
        self.result.imports.add(HELPER_IMPORT)
        return f"!({call})" if negated else call

    def _arguments(self) -> list[str]:
        """The parenthesised list of an IN."""
        if self.peek() is None or self.peek()[1] != "(":
            return [self.parse_concat()]
        self.take()
        out: list[str] = []
        while self.peek() is not None and self.peek()[1] != ")":
            out.append(self.parse_or())
            if self.peek() is not None and self.peek()[1] == ",":
                self.take()
        if self.peek() is not None and self.peek()[1] == ")":
            self.take()
        return out

    ARITHMETIC = {"+": "add", "-": "sub", "*": "mul", "/": "div"}

    def parse_arithmetic(self) -> str:
        """`a + b` on a BigDecimal does not compile in Java, and on a boxed null it throws."""
        left = self.parse_primary()
        while True:
            token = self.peek()
            if token is None or token[0] != "op" or token[1] not in self.ARITHMETIC:
                return left
            operator = self.take()[1]
            right = self.parse_primary()
            self.result.imports.add(HELPER_IMPORT)
            left = f"{HELPER}.{self.ARITHMETIC[operator]}({left}, {right})"

    def parse_concat(self) -> str:
        parts = [self.parse_arithmetic()]
        while self.peek() is not None and self.peek()[1] == "||":
            self.take()
            parts.append(self.parse_arithmetic())
        if len(parts) == 1:
            return parts[0]
        self.result.imports.add(HELPER_IMPORT)
        return f"{HELPER}.concat({', '.join(parts)})"

    def parse_case(self) -> str:
        """`CASE x WHEN a THEN b ... ELSE c END` as a chain of ternaries.

        A CASE expression is not a statement, so the statement lowering never sees it; without this every routine
        holding one is refused, which is how `tier_discount` -- a three-line pure function -- failed to generate.
        """
        self.take()  # CASE
        selector = None
        if not self.at_word("WHEN"):
            selector = self.parse_or()
        branches: list[tuple[str, str]] = []
        while self.at_word("WHEN"):
            self.take()
            condition = self.parse_or()
            if self.at_word("THEN"):
                self.take()
            branches.append((condition, self.parse_or()))
        otherwise = "null"
        if self.at_word("ELSE"):
            self.take()
            otherwise = self.parse_or()
        if self.at_word("END"):
            self.take()

        rendered = otherwise
        for condition, value in reversed(branches):
            if selector is not None:
                self.result.imports.add(HELPER_IMPORT)
                condition = f"{HELPER}.eq({selector}, {condition})"
            rendered = f"({condition} ? {value} : {rendered})"
        return rendered

    def parse_primary(self) -> str:
        """Everything tighter than concatenation: literals, names, calls, parentheses, arithmetic.

        A parenthesised group is parsed again from the top, so `NOT (a = b)` becomes `!(Plsql.eq(a, b))` rather
        than a literal `(a = b)` that does not compile.
        """
        out: list[str] = []
        while self.position < len(self.tokens):
            kind, value = self.peek()
            if value == "," and not out:
                break
            if value == "," :
                break
            if value == "||" or (kind == "op" and (value in COMPARISONS or value in self.ARITHMETIC)) \
                    or (kind == "name" and value.upper() in ("AND", "OR", "IS", "NOT", "IN", "BETWEEN",
                                                             "LIKE", "WHEN", "THEN", "ELSE", "END")):
                break
            if kind == "op" and value == ")":
                break
            if kind == "name" and value.upper() == "CASE":
                out.append(self.parse_case())
                continue
            if kind == "op" and value == "(":
                self.take()
                inner = self.parse_or()
                if self.peek() is not None and self.peek()[1] == ")":
                    self.take()
                out.append(f"({inner})")
                continue
            if kind == "name" and self.position + 1 < len(self.tokens) \
                    and self.tokens[self.position + 1][1] == "(":
                out.append(self._call())
                continue
            self.take()
            out.append(self._atom(kind, value))
        return "".join(out).strip()

    def _call(self) -> str:
        """A function call: the name, then each argument parsed as a full expression."""
        name = self._name(self.take()[1])
        self.take()  # the '('
        arguments: list[str] = []
        while self.peek() is not None and self.peek()[1] != ")":
            arguments.append(self.parse_or())
            if self.peek() is not None and self.peek()[1] == ",":
                self.take()
            elif self.peek() is not None and self.peek()[1] != ")":
                arguments.append(self.take()[1])
        if self.peek() is not None and self.peek()[1] == ")":
            self.take()
        return f"{name}({', '.join(a for a in arguments if a)})"

    def _atom(self, kind: str, value: str) -> str:
        if kind == "bind":
            # `:NEW.col` / `:OLD.col` in a trigger, or a host variable. Neither has a Java equivalent here, and
            # triggers are a REDESIGN anyway, so this is refused rather than rendered.
            self.result.unknown.append(value)
            return value
        if kind == "attribute":
            # `SQL%ROWCOUNT`, `c%NOTFOUND`: one name, not a modulo
            key = " ".join(value.split()).replace(" ", "").lower()
            mapped = self.scope.get(key)
            if mapped is None:
                self.result.unknown.append(value)
                return value
            return mapped
        if kind == "string":
            return _string(value)
        if kind == "number":
            return value
        if kind == "op":
            return value if value in "()," else f" {value} "
        if kind == "name":
            return self._name(value)
        return value

    def _name(self, value: str) -> str:
        upper = value.upper()
        following = self.peek()[1] if self.peek() is not None else ""
        if upper in ("NULL", "TRUE", "FALSE"):
            return upper.lower()
        if upper in VALUES:
            self.result.imports.add(HELPER_IMPORT)
            return VALUES[upper]
        if following == "(" or upper in FUNCTIONS:
            # a sibling routine is a call too, and the scope knows its Java name; checking FUNCTIONS first
            # would report every local function call as unknown
            if value.lower() in self.scope:
                return self.scope[value.lower()]
            function = FUNCTIONS.get(upper)
            if function is None:
                self.result.unknown.append(value)
                return value
            self.result.imports.add(HELPER_IMPORT)
            return function
        if value.lower() in self.scope:
            # the whole dotted name first: a cursor FOR loop's `r.order_id` that was bound out of the SQL (#10)
            # is one repository parameter, and splitting it would look for a record called `r` that the
            # repository does not have
            return self.scope[value.lower()]
        if "." in value:
            head, _, tail = value.partition(".")
            if tail.upper() == "NEXTVAL":
                # 採番。移行先の方式は DDL から導いてあり、呼ぶ口は Sequences（計画 §9）
                self.result.imports.add(SEQUENCES_IMPORT)
                self.result.sequences.add(head.lower())
                return f'sequences.next("{head.lower()}")'
            if head.lower() not in self.scope:
                # `v_ids.COUNT` on a collection, or a package-qualified name: neither is a record field, and
                # rendering it as one produces a call to a method that does not exist
                self.result.unknown.append(value)
                return value
            return f"{self.scope[head.lower()]}.{java_name(tail)}()"
        self.result.unknown.append(value)
        return java_name(value)


def _string(literal: str) -> str:
    body = literal[1:-1].replace("''", "'")
    escaped = body.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
