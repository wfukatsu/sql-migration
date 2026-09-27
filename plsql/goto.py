"""#139: GOTO, rebuilt as the structured jumps Java has.

PL/SQL's GOTO cannot go into an IF, a LOOP or a block (PLS-00375): its label is a statement of the sequence the
GOTO is in, or of a sequence around it. That is what makes it structured in disguise. Seen from the label's
sequence, the GOTO sits inside one statement of it (the "holder"), and

* a forward GOTO (the label after the holder) skips the statements between: they become a labelled block that the
  GOTO leaves, `L: { holder ... ; break L; ... }` -- IR `Block(jump_label=L)` and `Exit(label=L)`;
* a backward GOTO (the label on the holder or before it) runs them again: they become a loop the GOTO continues,
  `L: while (true) { ... continue L; ... break L; }` -- IR `Loop(basic, label=L)`, `Continue(label=L)` and an
  `Exit(label=L)` at the end.

Several GOTOs of one sequence give several such ranges, and Java needs them nested. A forward range may start
earlier (the break still lands after it) and a backward one may end later (the loop still runs what follows once),
so crossing ranges are widened until they nest. Only a forward range that starts before a backward one it ends
inside cannot be: that GOTO stays a `Goto`, with the reason, and LOWER-002 still asks a person.

This runs on the lowered routine, before any analysis reads it, so the rules and the generator see only the
structured form -- a GOTO that is rebuilt is no longer a GOTO anywhere downstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ir import model as M
from .source import SourceRange


@dataclass
class _Range:
    label: str            # the PL/SQL label
    forward: bool
    start: int
    end: int              # inclusive
    gotos: list = field(default_factory=list)
    name: str = ""        # the label of the Java block or loop


def rewrite(routine: M.Routine) -> None:
    """Rebuild every GOTO of `routine` that can be; mark the others with why not."""
    if not any(s.kind == "Goto" for s in _all(routine)):
        return
    # a new block or loop takes the GOTO's label as its Java label, unless a loop already has it
    taken = {(s.label or "").lower() for s in _all(routine) if s.kind == "Loop" and s.label}
    context = _Context(routine=routine, taken=taken)
    # which sequence each GOTO's label is in: the innermost enclosing one that has it (PL/SQL's visibility)
    _resolve(routine.body, {}, context)
    for handler in routine.exception_handlers:
        # a routine's own handler has no sequence around it to jump to
        _resolve(handler.body, {}, context)
    _restructure(routine.body, None, context)
    for handler in routine.exception_handlers:
        _restructure(handler.body, None, context)
    for node in context.unresolved:
        node.add("WARN", "GOTO_NOT_RESTRUCTURED",
                 f"GOTO {node.label}: {context.why.get(id(node), 'cannot be rebuilt as a block or a loop')}")


@dataclass
class _Context:
    routine: M.Routine
    taken: set[str]
    target: dict[int, int] = field(default_factory=dict)       # id(goto) -> id(sequence of its label)
    unresolved: list = field(default_factory=list)
    why: dict[int, str] = field(default_factory=dict)
    counter: int = 0

    def fresh(self, base: str) -> str:
        from .gen_java.types import java_name

        java = {java_name(t) for t in self.taken if t}
        name = base
        n = 2
        while name in self.taken or java_name(name) in java:
            name = f"{base}_{n}"
            n += 1
        self.taken.add(name)
        return name


def _sequences(statement: M.Statement):
    """The statement lists directly under a statement, each with whether it is an exception handler's."""
    for branch in getattr(statement, "branches", None) or []:
        yield branch.body, False
    if getattr(statement, "else_body", None):
        yield statement.else_body, False
    if getattr(statement, "body", None) is not None and statement.kind in ("Loop", "Block"):
        yield statement.body, False
    for handler in getattr(statement, "exception_handlers", None) or []:
        yield handler.body, True


def _resolve(statements: list[M.Statement], visible: dict[str, int], context: _Context) -> None:
    here = dict(visible)
    for statement in statements:
        for label in statement.labels or []:
            here[label.lower()] = id(statements)
    for statement in statements:
        if statement.kind == "Goto":
            target = here.get((statement.label or "").lower())
            if target is None:
                context.unresolved.append(statement)
                context.why[id(statement)] = (
                    "its label is not in this sequence of statements nor in one around it -- inside an IF, a LOOP "
                    "or a block, or not followed by a statement -- which Oracle refuses too (PLS-00375)")
            else:
                context.target[id(statement)] = target
        for inner, _ in _sequences(statement):
            _resolve(inner, here, context)


