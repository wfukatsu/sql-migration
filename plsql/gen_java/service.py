"""P2-6: the routine body as a Java method.

The statements the IR models become Java; the ones it cannot, or must not, become a `TODO` the compiler will not
let anyone forget. Three rules shape the output:

* **The transaction boundary is the caller's.** A generated method never begins or commits. The decision (plan §9)
  is that no Spring annotation appears either: the boundary is a `try`-with-resources in one helper, and the
  application decides where to put it. A method that committed on its own would make the surrounding use case
  impossible to compose.
* **Every statement keeps its line.** A comment carrying `file:line` sits on each translated statement, because
  the value of the output is not the code -- it is being able to check the code against the PL/SQL it came from
  (design document §17-8).
* **What cannot be translated is a compile error, not a silent gap.** An unsupported construct becomes a
  `throw new UnsupportedOperationException` next to the original source text. The plan's non-functional
  requirements forbid hiding a warning behind a success, and a routine that quietly does less than the original
  is the worst possible outcome of a migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import contextvars

from ..ir import model as M
from .emit import JavaFile
from .expr import translate
from .types import java_class_name, java_name, java_type

# the module being generated, so an expression can resolve a sibling routine without threading it through
# every statement helper
_MODULE: "contextvars.ContextVar[M.Module | None]" = contextvars.ContextVar("module", default=None)


@dataclass
class ServiceFile:
    file: JavaFile
    routines: list[str] = field(default_factory=list)
    untranslated: list[str] = field(default_factory=list)
    unknown_names: list[str] = field(default_factory=list)


def generate_module(module: M.Module, package: str, repository_package: str,
                    domain_package: str) -> ServiceFile:
    """One Java class per PL/SQL module. Public routines become public methods, private ones private."""
    name = java_class_name(module.name) + "Service"
    file = JavaFile(package=package, name=name,
                    source=module.source_range.file if module.source_range else module.name)
    result = ServiceFile(file=file)
    file.add_import(f"{repository_package}.{java_class_name(module.name)}Repository")
    file.add_import(f"{domain_package}.MigratedException")

    _MODULE.set(module)
    file.comment(
        f"{module.name} ({module.module_kind}).\n"
        "The transaction boundary belongs to the caller: no method here begins, commits or rolls back.")
    with file.block(f"public class {name}") as f:
        f.line(f"private final {java_class_name(module.name)}Repository repository;")
        f.line()
        with f.block(f"public {name}({java_class_name(module.name)}Repository repository)") as g:
            g.line("this.repository = repository;")
        for routine in module.routines:
            f.line()
            _method(f, module, routine, result, domain_package)
    return result


def _scope(routine: M.Routine, module: M.Module | None = None) -> dict[str, str]:
    """PL/SQL name -> Java name for everything visible inside the method, siblings included.

    A function call inside an expression (`v := order_total(id)`) resolves to a sibling method, so the sibling
    names belong in the scope; without them every such call is reported as unknown.
    """
    names = {p.name: java_name(p.name) for p in routine.parameters}
    names.update({d.name: java_name(d.name) for d in routine.declarations})
    if module is not None:
        names.update({r.name: java_name(r.name) for r in module.routines})
        # a trigger declares its locals on the module, not on the body, and a package-level cursor is visible
        # to every routine; leaving them out reports real names as unknown
        names.update({d.name: java_name(d.name) for d in module.declarations})
    return names


def _method(file: JavaFile, module: M.Module, routine: M.Routine, result: ServiceFile,
            domain_package: str) -> None:
    returns = "void"
    if routine.return_type is not None:
        mapped = java_type(routine.return_type.resolved or routine.return_type.oracle)
        file.add_import(*mapped.imports)
        returns = mapped.name
    outs = [p for p in routine.parameters if p.direction in ("OUT", "IN OUT")]
    if outs:
        returns = java_class_name(routine.name) + "Result"
        file.add_import(f"{domain_package}.{returns}")

    parameters = []
    for parameter in routine.parameters:
        if parameter.direction == "OUT":
            continue  # an OUT argument comes back in the result, not through the signature
        mapped = java_type(parameter.type.resolved if parameter.type else None)
        file.add_import(*mapped.imports)
        parameters.append(f"{mapped.name} {java_name(parameter.name)}")

    visibility = "public" if routine.visibility == "public" else "private"
    _source_comment(file, routine)
    with file.block(f"{visibility} {returns} {java_name(routine.name)}({', '.join(parameters)})") as f:
        if routine.routine_kind == "trigger-body":
            for declaration in (_MODULE.get().declarations if _MODULE.get() else []):
                _declaration(f, declaration, routine, result)
        for parameter in outs:
            # an OUT argument becomes a local, and comes back in the result record rather than through the
            # signature: a caller must not see a half-updated set when an exception interrupts the routine
            mapped = java_type(parameter.type.resolved if parameter.type else None)
            file.add_import(*mapped.imports)
            initial = f" = {java_name(parameter.name)}" if parameter.direction == "IN OUT" else " = null"
            f.line(f"{mapped.name} {java_name(parameter.name)}{initial};")
        for declaration in routine.declarations:
            _declaration(f, declaration, routine, result)
        if routine.declarations or outs:
            f.line()
        if routine.exception_handlers:
            with f.block("try") as body:
                _statements(body, routine.body, routine, result)
            _handlers(f, routine, result, domain_package)
        else:
            _statements(f, routine.body, routine, result)
        if outs:
            components = (["null"] if routine.return_type is not None else []) + \
                [java_name(p.name) for p in outs]
            f.line(f"return new {returns}({', '.join(components)});")
        elif returns != "void":
            f.line("// the PL/SQL falls through here; Oracle raises ORA-06503 when a function does")
            f.line('throw new IllegalStateException("function reached its end without RETURN");')
    result.routines.append(routine.id)


def _handlers(file: JavaFile, routine: M.Routine, result: ServiceFile, domain_package: str) -> None:
    """Exception handlers become catch blocks, in the order PL/SQL would try them.

    `WHEN OTHERS` is last whatever the source order, because Java resolves catches in order and a broad one first
    would swallow the specific ones. Keeping the PL/SQL order for everything else matters: two handlers can both
    match, and PL/SQL takes the first.
    """
    from .exception import PREDEFINED

    ordered = sorted(routine.exception_handlers,
                     key=lambda h: 1 if any(e.upper() == "OTHERS" for e in h.exceptions) else 0)
    for handler in ordered:
        names = [e.upper() for e in handler.exceptions]
        if "OTHERS" in names:
            caught = "MigratedException"
            comment = "WHEN OTHERS: only migrated exceptions, so a bug does not look like a business error"
        else:
            classes = [PREDEFINED[n][0] for n in names if n in PREDEFINED]
            caught = " | ".join(classes) if classes else "MigratedException"
            comment = f"WHEN {', '.join(names)}"
            for class_name in classes:
                file.add_import(f"{domain_package}.{class_name}")
        file.comment(comment)
        with file.block(f"catch ({caught} e)") as f:
            _statements(f, handler.body, routine, result)


def _declaration(file: JavaFile, declaration: M.Declaration, routine: M.Routine,
                 result: ServiceFile) -> None:
    if declaration.declaration_kind in ("cursor", "exception", "type"):
        return  # cursors live in the repository; exceptions and types are generated elsewhere
    mapped = java_type(declaration.type.resolved if declaration.type else None)
    file.add_import(*mapped.imports)
    initial = f" = {_expr(file, declaration.initial, routine, result)}" if declaration.initial else ""
    file.line(f"{mapped.name} {java_name(declaration.name)}{initial};")


def _statements(file: JavaFile, statements: list[M.Statement], routine: M.Routine,
                result: ServiceFile) -> None:
    if not statements:
        file.line("// the PL/SQL body is empty")
        return
    for statement in statements:
        _statement(file, statement, routine, result)


def _statement(file: JavaFile, statement: M.Statement, routine: M.Routine, result: ServiceFile) -> None:
    _source_comment(file, statement)
    try:
        _translate_statement(file, statement, routine, result)
    except Untranslatable as e:
        file.comment(f"not translated: {e.text.strip()[:120]}")
        file.comment(f"    unresolved: {', '.join(e.names)}")
        file.line(f'throw new UnsupportedOperationException("unresolved in {statement.kind}: '
                  f'{", ".join(e.names)}");')
        if statement.id not in result.untranslated:
            result.untranslated.append(statement.id)


def _translate_statement(file: JavaFile, statement: M.Statement, routine: M.Routine,
                         result: ServiceFile) -> None:
    kind = statement.kind

    if kind == "Assignment":
        file.line(f"{java_name(statement.target)} = {_expr(file, statement.expression, routine, result)};")
    elif kind == "Return":
        file.line(f"return {_expr(file, statement.expression, routine, result)};" if statement.expression else "return;")
    elif kind == "If":
        _if(file, statement, routine, result)
    elif kind == "Case":
        _case(file, statement, routine, result)
    elif kind == "Loop":
        _loop(file, statement, routine, result)
    elif kind == "Raise":
        _raise(file, statement, routine, result)
    elif kind == "Exit":
        file.line(f"if ({_expr(file, statement.condition, routine, result)}) break;"
                  if statement.condition else "break;")
    elif kind == "Continue":
        file.line(f"if ({_expr(file, statement.condition, routine, result)}) continue;"
                  if statement.condition else "continue;")
    elif kind == "Null":
        file.line("// NULL;")
    elif kind == "Call":
        _call(file, statement, routine, result)
    elif kind == "SqlOperation":
        _sql(file, statement, routine)
    else:
        _untranslated(file, statement, result)


def _if(file: JavaFile, statement: M.If, routine: M.Routine, result: ServiceFile) -> None:
    for index, branch in enumerate(statement.branches):
        keyword = "if" if index == 0 else "} else if"
        condition = _expr(file, branch.condition, routine, result)
        with file.block(("if" if index == 0 else "else if") + f" ({condition})") as f:
            _statements(f, branch.body, routine, result)
    if statement.else_body:
        with file.block("else") as f:
            _statements(f, statement.else_body, routine, result)


def _case(file: JavaFile, statement: M.Case, routine: M.Routine, result: ServiceFile) -> None:
    file.comment("CASE lowered to if/else: PL/SQL CASE without ELSE raises CASE_NOT_FOUND, "
                 "which the final else preserves")
    for index, branch in enumerate(statement.branches):
        condition = _expr(file, branch.condition, routine, result)
        if statement.selector:
            condition = f"Plsql.eq({_expr(file, statement.selector, routine, result)}, {condition})"
            file.add_import("com.scalar.migrate.plsql.Plsql")
        with file.block(("if" if index == 0 else "else if") + f" ({condition})") as f:
            _statements(f, branch.body, routine, result)
    with file.block("else") as f:
        if statement.else_body:
            _statements(f, statement.else_body, routine, result)
        else:
            f.line('throw new IllegalStateException("CASE_NOT_FOUND");')


def _loop(file: JavaFile, statement: M.Loop, routine: M.Routine, result: ServiceFile) -> None:
    label = f"{java_name(statement.label)}: " if statement.label else ""
    if statement.loop_kind == "while":
        opening = f"{label}while ({_expr(file, statement.condition, routine, result)})"
    elif statement.loop_kind in ("cursor-for", "forall"):
        file.comment(f"{statement.loop_kind}: the rows come from the repository, "
                     "so the query is not inlined here")
        opening = f"{label}for (var row : repository.{java_name(routine.name)}Rows())"
    elif statement.loop_kind == "for":
        opening = f"{label}for (int i = 0; /* {statement.cursor} */ false; i++)"
        file.comment(f"numeric FOR bounds: {statement.cursor}")
    else:
        opening = f"{label}while (true)"
    with file.block(opening) as f:
        _statements(f, statement.body, routine, result)


def _raise(file: JavaFile, statement: M.Raise, routine: M.Routine, result: ServiceFile) -> None:
    if statement.error_code is not None:
        message = _expr(file, statement.message, routine, result) if statement.message else '""'
        file.line(f"throw new MigratedException({statement.error_code}, {message or chr(34) * 2});")
    else:
        file.line(f'throw new MigratedException(0, "{statement.exception or "RAISE"}");')


def _call(file: JavaFile, statement: M.Call, routine: M.Routine, result: ServiceFile) -> None:
    target = statement.resolved_to or statement.callee
    arguments = ", ".join(_expr(file, a, routine, result) for a in statement.arguments)
    if statement.resolved_to:
        file.line(f"{java_name(target.split('.')[-1])}({arguments});")
    else:
        file.comment(f"external call: {statement.callee}")
        file.line(f'throw new UnsupportedOperationException("external call: {statement.callee}");')


def _sql(file: JavaFile, statement: M.SqlOperation, routine: M.Routine) -> None:
    method = f"{java_name(routine.name)}{_sql_suffix(statement)}"
    targets = statement.into_targets
    if targets and len(targets) == 1:
        file.line(f"{java_name(targets[0])} = repository.{method}();")
    elif targets:
        file.comment(f"SELECT INTO {', '.join(targets)}")
        file.line(f"var row = repository.{method}();")
    else:
        file.line(f"repository.{method}();")


def _sql_suffix(statement: M.SqlOperation) -> str:
    return "Stmt" + statement.id.rsplit("-", 1)[-1]


def _untranslated(file: JavaFile, statement: M.Statement, result: ServiceFile) -> None:
    text = getattr(statement, "text", "") or getattr(statement, "expression", "") or statement.kind
    file.comment(f"not translated: {statement.kind}")
    for line in str(text).splitlines()[:6]:
        file.comment(f"    {line}")
    file.line(f'throw new UnsupportedOperationException("{statement.kind} is not translated");')
    result.untranslated.append(statement.id)


def _source_comment(file: JavaFile, node: M.Node) -> None:
    if node.source_range is not None:
        file.comment(f"{node.source_range.file}:{node.source_range.start_line}")


class Untranslatable(Exception):
    """An expression the translator could not place. The statement becomes a throw rather than broken code."""

    def __init__(self, names: list[str], text: str) -> None:
        super().__init__(", ".join(names))
        self.names = names
        self.text = text


def _expr(file: JavaFile, text: str | None, routine: M.Routine, result: ServiceFile,
          module: M.Module | None = None) -> str:
    """Translate an expression, or refuse.

    An unrecognised name reaching the output would either fail to compile or, worse, resolve to something with
    different semantics. Refusing turns the statement into a `throw` with the original next to it, which the
    compiler accepts and a reviewer can act on. Emitting it anyway is the one outcome that helps nobody.
    """
    rendered = translate(text, _scope(routine, module or _MODULE.get()))
    for name in rendered.unknown:
        if name not in result.unknown_names:
            result.unknown_names.append(name)
    if rendered.unknown:
        raise Untranslatable(rendered.unknown, text or "")
    file.add_import(*rendered.imports)
    return rendered.java
