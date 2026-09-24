"""Does the anonymised tree make the tool conclude what the original did?

If it does not, the KPIs measured on the anonymised code are not about the original, and adding it to the corpus
would be adding noise with a convincing label. Compared per routine, and in shape only -- no name and no literal is
part of what is compared, so the comparison itself says nothing about the original:

* what the routine is, how many parameters and declarations it has;
* each statement from the source: its kind, the SQL kind, how many tables it reads and writes, its lock, the
  diagnostics on it;
* the transaction and external effects;
* the rules that fired, and the verdict before any evidence.
"""

from __future__ import annotations

from pathlib import Path

from ..analysis import analyse as analyse_program
from ..report import analyse
from ..rules.engine import RuleSet, decide
from . import Mapping


def _schema(root: Path) -> str | None:
    found = root / "schema.sql"
    return str(found) if found.exists() else None


def _statements(nodes, out: list) -> None:
    for node in nodes or []:
        diagnostics = sorted(d.code for d in getattr(node, "diagnostics", []) or [])
        out.append((node.kind, getattr(node, "sql_kind", None), len(getattr(node, "read_set", None) or []),
                    len(getattr(node, "write_set", None) or []), getattr(node, "locking_mode", None),
                    tuple(diagnostics)))
        for key in ("body", "else_body", "exception_handlers"):
            _statements(getattr(node, key, None), out)
        for branch in getattr(node, "branches", None) or []:
            _statements(branch.body, out)
        query = getattr(node, "query", None)
        if query is not None:
            _statements([query], out)


def shapes(root: str | Path) -> dict[str, dict]:
    root = Path(root)
    analysis = analyse(str(root), _schema(root))
    program = analyse_program(analysis.program)
    decisions = decide(analysis.program, program, RuleSet.load(), None)
    failed = {Path(f).name for f in [p.file for p in analysis.parsed if not p.ok]}
    out = {}
    for module in analysis.program.modules:
        for routine in module.routines:
            statements: list = []
            _statements(routine.body, statements)
            _statements(routine.exception_handlers, statements)
            decision = decisions.get(routine.id)
            effects = routine.transaction_effects
            out[routine.id.upper()] = {
                "kind": routine.routine_kind, "parameters": len(routine.parameters),
                "declarations": len(routine.declarations), "statements": statements,
                "transaction": (effects.commits, effects.rollbacks, effects.savepoints, effects.autonomous),
                "dynamicSql": routine.external_effects.dynamic_sql,
                "rules": sorted(m.rule.id for m in decision.matches) if decision else None,
                "verdict": decision.rule_verdict if decision else None,
            }
    return {"routines": out, "failedFiles": len(failed), "files": len(analysis.parsed)}


def _renamed(routine_id: str, mapping: Mapping) -> str:
    """The id the routine has after anonymising. Looked up, never assigned: a name the lexer reads as a keyword
    (`cancel`, `purge`) was not replaced, and is the same on both sides. `~2` marks an overload and is kept."""
    def part(text: str) -> str:
        base, _, overload = text.partition("~")
        return mapping.names.get(base, base).upper() + (f"~{overload}" if overload else "")
    return ".".join(part(text) for text in routine_id.split("."))


def compare(original: str | Path, anonymised: str | Path, mapping: Mapping) -> list[str]:
    """What differs, in words that name only the anonymised side."""
    before, after = shapes(original), shapes(anonymised)
    problems = []
    if (before["files"], before["failedFiles"]) != (after["files"], after["failedFiles"]):
        problems.append(f"files parsed: the original has {before['files']} files and {before['failedFiles']} that do "
                        f"not parse; the anonymised tree has {after['files']} and {after['failedFiles']}")
    expected = {_renamed(routine, mapping): shape for routine, shape in before["routines"].items()}
    for routine in sorted(set(expected) | set(after["routines"])):
        mine, theirs = expected.get(routine), after["routines"].get(routine)
        if mine is None or theirs is None:
            problems.append(f"{routine.lower()}: only in the {'anonymised' if mine is None else 'original'} tree")
            continue
        for key in mine:
            if mine[key] != theirs[key]:
                problems.append(f"{routine.lower()}: {key} differs -- original {mine[key]!r}, anonymised {theirs[key]!r}"
                                if key != "statements" else
                                f"{routine.lower()}: statements differ -- " + _first_difference(mine[key], theirs[key]))
    return problems


def _first_difference(mine: list, theirs: list) -> str:
    if len(mine) != len(theirs):
        return f"{len(mine)} in the original, {len(theirs)} in the anonymised tree"
    for index, (a, b) in enumerate(zip(mine, theirs), 1):
        if a != b:
            return f"statement {index}: original {a!r}, anonymised {b!r}"
    return "?"
