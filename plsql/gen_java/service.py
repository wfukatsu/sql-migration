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
from ..lower import _walk
from .emit import JavaFile
from .expr import translate
from .types import java_class_name, java_name, java_type, record_columns

# the module being generated, so an expression can resolve a sibling routine without threading it through
# every statement helper
_MODULE: "contextvars.ContextVar[M.Module | None]" = contextvars.ContextVar("module", default=None)
_DOMAIN: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("domain", default=None)
_ROWCOUNT_SEEN: "contextvars.ContextVar[bool]" = contextvars.ContextVar("rowcount", default=False)


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
    _DOMAIN.set(domain_package)
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
    names = {"SQL%ROWCOUNT": "rowCount", "sql%rowcount": "rowCount"}
    names.update({p.name: java_name(p.name) for p in routine.parameters})
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
    _ROWCOUNT_SEEN.set(False)
    _source_comment(file, routine)
    with file.block(f"{visibility} {returns} {java_name(routine.name)}({', '.join(parameters)}) "
                    "throws Exception") as f:
        if routine.routine_kind == "trigger-body":
            for declaration in (_MODULE.get().declarations if _MODULE.get() else []):
                _declaration(f, declaration, routine, result)
        if any((s.sql_kind or "").upper() in ("INSERT", "UPDATE", "DELETE", "MERGE")
               for s in _walk(routine.body) + [x for h in routine.exception_handlers for x in _walk(h.body)]
               if s.kind == "SqlOperation"):
            # one declaration per method: a routine may hold several DML statements, in different blocks
            f.line("int rowCount = 0;")
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
        if outs and not _always_exits(routine, result):
            components = (["null"] if routine.return_type is not None else []) + \
                [java_name(p.name) for p in outs]
            f.line(f"return new {returns}({', '.join(components)});")
        elif returns != "void" and not outs and not _always_exits(routine, result):
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
    caught_already: set[str] = set()
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
        if caught in caught_already:
            # PL/SQL allows a nested block to have its own WHEN OTHERS, and the lowering attaches both to the
            # routine; Java rejects two catches of one type. Which handler applies depends on the block
            # structure, so the second is reported rather than silently merged into the first.
            file.comment(f"{comment}: a second handler for the same type, from a nested block")
            file.comment("    the block structure decides which one applies; this needs a human")
            result.untranslated.append(handler.id)
            continue
        caught_already.add(caught)
        file.comment(comment)
        with file.block(f"catch ({caught} e)") as f:
            _statements(f, handler.body, routine, result)


def _always_throws(statement: M.Statement, result: ServiceFile) -> bool:
    return statement.id in result.untranslated or statement.kind == "Raise"


def _always_exits(routine: M.Routine, result: ServiceFile) -> bool:
    """Does every path out of the generated body already return or throw?

    Java rejects a statement after one, so both the fall-through guard and the result return have to ask. A
    statement the generator refused counts as a throw: that is exactly what it emits.
    """
    def exits(statements: list[M.Statement]) -> bool:
        if not statements:
            return False
        for statement in statements:
            if statement.id in result.untranslated:
                return True   # the refusal throws, and the rest of the block was dropped
        return statements[-1].kind in ("Return", "Raise")

    if not exits(routine.body):
        return False
    if not routine.exception_handlers:
        return True
    return all(exits(h.body) for h in routine.exception_handlers)


def _row_type(declaration: M.Declaration) -> str | None:
    """A local whose type is a record has the record P2-5 generated for it, not `Object`.

    Both shapes land here: a `%ROWTYPE` of a table, and a package-local `TYPE t IS RECORD (...)`. They are the
    same thing -- a named list of typed fields -- and the generator names them apart only so that a table's row
    and a package's record cannot collide.
    """
    if declaration.type is None:
        return None
    if declaration.type.origin == "rowtype":
        return java_class_name(declaration.type.oracle.split("%")[0]) + "Row"
    if declaration.type.origin == "record":
        return java_class_name(declaration.type.oracle.rpartition(".")[2])
    return None


