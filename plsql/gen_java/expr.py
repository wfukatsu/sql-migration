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
AUDIT_IMPORT = "com.scalar.migrate.plsql.AuditContext"

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
    "MOD": f"{HELPER}.mod", "ABS": f"{HELPER}.abs", "UPPER": f"{HELPER}.upper",
    "INITCAP": f"{HELPER}.initcap", "TRIM": f"{HELPER}.trim",
    # text that is not a number raises Plsql.ValueError, which a VALUE_ERROR handler catches (as ORA-06502 does)
    "TO_NUMBER": f"{HELPER}.toNumber",
}
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
# here: the VARRAY bound is not kept, so it stays unknown
COLLECTION_METHODS = {"COUNT": "count", "FIRST": "first", "LAST": "last", "NEXT": "next", "PRIOR": "prior",
                      "EXISTS": "exists", "DELETE": "delete", "EXTEND": "extend", "TRIM": "trimTable"}


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


def translate(text: str | None, names: dict[str, str] | None = None, boolean_value: bool = False) -> Expression:
    """Render one PL/SQL expression as Java. `names` maps PL/SQL identifiers to the Java ones in scope.

    `boolean_value`: the result lands in a PL/SQL BOOLEAN (an assignment, a RETURN, an initialiser) rather than
    in an IF. There UNKNOWN has to survive as `null` -- `v_ok := a > 1` with a NULL `a` leaves `v_ok` NULL, and a
    later `NOT v_ok` must not fire. The helper's comparisons only say "is TRUE", so the expression is rendered
    twice, once as "is TRUE" and once as "is FALSE", and `Plsql.bool3` puts the three values back together.
    """
    if text is None or not text.strip():
        return Expression("")
    scope = {k.lower(): v for k, v in (names or {}).items()}
    tokens = _tokens(text.strip())
    result = Expression("")
    rendered, logical = _render(tokens, scope, result)
    if boolean_value and logical:
        is_true, _ = _render(tokens, scope, Expression(""), strict=True)
        is_false, _ = _render(tokens, scope, Expression(""), negate=True, strict=True)
        rendered = f"{HELPER}.bool3({is_true}, {is_false})"
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

    def parse_comparison(self, negate: bool = False) -> str:
        left = self.parse_concat()
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
            return self._predicate(negated != negate, "like", "notLike", f"{left}, {self.parse_concat()}")
        if negated:
            self.result.unknown.append("NOT")
            return f"!({left})"
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] in COMPARISONS:
            operator = self.take()[1]
            right = self.parse_concat()
            self.logical = True
            self.result.imports.add(HELPER_IMPORT)
            method = COMPARISONS[operator]
            return f"{HELPER}.{self.NEGATED[method] if negate else method}({left}, {right})"
        if negate:
            # BOOLEAN の変数や関数の値。NULL のとき `NOT x` は UNKNOWN なので、FALSE のときだけ true にする
            self.result.imports.add(HELPER_IMPORT)
            return f"{HELPER}.isFalse({left})"
        if self.strict:
            # bool3 の引数は boolean。BOOLEAN の変数をそのまま渡すと、NULL のとき unboxing で落ちる
            self.result.imports.add(HELPER_IMPORT)
            return f"{HELPER}.isTrue({left})"
        return left

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

    ARITHMETIC = {"+": "add", "-": "sub", "*": "mul", "/": "div"}
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

    def _binary(self, operators: tuple[str, ...], operand) -> str:
        left = operand()
        while True:
            token = self.peek()
            if token is None or token[0] != "op" or token[1] not in operators:
                return left
            operator = self.take()[1]
            if operator == "*" and self.peek() is not None and self.peek()[1] == "*":
                # `**` はべき乗。ヘルパに無いので、掛け算 2 つとして読まずに拒む
                self.take()
                self.result.unknown.append("**")
            right = operand()
            self.result.imports.add(HELPER_IMPORT)
            left = f"{HELPER}.{self.ARITHMETIC[operator]}({left}, {right})"

    def parse_unary(self) -> str:
        """`-x` and `+x`. Without this the leading sign was read as a binary operator with nothing on its
        left, and the output was `Plsql.sub(, x)` -- Java that does not compile. It had been that way for
        every `-x`, and only stayed hidden because no corpus statement reached the generator with one (#14
        rewrote `-v_qtys(i)` into `-r.qty`, which did)."""
        token = self.peek()
        if token is None or token[0] != "op" or token[1] not in ("-", "+"):
            return self.parse_primary()
        operator = self.take()[1]
        operand = self.parse_unary()
        if operator == "+":
            return operand   # Oracle の単項プラスは値を変えない
        self.result.imports.add(HELPER_IMPORT)
        return f"{HELPER}.neg({operand})"

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
                                                             # `CAST(x AS DATE)` の区切り。PL/SQL の式に
                                                             # `AS` が現れるのはここだけである
                                                             "AS")):
                break
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
                out.append(self._cast() if value.upper() == "CAST" else self._call())
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

    def _call(self) -> str:
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
        head, _, tail = plsql_name.partition(".")
        collection = self.scope.get(f"{head.lower()}#collection")
        constructor = self.scope.get(f"{plsql_name.lower()}#constructor")
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
            return f"{HELPER}.table({', '.join(arguments)})"
        if collection:
            self.result.imports.add(HELPER_IMPORT)
            java = self.scope[head.lower()]
            if not tail:
                return f"{HELPER}.at({java}, {', '.join(arguments)})"       # `v(i)`: an element
            method = COLLECTION_METHODS.get(tail.upper())
            if method is None:
                self.result.unknown.append(plsql_name)
                return plsql_name
            return f"{HELPER}.{method}({', '.join([java] + arguments)})"    # `v.NEXT(k)`, `v.EXISTS(k)` ...
        if expected != [""] and len(expected) == len(arguments) and not any("=>" in a for a in arguments):
            # a sibling that declares NUMBER takes BigDecimal; the argument may be a Long / Integer local or a literal
            for i, java in enumerate(expected):
                if java == "BigDecimal" and arguments[i] != "null" and not arguments[i].startswith(f"{HELPER}.dec("):
                    self.result.imports.add(HELPER_IMPORT)
                    arguments[i] = f"{HELPER}.dec({arguments[i]})"
        return f"{name}({', '.join(arguments)})"

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
            return value
        if kind == "op":
            return value if value in "()," else f" {value} "
        if kind == "name":
            return self._name(value)
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
            if tail.upper() == "LIMIT" and self.scope.get(f"{head.lower()}#limit"):
                return self.scope[f"{head.lower()}#limit"]   # a VARRAY's declared bound (#45)
            if self.scope.get(f"{head.lower()}#collection") and tail.upper() in COLLECTION_METHODS \
                    and tail.upper() not in ("NEXT", "PRIOR", "EXISTS"):
                # `v.COUNT`, `v.FIRST`, `v.LAST`, `v.DELETE`, `v.EXTEND`, `v.TRIM` on a local collection (#45)
                self.result.imports.add(HELPER_IMPORT)
                return f"{HELPER}.{COLLECTION_METHODS[tail.upper()]}({self.scope[head.lower()]})"
            if head.lower() not in self.scope or tail.upper() in COLLECTION_ATTRIBUTES:
                # `v_ids.COUNT` on a collection the scope does not know as one, or a package-qualified name:
                # neither is a record field, and rendering it as one produces a call to a method that does
                # not exist (#40)
                self.result.unknown.append(value)
                return value
            return f"{self.scope[head.lower()]}.{java_name(tail)}()"
        self.result.unknown.append(value)
        return java_name(value)


def _string(literal: str) -> str:
    body = literal[1:-1].replace("''", "'")
    escaped = body.replace("\\", "\\\\").replace('"', '\\"')
    # a PL/SQL literal may run over several lines; a Java one may not, and the raw newline did not compile
    escaped = escaped.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'
