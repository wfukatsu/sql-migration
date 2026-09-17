"""P2-5: domain exceptions, and the error-code registry that keeps them honest.

Oracle's `-20000` range is a business contract: callers branch on those numbers, and a migration that renumbers
them breaks code nobody is looking at. So the registry keeps the original code on every generated exception
(design document §6.6) and refuses to hand the same code to two different meanings.

Predefined exceptions get their own types because their firing conditions are part of the behaviour being
preserved: `NO_DATA_FOUND` and `TOO_MANY_ROWS` are what a `SELECT INTO` does, not incidental errors.
"""

from __future__ import annotations

import hashlib
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
}


@dataclass
class ErrorCode:
    code: int
    class_name: str
    oracle_name: str
    message: str = ""
    routines: list[str] = field(default_factory=list)


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
            entry = ErrorCode(code=code, class_name=class_name, oracle_name=oracle_name, message=message)
            if routine is not None:
                entry.routines.append(routine)
            self.codes[code] = entry
            return entry
        if existing.class_name != class_name:
            self.conflicts.append((code, existing.class_name, class_name))
        if routine is not None and routine not in existing.routines:
            existing.routines.append(routine)
        return existing

    def to_dict(self) -> dict:
        return {
            "codes": [
                {"code": e.code, "class": e.class_name, "oracle": e.oracle_name,
                 "message": e.message, "raisedBy": e.routines}
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
                    registry.add(statement.error_code, _class_for(statement, module),
                                 f"RAISE_APPLICATION_ERROR({statement.error_code})",
                                 statement.message or "", routine.id)
                elif statement.exception:
                    name = statement.exception.upper()
                    known = PREDEFINED.get(name)
                    if known:
                        registry.add(known[1], known[0], name, known[2], routine.id)
                    else:
                        registry.add(_user_code(name), java_class_name(name) + "Exception", name,
                                     "declared in PL/SQL", routine.id)
            for handler in routine.exception_handlers:
                for name in handler.exceptions:
                    known = PREDEFINED.get(name.upper())
                    if known:
                        registry.add(known[1], known[0], name.upper(), known[2], routine.id)
    return registry


def _class_for(statement: M.Raise, module: M.Module) -> str:
    """One class per business code, named after the module that raises it."""
    return f"{java_class_name(module.name)}Error{abs(statement.error_code or 0)}Exception"


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
ALWAYS = ("NO_DATA_FOUND", "TOO_MANY_ROWS")


def generate(program: M.Program, package: str) -> tuple[list[JavaFile], Registry]:
    registry = collect(program)
    for name in ALWAYS:
        class_name, code, message = PREDEFINED[name]
        registry.add(code, class_name, name, message, routine=None)
    files = [base_exception(package)]
    files += [exception_class(entry, package) for entry in
              sorted(registry.codes.values(), key=lambda e: e.code)]
    return files, registry
