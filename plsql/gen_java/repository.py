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

import re

from dataclasses import dataclass, field

import contextvars

from ..ir import model as M
from ..limits import Limits
from ..lower import _walk
from .emit import JavaFile
from .expr import translate
from .types import java_class_name, java_name, java_type

RUNNER_IMPORT = "com.scalar.migrate.runtime.PlanRunner"
CONNECTION_IMPORT = "java.sql.Connection"


@dataclass
class RepositoryFile:
    file: JavaFile
    methods: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    # 走査を生成したが、その routine の行数上限が決められていなかったもの（#19）。生成は続く——
    # 既定値でも動くコードは出る——が、`--limits-strict` はこれを合否に使う
    undecided_limits: list[str] = field(default_factory=list)


LOOP_ROW_SUFFIX = "Row"

# 走査行数の上限。生成の 1 回につき 1 つで、routine ごとの上書きは Limits が持つ（P4-5 の続き、2026-09-17）
_LIMITS: "contextvars.ContextVar[Limits]" = contextvars.ContextVar("limits", default=Limits())


def set_limits(limits: Limits) -> None:
    _LIMITS.set(limits)


def _loop_rows(file: JavaFile, name: str, loop: M.Loop, result: RepositoryFile, domain_package: str,
               routine_id: str | None = None) -> None:
    """A cursor FOR loop's query, as a method returning every row.

    Every row, not a streaming cursor. An Oracle cursor holds its position across the transaction; ScalarDB has
    no equivalent, and pretending otherwise is the thing `CUR-001` exists to warn about. Reading the rows into
    memory says plainly what is happening, and the row limit is then a decision a reviewer can see rather than
    an unbounded cursor nobody counted.
    """
    statement = loop.query
    record = _record_for(name)
    file.add_import(f"{domain_package}.{record}", "java.util.ArrayList", "java.util.HashMap",
                    "java.util.List", "java.util.Map", "com.scalar.migrate.runtime.Residual")
    parameters, _ = _parameters(file, statement)
    limit = _LIMITS.get().for_routine(routine_id) if routine_id else None
    if routine_id and not _LIMITS.get().decided(routine_id) and routine_id not in result.undecided_limits:
        result.undecided_limits.append(routine_id)
    sql = statement.target_sql[0] if statement.target_sql else statement.original_sql
    with file.block(f"public List<{record}> {name}({', '.join(parameters)}) throws SQLException") as f:
        f.line(f'String sql = "{_escape(sql)}";')
        f.line("Map<String, Object> params = new HashMap<>();")
        scope = {b.plsql_variable or b.name: java_name(b.name) for b in statement.binds if not b.expression}
        for bind in statement.binds:
            f.line(f'params.put("{bind.name}", {_bound(file, bind, scope)});')
        f.line("List<Object> values = new ArrayList<>();")
        f.line("String bound = Residual.bindNamed(sql, params, values);")
        f.line(f"List<{record}> rows = new ArrayList<>();")
        with f.block("try (PreparedStatement statement = connection.prepareStatement(bound))") as g:
            with g.block("for (int i = 0; i < values.size(); i++)") as h:
                h.line("statement.setObject(i + 1, values.get(i));")
            with g.block("try (ResultSet rows_ = statement.executeQuery())") as g2:
                with g2.block("while (rows_.next())") as g3:
                    if limit is not None:
                        # 読みながら数える。全部読んでから数えると、止める前にメモリを使い切っている
                        g3.comment(f"走査行数の上限: {_LIMITS.get().explain(routine_id)}")
                        with g3.block(f"if (rows.size() >= {limit})") as g4:
                            g4.line(f'throw new IllegalStateException("{name}: 走査行数が上限 {limit} 行を'
                                    f'超えた。上限は limits.yaml で決める。生成コードは cursor の行を先に'
                                    f'全部読むので、ここで止めないとメモリを使い切る");')
                    arguments = ", ".join(
                        _into_java(file, statement, i) for i in range(1, len(statement.into_columns or []) + 1))
                    g3.line(f"rows.add(new {record}({arguments}));")
        f.line("return rows;")
    result.methods.append(name)


