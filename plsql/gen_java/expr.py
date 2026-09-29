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

from .types import java_name, plsql_identity

HELPER = "Plsql"
HELPER_IMPORT = "com.scalar.migrate.plsql.Plsql"
SEQUENCES_IMPORT = "com.scalar.migrate.plsql.Sequences"
AUDIT_IMPORT = "com.scalar.migrate.plsql.AuditContext"

TOKEN = re.compile(r"""
    (?P<string>'(?:[^']|'')*')
  | (?P<comment>--[^\n]*|/\*.*?\*/)
  | (?P<inquiry>\$\$[A-Za-z_][\w$#]*)
  | (?P<quoted>"[^"]+")
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<bind>:[A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)
  | (?P<attribute>[A-Za-z][\w$#]*\s*%\s*[A-Za-z][\w$#]*)
  | (?P<name>[A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)*)
  | (?P<op><=|>=|<>|!=|~=|\^=|\|\||:=|[-+*/(),=<>%])
  | (?P<space>\s+)
""", re.VERBOSE | re.DOTALL)

COMPARISONS = {"=": "eq", "<>": "ne", "!=": "ne", "~=": "ne", "^=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
# Oracle built-ins the helper covers. Anything else is reported, not invented.
FUNCTIONS = {
    "NVL": f"{HELPER}.nvl", "ROUND": f"{HELPER}.round", "TRUNC": f"{HELPER}.trunc",
    "TO_CHAR": f"{HELPER}.text", "RTRIM": f"{HELPER}.rtrim", "LTRIM": f"{HELPER}.ltrim",
    "MOD": f"{HELPER}.mod", "ABS": f"{HELPER}.abs", "UPPER": f"{HELPER}.upper",
    "INITCAP": f"{HELPER}.initcap", "TRIM": f"{HELPER}.trim",
    # text that is not a number raises Plsql.ValueError, which a VALUE_ERROR handler catches (as ORA-06502 does)
    "TO_NUMBER": f"{HELPER}.toNumber",
    # #84: the SQL functions PL/SQL calls most, each with Oracle's NULL / empty-string rules (Plsql)
    "LENGTH": f"{HELPER}.length", "LOWER": f"{HELPER}.lower", "SUBSTR": f"{HELPER}.substr",
    "INSTR": f"{HELPER}.instr", "REPLACE": f"{HELPER}.replace", "LPAD": f"{HELPER}.lpad", "RPAD": f"{HELPER}.rpad",
    "CONCAT": f"{HELPER}.concat", "COALESCE": f"{HELPER}.coalesce", "NVL2": f"{HELPER}.nvl2",
    "GREATEST": f"{HELPER}.greatest", "LEAST": f"{HELPER}.least", "POWER": f"{HELPER}.power",
    "SQRT": f"{HELPER}.sqrt", "CEIL": f"{HELPER}.ceil", "FLOOR": f"{HELPER}.floor", "SIGN": f"{HELPER}.sign",
    "CHR": f"{HELPER}.chr", "ASCII": f"{HELPER}.ascii", "TO_DATE": f"{HELPER}.toDate",
    "ADD_MONTHS": f"{HELPER}.addMonths", "LAST_DAY": f"{HELPER}.lastDay",
    # #140, each measured on Oracle 26ai for NULL, its boundaries and the ORA number it raises (PlsqlBuiltinsTest).
    # EXTRACT is read by its own rule (`_extract`), not as a call
    "NULLIF": f"{HELPER}.nullif", "MONTHS_BETWEEN": f"{HELPER}.monthsBetween", "TO_TIMESTAMP": f"{HELPER}.toTimestamp",
    "LENGTHB": f"{HELPER}.lengthb", "TRANSLATE": f"{HELPER}.translate", "BITAND": f"{HELPER}.bitand",
    # PL/SQL's RAWTOHEX reads a text argument as hex, where SQL's writes its bytes: the helper is the PL/SQL one
    "RAWTOHEX": f"{HELPER}.rawToHex", "HEXTORAW": f"{HELPER}.hexToRaw",
    # a value that differs on every call: SEM-014 keeps the routine REVIEW until the migration decides its source
    "SYS_GUID": f"{HELPER}.sysGuid",
    # Oracle's regular expressions, translated to java.util.regex at run time (OracleRegex). REGEXP_LIKE is a
    # condition: it returns a Boolean that is NULL when an argument is, like a BOOLEAN function
    "REGEXP_LIKE": f"{HELPER}.regexpLike", "REGEXP_SUBSTR": f"{HELPER}.regexpSubstr",
    "REGEXP_REPLACE": f"{HELPER}.regexpReplace", "REGEXP_INSTR": f"{HELPER}.regexpInstr",
    "REGEXP_COUNT": f"{HELPER}.regexpCount",
}

# SQL functions PL/SQL does not have: a PL/SQL expression calling one does not compile (PLS-00204, measured on 26ai).
# Refused with that reason rather than as an unknown name
SQL_ONLY = {"DECODE", "DUMP"}

# `EXTRACT(field FROM x)`: the fields of a DATE / TIMESTAMP [WITH TIME ZONE] the helper reads (#140)
EXTRACT_FIELDS = {"YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND", "TIMEZONE_HOUR", "TIMEZONE_MINUTE"}

# the predefined inquiry directives (`$$PLSQL_CODE_TYPE`, ...) whose value is a setting of the source database the
# migration does not see. `$$PLSQL_UNIT` comes from the scope, `$$PLSQL_LINE` from the preprocessor (#140)
PREDEFINED_INQUIRY = {"plsql_code_type", "plsql_debug", "plsql_optimize_level", "plsql_warnings",
                      "plscope_settings", "nls_length_semantics", "plsql_unit_owner", "plsql_unit_type",
                      "permit_92_wrap_format", "plsql_ccflags", "plsql_line", "plsql_unit"}

# Values, not calls. SYSDATE is the database clock, which is not the JVM clock -- the helper takes it from the
# caller so that a generated routine is testable and the difference stays visible.
VALUES = {"SYSDATE": f"{HELPER}.sysdate()"}

# Values the caller supplies instead (#1, #8). `USER` is the database session's user, which the target has
# nothing equivalent to, and `SYSTIMESTAMP` is a clock the comparison harness cannot pin on the Oracle side
# (`semantics.json`: fixedDatePinsSystimestamp is false), so a column written from it was masked and never
# compared. Both become one argument the caller passes, which is what makes them fixable and comparable.
#
# SYSDATE is deliberately not here: `ALTER SYSTEM SET FIXED_DATE` does pin it, so it is already comparable,
# and moving it would put an argument on many routines that buys nothing. Oracle reads the two separately
# too, so a routine using both never had one instant to begin with.
AUDIT = {"USER": "audit.user()", "SYSTIMESTAMP": "audit.now()"}
KEYWORDS = {"AND", "OR", "NOT", "NULL", "IS", "TRUE", "FALSE", "MOD", "BETWEEN", "IN", "LIKE"}
# collection methods (`v.FIRST`, `v.NEXT(k)`, `v.EXTEND`): the generated List does not have them (#40)
COLLECTION_ATTRIBUTES = {"FIRST", "LAST", "NEXT", "PRIOR", "EXISTS", "DELETE", "EXTEND", "TRIM", "LIMIT", "COUNT"}
# what each becomes on a local collection the scope knows (`name#collection`), see Plsql (#45). LIMIT is not
# here: it is the declared bound, read from the scope (`v#limit`)
COLLECTION_METHODS = {"COUNT": "count", "FIRST": "first", "LAST": "last", "NEXT": "next", "PRIOR": "prior",
                      "EXISTS": "exists", "DELETE": "delete", "EXTEND": "extend", "TRIM": "trimTable"}


def _builtin_function(upper: str):
    from ..builtins import lookup

    builtin = lookup(upper)
    return builtin if builtin is not None and builtin.function else None


@dataclass
class Expression:
    java: str
    imports: set[str] = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)   # names the translator could not place
    # 式が採る sequence。Repository が Sequences を受け取る必要があるかを、生成側が知るため
    sequences: set[str] = field(default_factory=set)
    # 式が呼び出し側から受け取る値（USER / SYSTIMESTAMP）を使うか。使うなら AuditContext が要る（#1・#8）
    audit: bool = False

    @property
    def translatable(self) -> bool:
        return not self.unknown


