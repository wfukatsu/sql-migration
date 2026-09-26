"""#54: schema object types and the collections of them, taken to where the generated code can hold them.

Three shapes, all from samples/oracle-samples (b06_4_collection_in_sql, emp_grades):

* ``SELECT t(a, b, 'X') BULK COLLECT INTO v FROM ...``: ScalarDB SQL cannot build an object in its select list.
  The query reads the columns the constructor takes, and the application builds each record, the same way an
  expression in the select list is lifted out of the SQL:

      v := ts();
      FOR r_obj_1 IN (SELECT a, b FROM ...) LOOP v.EXTEND; v(v.LAST) := t(r_obj_1.a, r_obj_1.b, 'X'); END LOOP;

* ``SELECT COUNT(*) INTO n FROM TABLE(v) [WHERE cond]``: the rows are the application's own collection, so the
  query is a loop over it. Only ``COUNT(*)`` is taken; anything else stays SQL and is refused with its reason.

      n := 0;
      FOR i_obj_1 IN 1 .. v.COUNT LOOP v_obj_1 := v(i_obj_1); IF <cond on v_obj_1> THEN n := n + 1; END IF; END LOOP;

* a PIPELINED function: ``PIPE ROW (x)`` adds to a collection the function returns at its ``RETURN;``.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from .ir import model as M
from .symbols import OracleSchema, Symbol, SymbolTable

BULK_SELECT = re.compile(r"^\s*SELECT\s+(?P<select>.+?)\s+BULK\s+COLLECT\s+INTO\s+(?P<target>[\w$#]+)\s+"
                         r"(?P<rest>FROM\s+.+)$", re.IGNORECASE | re.DOTALL)
COUNT_TABLE = re.compile(r"^\s*SELECT\s+COUNT\s*\(\s*\*\s*\)\s+INTO\s+(?P<target>[\w$#]+)\s+FROM\s+TABLE\s*\(\s*"
                         r"(?P<collection>[\w$#]+)\s*\)\s*(?P<alias>[A-Za-z][\w$#]*)?\s*"
                         r"(?:WHERE\s+(?P<where>.+?))?\s*;?\s*$", re.IGNORECASE | re.DOTALL)
PIPE_ROW = re.compile(r"^\s*PIPE\s+ROW\s*\((?P<value>.*)\)\s*;?\s*$", re.IGNORECASE | re.DOTALL)
_RESERVED = {"WHERE", "GROUP", "ORDER", "CONNECT", "START"}


def rewrite(program: M.Program, schema: OracleSchema | None, symbols: SymbolTable | None = None) -> None:
    if schema is None or not schema.object_types:
        return
    for module in program.modules:
        for routine in module.routines:
            _resolve_return(routine, schema)
            before = len(routine.declarations)
            counter = [0]
            routine.body = _sequence(routine.body, routine, schema, counter)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, routine, schema, counter)
            _pipelined(routine, schema)
            _declare(symbols, routine, routine.declarations[before:], module.name)


def _resolve_return(routine: M.Routine, schema: OracleSchema) -> None:
    returned = routine.return_type
    if returned is None or returned.origin not in ("declared", None):
        return
    name = re.sub(r"\s+PIPELINED$", "", returned.oracle or "", flags=re.IGNORECASE).strip()
    if schema.object_record(name):
        routine.return_type = M.TypeRef(name, schema.object_record(name), "record", returned.schema_snapshot)
    elif name.lower() in schema.table_types:
        routine.return_type = M.TypeRef(name, f"TABLE OF {schema.table_types[name.lower()]}", "collection",
                                        returned.schema_snapshot)


def _declare(symbols: SymbolTable | None, routine: M.Routine, declarations: list[M.Declaration],
             module: str = "") -> None:
    scope = (symbols.scopes.get(routine.id) or symbols.scopes.get(module)) if symbols else None
    if scope is None:
        return
    for declaration in declarations:
        scope.declare(Symbol(name=declaration.name, kind="variable", scope=routine.id,
                             type=declaration.type, source_range=declaration.source_range))


def _holder_type(routine: M.Routine, name: str) -> M.TypeRef | None:
    for holder in list(routine.declarations) + list(routine.parameters):
        if holder.name.lower() == name.lower():
            return holder.type
    return None


def _free(routine: M.Routine, stem: str, counter: list[int]) -> str:
    taken = {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}
    while True:
        counter[0] += 1
        name = f"{stem}{counter[0]}"
        if name not in taken:
            return name


def _sequence(statements: list[M.Statement], routine: M.Routine, schema: OracleSchema,
              counter: list[int]) -> list[M.Statement]:
    out: list[M.Statement] = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, schema, counter))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, schema, counter)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, routine, schema, counter)
        replacement = None
        if statement.kind == "SqlOperation" and (statement.sql_kind or "").upper() == "SELECT":
            replacement = _constructed(statement, routine, schema, counter) or \
                _count_over_collection(statement, routine, schema, counter)
        out.extend(replacement if replacement is not None else [statement])
    return out


def _constructed(statement: M.SqlOperation, routine: M.Routine, schema: OracleSchema,
                 counter: list[int]) -> list[M.Statement] | None:
    """`SELECT t(a, b, 'X') BULK COLLECT INTO v FROM ...` -> a loop that builds each record in the application."""
    match = BULK_SELECT.match(statement.original_sql or "")
    if match is None:
        return None
    target = match.group("target")
    target_type = _holder_type(routine, target)
    if target_type is None or target_type.origin != "collection":
        return None
    try:
        tree = sqlglot.parse_one(f"SELECT {match.group('select')} {match.group('rest')}", dialect="oracle")
    except Exception:  # noqa: BLE001
        return None
    projections = tree.expressions if isinstance(tree, exp.Select) else []
    if len(projections) != 1:
        return None
    call = projections[0].unalias() if isinstance(projections[0], exp.Alias) else projections[0]
    name = (call.name if isinstance(call, exp.Anonymous) else "").lower()
    element = (target_type.resolved or "").removeprefix("TABLE OF ").strip().lower()
    if not name or name != element or not schema.object_record(name):
        return None
    row = _free(routine, "r_obj_", counter)
    columns: list[str] = []
    arguments: list[str] = []
    for argument in call.expressions:
        if isinstance(argument, exp.Column) and not argument.table:
            if argument.name.lower() not in columns:
                columns.append(argument.name.lower())
            arguments.append(f"{row}.{argument.name.lower()}")
        elif isinstance(argument, exp.Literal) or isinstance(argument, exp.Null):
            arguments.append(argument.sql(dialect="oracle"))
        else:
            return None   # an expression over columns: not a shape the samples need; left as SQL, refused
    if not columns:
        return None
    tree.set("expressions", [exp.column(c) for c in columns])
    query = tree.sql(dialect="oracle")
    where = statement.source_range
    operation = M.SqlOperation(id=f"{statement.id}obj", kind="SqlOperation", source_range=where, sql_kind="SELECT",
                               original_sql=query, cardinality="MANY")
    body = [M.Call(id=f"{statement.id}extend", kind="Call", source_range=where, callee=f"{target}.EXTEND", arguments=[]),
            M.Assignment(id=f"{statement.id}element", kind="Assignment", source_range=where,
                         target=f"{target}({target}.LAST)", expression=f"{name}({', '.join(arguments)})")]
    loop = M.Loop(id=f"{statement.id}loop", kind="Loop", source_range=where, loop_kind="cursor-for",
                  variable=row, query=operation, body=body, cursor=f"{row} IN ({query})")
    loop.add("INFO", "OBJECT_BUILT", f"{name}(…) は ScalarDB SQL の select list で組めないので、列を読んでアプリで "
                                     f"record を組み、{target} に足す（#54）")
    initial = M.Assignment(id=f"{statement.id}init", kind="Assignment", source_range=where, target=target,
                           expression=f"{target_type.oracle}()")
    return [initial, loop]


def _count_over_collection(statement: M.SqlOperation, routine: M.Routine, schema: OracleSchema,
                           counter: list[int]) -> list[M.Statement] | None:
    """`SELECT COUNT(*) INTO n FROM TABLE(v) [WHERE cond]` -> a loop over the application's own collection."""
    match = COUNT_TABLE.match(statement.original_sql or "")
    if match is None or (match.group("alias") or "").upper() in _RESERVED:
        return None
    collection = match.group("collection")
    collection_type = _holder_type(routine, collection)
    if collection_type is None or collection_type.origin != "collection":
        return None
    element = (collection_type.resolved or "").removeprefix("TABLE OF ").strip().lower()
    record = schema.object_record(element)
    if record is None:
        return None
    where = statement.source_range
    row = _free(routine, "v_obj_", counter)
    index = _free(routine, "i_obj_", counter)
    routine.declarations.append(M.Declaration(id=f"{statement.id}row", kind="Declaration", source_range=where,
                                              name=row, declaration_kind="variable",
                                              type=M.TypeRef(element, record, "record", None)))
    target = match.group("target")
    count = [M.Assignment(id=f"{statement.id}count", kind="Assignment", source_range=where, target=target,
                          expression=f"{target} + 1")]
    body: list[M.Statement] = [M.Assignment(id=f"{statement.id}row", kind="Assignment", source_range=where,
                                            target=row, expression=f"{collection}({index})")]
    condition = match.group("where")
    if condition:
        fields = {f for f, _ in schema.object_types[element]}
        alias = (match.group("alias") or "").lower()

        def qualify(node):
            if isinstance(node, exp.Column) and node.name.lower() in fields and \
                    (not node.table or node.table.lower() == alias):
                return exp.column(node.name.lower(), table=row)
            return node
        try:
            tree = sqlglot.parse_one(condition, dialect="oracle").transform(qualify)
        except Exception:  # noqa: BLE001
            return None
        body.append(M.If(id=f"{statement.id}if", kind="If", source_range=where,
                         branches=[M.Branch(condition=tree.sql(dialect="oracle"), body=count)]))
    else:
        body.extend(count)
    loop = M.Loop(id=f"{statement.id}loop", kind="Loop", source_range=where, loop_kind="for",
                  cursor=f"{index} IN 1 .. {collection}.COUNT", body=body)
    loop.add("INFO", "TABLE_COLLECTION", f"TABLE({collection}) はアプリが持つコレクションなので、SQL ではなくその要素を"
                                         f"回して数える（#54）")
    return [M.Assignment(id=f"{statement.id}zero", kind="Assignment", source_range=where, target=target,
                         expression="0"), loop]