def _into_java(file: JavaFile, statement: M.SqlOperation, index: int) -> str:
    """One column of a loop row, read and typed the way the rest of the generator reads columns."""
    from .dto import loop_component_type

    oracle = (statement.into_oracle_types or [])
    declared = oracle[index - 1] if index - 1 < len(oracle) else None
    mapped = loop_component_type(declared)
    file.add_import(*mapped.imports)
    raw = _read(file, statement, index).replace("rows.getObject", "rows_.getObject")
    if mapped.name == "BigDecimal":
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.dec({raw})"
    return f"({mapped.name}) {raw}"


def generate_module(module: M.Module, package: str, domain_package: str) -> RepositoryFile:
    name = java_class_name(module.name) + "Repository"
    file = JavaFile(package=package, name=name,
                    source=module.source_range.file if module.source_range else module.name)
    result = RepositoryFile(file=file)
    file.add_import(CONNECTION_IMPORT, "java.sql.PreparedStatement", "java.sql.ResultSet", "java.sql.SQLException")
    file.add_import(f"{domain_package}.NoDataFoundException", f"{domain_package}.TooManyRowsException")
    file.domain_package = domain_package  # _row needs it to import the %ROWTYPE record
    # 採番する statement があるときだけ Sequences を持たせる。使わないクラスに依存を足すと、移行先は
    # 採番していない Repository にも実装を用意させられることになる
    takes_sequences = _uses_sequences(module)
    if takes_sequences:
        file.add_import("com.scalar.migrate.plsql.Sequences")

    file.comment(
        f"SQL of {module.name}.\n"
        "Every method runs inside the caller's transaction: the connection comes in, and nothing here commits.")
    with file.block(f"public class {name}") as f:
        f.line("private final Connection connection;")
        if takes_sequences:
            f.line("private final Sequences sequences;")
        f.line()
        signature = "Connection connection" + (", Sequences sequences" if takes_sequences else "")
        with f.block(f"public {name}({signature})") as g:
            g.line("this.connection = connection;")
            if takes_sequences:
                g.line("this.sequences = sequences;")
        for routine in module.routines:
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            loop_queries = {loop.query.id: loop for loop in statements
                            if loop.kind == "Loop" and getattr(loop, "query", None) is not None}
            for statement in statements:
                if statement.kind != "SqlOperation" or not statement.original_sql:
                    continue
                f.line()
                loop = loop_queries.get(statement.id)
                if loop is not None:
                    if statement.target_status == "ERROR":
                        _unsupported(f, loop_method(routine, loop), statement, result)
                    else:
                        _loop_rows(f, loop_method(routine, loop), loop, result, domain_package, routine.id)
                    continue
                _method(f, routine, statement, result)
    return result


def _uses_sequences(module: M.Module) -> bool:
    """この module のどこかが採番するか。`seq_x.NEXTVAL` は式として持ち上げられている（P4-4 の仕組み）。"""
    for routine in module.routines:
        statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
        for statement in statements:
            for bind in getattr(statement, "binds", None) or []:
                if bind.expression and ".NEXTVAL" in bind.expression.upper():
                    return True
    return False


def loop_method(routine: M.Routine, loop: M.Loop) -> str:
    """The repository method a cursor FOR loop reads its rows from. The service calls the same name."""
    return f"{java_name(routine.name)}Loop{loop.id.rsplit('-', 1)[-1]}"


def _record_for(method: str) -> str:
    """`orderTotalLoop1` -> `OrderTotalLoop1Row`.

    Not `java_class_name`, which lowercases the rest of each part and would turn this into
    `Ordertotalloop1Row`. One helper, so the repository, the DTO and the service cannot disagree.
    """
    return method[0].upper() + method[1:] + LOOP_ROW_SUFFIX


def loop_record(routine: M.Routine, loop: M.Loop) -> str:
    return _record_for(loop_method(routine, loop))


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
        scope = {b.plsql_variable or b.name: java_name(b.name) for b in statement.binds if not b.expression}
        for bind in statement.binds:
            f.line(f'params.put("{bind.name}", {_bound(file, bind, scope)});')
        f.line("List<Object> values = new ArrayList<>();")
        f.line("String bound = Residual.bindNamed(sql, params, values);")
        with f.block("try (PreparedStatement statement = connection.prepareStatement(bound))") as g:
            with g.block("for (int i = 0; i < values.size(); i++)") as h:
                h.line("statement.setObject(i + 1, values.get(i));")
            if returns == "int":
                g.line("return statement.executeUpdate();")
            elif statement.cardinality == "AT_MOST_ONE":
                _first_row(g, reader)
            elif statement.into_targets:
                _select_into(g, reader, statement)
            else:
                g.line("statement.execute();")
                if returns != "void":
                    g.line("return null;")
    result.methods.append(name)


