"""P2-5: domain exceptions, and the error-code registry that keeps them honest.

Oracle's `-20000` range is a business contract: callers branch on those numbers, and a migration that renumbers
them breaks code nobody is looking at. So the registry keeps the original code on every generated exception
(design document §6.6) and refuses to hand the same code to two different meanings.

Predefined exceptions get their own types because their firing conditions are part of the behaviour being
preserved: `NO_DATA_FOUND` and `TOO_MANY_ROWS` are what a `SELECT INTO` does, not incidental errors.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from ..ir import model as M
from ..lower import _walk
from .emit import JavaFile
from .types import java_class_name

# predefined exceptions whose firing conditions the generated code has to reproduce
PREDEFINED = {
    "NO_DATA_FOUND": ("NoDataFoundException", 100,
                      "SELECT INTO matched no row"),
    "TOO_MANY_ROWS": ("TooManyRowsException", -1422,
                      "SELECT INTO matched more than one row"),
    "DUP_VAL_ON_INDEX": ("DuplicateValueException", -1,
                         "a unique constraint was violated"),
    "INVALID_NUMBER": ("InvalidNumberException", -1722,
                       "a string could not be converted to a number"),
    "ZERO_DIVIDE": ("ZeroDivideException", -1476, "division by zero"),
    "VALUE_ERROR": ("ValueErrorException", -6502, "a conversion or size error"),
    # the runtime raises these from collections and cursors (`Plsql.at`, `Plsql.Cursor`), and the generated code
    # from a CASE statement without ELSE; service._guarded turns them into these classes (#99)
    "SUBSCRIPT_BEYOND_COUNT": ("SubscriptBeyondCountException", -6533, "a subscript past the collection's count"),
    "SUBSCRIPT_OUTSIDE_LIMIT": ("SubscriptOutsideLimitException", -6532, "a subscript outside the legal range"),
    "COLLECTION_IS_NULL": ("CollectionIsNullException", -6531, "a collection that was never initialised"),
    "INVALID_CURSOR": ("InvalidCursorException", -1001, "a cursor that is not open"),
    "CURSOR_ALREADY_OPEN": ("CursorAlreadyOpenException", -6511, "OPEN of a cursor that is already open"),
    "CASE_NOT_FOUND": ("CaseNotFoundException", -6592, "no WHEN of a CASE statement matched and it has no ELSE"),
}


@dataclass
class ErrorCode:
    code: int
    class_name: str
    oracle_name: str
    message: str = ""
    routines: list[str] = field(default_factory=list)
    # the generated code throws this one itself, from `SELECT INTO`. Without saying so, a class with an empty
    # `raisedBy` reads as "nothing raises this", which is the opposite of why it is in the program.
    by_generator: bool = False


class Registry:
    """Every error code the program raises, with the routines that raise it.

    Two different meanings sharing one code is a conflict the migration has to resolve, not something the
    generator may quietly pick a winner for.
    """

    def __init__(self) -> None:
        self.codes: dict[int, ErrorCode] = {}
        self.conflicts: list[tuple[int, str, str]] = []

    def add(self, code: int, class_name: str, oracle_name: str, message: str,
            routine: str | None) -> ErrorCode:
        """`routine` is None for a class the generator itself raises: it is in the program because the
        generated code can throw it, not because a routine named it."""
        existing = self.codes.get(code)
        if existing is None:
            entry = ErrorCode(code=code, class_name=class_name, oracle_name=oracle_name, message=message,
                              by_generator=routine is None)
            if routine is not None:
                entry.routines.append(routine)
            self.codes[code] = entry
            return entry
        if existing.class_name != class_name:
            self.conflicts.append((code, existing.class_name, class_name))
        if routine is None:
            existing.by_generator = True
        elif routine not in existing.routines:
            existing.routines.append(routine)
        return existing

    def to_dict(self) -> dict:
        return {
            "codes": [
                {"code": e.code, "class": e.class_name, "oracle": e.oracle_name,
                 "message": e.message, "raisedBy": e.routines, "raisedByGenerator": e.by_generator}
                for e in sorted(self.codes.values(), key=lambda e: e.code)],
            "conflicts": [{"code": c, "first": a, "second": b} for c, a, b in self.conflicts],
        }


def collect(program: M.Program) -> Registry:
    registry = Registry()
    for module in program.modules:
        for routine in module.routines:
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            for statement in statements:
                if statement.kind != "Raise":
                    continue
                if statement.error_code is not None:
                    registry.add(statement.error_code, _class_for(statement, module, program),
                                 f"RAISE_APPLICATION_ERROR({statement.error_code})",
                                 statement.message or "", routine.id)
                elif statement.exception:
                    name = statement.exception.upper()
                    known = PREDEFINED.get(predefined_name(name, routine, module) or "")
                    if known:
                        registry.add(known[1], known[0], name, known[2], routine.id)
                    else:
                        code, class_name = class_of(name, routine, module, program=program)
                        registry.add(code, class_name, name, "declared in PL/SQL", routine.id)
            handlers = list(routine.exception_handlers) + [
                h for s in statements for h in getattr(s, "exception_handlers", []) or []]
            for handler in handlers:
                for name in handler.exceptions:
                    known = PREDEFINED.get(predefined_name(name, routine, module) or "")
                    if known:
                        registry.add(known[1], known[0], (predefined_name(name, routine, module) or ""), known[2],
                                     routine.id)
                    elif name.upper() != "OTHERS":
                        # a handler catches this class by name, so the class has to exist even when the RAISE is
                        # in another routine (or nowhere: `PRAGMA EXCEPTION_INIT` binds it to an Oracle error)
                        code, class_name = class_of(name, routine, module, program=program)
                        registry.add(code, class_name, name.upper(), "declared in PL/SQL", routine.id)
    return registry


def declared_code(name: str, *holders) -> int | None:
    """The Oracle number `PRAGMA EXCEPTION_INIT(name, -2291)` bound to a declared exception, from the routine's
    or the package's declarations (`lower._bind_exception_codes` puts it in `initial`). None when unbound."""
    for holder in holders:
        for scope in _scopes(holder):
            for declaration in getattr(scope, "declarations", None) or []:
                if declaration.declaration_kind == "exception" and declaration.name.upper() == name.upper() \
                        and declaration.initial and re.fullmatch(r"-?\d+", declaration.initial.strip()):
                    return int(declaration.initial)
    return None