def _declaration(file: JavaFile, declaration: M.Declaration, routine: M.Routine,
                 result: ServiceFile) -> None:
    if declaration.declaration_kind in ("cursor", "exception", "type"):
        return  # cursors live in the repository; exceptions and types are generated elsewhere
    row = _row_type(declaration)
    if row is not None:
        file.add_import(f"{_DOMAIN.get()}.{row}" if _DOMAIN.get() else row)
        file.line(f"{row} {java_name(declaration.name)};")
        return
    mapped = java_type(declaration.type.resolved if declaration.type else None)
    file.add_import(*mapped.imports)
    # PL/SQL initialises a declared variable to NULL; Java leaves it definitely-unassigned, and a handler that
    # reads it then fails to compile. Writing the NULL out keeps the two the same.
    initial = " = null" if mapped.name not in ("int", "long", "double", "boolean") else ""
    if declaration.initial:
        rendered = _expr(file, declaration.initial, routine, result)
        if mapped.name == "BigDecimal" and rendered.lstrip("-").replace(".", "", 1).isdigit():
            file.add_import("com.scalar.migrate.plsql.Plsql")
            rendered = f"Plsql.number({rendered})"
        initial = f" = {rendered}"
    file.line(f"{mapped.name} {java_name(declaration.name)}{initial};")


def _statements(file: JavaFile, statements: list[M.Statement], routine: M.Routine,
                result: ServiceFile) -> None:
    if not statements:
        file.line("// the PL/SQL body is empty")
        return
    for statement in statements:
        _statement(file, statement, routine, result)
        if _always_throws(statement, result):
            # Java rejects a statement after one that always throws. Stopping here is also honest: the rest of
            # the block cannot run, and pretending otherwise would hide how much of the routine is missing.
            file.comment("the rest of this block is unreachable while the statement above is unresolved")
            break


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
        # the target goes through the translator too: `:NEW.col` is not a Java name, and rendering it anyway
        # produced code that did not compile
        target = _expr(file, statement.target, routine, result)
        value = _expr(file, statement.expression, routine, result)
        file.line(f"{target} = {_coerce(file, value, _local_type(routine, statement.target))};")
    elif kind == "Return":
        returns = java_type(routine.return_type.resolved or routine.return_type.oracle).name \
            if routine.return_type is not None else "void"
        file.line(f"return {_coerce(file, _expr(file, statement.expression, routine, result), returns)};"
                  if statement.expression else "return;")
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
    elif statement.loop_kind in ("cursor-for", "forall", "for"):
        # The query of a cursor FOR loop, and the bounds of a numeric one, are not modelled as statements, so
        # there is nothing to iterate yet. Emitting a call to a repository method that does not exist would give
        # code that cannot compile; refusing keeps the gap where a reviewer sees it.
        raise Untranslatable([f"{statement.loop_kind} loop"], statement.cursor or statement.kind)
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
        module = _MODULE.get()
        owner = statement.resolved_to.rsplit(".", 1)[0] if "." in statement.resolved_to else None
        if module is not None and owner is not None and owner != module.name:
            # another module's service would have to be injected; that is a composition decision, not a
            # translation, so it is refused rather than guessed
            raise Untranslatable([statement.resolved_to], f"call into {owner}")
        file.line(f"{java_name(target.split('.')[-1])}({arguments});")
    else:
        file.comment(f"external call: {statement.callee}")
        file.line(f'throw new UnsupportedOperationException("external call: {statement.callee}");')


def _sql(file: JavaFile, statement: M.SqlOperation, routine: M.Routine) -> None:
    method = f"{java_name(routine.name)}{_sql_suffix(statement)}"
    arguments = ", ".join(java_name(b.plsql_variable or b.name) for b in statement.binds)
    if statement.plan_id or statement.target_status == "PLANNED":
        # the plan hands back rows, and turning them into the PL/SQL variables is a decision (which row? what
        # when there are none?), so it is left to the reviewer rather than guessed
        raise Untranslatable(["execution plan result"], statement.original_sql)
    targets = statement.into_targets
    if targets and statement.cardinality == "MANY":
        # BULK COLLECT fills collections from every matching row. Treating it as a one-row SELECT INTO, which
        # is what the shape otherwise looks like, turns "no rows" and "many rows" into exceptions the original
        # never raised -- and quietly loses every row after the first when it does not.
        raise Untranslatable([f"BULK COLLECT INTO {', '.join(targets)}"], statement.original_sql)
    if targets and len(targets) == 1:
        file.line(f"{java_name(targets[0])} = "
                  f"{_into(file, f'repository.{method}({arguments})', _local_type(routine, targets[0]))};")
    elif targets:
        # the repository returns the columns positionally, in the order the SELECT names them
        if any("." in target for target in targets):
            _record_into(file, routine, targets, method, arguments, statement)
            return
        file.comment(f"SELECT INTO {', '.join(targets)}")
        file.line(f"var row = repository.{method}({arguments});")
        for index, target in enumerate(targets):
            file.line(f"{java_name(target)} = "
                      f"{_into(file, f'row[{index}]', _local_type(routine, target))};")
    elif (statement.sql_kind or "").upper() in ("INSERT", "UPDATE", "DELETE", "MERGE"):
        # SQL%ROWCOUNT is part of the behaviour: `update_email` raises when it is zero. One variable per
        # statement, because a routine may hold several DML statements in one scope.
        file.line(f"rowCount = repository.{method}({arguments});")
    else:
        file.line(f"repository.{method}({arguments});")


