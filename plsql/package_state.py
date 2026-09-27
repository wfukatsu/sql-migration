"""#46: package variables the caller carries (limits.yaml `packageState.carried`).

`g_calls PLS_INTEGER := 0` in a package body is session state: Oracle keeps one per session. The generated
Service is one object for every caller, so a field would be one counter for the process -- STATE-001 refuses
that. When the project decides that the caller carries the state, every routine that touches the variable
(directly, or through a callee that does) gets it as an IN OUT parameter of the same name, added here before
generation. The generator then treats it like any IN OUT argument: it comes in through the signature and goes
back in the result record, and a call inside the package passes the caller's own copy along.
"""
from __future__ import annotations

import re

from .ir import model as M
from .limits import PackageState
from .lower import _walk


def carry(program: M.Program, decided: PackageState | None) -> None:
    if decided is None or not decided.carried:
        return
    for module in program.modules:
        if module.module_kind != "package" or not decided.decided(module.name):
            continue
        variables = [d for d in module.declarations if d.declaration_kind == "variable"]
        for variable in variables:
            carriers = {r.id for r in module.routines if _mentions(r, variable.name)}
            # #141: a call that leaves an argument to `DEFAULT g_level` reads the variable where the call is: the
            # caller passes its own copy, so it carries the variable too
            everywhere = [r for m in program.modules for r in m.routines]
            carriers |= {r.id for r in everywhere if _omits_a_default_reading(r, module, variable.name)}
            # a routine that calls a carrier carries too: it has to pass the value along and take it back. In any
            # module: a standalone procedure that calls `emp_api.give_raise` carries g_calls as well (found with
            # samples/oracle-samples b05_3, 2026-09-25 -- only the package's own routines were looked at)
            everywhere = [r for m in program.modules for r in m.routines]
            changed = True
            while changed:
                changed = False
                for routine in everywhere:
                    if routine.id in carriers:
                        continue
                    if any(s.kind == "Call" and s.resolved_to in carriers for s in _statements(routine)):
                        carriers.add(routine.id)
                        changed = True
            for routine in everywhere:
                if routine.id not in carriers or any(p.name.lower() == variable.name.lower() for p in routine.parameters):
                    continue
                routine.parameters.append(M.Parameter(
                    id=f"{routine.id}#{variable.name}", kind="Parameter", source_range=variable.source_range,
                    name=variable.name, direction="IN OUT", type=variable.type, default=variable.initial, carried=True))
                routine.add("INFO", "STATE_CARRIED",
                            f"package 変数 {variable.name} は呼び出し側が運ぶ（limits.yaml packageState.carried: "
                            f"{decided.why(module.name)}）。IN OUT 引数として受け取り、結果で返す")


def _omits_a_default_reading(caller: M.Routine, module: M.Module, name: str) -> bool:
    """Whether `caller` calls a routine of `module` and leaves out an argument whose DEFAULT reads the variable."""
    from .gen_java.service import NAMED_ARGUMENT, _closing_paren, _split_arguments

    reading = re.compile(rf"(?<![\w$#]){re.escape(name)}(?![\w$#(])", re.IGNORECASE)
    for callee in module.routines:
        wanted = [(i, p) for i, p in enumerate(callee.parameters)
                  if p.default and not p.carried and reading.search(re.sub(r"'(?:[^']|'')*'", "''", p.default))]
        if not wanted or callee is caller:
            continue
        calls: list[list[str]] = []
        for statement in _statements(caller):
            if statement.kind == "Call" and getattr(statement, "resolved_to", None) == callee.id:
                calls.append(list(statement.arguments))
        # a call inside an expression: `pkg.f(...)`, `f(...)` inside the package, or `pkg.f` with no arguments
        spelled = re.compile(rf"(?<![\w$#.])(?:{re.escape(module.name)}\.)?{re.escape(callee.name)}(?![\w$#])",
                             re.IGNORECASE)
        for statement in _statements(caller):
            for value in vars(statement).values():
                for text in ([value] if isinstance(value, str) else value if isinstance(value, list) else []):
                    if not isinstance(text, str) or statement.kind == "Call" and text == statement.callee:
                        continue
                    bare = re.sub(r"'(?:[^']|'')*'", lambda m: " " * len(m.group()), text)
                    for match in spelled.finditer(bare):
                        rest = bare[match.end():].lstrip()
                        if not rest.startswith("("):
                            calls.append([])
                            continue
                        opening = match.end() + (len(bare[match.end():]) - len(rest))
                        close = _closing_paren(text, opening)
                        calls.append(_split_arguments(text[opening + 1:close]) if close is not None else [])
        for arguments in calls:
            positional = [a for a in arguments if not NAMED_ARGUMENT.match(a)]
            named = {a.partition("=>")[0].strip().lower() for a in arguments if NAMED_ARGUMENT.match(a)}
            if any(i >= len(positional) and p.name.lower() not in named for i, p in wanted):
                return True
    return False


def _statements(routine: M.Routine) -> list[M.Statement]:
    return _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]


def _mentions(routine: M.Routine, name: str) -> bool:
    pattern = re.compile(rf"(?<![\w$#.]){re.escape(name)}(?![\w$#(])", re.IGNORECASE)
    for statement in _statements(routine):
        for value in vars(statement).values():
            if isinstance(value, str) and pattern.search(value):
                return True
            if isinstance(value, list) and any(isinstance(v, str) and pattern.search(v) for v in value):
                return True
        for branch in getattr(statement, "branches", []) or []:
            if isinstance(getattr(branch, "condition", None), str) and pattern.search(branch.condition):
                return True
    return any(d.initial and pattern.search(d.initial) for d in routine.declarations)