def _scopes(holder) -> list:
    """Where a name declared in `holder` may be: a routine's nested blocks first, then the routine. A block's own
    `e_blk EXCEPTION; PRAGMA EXCEPTION_INIT(e_blk, -20078)` was never looked at, so `WHEN e_blk` caught a class
    that the RAISE did not throw (#101). The blocks are not told apart: two blocks of one routine declaring the
    same name with different numbers take the first."""
    if not isinstance(holder, M.Routine):
        return [holder]
    statements = _walk(holder.body) + [s for h in holder.exception_handlers for s in _walk(h.body)]
    return [s for s in statements if s.kind == "Block" and getattr(s, "declarations", None)] + [holder]


def predefined_name(name: str, *holders) -> str | None:
    """The predefined exception `name` means where it is written, or None.

    `STANDARD.INVALID_NUMBER` / `SYS.STANDARD.INVALID_NUMBER` is the predefined one whatever is declared. A bare
    `INVALID_NUMBER` is the predefined one unless the routine or its package declares an exception of that name,
    which hides it (`invalid_number EXCEPTION;` -- then `WHEN INVALID_NUMBER` does not catch ORA-01722: measured on
    Oracle 26ai 23.26.3, the language reference's 11-9). Both were read as the predefined one, and the qualified
    name as an exception of a package called STANDARD."""
    upper = name.upper()
    for prefix in ("SYS.STANDARD.", "STANDARD."):
        if upper.startswith(prefix) and upper[len(prefix):] in PREDEFINED:
            return upper[len(prefix):]
    if upper in PREDEFINED and not _declares(upper, *holders):
        return upper
    return None


def _declares(name: str, *holders) -> bool:
    return any(declaration.declaration_kind == "exception" and declaration.name.upper() == name
               for holder in holders for scope in _scopes(holder)
               for declaration in getattr(scope, "declarations", None) or [])


