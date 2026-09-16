"""P2-7: the repository -- where the SQL actually runs.

The statements the converter approved are issued as ScalarDB SQL through the JDBC driver (plan §9). Those it
turned into a plan are handed to `PlanRunner`, which fetches through ScalarDB and runs the original SQL in H2.
Those it refused are not written at all: a method that cannot be implemented throws, next to the SQL it came
from, so the compiler keeps the gap visible.

Three behaviours are the repository's job and nowhere else's:

* **`SELECT INTO` semantics.** Oracle raises `NO_DATA_FOUND` on no row and `TOO_MANY_ROWS` on more than one. A
  JDBC result set does neither, so the generated method checks and raises the same exceptions -- otherwise a
  migrated routine silently takes a different branch.
* **Affected rows.** `SQL%ROWCOUNT` is part of the behaviour: `update_email` raises when it is zero.
* **The caller's transaction.** Every method takes the connection; none of them opens or closes one (P2-9).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ir import model as M
from ..lower import _walk
from .emit import JavaFile
from .types import java_class_name, java_name, java_type

RUNNER_IMPORT = "com.scalar.migrate.runtime.PlanRunner"
CONNECTION_IMPORT = "java.sql.Connection"


@dataclass
class RepositoryFile:
    file: JavaFile
    methods: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)


def generate_module(module: M.Module, package: str, domain_package: str) -> RepositoryFile:
    name = java_class_name(module.name) + "Repository"
    file = JavaFile(package=package, name=name,
                    source=module.source_range.file if module.source_range else module.name)
    result = RepositoryFile(file=file)
    file.add_import(CONNECTION_IMPORT, "java.sql.PreparedStatement", "java.sql.ResultSet", "java.sql.SQLException")
    file.add_import(f"{domain_package}.NoDataFoundException", f"{domain_package}.TooManyRowsException")

    file.comment(
        f"SQL of {module.name}.\n"
        "Every method runs inside the caller's transaction: the connection comes in, and nothing here commits.")
    with file.block(f"public class {name}") as f:
        f.line("private final Connection connection;")
        f.line()
        with f.block(f"public {name}(Connection connection)") as g:
            g.line("this.connection = connection;")
        for routine in module.routines:
            for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
                if statement.kind != "SqlOperation" or not statement.original_sql:
                    continue
                f.line()
                _method(f, routine, statement, result)
    return result


def _method(file: JavaFile, routine: M.Routine, statement: M.SqlOperation,
            result: RepositoryFile) -> None:
    name = f"{java_name(routine.name)}Stmt{statement.id.rsplit('-', 1)[-1]}"
    if statement.source_range is not None:
        file.comment(f"{statement.source_range.file}:{statement.source_range.start_line}")
    file.comment(_one_line(statement.original_sql))

    if statement.target_status == "ERROR":
        _unsupported(file, name, statement, result)
        return
    if statement.target_status == "PLANNED" or statement.plan_id:
        _planned(file, name, statement, result)
        return
    _direct(file, name, statement, result)


def _direct(file: JavaFile, name: str, statement: M.SqlOperation, result: RepositoryFile) -> None:
    sql = statement.target_sql[0] if statement.target_sql else statement.original_sql
    parameters, arguments = _parameters(file, statement)
    returns, reader = _return(file, statement)

    file.add_import("com.scalar.migrate.runtime.Residual", "java.util.ArrayList", "java.util.HashMap",
                    "java.util.List", "java.util.Map")
    with file.block(f"public {returns} {name}({', '.join(parameters)}) throws SQLException") as f:
        f.comment("the converter emits named placeholders and JDBC understands only positional ones;"
                  " Residual.bindNamed is the rewrite the runtime already uses for plans")
        f.line(f'String sql = "{_escape(sql)}";')
        f.line("Map<String, Object> params = new HashMap<>();")
        for bind in statement.binds:
            f.line(f'params.put("{bind.name}", {java_name(bind.name)});')
        f.line("List<Object> values = new ArrayList<>();")
        f.line("String bound = Residual.bindNamed(sql, params, values);")
        with f.block("try (PreparedStatement statement = connection.prepareStatement(bound))") as g:
            with g.block("for (int i = 0; i < values.size(); i++)") as h:
                h.line("statement.setObject(i + 1, values.get(i));")
            if returns == "int":
                g.line("return statement.executeUpdate();")
            elif statement.into_targets:
                _select_into(g, reader, statement)
            else:
                g.line("statement.execute();")
                if returns != "void":
                    g.line("return null;")
    result.methods.append(name)


def _select_into(file: JavaFile, reader: str, statement: M.SqlOperation) -> None:
    """Reproduce what Oracle does: no row raises, more than one row raises."""
    with file.block("try (ResultSet rows = statement.executeQuery())") as f:
        with f.block("if (!rows.next())") as g:
            g.line('throw new NoDataFoundException("SELECT INTO matched no row");')
        f.line(f"var value = {reader};")
        with f.block("if (rows.next())") as g:
            g.line('throw new TooManyRowsException("SELECT INTO matched more than one row");')
        f.line("return value;")


def _planned(file: JavaFile, name: str, statement: M.SqlOperation, result: RepositoryFile) -> None:
    """ScalarDB SQL cannot run this one, so the plan does: fetch through ScalarDB, then H2."""
    file.add_import(RUNNER_IMPORT, "java.util.Map")
    parameters, arguments = _parameters(file, statement)
    plan = statement.plan_id or f"{statement.id}.plan.json"
    with file.block(f"public PlanRunner.Result {name}({', '.join(parameters)}) throws Exception") as f:
        f.comment("ScalarDB SQL cannot run this statement; the plan fetches through ScalarDB and runs the "
                  "original SQL in H2, inside this transaction")
        f.line(f'var plan = PlanRunner.resource("plans/{plan}");')
        binds = ", ".join(f'"{b.name}", {java_name(b.name)}' for b in statement.binds)
        f.line(f"return PlanRunner.join(connection, plan, Map.of({binds}));")
    result.methods.append(name)
    result.planned.append(statement.id)


def _unsupported(file: JavaFile, name: str, statement: M.SqlOperation, result: RepositoryFile) -> None:
    """The method exists with the signature it would have had, and throws.

    Changing the return type as well would make the service's call site disagree with it, and the compiler would
    report a type error instead of the refusal that is actually there.
    """
    reasons = [d.message for d in statement.diagnostics if d.severity == "ERROR"] or ["not convertible"]
    parameters, _ = _parameters(file, statement)
    returns, _ = _return(file, statement)
    returns = "Object" if returns == "void" else returns
    with file.block(f"public {returns} {name}({', '.join(parameters)})") as f:
        f.comment("ScalarDB cannot run this statement:")
        for reason in reasons[:3]:
            f.comment(f"    {reason}")
        f.line(f'throw new UnsupportedOperationException("{_escape(reasons[0])[:160]}");')
    result.methods.append(name)
    result.unsupported.append(statement.id)


def _parameters(file: JavaFile, statement: M.SqlOperation) -> tuple[list[str], list[str]]:
    parameters, arguments = [], []
    for bind in statement.binds:
        mapped = java_type(bind.oracle_type)
        file.add_import(*mapped.imports)
        parameters.append(f"{mapped.name} {java_name(bind.name)}")
        arguments.append(java_name(bind.name))
    return parameters, arguments


def _return(file: JavaFile, statement: M.SqlOperation) -> tuple[str, str]:
    if (statement.sql_kind or "").upper() in ("INSERT", "UPDATE", "DELETE", "MERGE"):
        return "int", ""   # SQL%ROWCOUNT is part of the behaviour
    if statement.into_targets:
        if len(statement.into_targets) == 1:
            return "Object", "rows.getObject(1)"
        return "Object[]", "new Object[] {" + ", ".join(
            f"rows.getObject({i})" for i in range(1, len(statement.into_targets) + 1)) + "}"
    return "void", ""


def _one_line(sql: str) -> str:
    return " ".join(sql.split())[:160]


def _escape(text: str) -> str:
    return " ".join(text.split()).replace("\\", "\\\\").replace('"', '\\"')
