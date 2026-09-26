"""`WHERE :p IS NULL OR col = :p`: an optional filter, split into the two queries it is (#54 follow-up).

    FOR r IN (SELECT ... FROM employees WHERE p_dept IS NULL OR department_id = p_dept) LOOP body END LOOP;

ScalarDB SQL cannot test a bind for NULL in WHERE. The predicate says "every row when p_dept is NULL, the rows of
that department otherwise", which is two queries, and which one runs is decided by a value the routine holds:

    IF p_dept IS NULL THEN
      FOR r IN (SELECT ... FROM employees) LOOP body END LOOP;
    ELSE
      FOR r IN (SELECT ... FROM employees WHERE department_id = p_dept) LOOP body END LOOP;
    END IF;

Same rows, same order of statements; the body is written twice. Taken for a cursor FOR loop and a SELECT INTO,
when the pattern is the whole WHERE or one conjunct of it, with the variable a parameter or local of the routine.
Found with samples/oracle-samples emp_grades.
"""

from __future__ import annotations

import copy

import sqlglot
from sqlglot import exp

from .ir import model as M


def rewrite(program: M.Program) -> None:
    for module in program.modules:
        for routine in module.routines:
            names = {p.name.lower() for p in routine.parameters} | {d.name.lower() for d in routine.declarations}
            routine.body = _sequence(routine.body, names)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, names)


def _sequence(statements: list[M.Statement], names: set[str]) -> list[M.Statement]:
    out = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, names))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, names)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, names)
        out.append(_split(statement, names) or statement)
    return out


def _optional(condition: exp.Expression, names: set[str]):
    """(variable, the conjunct, the equality that remains) when `condition` is `v IS NULL OR col = v`."""
    condition = condition.unnest() if isinstance(condition, exp.Paren) else condition
    if not isinstance(condition, exp.Or):
        return None
    sides = [condition.this.unnest() if isinstance(condition.this, exp.Paren) else condition.this,
             condition.expression.unnest() if isinstance(condition.expression, exp.Paren) else condition.expression]
    for null_side, equal_side in (sides, sides[::-1]):
        if not (isinstance(null_side, exp.Is) and isinstance(null_side.expression, exp.Null)
                and isinstance(null_side.this, exp.Column) and not null_side.this.table
                and null_side.this.name.lower() in names):
            continue
        variable = null_side.this.name.lower()
        if isinstance(equal_side, exp.EQ):
            refs = [n for n in (equal_side.this, equal_side.expression)
                    if isinstance(n, exp.Column) and not n.table and n.name.lower() == variable]
            if len(refs) == 1:
                return variable, equal_side
    return None


def _conjuncts(condition: exp.Expression) -> list[exp.Expression]:
    if isinstance(condition, exp.And):
        return _conjuncts(condition.this) + _conjuncts(condition.expression)
    return [condition]


def _split(statement: M.Statement, names: set[str]) -> M.Statement | None:
    operation = statement.query if statement.kind == "Loop" and statement.loop_kind == "cursor-for" \
        else statement if statement.kind == "SqlOperation" and (statement.sql_kind or "").upper() == "SELECT" else None
    if operation is None or not operation.original_sql:
        return None
    try:
        tree = sqlglot.parse_one(operation.original_sql, dialect="oracle")
    except Exception:  # noqa: BLE001
        return None
    where = tree.args.get("where") if isinstance(tree, exp.Select) else None
    if where is None:
        return None
    conjuncts = _conjuncts(where.this)
    found = [(i, _optional(c, names)) for i, c in enumerate(conjuncts)]
    found = [(i, f) for i, f in found if f is not None]
    if len(found) != 1:
        return None
    index, (variable, equality) = found[0]
    rest = [c for i, c in enumerate(conjuncts) if i != index]

    def query(with_filter: bool) -> str:
        copy_tree = tree.copy()
        kept = [c.copy() for c in rest] + ([equality.copy()] if with_filter else [])
        copy_tree.set("where", exp.Where(this=exp.and_(*kept)) if kept else None)
        return copy_tree.sql(dialect="oracle")

    everything, filtered = _twin(statement, "all", query(False)), _twin(statement, "eq", query(True))
    node = M.If(id=f"{statement.id}opt", kind="If", source_range=statement.source_range,
                branches=[M.Branch(condition=f"{variable} IS NULL", body=[everything])], else_body=[filtered])
    node.add("INFO", "OPTIONAL_FILTER", f"`{variable} IS NULL OR …` は ScalarDB SQL で書けないので、{variable} が NULL "
                                        f"のときの問合せ（絞らない）とそれ以外（等号で絞る）に分けた")
    return node


def _twin(statement: M.Statement, suffix: str, query: str) -> M.Statement:
    """A copy of the statement over `query`, with ids of its own (method names come from ids)."""
    twin = copy.deepcopy(statement)

    def renumber(node) -> None:
        if isinstance(node, list):
            for item in node:
                renumber(item)
            return
        if not hasattr(node, "__dataclass_fields__"):
            return
        if isinstance(getattr(node, "id", None), str):
            node.id = f"{node.id}{suffix}"
        for name in node.__dataclass_fields__:
            value = getattr(node, name, None)
            if isinstance(value, list) or hasattr(value, "__dataclass_fields__"):
                renumber(value)

    renumber(twin)
    if twin.kind == "Loop":
        twin.query.original_sql = query
        twin.cursor = f"{twin.variable} IN ({query})"
    else:
        head, _, _ = (statement.original_sql or "").partition(" FROM ")
        twin.original_sql = f"{head} FROM {query.partition(' FROM ')[2]}" if " INTO " in head.upper() else query
    return twin
