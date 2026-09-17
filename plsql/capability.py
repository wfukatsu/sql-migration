"""P2-4: what ScalarDB can actually run, decided per statement and fed back into the verdict.

Everything before this point judges PL/SQL on its own terms. This is where the target gets a say: each SQL
statement goes through the existing converter (P1-6), and what comes back -- a verdict, an access path, a plan --
lands on the IR node where the rules can see it.

    check(program, registry, symbols)      # fills target_status / access_path / plan on every SqlOperation
    decide(program, analysis, ruleset, evidence)   # SQL-001 / SQL-002 / SCAN-001 can now fire

## Why this is not a dialect implementation

The design document (§7.3) rejects writing a full ScalarDB dialect up front, because a best-effort translation
that silently approximates is worse than a refusal. The converter already works that way -- an unsupported
construct is an `ERROR`, not an approximation -- so this module only has to carry its answer, not repeat its
judgement.

## The one thing only this layer can decide

Write-then-scan (AUTO prohibition 11) needs the access path. P2-1 reports every routine that writes a table and
then reads it, which is a superset: reading by key after writing is allowed, scanning is not. The access path
comes from the converter, so narrowing the superset to the real restriction happens here and nowhere else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scalardb_migrate.schema import SchemaRegistry

from .analysis import ProgramAnalysis
from .ir import model as M
from .lower import _walk
from .source import Issue
from .sqlbridge import analyse as analyse_sql
from .symbols import SymbolTable

# the converter's own words for an access that does not need a scan
KEY_ACCESS = re.compile(r"->\s*(GET|partition SCAN)", re.IGNORECASE)
SCAN_ACCESS = re.compile(r"->\s*(index SCAN|cross-partition SCAN)", re.IGNORECASE)


@dataclass
class CapabilityReport:
    """What the target said about each statement, and what that means for the routine."""

    statuses: dict[str, str] = field(default_factory=dict)          # sql id -> OK | WARN | PLANNED | ERROR
    access_paths: dict[str, str] = field(default_factory=dict)      # sql id -> the converter's message
    plans: dict[str, dict] = field(default_factory=dict)            # sql id -> plan.json
    issues: list[Issue] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for status in self.statuses.values():
            out[status] = out.get(status, 0) + 1
        return out

    def rate(self) -> float:
        """Statements ScalarDB SQL can run directly, as a share of all of them."""
        if not self.statuses:
            return 1.0
        runnable = sum(1 for s in self.statuses.values() if s in ("OK", "WARN"))
        return runnable / len(self.statuses)


def check(program: M.Program, registry: SchemaRegistry, symbols: SymbolTable | None = None,
          storage: str = "jdbc") -> CapabilityReport:
    """Run every SQL statement through the converter and write the answer onto the IR."""
    report = CapabilityReport()
    for module in program.modules:
        for routine in module.routines:
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            # A routine whose read was locked (`SELECT ... FOR UPDATE`) must not have its write quietly
            # rewritten: the lock was what made the read-modify-write safe, and conversion drops it
            # (WARN ROW_LOCK). Leaving the write as something ScalarDB refuses keeps the loss visible at the
            # call site. P3-4 measured what happens without it -- one of two concurrent transactions is
            # rejected -- and that is a redesign, not a rewrite.
            locked = any(getattr(s, "locking_mode", None) for s in statements)
            for statement in statements:
                if statement.kind != "SqlOperation" or not statement.original_sql:
                    continue
                result = analyse_sql(statement, scope=routine.id, symbols=symbols,
                                    registry=registry, storage=storage, lift=not locked)
                report.statuses[statement.id] = result.status
                if result.access_path:
                    report.access_paths[statement.id] = result.access_path
                if result.plan is not None:
                    report.plans[statement.id] = result.plan
                report.issues.extend(
                    Issue(i["severity"], i["code"], i["message"], statement.source_range)
                    for i in result.issues if i["severity"] == "ERROR")
    return report


def is_key_access(access_path: str | None) -> bool:
    """A GET or a partition scan reaches rows by key; an index or cross-partition scan does not.

    This is the distinction ScalarDB's write-then-scan restriction turns on, and the converter already states it
    in the access-path message, so it is read here rather than re-derived.
    """
    if not access_path:
        return False
    return bool(KEY_ACCESS.search(access_path)) and not SCAN_ACCESS.search(access_path)


def scan_after_write(program: M.Program, report: CapabilityReport) -> list[tuple[str, str, str]]:
    """(routine, table, sql id) where a scan reads a table this routine has already written.

    P2-1 answers the coarse question -- does the routine write and then read the same table -- and this narrows it
    with the access path. Reading by key after writing is allowed (measured in P2-9); scanning is not.
    """
    found: list[tuple[str, str, str]] = []
    for module in program.modules:
        for routine in module.routines:
            written: set[str] = set()
            for statement in _walk(routine.body):
                for table in statement.read_set:
                    if table not in written:
                        continue
                    if statement.kind == "SqlOperation" and \
                            is_key_access(report.access_paths.get(statement.id)):
                        continue  # key access after a write is fine
                    found.append((routine.id, table, statement.id))
                written.update(statement.write_set)
    return sorted(set(found))


def annotate(program: M.Program, report: CapabilityReport) -> None:
    """Mark the statements that scan a table the routine already wrote.

    The diagnostic is what the rule matches on, so the decision stays in the rule file: this layer states the
    fact, `rules/scalardb_capability.yaml` says what it means.
    """
    by_id = {s.id: s for m in program.modules for r in m.routines
             for s in _walk(r.body) + [x for h in r.exception_handlers for x in _walk(h.body)]}
    for _, table, sql_id in scan_after_write(program, report):
        statement = by_id.get(sql_id)
        if statement is None:
            continue
        if any(d.code == "SCAN_AFTER_WRITE" for d in statement.diagnostics):
            continue
        statement.add("ERROR", "SCAN_AFTER_WRITE",
                      f"{table} was written earlier in this transaction; ScalarDB refuses to scan it")

    # A `SELECT INTO` that does not reach its row by key can match more than one, and Oracle raises
    # TOO_MANY_ROWS when it does. Telling that from a key lookup needs the access path, which is why this is the
    # layer that can say it -- P2-2 left the case open for exactly this reason.
    for statement in by_id.values():
        if statement.kind != "SqlOperation" or not statement.into_targets:
            continue
        if is_key_access(report.access_paths.get(statement.id)):
            continue
        if any(d.code == "MULTI_ROW_INTO" for d in statement.diagnostics):
            continue
        statement.add("WARN", "MULTI_ROW_INTO",
                      "SELECT INTO does not reach its row by key, so it can match several and raise "
                      "TOO_MANY_ROWS")