def class_of(name: str, *holders, program=None) -> tuple[int, str]:
    """(code, Java class) for a PL/SQL exception name: predefined, PRAGMA-bound, or a pseudo-code of its own.

    A bound exception keeps Oracle's number (#50, 2026-09-25): a guard the generator writes for a FOREIGN KEY
    raises -2291, and `WHEN e_fk_violation` (EXCEPTION_INIT -2291) has to catch it. Before, the class carried a
    pseudo-code and the two never met. A number Oracle already names (-1 is DUP_VAL_ON_INDEX) is that class.
    """
    upper = name.upper()
    predefined = predefined_name(upper, *holders)
    if predefined is not None:
        return PREDEFINED[predefined][1], PREDEFINED[predefined][0]
    if "." in upper and program is not None:
        # `WHEN emp_api.e_invalid_raise`: the package's exception, so the package's class -- the one its own
        # `RAISE e_invalid_raise` throws. Named from the qualified text it was `EmpApiEInvalidRaiseException`,
        # which nothing ever threw (#48, samples/oracle-samples b05_3, 2026-09-25)
        owner, _, bare = upper.partition(".")
        module = next((m for m in program.modules if m.name.upper() == owner), None)
        if module is not None:
            return class_of(bare, module, *module.routines)
    code = declared_code(upper, *holders)
    if code is not None:
        predefined = next((c for c, k, _ in PREDEFINED.values() if k == code), None)
        return code, predefined or user_class(name)
    return _user_code(upper), user_class(name)


def bound_class(code: int, program) -> str | None:
    """The class of a declared exception bound to `code` anywhere in the program, for a RAISE by number."""
    if program is None or any(code == k for _, k, _ in PREDEFINED.values()):
        return None
    for module in program.modules:
        holders = [module] + [scope for routine in module.routines for scope in _scopes(routine)]
        for holder in holders:
            for declaration in getattr(holder, "declarations", None) or []:
                if declaration.declaration_kind == "exception" and declared_code(declaration.name, holder) == code:
                    return user_class(declaration.name)
    return None


def user_class(name: str) -> str:
    """The Java class of a PL/SQL-declared exception. One place, because RAISE and WHEN have to agree on it.
    A declared exception named like a predefined one (`invalid_number EXCEPTION;`) is not that one, so its class
    is not the predefined class either."""
    base = java_class_name(name)
    return ("Declared" if name.upper() in PREDEFINED else "") + base + "Exception"


def _class_for(statement: M.Raise, module: M.Module, program: M.Program | None = None) -> str:
    """One class per business code, named after the module that raises it."""
    predefined = next((c for c, code, _ in PREDEFINED.values() if code == statement.error_code), None)
    bound = bound_class(statement.error_code, program) if statement.error_code is not None else None
    if bound is not None:
        # a number a declared exception is bound to (PRAGMA EXCEPTION_INIT): the RAISE by number throws that
        # class (service._raise), so the registry has to name it the same
        return bound
    if predefined is not None:
        # a code Oracle already names (-6502 is VALUE_ERROR) is that exception, not a business error of the
        # module: a second class for the code would keep the predefined one from being written at all
        return predefined
    code = statement.error_code or 0
    if not -20999 <= code <= -20000:
        # an Oracle error, not a business one (a CHECK guard's -2290, a scalar subquery's -1427): it means the same
        # whichever module raises it, so it is named after the number. Named after the first module that raised it,
        # every other module threw `B043ImplicitCursorAttrsError2290Exception` (samples/oracle-samples, 2026-09-26)
        return f"Ora{abs(code):05d}Exception"
    return f"{java_class_name(module.name)}Error{abs(code)}Exception"


def _user_code(name: str) -> int:
    """A stable, negative pseudo-code for a PL/SQL exception that carries no Oracle number.

    `hash()` is salted per process, so using it here would give the same exception a different code on every run
    and make any golden comparison flake. The digest is what keeps a regenerated registry comparable.
    """
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return -(900000 + int(digest[:4], 16) % 1000)


def base_exception(package: str) -> JavaFile:
    file = JavaFile(package=package, name="MigratedException", source="(generator)")
    file.comment("Base of every exception the migration preserves. `code` is Oracle's, not a new numbering:\n"
                 "callers branch on those numbers, so renumbering them breaks code nobody is looking at.")
    with file.block("public class MigratedException extends RuntimeException") as f:
        f.line("private final int code;")
        f.line()
        with f.block("public MigratedException(int code, String message)") as g:
            g.line("super(message);")
            g.line("this.code = code;")
        f.line()
        with f.block("public int code()") as g:
            g.line("return code;")
    return file


def exception_class(entry: ErrorCode, package: str) -> JavaFile:
    file = JavaFile(package=package, name=entry.class_name, source=entry.oracle_name)
    file.comment(f"{entry.oracle_name}: {entry.message}" if entry.message else entry.oracle_name)
    with file.block(f"public class {entry.class_name} extends MigratedException") as f:
        f.line(f"public static final int CODE = {entry.code};")
        f.line()
        with f.block(f"public {entry.class_name}(String message)") as g:
            g.line("super(CODE, message);")
    return file