def _rowtype_read(statement: M.SqlOperation) -> bool:
    """One INTO target fed by more than one column: a %ROWTYPE read.

    The single-target path would return column 1 and let the caller cast it, which is a wrong answer that only
    shows up when the DTO type happens to differ. The row has to be built from every column instead, in the
    order the DDL declares -- which is the order `dto.row_record` gives the record's components.
    """
    return len(statement.into_targets) == 1 and len(statement.into_columns or []) > 1


def _first_row(file: JavaFile, reader: str) -> None:
    """An explicit cursor's first `FETCH` (#11): the row, or null when there was none.

    Not `_select_into`. No row is not an error here -- the routine wrote what to do about it in its
    `%NOTFOUND` branch -- and the query is bounded to one row, so there is no second one to complain about.
    `null` is the row's absence, which is why the values come back wrapped: a row whose column is NULL is a
    row, and the caller has to be able to tell the two apart.
    """
    with file.block("try (ResultSet rows = statement.executeQuery())") as f:
        with f.block("if (!rows.next())") as g:
            g.line("return null;   // %NOTFOUND")
        f.line(f"return {reader};")


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


def _refuse(file: JavaFile, name: str, statement: M.SqlOperation, result: RepositoryFile, reason: str) -> None:
    """Keep the signature the call site expects, and throw instead of answering."""
    parameters, _ = _parameters(file, statement)
    returns, _ = _return(file, statement)
    returns = "Object" if returns == "void" else returns
    with file.block(f"public {returns} {name}({', '.join(parameters)}) throws SQLException") as f:
        f.comment("the generator refuses this statement:")
        f.comment(f"    {reason}")
        f.line(f'throw new UnsupportedOperationException("{_escape(reason)}");')
    result.methods.append(name)
    result.unsupported.append(statement.id)


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


NUMERIC_STORAGE = ("BIGINT", "INT", "DOUBLE", "FLOAT")


def _scale(oracle_type: str | None) -> int:
    """How many decimals the column keeps when it is stored as an integer, per the Oracle type."""
    return java_type(oracle_type).scale or 0


def _bound(file: JavaFile, bind: M.BindVariable, scope: dict[str, str] | None = None) -> str:
    """The expression that binds this value.

    The column's ScalarDB type comes from the schema that was actually loaded, not from the Oracle type, because
    the same NUMBER(12,2) is a scaled BIGINT under one money convention and a DOUBLE under the other. A bind the
    analysis could not attribute to exactly one column is passed through unchanged -- the old behaviour, which is
    right when there is nothing better to say.
    """
    name = _java_value(file, bind, scope)
    if not bind.scalardb_type:
        return name
    file.add_import("com.scalar.migrate.plsql.Plsql")
    return f'Plsql.bind({name}, "{bind.scalardb_type}", {_scale(bind.column_oracle_type)})'


def _java_value(file: JavaFile, bind: M.BindVariable, scope: dict[str, str] | None) -> str:
    """The Java that produces this bind's value.

    Usually the parameter itself. For a bind lifted out of SQL (P4-4) it is the PL/SQL expression, translated by
    the same translator the service body uses -- so `ROUND(:v_total * :p_rate, 2)` becomes the same
    `Plsql.round(Plsql.mul(...), 2)` it would have become anywhere else, and there is one implementation of
    what those functions mean rather than two.
    """
    if not bind.expression:
        return java_name(bind.name)
    rendered = translate(bind.expression, scope or {})
    file.add_import(*rendered.imports)
    return rendered.java


def _read(file: JavaFile, statement: M.SqlOperation, index: int) -> str:
    """The expression that reads select item `index` (1-based) back as a PL/SQL value.

    Only a column whose PL/SQL type is BigDecimal is converted. A `NUMBER(9)` column comes back from JDBC as the
    Integer or Long the generated code already expects, and wrapping it would hand a BigDecimal to a variable
    declared Long -- which is a cast failure at run time, not a fix.
    """
    raw = f"rows.getObject({index})"
    types = statement.into_types or []
    kind = types[index - 1] if index - 1 < len(types) else None
    oracle = (statement.into_oracle_types or [])
    declared = oracle[index - 1] if index - 1 < len(oracle) else None
    if kind not in NUMERIC_STORAGE or java_type(declared).name != "BigDecimal":
        return raw
    file.add_import("com.scalar.migrate.plsql.Plsql")
    return f'Plsql.read({raw}, "{kind}", {_column_scale(statement, index)})'


