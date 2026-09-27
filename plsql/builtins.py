"""Oracle-supplied packages the generator knows (Issue #55): their signatures, and what the target does instead.

A call into a package the program does not contain has no signature to read, so named arguments cannot be put in
order (`DBMS_APPLICATION_INFO.SET_MODULE(module_name => 'X', action_name => 'Y')`), and nothing says whether the
call writes, commits or sends anything. For the few packages sample code and real code reach for most, both are
knowable from Oracle's documentation, and are written down here -- once, for the analysis, the statement
generator and the expression translator alike.

Each entry says what the target does in its place, and why that keeps the routine's meaning:

* ``noop``    nothing. For calls that only change what the session shows about itself (``V$SESSION.MODULE``)
* ``helper``  a `Plsql` method in runtime-java that does the same thing in the JVM (sleep, a random number, a
              clock in hundredths of a second)

What is not in the table is still refused, with the reason it always had (CALL-001, "external call"). Add an
entry only when the target's behaviour is the same thing, not a guess at it: `DBMS_SESSION.SET_IDENTIFIER`, for
instance, feeds `SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')`, which the target has no equivalent of, so it is
not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NAMED = re.compile(r"^\s*([A-Za-z][\w$#]*)\s*=>\s*(.+)$", re.DOTALL)


@dataclass(frozen=True)
class Builtin:
    name: str                              # PACKAGE.MEMBER
    parameters: tuple[str, ...]            # Oracle's parameter names, in order
    java: str | None                       # the helper, `Plsql.sleep`; None for a no-op
    note: str                              # what the target does instead, and why that is the same thing
    function: bool = False                 # used in an expression (returns a value) rather than as a statement
    arities: tuple[int, ...] = ()          # the argument counts Oracle accepts; () means exactly len(parameters)
    defaults: dict[str, str] = field(default_factory=dict)   # a parameter Oracle lets you leave out, and its value

    def accepts(self, count: int) -> bool:
        if self.arities:
            return count in self.arities
        required = len([p for p in self.parameters if p not in self.defaults])
        return required <= count <= len(self.parameters)


_NOOP_SESSION = ("V$SESSION に出る値を変えるだけで、データにもトランザクションにも触れない。移行先に相当するものが無いので、"
                 "何もしない（監視で使っていたなら、アプリのログや tracing に置き換える）")

_NOOP_STATS = ("Oracle のオプティマイザ統計を集めるだけで、データにもトランザクションにも触れない。ScalarDB に"
               "オプティマイザ統計は無いので、何もしない（バックエンドの統計は運用で取る）")

BUILTINS: dict[str, Builtin] = {b.name: b for b in [
    Builtin("DBMS_APPLICATION_INFO.SET_MODULE", ("module_name", "action_name"), None, _NOOP_SESSION),
    Builtin("DBMS_APPLICATION_INFO.SET_ACTION", ("action_name",), None, _NOOP_SESSION),
    Builtin("DBMS_APPLICATION_INFO.SET_CLIENT_INFO", ("client_info",), None, _NOOP_SESSION),
    Builtin("DBMS_STATS.GATHER_SCHEMA_STATS",
            ("ownname", "estimate_percent", "block_sample", "method_opt", "degree", "granularity", "cascade",
             "stattab", "statid", "options", "statown", "no_invalidate", "gather_temp", "gather_fixed",
             "stattype", "force", "obj_filter_list"), None, _NOOP_STATS,
            defaults={p: "NULL" for p in ("estimate_percent", "block_sample", "method_opt", "degree", "granularity",
                                           "cascade", "stattab", "statid", "options", "statown", "no_invalidate",
                                           "gather_temp", "gather_fixed", "stattype", "force", "obj_filter_list")}),
    Builtin("DBMS_STATS.GATHER_TABLE_STATS",
            ("ownname", "tabname", "partname", "estimate_percent", "block_sample", "method_opt", "degree",
             "granularity", "cascade", "stattab", "statid", "statown", "no_invalidate", "stattype", "force"),
            None, _NOOP_STATS,
            defaults={p: "NULL" for p in ("partname", "estimate_percent", "block_sample", "method_opt", "degree",
                                           "granularity", "cascade", "stattab", "statid", "statown", "no_invalidate",
                                           "stattype", "force")}),
    Builtin("DBMS_SESSION.SLEEP", ("seconds",), "Plsql.sleep",
            "指定した秒数だけ待つ（小数可）。トランザクションは開いたまま待つのも Oracle と同じ"),
    Builtin("DBMS_LOCK.SLEEP", ("seconds",), "Plsql.sleep",
            "DBMS_SESSION.SLEEP の旧名。指定した秒数だけ待つ"),
    Builtin("DBMS_OUTPUT.PUT_LINE", ("item",), "Plsql.putLine", "出力バッファ（スレッドごと）に 1 行足す"),
    Builtin("DBMS_OUTPUT.PUT", ("item",), "Plsql.put", "出力バッファの行に足す"),
    Builtin("DBMS_OUTPUT.NEW_LINE", (), "Plsql.newLine", "出力バッファの行を閉じる"),
    Builtin("DBMS_RANDOM.VALUE", ("low", "high"), "Plsql.randomValue",
            "0 以上 1 未満、または low 以上 high 未満の乱数（NUMBER）。Oracle と同じ値にはならない", function=True,
            arities=(0, 2)),
    Builtin("DBMS_RANDOM.STRING", ("opt", "len"), "Plsql.randomString",
            "opt（'U' / 'L' / 'A' / 'X' / 'P'）の文字で len 文字の乱数文字列。Oracle と同じ値にはならない",
            function=True),
    Builtin("DBMS_UTILITY.GET_TIME", (), "Plsql.getTime",
            "経過時間の目盛り（1/100 秒）。差を取るためだけの値で、起点は Oracle と違う", function=True),
    # #132: DBMS_LOB の読むだけの関数。生成コードは CLOB を String、BLOB を byte[] で持つ（gen_java/types.py）ので、
    # LOB の位置指定子ではなく値そのものに同じことをする。副作用は無い（データにもトランザクションにも触れない）。
    # CREATETEMPORARY / APPEND など引数を書き換えるもの、BFILE やファイルを読むものは載せない（EXT-001 のまま）
    Builtin("DBMS_LOB.GETLENGTH", ("lob_loc",), "Plsql.lobGetLength",
            "長さ（CLOB は文字数、BLOB はバイト数）。NULL なら NULL", function=True),
    Builtin("DBMS_LOB.SUBSTR", ("lob_loc", "amount", "offset"), "Plsql.lobSubstr",
            "offset 文字（BLOB はバイト）目から amount 個。amount の既定は 32767、offset の既定は 1。どれかが NULL、"
            "amount が 1〜32767 の外、offset が 1 未満か長さを超えるなら NULL（Oracle 26ai で実測）", function=True,
            arities=(1, 2, 3)),
    Builtin("DBMS_LOB.INSTR", ("lob_loc", "pattern", "offset", "nth"), "Plsql.lobInstr",
            "offset 文字目から探して nth 番目に現れる位置。無ければ 0。どれかが NULL か空、offset か nth が 1 未満なら "
            "NULL（SQL の INSTR と違い、負の offset で後ろから探すことはしない。Oracle 26ai で実測）", function=True,
            arities=(2, 3, 4)),
]}


def lookup(name: str | None) -> Builtin | None:
    key = re.sub(r"\s+", "", name or "").upper()
    return BUILTINS.get(key) or _standard(key)


# `SYS.STANDARD.BITAND(x, y)` / `STANDARD.TO_CHAR(x)`: a SQL function named through the package that defines it,
# which code does when a function of its own hides the built-in (oos_util_bit.bitand). It is the built-in the
# expression translator reads (plsql/gen_java/expr.py FUNCTIONS), not code nobody analysed (#140)
_STANDARD = re.compile(r"^(?:SYS\.)?STANDARD\.([A-Z][\w$#]*)$")


def _standard(key: str) -> Builtin | None:
    match = _STANDARD.match(key)
    if match is None:
        return None
    from .gen_java.expr import FUNCTIONS

    java = FUNCTIONS.get(match.group(1))
    if java is None:
        return None
    return Builtin(key, (), java, f"{match.group(1)} itself, named through STANDARD", function=True, arities=tuple(range(10)))


def external_packages(text: str, pattern: "re.Pattern[str]") -> list[str]:
    """The external packages (`pattern`, lower.EXTERNAL_PACKAGES) a routine's text calls into, leaving out the
    members this table knows: `DBMS_LOB.GETLENGTH(c)` is a function of a value, not a call into a package that
    writes, commits or sends anything (#132). A mention with no known member (`DBMS_LOB.APPEND`, a constant such
    as `DBMS_LOB.LOBMAXSIZE`) still counts."""
    found: set[str] = set()
    for match in pattern.finditer(text):
        member = re.match(r"\s*\.\s*([A-Za-z][\w$#]*)", text[match.end():])
        if member and lookup(f"{match.group(0)}.{member.group(1)}") is not None:
            continue
        found.add(match.group(0).upper())
    return sorted(found)


class BuiltinArgumentError(ValueError):
    pass


def ordered(builtin: Builtin, arguments: list[str]) -> list[str]:
    """The arguments in the builtin's parameter order, named ones placed by name and defaults filled in."""
    positional = [a for a in arguments if not NAMED.match(a)]
    named = {}
    for a in arguments:
        match = NAMED.match(a)
        if match:
            named[match.group(1).lower()] = match.group(2).strip()
    if any(NAMED.match(a) for a in arguments[:len(positional)]) or \
            any(not NAMED.match(a) for a in arguments[len(positional):]):
        raise BuiltinArgumentError(f"{builtin.name}: 位置で渡す引数は名前付きの前に置く")
    unknown = set(named) - {p.lower() for p in builtin.parameters}
    if unknown:
        raise BuiltinArgumentError(f"{builtin.name} に {', '.join(sorted(unknown))} という引数は無い")
    out = list(positional)
    for parameter in builtin.parameters[len(positional):]:
        if parameter.lower() in named:
            out.append(named.pop(parameter.lower()))
        elif parameter in builtin.defaults:
            out.append(builtin.defaults[parameter])
        else:
            break
    if named:
        raise BuiltinArgumentError(f"{builtin.name}: {', '.join(named)} の前の引数が渡されていない")
    if not builtin.accepts(len(out)):
        raise BuiltinArgumentError(f"{builtin.name} は引数 {len(out)} 個では呼べない")
    return out