def _into(file: JavaFile, value: str, target_type: str) -> str:
    """Put a value the repository returned into a local of the declared type.

    A cast is not enough for a number. The column a `SELECT INTO` reads decides what JDBC hands back -- a
    ScalarDB `COUNT(*)` arrives as a Long -- while the local's type comes from the PL/SQL declaration, which for
    any `NUMBER` is BigDecimal. Casting one to the other throws at run time, and only for the routines whose
    SELECT happens to return the other kind. Coercing instead is what Oracle does on the same assignment.
    """
    if target_type == "BigDecimal":
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.dec({value})"
    return f"({target_type}) {value}"


def _record_into(file: JavaFile, routine: M.Routine, targets: list[str], method: str, arguments: str,
                 statement: M.SqlOperation) -> None:
    """`SELECT a, b INTO v_rec.a, v_rec.b` builds the record in one go.

    PL/SQL fills the fields one at a time; a Java record is immutable, so it is constructed once from all of
    them. That is only the same thing if every field is assigned by this statement, so a statement that fills
    part of a record is refused rather than silently constructed with nulls in the rest.
    """
    holders = {target.rpartition(".")[0] for target in targets}
    if len(holders) != 1:
        raise Untranslatable([f"SELECT INTO fields of more than one record ({', '.join(targets)})"],
                             statement.original_sql)
    holder = holders.pop()
    record = _local_type(routine, holder)
    fields = [target.rpartition(".")[2] for target in targets]
    declared = _record_fields(routine, holder)
    if declared is not None and [f.lower() for f in fields] != [f.lower() for f in declared]:
        raise Untranslatable(
            [f"SELECT INTO only part of {holder} ({', '.join(fields)} of {', '.join(declared)})"],
            statement.original_sql)
    file.comment(f"SELECT INTO {', '.join(targets)}")
    file.line(f"var row = repository.{method}({arguments});")
    casts = ", ".join(_into(file, f"row[{i}]", java_type(kind).name)
                      for i, kind in enumerate(_record_types(routine, holder) or [None] * len(fields)))
    file.line(f"{java_name(holder)} = new {record}({casts});")


def _record_fields(routine: M.Routine, holder: str) -> list[str] | None:
    return [name for name, _ in _record_shape(routine, holder)] or None


def _record_types(routine: M.Routine, holder: str) -> list[str] | None:
    return [kind for _, kind in _record_shape(routine, holder)] or None


def _record_shape(routine: M.Routine, holder: str) -> list[tuple[str, str]]:
    for declaration in routine.declarations:
        if declaration.name.lower() == holder.lower() and declaration.type is not None:
            return record_columns(declaration.type.resolved or "")
    return []


def _coerce(file: JavaFile, value: str, target_type: str) -> str:
    """A numeric literal or a ternary is not a BigDecimal; the helper makes it one."""
    if target_type == "BigDecimal" and not value.startswith("Plsql.dec("):
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.dec({value})"
    return value


def _local_type(routine: M.Routine, target: str) -> str:
    """The repository hands back Object; the local it lands in has a declared type."""
    for holder in list(routine.declarations) + list(routine.parameters):
        if holder.name.lower() != target.lower() or holder.type is None:
            continue
        if isinstance(holder, M.Declaration):
            row = _row_type(holder)
            if row is not None:
                return row
        return java_type(holder.type.resolved or holder.type.oracle).name
    return "Object"


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