def _restructure(statements: list[M.Statement], loop: "M.Loop | None", context: _Context) -> None:
    """Inner sequences first, then the ranges of the GOTOs whose label is in this one. `loop` is the innermost loop
    around this sequence: an EXIT without a label inside a new loop has to keep leaving that one."""
    for statement in statements:
        for inner, _ in _sequences(statement):
            _restructure(inner, statement if statement.kind == "Loop" else loop, context)
    mine = [(node, index) for index, top in enumerate(statements) for node in _walk([top])
            if node.kind == "Goto" and context.target.get(id(node)) == id(statements)]
    if not mine:
        return
    ranges: dict[tuple[str, bool], _Range] = {}
    for node, holder in mine:
        label = node.label.lower()
        at = next(i for i, s in enumerate(statements) if label in [x.lower() for x in s.labels or []])
        forward = at > holder
        start, end = (holder, at - 1) if forward else (at, holder)
        found = ranges.get((label, forward))
        if found is None:
            ranges[(label, forward)] = _Range(label, forward, start, end, [node])
        else:
            found.start, found.end = min(found.start, start), max(found.end, end)
            found.gotos.append(node)
    spans = sorted(ranges.values(), key=lambda r: (r.start, -r.end, r.forward))
    if not _nest(spans):
        for span in spans:
            for node in span.gotos:
                context.unresolved.append(node)
                context.why[id(node)] = (
                    "its range crosses another GOTO's: a forward GOTO that starts before a backward GOTO's label and "
                    "lands inside the statements that GOTO repeats, which no nesting of blocks and loops expresses")
        return
    for span in spans:
        span.name = context.fresh(span.label)
    statements[:] = _build(statements, 0, len(statements) - 1, spans, loop, context)


def _nest(spans: list[_Range]) -> bool:
    """Widen crossing ranges until they nest: a forward one's start moves up, a backward one's end moves down."""
    changed = True
    while changed:
        changed = False
        for a in spans:
            for b in spans:
                if a is b or not (a.start < b.start <= a.end < b.end):
                    continue
                if b.forward:
                    b.start = a.start
                elif not a.forward:
                    a.end = b.end
                else:
                    return False
                changed = True
    return True


def _build(statements, low, high, spans, loop, context) -> list[M.Statement]:
    out: list[M.Statement] = []
    ordered = sorted(spans, key=lambda r: (r.start, -r.end, r.forward))
    index = low
    while index <= high:
        top = next((s for s in ordered if s.start == index), None)
        if top is None:
            out.append(statements[index])
            index += 1
            continue
        inner = [s for s in ordered if s is not top and top.start <= s.start and s.end <= top.end]
        ordered = [s for s in ordered if s is not top and s not in inner]
        body = _build(statements, top.start, top.end, inner, loop, context)
        out.append(_wrap(top, body, loop, context))
        index = top.end + 1
    return out


def _wrap(span: _Range, body: list[M.Statement], loop: "M.Loop | None", context: _Context) -> M.Statement:
    first = body[0]
    source = first.source_range
    context.counter += 1
    ident = f"{context.routine.id}#goto-{context.counter}"
    if span.forward:
        for node in span.gotos:
            _jump(node, "Exit", span.name, "forward")
        return M.Block(id=ident, kind="Block", source_range=source, body=body, jump_label=span.name)
    # an EXIT or CONTINUE without a label that meant the loop around this sequence would now mean the new loop
    plain = [s for s in _outside_loops(body) if s.kind in ("Exit", "Continue") and not s.label]
    if plain and loop is not None:
        if not loop.label:
            loop.label = context.fresh("loop")
        for node in plain:
            node.label = loop.label
    for node in span.gotos:
        _jump(node, "Continue", span.name, "backward")
    last = source if body[-1].source_range is None else body[-1].source_range
    leave = M.ControlStatement(id=f"{ident}.exit", kind="Exit", source_range=last, label=span.name)
    node = M.Loop(id=ident, kind="Loop", source_range=_span(source, last), loop_kind="basic", label=span.name,
                  body=body + [leave])
    node.add("INFO", "GOTO_LOOP", f"statements a backward GOTO {span.label} repeats, as a loop (#139)")
    return node


def _jump(node: M.ControlStatement, kind: str, name: str, direction: str) -> None:
    written = node.label
    node.kind = kind
    node.label = name
    node.diagnostics = [d for d in node.diagnostics if d.code != "GOTO"]
    node.add("INFO", "GOTO_RESTRUCTURED",
             f"GOTO {written} ({direction}) is {'EXIT' if kind == 'Exit' else 'CONTINUE'} {name} of the "
             f"{'block' if kind == 'Exit' else 'loop'} it was rebuilt as (#139)")


def _span(first: SourceRange | None, last: SourceRange | None) -> SourceRange | None:
    if first is None or last is None:
        return first or last
    return SourceRange(first.file, first.start_line, last.end_line, first.start_column, last.end_column)


def _outside_loops(statements: list[M.Statement]) -> list[M.Statement]:
    """The statements under `statements`, not looking into a loop (an EXIT there is that loop's)."""
    out = []
    for statement in statements:
        out.append(statement)
        if statement.kind == "Loop":
            continue
        for inner, _ in _sequences(statement):
            out.extend(_outside_loops(inner))
    return out


def _walk(statements: list[M.Statement]) -> list[M.Statement]:
    out = []
    for statement in statements:
        out.append(statement)
        for inner, _ in _sequences(statement):
            out.extend(_walk(inner))
    return out


def _all(routine: M.Routine) -> list[M.Statement]:
    return _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]

