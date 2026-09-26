"""A scalar subquery in an UPDATE's SET, read before the UPDATE (#56, samples/oracle-samples emp_dept_upd_v_trg).

    UPDATE employees SET department_id = (SELECT department_id FROM departments WHERE department_name = :x) ...

ScalarDB SQL takes only values in SET. When the subquery does not read the row being updated (it is not
correlated), its answer is the same before the UPDATE as during it -- the two run in one transaction -- so it is
read first, into a variable the SET then writes:

    BEGIN
      SELECT department_id INTO v_sub_1 FROM departments WHERE department_name = :x;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN v_sub_1 := NULL;                          -- a subquery with no row is NULL
      WHEN TOO_MANY_ROWS THEN RAISE ORA-01427;                          -- and one with two rows is an error
    END;
    UPDATE employees SET department_id = v_sub_1 ...

This changes the form, not the result, so it needs no decision. A correlated subquery (it reads the updated
row's columns) is per row, and is left as it was: the converter refuses it with its reason.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .ir import model as M
from .symbols import OracleSchema, Symbol, SymbolTable

TOO_MANY = "'ORA-01427: single-row subquery returns more than one row'"


def rewrite(program: M.Program, schema: OracleSchema | None, symbols: SymbolTable | None = None) -> None:
    for module in program.modules:
        for routine in module.routines:
            before = len(routine.declarations)
            counter = [0]
            routine.body = _sequence(routine.body, routine, schema, counter)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, routine, schema, counter)
            # a trigger's body resolves in the trigger's scope, which is named after the module
            scope = (symbols.scopes.get(routine.id) or symbols.scopes.get(module.name)) if symbols else None
            for declaration in routine.declarations[before:]:
                if scope is not None:
                    scope.declare(Symbol(name=declaration.name, kind="variable", scope=routine.id,
                                         type=declaration.type, source_range=declaration.source_range))


def _sequence(statements, routine, schema, counter):
    out = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, schema, counter))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, schema, counter)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, routine, schema, counter)
        replaced = _split(statement, routine, schema, counter) \
            if statement.kind == "SqlOperation" and (statement.sql_kind or "").upper() == "UPDATE" else None
        out.extend(replaced if replaced is not None else [statement])
    return out


def _split(statement: M.SqlOperation, routine: M.Routine, schema: OracleSchema | None,
           counter: list[int]) -> list[M.Statement] | None:
    if "RETURNING" in (statement.original_sql or "").upper():
        return None
    try:
        tree = sqlglot.parse_one(statement.original_sql or "", dialect="oracle")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(tree, exp.Update) or not isinstance(tree.this, exp.Table):
        return None
    updated = tree.this.name.lower()
    updated_names = {updated, (tree.this.alias or "").lower()} - {""}
    updated_columns = set((schema.columns(updated) or {}) if schema else {})
    reads: list[M.Statement] = []
    for assignment in tree.expressions or []:
        if not isinstance(assignment, exp.EQ) or not isinstance(assignment.expression, exp.Subquery):
            continue
        select = assignment.expression.this
        if not isinstance(select, exp.Select) or len(select.expressions) != 1:
            return None
        inner = {(t.alias or t.name).lower() for t in select.find_all(exp.Table)}
        inner_columns = set()
        for table in select.find_all(exp.Table):
            inner_columns |= set((schema.columns(table.name) or {}) if schema else {})
        for column in select.find_all(exp.Column):
            qualifier = (column.table or "").lower()
            if qualifier and qualifier in updated_names and qualifier not in inner:
                return None   # correlated with the updated row: per row, not before
            if not qualifier and column.name.lower() in updated_columns and column.name.lower() not in inner_columns:
                return None
        counter[0] += 1
        variable = f"v_sub_{counter[0]}"
        while any(d.name.lower() == variable for d in routine.declarations):
            counter[0] += 1
            variable = f"v_sub_{counter[0]}"
        target = assignment.this.name.lower() if isinstance(assignment.this, exp.Column) else ""
        oracle = (schema.column(updated, target) if schema and target else None) or "NUMBER"
        where = statement.source_range
        routine.declarations.append(M.Declaration(id=f"{statement.id}{variable}", kind="Declaration",
                                                  source_range=where, name=variable, declaration_kind="variable",
                                                  type=M.TypeRef(oracle, oracle, "declared", None)))
        read = M.SqlOperation(id=f"{statement.id}sub{counter[0]}", kind="SqlOperation", source_range=where,
                              sql_kind="SELECT", into_targets=[variable], cardinality="EXACTLY_ONE",
                              original_sql=f"SELECT {select.expressions[0].sql(dialect='oracle')} INTO {variable} "
                                           f"FROM {select.sql(dialect='oracle').split(' FROM ', 1)[1]}")
        block = M.Block(id=f"{statement.id}sub{counter[0]}block", kind="Block", source_range=where, body=[read])
        block.exception_handlers = [
            M.ExceptionHandler(id=f"{statement.id}sub{counter[0]}none", kind="ExceptionHandler", source_range=where,
                               exceptions=["NO_DATA_FOUND"],
                               body=[M.Assignment(id=f"{statement.id}sub{counter[0]}null", kind="Assignment",
                                                  source_range=where, target=variable, expression="NULL")]),
            M.ExceptionHandler(id=f"{statement.id}sub{counter[0]}many", kind="ExceptionHandler", source_range=where,
                               exceptions=["TOO_MANY_ROWS"],
                               body=[M.Raise(id=f"{statement.id}sub{counter[0]}raise", kind="Raise",
                                             source_range=where, error_code=-1427, message=TOO_MANY)]),
        ]
        block.add("INFO", "SUBQUERY_READ_FIRST",
                  f"SET {target} の副問合せは更新する行を読まないので、UPDATE の前に同じトランザクションで読む。"
                  f"0 行は NULL、2 行以上は ORA-01427（Oracle と同じ）")
        reads.append(block)
        assignment.set("expression", exp.column(variable))
    if not reads:
        return None
    statement.original_sql = tree.sql(dialect="oracle")
    return reads + [statement]