def _row(file: JavaFile, statement: M.SqlOperation) -> str:
    """Construct the %ROWTYPE record from every column, positionally.

    The record's components are generated from the same DDL, in the same order (`dto.row_record`), and the star
    was expanded into that order too, so position i of the result is component i. Nothing here re-derives the
    order; it is the one place all three agree by construction.
    """
    table = (statement.read_set or [None])[0]
    record = java_class_name(table) + "Row"
    file.add_import(f"{getattr(file, 'domain_package', '')}.{record}")
    arguments = []
    for i, oracle in enumerate(statement.into_oracle_types or [], start=1):
        mapped = java_type(oracle)
        file.add_import(*mapped.imports)
        arguments.append(f"({mapped.name}) {_read(file, statement, i)}")
    return f"new {record}({', '.join(arguments)})"


def _column_scale(statement: M.SqlOperation, index: int) -> int:
    """A BIGINT holding a value the DDL declared with decimals is holding it scaled; anything else is as it is."""
    kind = (statement.into_types or [])[index - 1] if index - 1 < len(statement.into_types or []) else None
    if kind not in ("BIGINT", "INT"):
        return 0
    oracle = statement.into_oracle_types or []
    return _scale(oracle[index - 1]) if index - 1 < len(oracle) else 0


def needs_audit(statement: M.SqlOperation) -> bool:
    """Whether this statement's values include one the caller supplies (#1, #8).

    Read off the lifted expression, the same way `_uses_sequences` reads `.NEXTVAL`: the value was taken out
    of the SQL by P4-4 and is computed here, so the context it needs is a parameter of this method.

    Asked of the translation, not of the text: `'USER'` is a string, and searching for the word in it grew a
    parameter into the method signature that nothing in the body read.
    """
    return any(bind.expression and translate(bind.expression).audit
               for bind in statement.binds or [])


def _parameters(file: JavaFile, statement: M.SqlOperation) -> tuple[list[str], list[str]]:
    from .dto import loop_component_type

    parameters, arguments = [], []
    if needs_audit(statement):
        # first, so the value the caller supplies is visible in the signature rather than buried among binds
        file.add_import("com.scalar.migrate.plsql.AuditContext")
        parameters.append("AuditContext audit")
        arguments.append("audit")
    # a lifted expression is computed here from the other binds, so it is not a parameter of its own
    for bind in (b for b in statement.binds if not b.expression):
        # a dotted PL/SQL name is a field of a row the caller is holding -- a cursor FOR loop's `r.qty` (#10).
        # The record that row comes out of models the PL/SQL loop variable, where every number is a NUMBER
        # (`loop_component_type`), so a parameter typed from the column's own width would be a Long the caller
        # cannot pass a BigDecimal to.
        mapped = (loop_component_type(bind.oracle_type) if "." in (bind.plsql_variable or "")
                  else java_type(bind.oracle_type))
        file.add_import(*mapped.imports)
        parameters.append(f"{mapped.name} {java_name(bind.name)}")
        arguments.append(java_name(bind.name))
    return parameters, arguments


def _return(file: JavaFile, statement: M.SqlOperation) -> tuple[str, str]:
    if (statement.sql_kind or "").upper() in ("INSERT", "UPDATE", "DELETE", "MERGE"):
        return "int", ""   # SQL%ROWCOUNT is part of the behaviour
    if statement.into_targets:
        if statement.cardinality == "AT_MOST_ONE":
            # always an array, even for one target: `null` has to mean "no row", and a one-value return could
            # not tell that apart from a row whose only column is NULL
            return "Object[]", "new Object[] {" + ", ".join(
                _read(file, statement, i)
                for i in range(1, max(len(statement.into_targets), len(statement.into_columns or [])) + 1)) + "}"
        if _rowtype_read(statement):
            return "Object", _row(file, statement)
        if len(statement.into_targets) == 1:
            return "Object", _read(file, statement, 1)
        return "Object[]", "new Object[] {" + ", ".join(
            _read(file, statement, i) for i in range(1, len(statement.into_targets) + 1)) + "}"
    return "void", ""


def _one_line(sql: str) -> str:
    return " ".join(sql.split())[:160]


def _escape(text: str) -> str:
    return " ".join(text.split()).replace("\\", "\\\\").replace('"', '\\"')