# the generated repository raises these two itself, from `SELECT INTO`, whether or not the PL/SQL ever named
# them. Emitting them only when the source mentions them left every repository importing classes that were
# not written -- Java that does not compile, which nothing noticed until the compile check (#21) asked.
# ZERO_DIVIDE: `Plsql.div` raises the runtime's own `Plsql.ZeroDivide`, and a `try` that has a ZERO_DIVIDE or an
# OTHERS handler turns it into this class on the way out (service._guarded). VALUE_ERROR likewise: a declaration
# with a precision or a length raises `Plsql.ValueError` from `Plsql.fit`, and so does text that is not a number
# wherever the helper reads a NUMBER (`Plsql.dec`, `Plsql.toNumber`, the arithmetic).
# The collection, cursor and CASE errors are written when a handler names them or a WHEN OTHERS translates to
# them (`generate`'s `used`, #99).
ALWAYS = ("NO_DATA_FOUND", "TOO_MANY_ROWS", "ZERO_DIVIDE", "VALUE_ERROR")

# Predefined exceptions nothing on the target raises by itself: the generated code has no unique-constraint
# violation to catch (ScalarDB reports a duplicate INSERT through the transaction, which is then unusable), and the
# conversion that Oracle reports as INVALID_NUMBER happens inside a SQL statement, which here is a bind of a value
# the helper has already read (a failure there is a VALUE_ERROR, as it is for a PL/SQL expression in Oracle).
# A handler for one of these runs in Oracle and never here.
NEVER_RAISED_BY_TARGET = ("DUP_VAL_ON_INDEX", "INVALID_NUMBER")

# ... except INVALID_NUMBER where a TO_NUMBER was lifted out of a SQL statement (#167): the repository computes it with
# `Plsql.sqlToNumber`, which raises ORA-01722 as the statement did, and the handler runs again.
def hoists_to_number(program: M.Program | None, routine: M.Routine | None = None) -> bool:
    """Whether a statement carries a TO_NUMBER lifted out of SQL (`sqlbridge`, TO_NUMBER_HOISTED): in `routine` or a
    routine its calls reach (Oracle's error goes up to the caller's handler, and so does the runtime's), or anywhere in
    the program when no routine is given."""
    if program is None:
        return False
    routines = {r.id: r for m in program.modules for r in m.routines}

    def statements(r: M.Routine):
        return _walk(r.body) + [x for h in r.exception_handlers for x in _walk(h.body)]

    if routine is None:
        todo = list(routines.values())
    else:
        todo, seen = [routine], {routine.id}
        for current in todo:
            for statement in statements(current):
                callee = routines.get(getattr(statement, "resolved_to", None) or "")
                if callee is not None and callee.id not in seen:
                    seen.add(callee.id)
                    todo.append(callee)
    return any(d.code == "TO_NUMBER_HOISTED" for r in todo for s in statements(r) for d in s.diagnostics)


# The same, by the Oracle number a `PRAGMA EXCEPTION_INIT` binds (#148 H3), with the name rule EXC-001 knows it by.
# The constraint errors are the database's: ScalarDB has no UNIQUE, NOT NULL, CHECK, FOREIGN KEY or column length,
# so nothing raises them unless a guard the project decided on (`constraints.enforce`) does. -6502 is VALUE_ERROR,
# which EXC-001 names as well. -54 (row lock busy) is left out: that handler is dropped by decision (#9 §B)
NEVER_RAISED_CODES = {
    -1: "DUP_VAL_ON_INDEX", -1722: "INVALID_NUMBER", -6502: "VALUE_ERROR",
    -1400: "ORA-01400", -1407: "ORA-01407", -1438: "ORA-01438", -12899: "ORA-12899",
    -2290: "ORA-02290", -2291: "ORA-02291", -2292: "ORA-02292",
}


def generate(program: M.Program, package: str, used=()) -> tuple[list[JavaFile], Registry]:
    """`used`: predefined exceptions the generated services throw without the PL/SQL naming them."""
    registry = collect(program)
    for name in (*ALWAYS, *sorted(set(used) - set(ALWAYS))):
        class_name, code, message = PREDEFINED[name]
        registry.add(code, class_name, name, message, routine=None)
    files = [base_exception(package)]
    files += [exception_class(entry, package) for entry in
              sorted(registry.codes.values(), key=lambda e: e.code)]
    return files, registry
