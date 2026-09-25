"""#48: a function with OUT / IN OUT arguments called inside an expression is hoisted into a statement.

    DBMS_OUTPUT.PUT_LINE('count: ' || emp_api.call_count);      -- call_count carries g_calls (IN OUT, #46)

    -> v_call_1 := emp_api.call_count(g_calls);                  -- a Call statement with `into`
       DBMS_OUTPUT.PUT_LINE('count: ' || v_call_1);

The generated Java takes a callee's OUT arguments back through a result record, which a call statement can
unpack and an expression cannot (`service._call`). So the call is moved in front of the statement, into a local of
the callee's return type, and the expression reads the local. Found with samples/oracle-samples b05_3 (2026-09-25).

Only positions where the call was going to run exactly once, before the statement, are rewritten: an
assignment's expression, a call statement's arguments, a RAISE message, a RETURN expression and the first
condition of an IF. An ELSIF condition or a loop condition runs conditionally or repeatedly, and hoisting would
change when the OUT arguments are written; those stay refused by the generator, with the reason.
"""

from __future__ import annotations

import re

from .ir import model as M
from .lower import overload_of

NAME = re.compile(r"(?<![\w$#.:])([A-Za-z][\w$#]*(?:\.[A-Za-z][\w$#]*)?)")
_LITERAL = re.compile(r"'(?:[^']|'')*'")
PREFIX = "v_call_"


def rewrite(program: M.Program) -> None:
    modules = {m.name.lower(): m for m in program.modules}
    for module in program.modules:
        for routine in module.routines:
            counter = [0]
            routine.body = _sequence(routine.body, routine, module, modules, counter)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, routine, module, modules, counter)


def _sequence(statements: list[M.Statement], routine: M.Routine, module: M.Module,
              modules: dict[str, M.Module], counter: list[int]) -> list[M.Statement]:
    out: list[M.Statement] = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, module, modules, counter))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, module, modules, counter)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, routine, module, modules, counter)
        out.extend(_hoisted(statement, routine, module, modules, counter))
        out.append(statement)
    return out


def _hoisted(statement: M.Statement, routine: M.Routine, module: M.Module, modules: dict[str, M.Module],
             counter: list[int]) -> list[M.Statement]:
    before: list[M.Statement] = []

    def process(text: str | None) -> str | None:
        if not text:
            return text
        return _replace(text, statement, routine, module, modules, counter, before)

    if statement.kind == "Assignment":
        statement.expression = process(statement.expression)
    elif statement.kind == "Call":
        statement.arguments = [process(a) for a in statement.arguments]
    elif statement.kind == "Raise":
        statement.message = process(statement.message)
    elif statement.kind == "Return":
        statement.expression = process(statement.expression)
    elif statement.kind == "If" and statement.branches:
        statement.branches[0].condition = process(statement.branches[0].condition)
    return before


def _callee(name: str, routine: M.Routine, module: M.Module, modules: dict[str, M.Module]) -> M.Routine | None:
    """The routine a name in an expression means, when it is a function with OUT / IN OUT arguments."""
    owner, _, bare = name.rpartition(".")
    if owner:
        target = modules.get(owner.lower())
    else:
        declared = {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}
        target = None if bare.lower() in declared else module
    if target is None:
        return None
    callee = next((r for r in target.routines if r.name.lower() == bare.lower() and overload_of(r) is None), None)
    if callee is None or callee.return_type is None:
        return None
    if not any(p.direction in ("OUT", "IN OUT") for p in callee.parameters):
        return None
    return callee


def _replace(text: str, statement: M.Statement, routine: M.Routine, module: M.Module,
             modules: dict[str, M.Module], counter: list[int], before: list[M.Statement]) -> str:
    masked = _LITERAL.sub(lambda m: "'" + "x" * (len(m.group(0)) - 2) + "'", text)
    out: list[str] = []
    position = 0
    for match in NAME.finditer(masked):
        if match.start() < position:
            continue
        callee = _callee(match.group(1), routine, module, modules)
        if callee is None:
            continue
        end = match.end()
        arguments: list[str] = []
        rest = masked[end:]
        if rest.lstrip().startswith("("):
            open_at = end + (len(rest) - len(rest.lstrip()))
            close_at = _matching(masked, open_at)
            if close_at is None:
                continue
            arguments = _split(text[open_at + 1:close_at], masked[open_at + 1:close_at])
            end = close_at + 1
        counter[0] += 1
        variable = _free_name(routine, counter[0])
        routine.declarations.append(M.Declaration(
            id=f"{statement.id}#decl-{variable}", kind="Declaration", source_range=statement.source_range,
            name=variable, declaration_kind="variable", type=callee.return_type))
        call = M.Call(id=f"{statement.id}hoist{counter[0]}", kind="Call", source_range=statement.source_range,
                      callee=match.group(1), resolved_to=callee.id, arguments=arguments, into=variable)
        call.add("INFO", "CALL_HOISTED",
                 f"{match.group(1)} は OUT / IN OUT 引数のある関数なので、式の中から文に出した。戻り値は {variable} に受け、"
                 f"OUT 引数は結果 record から受ける（#48）")
        before.append(call)
        out.append(text[position:match.start()])
        out.append(variable)
        position = end
    out.append(text[position:])
    return "".join(out)


def _matching(masked: str, open_at: int) -> int | None:
    depth = 0
    for index in range(open_at, len(masked)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _split(inner: str, masked_inner: str) -> list[str]:
    parts: list[str] = []
    depth, start = 0, 0
    for index, character in enumerate(masked_inner):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(inner[start:index].strip())
            start = index + 1
    tail = inner[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _free_name(routine: M.Routine, index: int) -> str:
    used = {d.name.lower() for d in routine.declarations} | {p.name.lower() for p in routine.parameters}
    while f"{PREFIX}{index}" in used:
        index += 1
    return f"{PREFIX}{index}"