def translate(text: str | None, names: dict[str, str] | None = None, boolean_value: bool = False,
              condition: bool = False, as_text: bool = False) -> Expression:
    """Render one PL/SQL expression as Java. `names` maps PL/SQL identifiers to the Java ones in scope.

    `boolean_value`: the result lands in a PL/SQL BOOLEAN (an assignment, a RETURN, an initialiser) rather than
    in an IF. There UNKNOWN has to survive as `null` -- `v_ok := a > 1` with a NULL `a` leaves `v_ok` NULL, and a
    later `NOT v_ok` must not fire. The helper's comparisons only say "is TRUE", so the expression is rendered
    twice, once as "is TRUE" and once as "is FALSE", and `Plsql.bool3` puts the three values back together.
    """
    if text is None or not text.strip():
        return Expression("")
    scope = {plsql_identity(k): v for k, v in (names or {}).items()}
    tokens = _tokens(text.strip())
    result = Expression("")
    # `condition`: an IF / ELSIF / WHILE / EXIT WHEN condition. A bare BOOLEAN there is branched on, and a NULL one
    # is not TRUE: `if (done)` unboxed a null and threw (samples/oracle-plsql-docs 4-31, #65)
    rendered, logical = _render(tokens, scope, result, strict=condition)
    if boolean_value and logical:
        is_true, _ = _render(tokens, scope, Expression(""), strict=True)
        is_false, _ = _render(tokens, scope, Expression(""), negate=True, strict=True)
        rendered = f"{HELPER}.bool3({is_true}, {is_false})"
    if as_text and len(tokens) == 1 and tokens[0][0] == "name" and scope.get(f"{tokens[0][1].lower()}#text"):
        # `DBMS_OUTPUT.PUT_LINE(t)`: the argument is written as text, as TO_CHAR(t) would write it (#94)
        rendered = scope[f"{tokens[0][1].lower()}#text"].format(rendered)
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
        if kind == "quoted":
            # a quoted identifier is a name: under the key that says which name it is (#72)
            out.append(("name", plsql_identity(match.group())))
        elif kind not in ("space", "comment"):
            # a comment inside an expression that runs over several lines (`substr(l_str, 1, -- why\n ...)`) is
            # not part of it; its words were read as names
            out.append((kind, match.group()))
        position = match.end()
    return out