def _pipelined(routine: M.Routine, schema: OracleSchema) -> None:
    """`PIPE ROW (x)` adds to a collection; `RETURN;` returns it. A PIPELINED function becomes one that returns
    the whole collection: the caller no longer reads rows as they are produced (they all exist first)."""
    from .lower import _walk

    pipes = [s for s in _walk(routine.body) if s.kind == "Unsupported" and PIPE_ROW.match(s.text or "")]
    returned = routine.return_type
    if not pipes or returned is None or returned.origin != "collection":
        return
    holder = "v_piped"
    routine.declarations.append(M.Declaration(id=f"{routine.id}#piped", kind="Declaration",
                                              source_range=routine.source_range, name=holder,
                                              declaration_kind="variable", type=returned,
                                              initial=f"{returned.oracle}()"))

    def replace(statements: list[M.Statement]) -> list[M.Statement]:
        out = []
        for statement in statements:
            for attribute in ("body", "else_body"):
                nested = getattr(statement, attribute, None)
                if nested:
                    setattr(statement, attribute, replace(nested))
            for branch in getattr(statement, "branches", []) or []:
                branch.body = replace(branch.body)
            pipe = PIPE_ROW.match(statement.text or "") if statement.kind == "Unsupported" else None
            if pipe:
                where = statement.source_range
                out += [M.Call(id=f"{statement.id}extend", kind="Call", source_range=where,
                               callee=f"{holder}.EXTEND", arguments=[]),
                        M.Assignment(id=f"{statement.id}pipe", kind="Assignment", source_range=where,
                                     target=f"{holder}({holder}.LAST)", expression=pipe.group("value").strip())]
                continue
            if statement.kind == "Return" and not statement.expression:
                statement.expression = holder
            out.append(statement)
        return out

    routine.body = replace(routine.body)
    routine.add("INFO", "PIPELINED", "PIPELINED 関数は、PIPE ROW で足した行をまとめて List で返す形にした。呼び出し側は"
                                     "行が出るそばから読むのではなく、全部そろってから受け取る（#54）")