def _render(tokens: list[tuple[str, str]], scope: dict[str, str], result: Expression,
            negate: bool = False, strict: bool = False) -> tuple[str, bool]:
    """Recursive descent over PL/SQL's precedence.

    A first attempt rewrote operators by marker substitution on the rendered string; it mis-split
    `a > 1 AND b = 'x'` because a marker cannot tell how far its operands reach. Parsing by precedence is both
    shorter and correct.
    """
    parser = _Parser(tokens, scope, result)
    parser.strict = strict
    rendered = parser.parse_or(negate)
    rest = parser.rest()
    return ((rendered + " " + rest).strip() if rest else rendered), parser.logical


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], scope: dict[str, str], result: Expression) -> None:
        self.tokens = tokens
        self.scope = scope
        self.result = result
        self.position = 0
        self.strict = False    # 素の BOOLEAN も isTrue / isFalse で包む（bool3 の引数にするとき）
        self.logical = False   # 比較か論理演算を通ったか（BOOLEAN の値として出すときに要る）

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
    # PL/SQL の条件は 3 値で、Java の boolean は 2 値である。ヘルパの比較は「TRUE のときだけ true」を返すので、
    # AND / OR はそのまま && / || に落とせる（TRUE になる条件が同じ）。落とせないのは NOT だけ: `!(eq(a, b))` は
    # a が NULL のとき true になるが、Oracle の `NOT (a = b)` は UNKNOWN で、IF はその枝に入らない。
    # だから NOT は `!` にせず、否定を演算子まで押し込む（De Morgan は 3 値でも成り立つ）。`negate` が
    # 立っているあいだ、各段は「元の式が FALSE のときだけ true」になる Java を返す。
    NEGATED = {"eq": "ne", "ne": "eq", "lt": "ge", "ge": "lt", "gt": "le", "le": "gt"}
    GROUP_END = {"AND", "OR", "THEN", "WHEN", "ELSE", "END", "LOOP"}

    def parse_or(self, negate: bool = False) -> str:
        terms = [self.parse_and(negate)]
        while self.at_word("OR"):
            self.take()
            self.logical = True
            terms.append(self.parse_and(negate))
        if len(terms) == 1:
            return terms[0]
        return " && ".join(terms) if negate else " || ".join(terms)

    def parse_and(self, negate: bool = False) -> str:
        terms = [self.parse_not(negate)]
        while self.at_word("AND"):
            self.take()
            self.logical = True
            terms.append(self.parse_not(negate))
        if len(terms) == 1:
            return terms[0]
        # 否定された AND は OR になる。外側の && より弱いので括弧が要る
        return "(" + " || ".join(terms) + ")" if negate else " && ".join(terms)

    def parse_not(self, negate: bool = False) -> str:
        if self.at_word("NOT"):
            self.take()
            self.logical = True
            return self.parse_not(not negate)
        if (negate or self.strict) and self._at_boolean_group():
            self.take()
            inner = self.parse_or(negate)
            if self.peek() is not None and self.peek()[1] == ")":
                self.take()
            return f"({inner})"
        return self.parse_comparison(negate)

    def _at_boolean_group(self) -> bool:
        """`( ... )` がそのまま 1 つの条件か。`NOT (a + b) > c` の括弧は比較の左辺で、条件ではない。"""
        token = self.peek()
        if token is None or token[1] != "(":
            return False
        depth = 0
        for index in range(self.position, len(self.tokens)):
            value = self.tokens[index][1]
            if value == "(":
                depth += 1
            elif value == ")":
                depth -= 1
                if depth == 0:
                    following = self.tokens[index + 1] if index + 1 < len(self.tokens) else None
                    return following is None or following[1] in (")", ",") \
                        or (following[0] == "name" and following[1].upper() in self.GROUP_END)
        return False

    def _padded_operand(self, start: int) -> str | None:
        """What the operand parsed from `start` is, when it is one token: "char" for a CHAR(n) local, "literal"
        for a quoted string. Oracle compares those two kinds blank-padded (#62)."""
        if self.position != start + 1:
            return None
        kind, value = self.tokens[start]
        if kind == "string":
            return "literal"
        if kind == "name" and f"{value.lower()}#blank_padded" in self.scope:
            return "char"
        return None

    def parse_comparison(self, negate: bool = False) -> str:
        start = self.position
        left = self.parse_concat()
        left_kind = self._padded_operand(start)
        if self.at_word("IS"):
            self.take()
            self.logical = True
            negated = self.at_word("NOT")
            if negated:
                self.take()
            if self.at_word("NULL"):
                self.take()
                self.result.imports.add(HELPER_IMPORT)
                # IS NULL は 2 値なので、否定はそのまま反対の述語になる
                return f"{HELPER}.{'isNotNull' if negated != negate else 'isNull'}({left})"
            return f"{left} is{'Not' if negated else ''}"
        negated = False
        if self.at_word("NOT"):
            # `x NOT IN (...)` / `x NOT LIKE ...`: the NOT belongs to the operator, not to a fresh operand
            self.take()
            negated = True
        if self.at_word("IN"):
            self.take()
            return self._predicate(negated != negate, "in", "notIn", f"{left}, {', '.join(self._arguments())}")
        if self.at_word("BETWEEN"):
            self.take()
            low = self.parse_concat()
            if self.at_word("AND"):
                self.take()
            high = self.parse_concat()
            return self._predicate(negated != negate, "between", "notBetween", f"{left}, {low}, {high}")
        if self.at_word("LIKE"):
            self.take()
            pattern = self.parse_concat()
            if self.at_word("ESCAPE"):
                # the escape character: NULL makes it UNKNOWN, more than one character is ORA-06502 (#140)
                self.take()
                return self._predicate(negated != negate, "like", "notLike", f"{left}, {pattern}, {self.parse_concat()}")
            return self._predicate(negated != negate, "like", "notLike", f"{left}, {pattern}")
        if negated:
            self.result.unknown.append("NOT")
            return f"!({left})"
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] in COMPARISONS:
            operator = self.take()[1]
            start = self.position
            right = self.parse_concat()
            right_kind = self._padded_operand(start)
            self.logical = True
            self.result.imports.add(HELPER_IMPORT)
            method = COMPARISONS[operator]
            if "char" in (left_kind, right_kind) and left_kind and right_kind:
                # a CHAR(n) local against a literal or another CHAR: Oracle pads the shorter one with blanks, which
                # is the same as ignoring trailing blanks on both (`first_name = 'John'` with 'John      ')
                left, right = f"{HELPER}.unpad({left})", f"{HELPER}.unpad({right})"
            return f"{HELPER}.{self.NEGATED[method] if negate else method}({left}, {right})"
        if negate:
            # BOOLEAN の変数や関数の値。NULL のとき `NOT x` は UNKNOWN なので、FALSE のときだけ true にする
            self.result.imports.add(HELPER_IMPORT)
            return f"{HELPER}.isFalse({left})"
        if self.strict and not self._primitive(start, left):
            # bool3 の引数は boolean。BOOLEAN の変数をそのまま渡すと、NULL のとき unboxing で落ちる
            self.result.imports.add(HELPER_IMPORT)
            return f"{HELPER}.isTrue({left})"
        return left

    def _primitive(self, start: int, rendered: str) -> bool:
        """An operand that is already a Java `boolean`, never null: a cursor attribute (`c%NOTFOUND` is a flag the
        generator keeps) or a comparison it spelled out (`(rowCount == 0)` for SQL%NOTFOUND), or a literal."""
        if rendered in ("true", "false"):
            return True
        if self.position == start + 1 and self.tokens[start][0] == "attribute":
            # except a cursor read at OPEN (#81): its %FOUND / %NOTFOUND are NULL before the first FETCH
            return not rendered.endswith((".found()", ".notFound()"))
        return rendered.startswith("(") and rendered.endswith(")") and any(op in rendered for op in ("==", "!=", " > "))

    def _predicate(self, negated: bool, plain: str, opposite: str, arguments: str) -> str:
        """`NOT IN` / `NOT BETWEEN` / `NOT LIKE` は、NULL が絡むと TRUE にならない。`!` では表せない。"""
        self.logical = True
        self.result.imports.add(HELPER_IMPORT)
        return f"{HELPER}.{opposite if negated else plain}({arguments})"

    def _arguments(self) -> list[str]:
        """The parenthesised list of an IN."""
        if self.peek() is None or self.peek()[1] != "(":
            return [self.parse_concat()]
        self.take()
        out: list[str] = []
        strict, self.strict = self.strict, False   # 候補は値であって、条件の背骨ではない
        while self.peek() is not None and self.peek()[1] != ")":
            out.append(self.parse_or())
            if self.peek() is not None and self.peek()[1] == ",":
                self.take()
        if self.peek() is not None and self.peek()[1] == ")":
            self.take()
        self.strict = strict
        return out

    ARITHMETIC = {"+": "add", "-": "sub", "*": "mul", "/": "div", "MOD": "mod"}
    ADDITIVE = ("+", "-")
    MULTIPLICATIVE = ("*", "/")

    def parse_arithmetic(self) -> str:
        """`a + b` on a BigDecimal does not compile in Java, and on a boxed null it throws.

        Two levels, because `a + b * c` is `a + (b * c)`. A single left-to-right loop over all four operators
        read it as `(a + b) * c` -- and the result compiled, ran, and wrote the wrong number.
        """
        return self._binary(self.ADDITIVE, self.parse_term)

    def parse_term(self) -> str:
        return self._binary(self.MULTIPLICATIVE, self.parse_unary)

    def _int32(self, start: int) -> str | None:
        """What the operand parsed from `start` is, when it is a 32-bit integer of PL/SQL: one PLS_INTEGER local
        (`p1`, marked `#pls_integer` in scope), one SIMPLE_INTEGER local (`#simple_integer`), or an integer literal
        in the 32-bit range, which Oracle computes with them in 32 bits (`v + 1 - 1` is ORA-01426 at 2147483647,
        #148 M4). None for anything else."""
        spans = getattr(self, "_int32_spans", {})
        if self.position != start + 1:
            # `s * 2 + 1`: the operand is itself 32-bit arithmetic, parsed by `_binary` (in parentheses or not)
            found = spans.get((start, self.position))
            if found is None and self.tokens[start][1] == "(" and self.tokens[self.position - 1][1] == ")":
                found = spans.get((start + 1, self.position - 1))
            return found
        kind, value = self.tokens[start]
        if kind == "name" and f"{value.lower()}#pls_integer" in self.scope:
            return "pls"
        if kind == "name" and f"{value.lower()}#simple_integer" in self.scope:
            return "simple"
        if re.fullmatch(r"\d+", str(value)) and int(value) <= 2147483647:
            return "literal"
        return None

    @staticmethod
    def _int32_result(left: str | None, right: str | None, operator: str) -> str | None:
        """The 32-bit arithmetic `left operator right` is done in, or None for NUMBER arithmetic. SIMPLE_INTEGER
        with SIMPLE_INTEGER (or an integer literal) wraps in two's complement; with a PLS_INTEGER, or PLS_INTEGER
        with either, it raises ORA-01426 past the range. Division yields a NUMBER. Two integer literals are PLS_INTEGER
        arithmetic too: `2147483647 + 1` and `65536 * 65536` are ORA-01426 (Oracle 26ai, #160); a negated literal is
        a PLS_INTEGER expression, not a literal (`s + (-1)` raises where `s - 1` wraps)."""
        if operator == "/" or left is None or right is None:
            return None
        if {left, right} <= {"simple", "literal"} and "simple" in (left, right):
            return "simple"
        return "pls"

    def _binary(self, operators: tuple[str, ...], operand) -> str:
        start = begin = self.position
        left = operand()
        left_int = self._int32(start)
        while True:
            token = self.peek()
            # `n MOD j`: PL/SQL's infix MOD is a word at the level of * and / (4-29, #137). Left to the name path it
            # became `nPlsql.modj`, which javac refused
            infix_mod = operators is self.MULTIPLICATIVE and self.at_word("MOD")
            if not infix_mod and (token is None or token[0] != "op" or token[1] not in operators):
                if left_int in ("pls", "simple") and self.position > begin + 1:
                    if not hasattr(self, "_int32_spans"):
                        self._int32_spans = {}
                    self._int32_spans[(begin, self.position)] = left_int
                return left
            operator = self.take()[1].upper()
            if operator == "*" and self.peek() is not None and self.peek()[1] == "*":
                # `**` は parse_unary が読む。ここに来るのは読めなかったときだけ
                self.take()
                self.result.unknown.append("**")
            start = self.position
            right = operand()
            right_int = self._int32(start)
            self.result.imports.add(HELPER_IMPORT)
            left = f"{HELPER}.{self.ARITHMETIC[operator]}({left}, {right})"
            left_int = self._int32_result(left_int, right_int, operator)
            if left_int == "pls":
                # PLS_INTEGER op PLS_INTEGER is computed in 32 bits: past the range it is ORA-01426 even when the
                # result goes into a NUMBER (samples/oracle-plsql-docs 3-4, #60), and so is each step of
                # `v + 1 - 1` -- the value that comes back into range raised first (#148 M4)
                left = f"{HELPER}.plsInteger({left})"
            elif left_int == "simple":
                # SIMPLE_INTEGER wraps instead of raising: 2147483647 + 1 is -2147483648 (Oracle 26ai, #148 M4).
                # BigDecimal.intValue keeps the low 32 bits, which is that wrap
                left = f"Integer.valueOf(((Number) {left}).intValue())"

    def parse_unary(self) -> str:
        """`-x` and `+x`. Without this the leading sign was read as a binary operator with nothing on its
        left, and the output was `Plsql.sub(, x)` -- Java that does not compile. It had been that way for
        every `-x`, and only stayed hidden because no corpus statement reached the generator with one (#14
        rewrote `-v_qtys(i)` into `-r.qty`, which did)."""
        token = self.peek()
        if token is None or token[0] != "op" or token[1] not in ("-", "+"):
            base = self.parse_primary()
            # `x ** n` binds tighter than * and / and than a sign (`-2 ** 2` is -(2 ** 2)): #84
            while (self.peek() is not None and self.peek()[1] == "*" and self.position + 1 < len(self.tokens)
                   and self.tokens[self.position + 1][1] == "*"):
                self.take()
                self.take()
                exponent = self.parse_unary()
                self.result.imports.add(HELPER_IMPORT)
                base = f"{HELPER}.power({base}, {exponent})"
            return base
        sign = self.position
        operator = self.take()[1]
        start = self.position
        operand = self.parse_unary()
        kind = self._int32(start)
        if operator == "+":
            if kind in ("pls", "simple"):
                self._int32_span(sign, kind)
            return operand   # Oracle の単項プラスは値を変えない
        self.result.imports.add(HELPER_IMPORT)
        negated = f"{HELPER}.neg({operand})"
        # the sign of a 32-bit integer is 32-bit arithmetic too (Oracle 26ai, #160): `-p` of -2147483648 is
        # ORA-01426, `-s` of a SIMPLE_INTEGER wraps back to -2147483648, and `-1` is a PLS_INTEGER expression
        if kind == "simple":
            self._int32_span(sign, "simple")
            return f"Integer.valueOf(((Number) {negated}).intValue())"
        if kind == "pls":
            self._int32_span(sign, "pls")
            return f"{HELPER}.plsInteger({negated})"
        if kind == "literal":
            self._int32_span(sign, "pls")   # a literal's negation cannot leave the range
        return negated

    def _int32_span(self, start: int, kind: str) -> None:
        """Remember that the tokens from `start` to here are 32-bit arithmetic of `kind` (see `_int32`)."""
        if not hasattr(self, "_int32_spans"):
            self._int32_spans = {}
        self._int32_spans[(start, self.position)] = kind

    def _as_text(self, start: int, rendered: str) -> str:
        """The operand parsed from `start`, as the text Oracle writes for it when it is one local whose Java type does
        not say which: a BINARY_DOUBLE is a Double like a REAL local or a NUMBER column read as one (4.0E+000, #91),
        a TIMESTAMP a LocalDateTime like a DATE (26-SEP-26 09.30.00.500000 AM, #94). The declaration says, and the
        service leaves the rendering under `name#text`."""
        if self.position != start + 1:
            return rendered
        kind, value = self.tokens[start]
        template = self.scope.get(f"{value.lower()}#text") if kind == "name" else None
        return template.format(rendered) if template else rendered

    def parse_concat(self) -> str:
        start = self.position
        parts = [self.parse_arithmetic()]
        texts = [self._as_text(start, parts[0])]
        while self.peek() is not None and self.peek()[1] == "||":
            self.take()
            start = self.position
            parts.append(self.parse_arithmetic())
            texts.append(self._as_text(start, parts[-1]))
        if len(parts) == 1:
            return parts[0]
        self.result.imports.add(HELPER_IMPORT)
        return f"{HELPER}.concat({', '.join(texts)})"

    def parse_case(self) -> str:
        """`CASE x WHEN a THEN b ... ELSE c END` as a chain of ternaries.

        A CASE expression is not a statement, so the statement lowering never sees it; without this every routine
        holding one is refused, which is how `tier_discount` -- a three-line pure function -- failed to generate.
        """
        self.take()  # CASE
        strict, self.strict = self.strict, False   # CASE の中は、それ自身の条件と値である
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
        self.strict = strict
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
                                                             "LIKE", "WHEN", "THEN", "ELSE", "END",
                                                             # `x LIKE p ESCAPE c` (#140)
                                                             "ESCAPE",
                                                             # `CAST(x AS DATE)` の区切り。PL/SQL の式に
                                                             # `AS` が現れるのはここだけである
                                                             "AS")):
                break
            if kind == "name" and value.upper() == "MOD" and out:
                break   # `n MOD j`: the infix operator, which _binary reads (#137); `MOD(n, j)` starts an operand
            if kind == "op" and value == ")":
                break
            if kind == "name" and value.upper() == "CASE":
                out.append(self.parse_case())
                continue
            if kind == "op" and value == "(":
                self.take()
                strict, self.strict = self.strict, False   # ここから先は値の括弧で、条件の背骨ではない
                inner = self.parse_or()
                self.strict = strict
                if self.peek() is not None and self.peek()[1] == ")":
                    self.take()
                out.append(f"({inner})")
                continue
            if kind == "name" and self.position + 1 < len(self.tokens) \
                    and self.tokens[self.position + 1][1] == "(":
                special = {"CAST": self._cast, "EXTRACT": self._extract}.get(value.upper())
                out.append(special() if special is not None and value.lower() not in self.scope else self._call())
                continue
            if kind == "attribute" and self.position + 1 < len(self.tokens) \
                    and self.tokens[self.position + 1][1] == "(":
                # `SQL%BULK_ROWCOUNT(i)`: an attribute that is a collection, read by element (#51)
                attribute = " ".join(value.split()).replace(" ", "").lower()
                holder = self.scope.get(attribute + "#collection")
                if holder is not None:
                    self.take()
                    self.take()   # (
                    element = self.parse_or()
                    if self.peek() is not None and self.peek()[1] == ")":
                        self.take()
                    self.result.imports.add(HELPER_IMPORT)
                    # the scope may name its own reader: SQL%BULK_ROWCOUNT(k) of an index FORALL did not run (#136)
                    reader = self.scope.get(attribute + "#read", f"{HELPER}.at")
                    out.append(f"{reader}({holder}, {element})")
                    continue
            self.take()
            out.append(self._atom(kind, value))
        return "".join(out).strip()

    # `CAST(x AS <type>)`。呼べる型は Oracle の挙動を実測で確かめたものだけにする（#13）
    CASTS = {"DATE": "castDate"}

    def _cast(self) -> str:
        """`CAST(v_shipped AS DATE)`。引数リストではないので、関数呼び出しの道には乗らない。

        `AS` も型名も、式の中では名前として読まれてしまい「置けない名前」になっていた。ここで
        読み切る。**実測で挙動を確かめた型だけ**を通す——`AS DATE` は秒未満を切り捨てる
        （Oracle 23ai で `.999999` を渡して確認した。四捨五入ではない）。それ以外の型は、
        何をするのか確かめていないので今までどおり拒む。
        """
        start = self.position
        self.take()   # CAST
        self.take()   # (
        value = self.parse_or()
        if not self.at_word("AS"):
            self.position = start
            return self._call()
        self.take()   # AS
        target = self.peek()
        mapped = self.CASTS.get(str(target[1]).upper()) if target is not None else None
        if mapped is None:
            self.position = start
            return self._call()   # 確かめていない型。名前として拒まれる
        self.take()
        if self.peek() is not None and self.peek()[1] == ")":
            self.take()
        self.result.imports.add(HELPER_IMPORT)
        return f"{HELPER}.{mapped}({value})"

    def _extract(self) -> str:
        """`EXTRACT(YEAR FROM d)` (#140): the field is a keyword and `FROM` a separator, so it is not an argument list.

        An INTERVAL is refused: the difference of two datetimes is a number of days in the generated code where
        Oracle has an INTERVAL DAY TO SECOND, and reading its fields out of the number would be wrong -- in Oracle
        a DATE difference is a NUMBER, and EXTRACT of one does not compile."""
        start = self.position
        self.take()   # EXTRACT
        self.take()   # (
        field = self.take()[1].upper() if self.peek() is not None else ""
        if not self.at_word("FROM") or field not in EXTRACT_FIELDS:
            self.position = start
            self.result.unknown.append(f"EXTRACT({field} FROM ...): この field は読まない")
            return self._skip_call()
        self.take()   # FROM
        value = self.parse_or()
        if self.peek() is not None and self.peek()[1] == ")":
            self.take()
        if value.lstrip("(").startswith(f"{HELPER}.sub(") or re.search(r"\bINTERVAL\b", value, re.IGNORECASE):
            self.result.unknown.append("EXTRACT(... FROM INTERVAL): INTERVAL 型（日時の差）はまだ模していない")
        self.result.imports.add(HELPER_IMPORT)
        return f'{HELPER}.extract("{field}", {value})'

    def _skip_call(self) -> str:
        """Past a call that was refused: the name and its balanced parentheses."""
        name = self.take()[1]
        depth = 0
        while self.peek() is not None:
            value = self.take()[1]
            if value == "(":
                depth += 1
            elif value == ")":
                depth -= 1
                if depth == 0:
                    break
        return name

    def _call(self) -> str:
        """A call's arguments are values, not the spine of a condition: `IF f(p_id) = 0` must pass `p_id`, not
        `Plsql.isTrue(p_id)` (#65 turned strict on for conditions; corpus pkg_shipment showed the leak)."""
        strict, self.strict = self.strict, False
        head = (self.peek()[1] if self.peek() is not None else "").lower()
        try:
            rendered = self._call_body()
            element = self.scope.get(f"{head}#element")
            # `recs(i).last_name`: a field of an element that is a record (12-22, 12-23, #67). The element is an
            # Object from Plsql.at; the collection's element class says which record it is
            while (element and element not in ("Object", "BigDecimal", "String", "Integer", "Long", "Double", "Float")
                   and self.peek() is not None and self.peek() == ("other", ".")
                   and self.position + 1 < len(self.tokens) and self.tokens[self.position + 1][0] == "name"):
                self.take()   # .
                field = self.take()[1]
                rendered = f"(({element}) {rendered}).{java_name(field)}()"
                element = None   # a field of the field would need its type: not modelled
            # `get_sum_multiples(m, sn)(n)`: an element of the collection the call returns (5-2, #93)
            while (self.peek() is not None and self.peek()[1] == "(" and self.position > 0
                   and self.tokens[self.position - 1][1] == ")"):
                self.take()   # (
                index = self.parse_or()
                if self.peek() is not None and self.peek()[1] == ")":
                    self.take()
                self.result.imports.add(HELPER_IMPORT)
                rendered = f"{HELPER}.at({rendered}, {index})"
            return rendered
        finally:
            self.strict = strict

    def _call_body(self) -> str:
        """A function call: the name, then each argument parsed as a full expression.

        まず**丸ごと名前として**引く。`p_ids(i)` はコレクションの要素で、生成コードは文が走る前から
        値として持っている（`pIds.get(i)`）。関数呼び出しとして描画すると、存在しない method を
        呼ぶ Java になる。dotted な `r.order_id` を丸ごと引いているのと同じ理由である。
        """
        whole = self._subscript()
        if whole is not None:
            return whole
        plsql_name = self.peek()[1]
        if plsql_name.upper() == "UPDATING":
            return self._event_of_column()
        if plsql_name.upper() == "SQLERRM":
            # `SQLERRM(n)`: the message of an error number, not the handler's own SQLERRM called (#76, 11-13)
            self.take()
            self.take()   # (
            code = self.parse_or()
            if self.peek() is not None and self.peek()[1] == ")":
                self.take()
            self.result.imports.add(HELPER_IMPORT)
            current = re.fullmatch(r"Plsql\.sqlerrm\((\w+)\.code\(\), \1\.getMessage\(\)\)", self.scope.get("sqlerrm") or "")
            if current:
                # inside a handler, the number of the error being handled gives its own message: SQLERRM(-20000)
                # after RAISE_APPLICATION_ERROR(-20000, 'Account past due.') is that text (11-13)
                caught = current.group(1)
                return f"{HELPER}.sqlerrmOf({code}, {caught}.code(), {caught}.getMessage())"
            return f"{HELPER}.sqlerrmOf({code})"
        if (plsql_name.upper() == "TO_CHAR" and self.position + 3 < len(self.tokens)
                and self.tokens[self.position + 1][1] == "(" and self.tokens[self.position + 3][1] == ")"
                and f"{self.tokens[self.position + 2][1].lower()}#text" in self.scope):
            # TO_CHAR(d) of a BINARY_DOUBLE (#91) or TIMESTAMP (#94) local, which its Java type cannot tell apart
            self.take()
            self.take()   # (
            start = self.position
            value = self._as_text(start, self.parse_or())
            self.take()   # )
            self.result.imports.add(HELPER_IMPORT)
            return value
        head, _, tail = plsql_name.partition(".")
        collection = self.scope.get(f"{head.lower()}#collection")
        constructor = self.scope.get(f"{plsql_name.lower()}#constructor")
        record = self.scope.get(f"{plsql_name.lower()}#record")
        if record and not collection and not constructor:
            # `emp_grade_t(a, b, 'X')`: a schema object type's constructor builds its record (#54)
            self.take()
            self.take()   # (
            arguments: list[str] = []
            while self.peek() is not None and self.peek()[1] != ")":
                arguments.append(self.parse_or())
                if self.peek() is not None and self.peek()[1] == ",":
                    self.take()
            if self.peek() is not None and self.peek()[1] == ")":
                self.take()
            fields = (self.scope.get(f"{plsql_name.lower()}#fields") or "").split(",")
            if len(fields) != len(arguments):
                self.result.unknown.append(f"{plsql_name}: {len(arguments)} arguments for {len(fields)} attributes")
                return plsql_name
            for i, java in enumerate(fields):
                if java == "BigDecimal" and arguments[i] != "null" and not arguments[i].startswith(f"{HELPER}.dec("):
                    self.result.imports.add(HELPER_IMPORT)
                    arguments[i] = f"{HELPER}.dec({arguments[i]})"
                elif java == "String" and arguments[i] != "null" and not arguments[i].startswith('"'):
                    self.result.imports.add(HELPER_IMPORT)
                    arguments[i] = f"{HELPER}.text({arguments[i]})"
            if self.scope.get(f"{plsql_name.lower()}#import"):
                self.result.imports.add(self.scope[f"{plsql_name.lower()}#import"])
            return f"new {record}({', '.join(arguments)})"
        if collection or constructor:
            self.take()   # the name: rendered below as a helper call, not through _name
            name = None
        else:
            name = self._name(self.take()[1])
        expected = (self.scope.get(f"{plsql_name.lower()}#parameters") or "").split(",")
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
        arguments = [a for a in arguments if a]
        if constructor:
            # `t_names('a', 'b')`: a nested table constructor (#45). An associative array has none. The
            # elements take the element type: `t_num(60, 80, 99)` into a List<BigDecimal> needs BigDecimals
            self.result.imports.add(HELPER_IMPORT)
            if self.scope.get(f"{plsql_name.lower()}#element") == "BigDecimal":
                arguments = [a if a == "null" or a.startswith(f"{HELPER}.dec(") else f"{HELPER}.dec({a})" for a in arguments]
            bound = self.scope.get(f"{plsql_name.lower()}#varray")
            if bound:
                # a VARRAY's constructor: the List keeps the bound, so EXTEND past it raises ORA-06532 (#160)
                return f"{HELPER}.varray({', '.join([bound] + arguments)})"
            return f"{HELPER}.table({', '.join(arguments)})"
        if collection:
            self.result.imports.add(HELPER_IMPORT)
            java = self.scope[head.lower()]
            if not tail:
                element = f"{HELPER}.at({java}, {', '.join(arguments)})"   # `v(i)`: an element
                # `nva(2)(3)`: an element of an element (#76, 5-11)
                while self.peek() is not None and self.peek()[1] == "(":
                    self.take()
                    index = self.parse_or()
                    if self.peek() is not None and self.peek()[1] == ")":
                        self.take()
                    element = f"{HELPER}.at({element}, {index})"
                return element
            method = COLLECTION_METHODS.get(tail.upper())
            if method is None:
                self.result.unknown.append(plsql_name)
                return plsql_name
            return f"{HELPER}.{method}({', '.join([java] + arguments)})"    # `v.NEXT(k)`, `v.EXISTS(k)` ...
        if expected != [""] and len(expected) == len(arguments) and not any("=>" in a for a in arguments):
            # a sibling that declares NUMBER takes BigDecimal; the argument may be a Long / Integer local or a literal
            for i, java in enumerate(expected):
                if i >= len(arguments) or arguments[i] == "null":
                    continue
                if java == "BigDecimal" and not arguments[i].startswith(f"{HELPER}.dec("):
                    self.result.imports.add(HELPER_IMPORT)
                    arguments[i] = f"{HELPER}.dec({arguments[i]})"
                elif java in ("Integer", "Double", "Float") and not re.fullmatch(r"-?\d+", arguments[i]) \
                        and (arguments[i].startswith(HELPER) or re.fullmatch(r"-?\d+\.\d+", arguments[i])):
                    # `test(0.66)` / `fibonacci(n - 2)`: helper arithmetic is Object or BigDecimal, and an
                    # Integer parameter rounds a NUMBER the way Plsql.toInt does (#75, 8-11 / 8-36)
                    self.result.imports.add(HELPER_IMPORT)
                    helper = {"Integer": "toInt", "Double": "toDouble", "Float": "toFloat"}[java]
                    arguments[i] = f"{HELPER}.{helper}({arguments[i]})"
        arguments.extend(self._extras(plsql_name))
        return f"{name}({', '.join(arguments)})"

    def _extras(self, plsql_name: str) -> list[str]:
        """What a routine of another module takes after the PL/SQL arguments (#48): the caller's `audit` (#1, #8)."""
        extra = self.scope.get(f"{plsql_name.lower()}#extra")
        if not extra:
            return []
        if extra == "audit":
            self.result.imports.add(AUDIT_IMPORT)
            self.result.audit = True
            return [extra]
        # a lifted local subprogram (#80): the enclosing routine's variables it reads, by their PL/SQL names
        return [self.scope.get(n.lower(), n) for n in extra.split(",")]

    def _subscript(self) -> str | None:
        """`name(index)` が丸ごと scope にあればそれを返し、トークンを読み進める。"""
        tokens = self.tokens[self.position:self.position + 4]
        if len(tokens) < 4 or tokens[1][1] != "(" or tokens[3][1] != ")":
            return None
        if tokens[0][0] != "name" or tokens[2][0] != "name":
            return None
        key = f"{tokens[0][1]}({tokens[2][1]})".lower()
        mapped = self.scope.get(key)
        if mapped is None:
            return None
        self.position += 4
        return mapped

    def _atom(self, kind: str, value: str) -> str:
        if kind == "inquiry":
            return self._inquiry(value)
        if kind == "bind":
            # `:NEW.col` / `:OLD.col` in a trigger, or a host variable. 以前はどちらも Java に相当する
            # ものが無いとして拒んでいた。#12 が trigger の行について答えを決めた——**呼び出し側が
            # 渡す**——ので、渡されたものは名前として解決する。渡されていないもの（host variable や、
            # DDL が無くて型を作れなかった列）は今までどおり拒む。
            mapped = self.scope.get(value.lower())
            if mapped is None:
                self.result.unknown.append(value)
                return value
            return mapped
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
            if value.isdigit() and int(value) > 2147483647:
                # a whole number past 32 bits is not a Java int literal: `i := 5000000000` did not compile (#100)
                return f'new java.math.BigDecimal("{value}")'
            return value
        if kind == "op":
            return value if value in "()," else f" {value} "
        if kind == "name":
            return self._name(value)
        return value

    def _inquiry(self, value: str) -> str:
        """`$$PLSQL_UNIT` and a PLSQL_CCFLAGS flag (`$$logger_debug`) in an expression (#140). A flag is what
        limits.yaml `conditionalCompilation.flags` says the source database compiled with, NULL when it says
        nothing -- the same assumption the `$IF` the preprocessor resolved made. `$$PLSQL_LINE` was written as the
        number by the preprocessor. The other predefined ones are settings this does not see."""
        from ..conditional import settings

        name = value[2:].lower()
        if name == "plsql_unit" and "$$plsql_unit" in self.scope:
            return self.scope["$$plsql_unit"]
        flags = settings().flags
        if name == "plsql_ccflags" and not flags:
            return "(String) null"   # no flag declared: PLSQL_CCFLAGS is empty, which is NULL
        if name in PREDEFINED_INQUIRY:
            self.result.unknown.append(f"{value}: 移行元のコンパイル設定で、この解析からは見えない")
            return value
        flag = flags.get(name)
        if flag is None:
            return "null"
        if isinstance(flag, bool):
            return "true" if flag else "false"
        if isinstance(flag, (int, float)):
            number = int(flag) if float(flag).is_integer() else flag
            return f'new java.math.BigDecimal("{number}")'
        self.result.unknown.append(f"{value}: {flag!r} は BOOLEAN / PLS_INTEGER / NULL ではない")
        return value

    def _event_of_column(self) -> str:
        """`UPDATING('SALARY')`: whether this UPDATE sets salary is known where the trigger is called, so the
        writer passes it as the BOOLEAN argument `UPDATING_SALARY` (`plsql.triggers.event_of_column`)."""
        self.take()                                                     # UPDATING
        if self.peek() is not None and self.peek()[1] == "(":
            self.take()
        literal = self.take()[1] if self.peek() is not None else ""
        if self.peek() is not None and self.peek()[1] == ")":
            self.take()
        key = f"updating_{literal.strip(chr(39)).lower()}"
        if key in self.scope:
            return self.scope[key]
        self.result.unknown.append(f"UPDATING({literal})")
        return f"UPDATING({literal})"

    def _name(self, value: str) -> str:
        upper = value.upper()
        following = self.peek()[1] if self.peek() is not None else ""
        for prefix in ("SYS.STANDARD.", "STANDARD."):
            # `sys.standard.bitand(p_x, p_y)`: the built-in itself, named past a package function that hides it
            if upper.startswith(prefix) and upper[len(prefix):] in FUNCTIONS and value.lower() not in self.scope:
                value, upper = value[len(prefix):], upper[len(prefix):]
                if following == "(":
                    self.result.imports.add(HELPER_IMPORT)
                    return FUNCTIONS[upper]
        if upper in SQL_ONLY and following == "(" and value.lower() not in self.scope:
            self.result.unknown.append(f"{value}: PL/SQL の式では使えない SQL 関数（PLS-00204）")
            return value
        if upper in ("NULL", "TRUE", "FALSE"):
            return upper.lower()
        if upper in VALUES:
            self.result.imports.add(HELPER_IMPORT)
            return VALUES[upper]
        if upper in AUDIT:
            # the caller says who and when (#1, #8); the generator does not reach for an ambient value
            self.result.imports.add(AUDIT_IMPORT)
            self.result.audit = True
            return AUDIT[upper]
        refused = self.scope.get(f"{value.lower()}#refused")
        if refused:
            # a routine the scope knows but an expression cannot call (OUT arguments); say why (#48)
            self.result.unknown.append(f"{value}: {refused}")
            return value
        builtin = _builtin_function(upper)
        if builtin is not None and value.lower() not in self.scope:
            # an Oracle-supplied function the generator knows (plsql/builtins.py, #55). Written without
            # parentheses (`DBMS_UTILITY.GET_TIME`) it is a call all the same
            self.result.imports.add(HELPER_IMPORT)
            return builtin.java if following == "(" else f"{builtin.java}()"
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
            if self.scope.get(f"{value.lower()}#parameters") == "" and following != "(":
                # a function that takes nothing, written without parentheses (`'…' || pkg.count`): Java needs them
                return f"{self.scope[value.lower()]}({', '.join(self._extras(value))})"
            return self.scope[value.lower()]
        if "." in value:
            head, _, tail = value.partition(".")
            if tail.upper() == "NEXTVAL":
                # 採番。移行先の方式は DDL から導いてあり、呼ぶ口は Sequences（計画 §9）
                self.result.imports.add(SEQUENCES_IMPORT)
                self.result.sequences.add(head.lower())
                return f'sequences.next("{head.lower()}")'
            if tail.upper() == "LIMIT" and self.scope.get(f"{head.lower()}#limit"):
                return self.scope[f"{head.lower()}#limit"]   # a VARRAY's declared bound (#45)
            if tail.upper() == "LIMIT" and self.scope.get(f"{head.lower()}#collection"):
                # an associative array or a nested table has no bound: LIMIT is NULL (5-28, #82)
                return "(java.math.BigDecimal) null"
            if self.scope.get(f"{head.lower()}#collection") and tail.upper() in COLLECTION_METHODS \
                    and tail.upper() not in ("NEXT", "PRIOR", "EXISTS"):
                # `v.COUNT`, `v.FIRST`, `v.LAST`, `v.DELETE`, `v.EXTEND`, `v.TRIM` on a local collection (#45)
                self.result.imports.add(HELPER_IMPORT)
                return f"{HELPER}.{COLLECTION_METHODS[tail.upper()]}({self.scope[head.lower()]})"
            fields = (self.scope.get(f"{head.lower()}#fields_of") or "").split(",")
            if head.lower() not in self.scope or (tail.upper() in COLLECTION_ATTRIBUTES and tail.lower() not in fields):
                # (`name1.first` of a record whose field is called `first` is the field: 5-45, #82)
                # `v_ids.COUNT` on a collection the scope does not know as one, or a package-qualified name:
                # neither is a record field, and rendering it as one produces a call to a method that does
                # not exist (#40)
                self.result.unknown.append(value)
                return value
            # `friend.name.first`: a field of a nested record, one accessor per step (5-35, #82)
            return self.scope[head.lower()] + "".join(f".{java_name(step)}()" for step in tail.split("."))
        self.result.unknown.append(value)
        return java_name(value)


def _string(literal: str) -> str:
    body = literal[1:-1].replace("''", "'")
    escaped = body.replace("\\", "\\\\").replace('"', '\\"')
    # a PL/SQL literal may run over several lines; a Java one may not, and the raw newline did not compile
    escaped = escaped.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'
