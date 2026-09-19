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
from .lower import _walk, walk_scoped
from .source import Issue
from .dynamic import annotate as annotate_dynamic, bind_using
from .sqlbridge import analyse as analyse_sql
from .limits import RowLocks
from .symbols import OracleSchema
from .limits import RowLocks
from .symbols import OracleSchema, SymbolTable

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
          storage: str = "jdbc", schema: "OracleSchema | None" = None,
          row_locks: "RowLocks | None" = None) -> CapabilityReport:
    """Run every SQL statement through the converter and write the answer onto the IR."""
    report = CapabilityReport()
    for module in program.modules:
        correlation = _correlation_fields(module, schema)
        for routine in module.routines:
            # each statement with the cursor FOR loops enclosing it: `r.order_id` in the body is the loop's
            # row, not a column (#10). A routine-level handler is outside every loop, so its scope is empty.
            scoped = walk_scoped(routine.body) + [(s, {}) for h in routine.exception_handlers
                                                  for s in _walk(h.body)]
            statements = [s for s, _ in scoped]
            # A routine whose read was locked (`SELECT ... FOR UPDATE`) must not have its write quietly
            # rewritten: the lock was what made the read-modify-write safe, and conversion drops it
            # (WARN ROW_LOCK). Leaving the write as something ScalarDB refuses keeps the loss visible at the
            # call site. P3-4 measured what happens without it -- one of two concurrent transactions is
            # rejected -- and that is a redesign, not a rewrite.
            locked = any(getattr(s, "locking_mode", None) for s in statements)
            if locked and (row_locks or RowLocks()).decided(routine.id):
                # #9 / 2026-09-18: この routine は**楽観制御 + 呼び出し側の再試行**へ移すと決めてある。
                # 安全なのは同じトランザクションの中で読んで書くからで、衝突は Consensus Commit が
                # 弾く（P3-4 で実測）。決めた人がいるので、拒否を続ける理由が無くなった。
                # 決めていない routine は今までどおり拒否する。
                locked = False
                for statement in statements:
                    if statement.kind == "SqlOperation" and getattr(statement, "locking_mode", None):
                        statement.add("WARN", "OPTIMISTIC",
                                      f"行ロックを落として楽観制御へ移すと決めてある"
                                      f"（{(row_locks or RowLocks()).why(routine.id)}）。"
                                      f"**弾かれた衝突を再試行するのは呼び出し側の責務**である"
                                      f"（計画 §9 / #9）")
            for statement, loops in scoped:
                loop_variables = _loop_fields(loops)
                if routine.routine_kind == "trigger-body":
                    # `:NEW.status` / `:OLD.status` are the row the trigger fired on. The target has no
                    # trigger, so the row comes from whoever calls the generated method -- the same answer
                    # #1 gave for `USER`: the caller supplies it, and the generator does not reach for an
                    # ambient value. Named here like a cursor FOR loop's row (#10), which is the machinery
                    # that already turns a qualified reference into a value the caller passes.
                    loop_variables = {**loop_variables, **correlation}
                if statement.kind == "DynamicSql":
                    # P4-7: a dynamic statement whose text is knowable becomes ordinary SQL, one per variant,
                    # and is then converted and checked like anything else. Enumerating without converting
                    # would show a reader plain SQL that nothing had looked at.
                    for index, variant in enumerate(annotate_dynamic(routine, statement) or [], start=1):
                        # `USING` は**位置で**束縛される。placeholder を渡す変数の名前に直して
                        # おくと、畳んだ文がそのあと静的な文とまったく同じ道を通る（P4-7）
                        operation = M.SqlOperation(
                            id=f"{statement.id}#variant-{index}", kind="SqlOperation",
                            source_range=statement.source_range,
                            original_sql=bind_using(variant.sql, statement.using),
                            binds=list(statement.using), into_targets=list(statement.into_targets))
                        result = analyse_sql(operation, scope=routine.id, symbols=symbols,
                                             registry=registry, storage=storage, lift=not locked,
                                             loop_variables=loop_variables)
                        statement.variant_statements.append(operation)
                        report.statuses[operation.id] = result.status
                        report.issues.extend(
                            Issue(i["severity"], i["code"], i["message"], statement.source_range)
                            for i in result.issues)
                    continue
                if statement.kind != "SqlOperation" or not statement.original_sql:
                    continue
                result = analyse_sql(statement, scope=routine.id, symbols=symbols,
                                    registry=registry, storage=storage, lift=not locked,
                                    loop_variables=loop_variables)
                report.statuses[statement.id] = result.status
                if result.access_path:
                    report.access_paths[statement.id] = result.access_path
                if result.plan is not None:
                    report.plans[statement.id] = result.plan
                report.issues.extend(
                    Issue(i["severity"], i["code"], i["message"], statement.source_range)
                    for i in result.issues if i["severity"] == "ERROR")
    return report


def _correlation_fields(module: M.Module, schema: "OracleSchema | None") -> dict[str, dict[str, str | None]]:
    """`NEW` / `OLD` と、trigger が掛かっている表の列。DDL が無ければ空（型を作れない）。"""
    if module.module_kind != "trigger" or not module.trigger_table or schema is None:
        return {}
    columns = schema.columns(module.trigger_table)
    if not columns:
        return {}
    fields = {name.lower(): oracle for name, oracle in columns.items()}
    return {"new": dict(fields), "old": dict(fields)}


def _loop_fields(loops: dict[str, M.Loop]) -> dict[str, dict[str, str | None]]:
    """What each loop variable's fields are declared as, taken from the query the loop iterates.

    The query has already been analysed when a statement in the body is reached -- `walk_scoped` yields it
    first, the same order `_walk` used -- so `into_columns` / `into_oracle_types` are filled in. The types come
    from there rather than from the DDL because that is the same pair the loop's record is generated from
    (`gen_java.repository.loop_record`), and a bind typed differently from the record it is read out of would
    not compile.
    """
    out: dict[str, dict[str, str | None]] = {}
    for name, loop in loops.items():
        query = loop.query
        if query is None:
            continue
        types = list(query.into_oracle_types or [])
        out[name] = {column: (types[i] if i < len(types) else None)
                     for i, column in enumerate(query.into_columns or []) if column}
    return out


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
    from .analysis import _called_in, _declared_names, _expressions, _names, build_call_graph

    routines = {r.id: r for m in program.modules for r in m.routines}
    module_of = {r.id: m.name for m in program.modules for r in m.routines}
    declared = {r.id: _declared_names(m, r) for m in program.modules for r in m.routines}
    by_name = _names(program)
    build_call_graph(program)   # resolves each Call to the routine it names; this runs before the program analysis

    def called(routine_id: str, expressions: list[str]) -> list[str]:
        return sorted({c for e in expressions for c in _called_in(e, by_name, module_of[routine_id], routine_id, declared[routine_id])})

    def callees(routine_id: str, statement: M.Statement) -> list[str]:
        """What the statement runs: the routine a Call names, and the functions its expressions call. A function
        is usually called from an assignment or a condition, so following Call statements alone missed its write."""
        direct = [statement.resolved_to] if statement.kind == "Call" and getattr(statement, "resolved_to", None) else []
        return direct + [c for c in called(routine_id, _expressions(statement)) if c not in direct]

    def initialisers(routine: M.Routine) -> list[str]:
        """The functions the declarations call: `v_n NUMBER := f(p_id);` runs before the first statement."""
        return called(routine.id, [d.initial for d in routine.declarations
                                   if d.initial and d.declaration_kind != "cursor"])

    def scans(statement: M.Statement, written: set[str]) -> list[str]:
        """The tables in `written` that the statement reads other than by key."""
        if statement.kind == "SqlOperation" and is_key_access(report.access_paths.get(statement.id)):
            return []   # key access after a write is fine
        return [t for t in statement.read_set if t in written]

    def everything(routine: M.Routine) -> list[M.Statement]:
        return _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]

    def reachable(routine_id: str, seen: set[str]) -> list[M.Routine]:
        """The routine and what it calls, transitively. A callee writes and scans inside the caller's
        transaction, so its statements count as the caller's."""
        routine = routines.get(routine_id)
        if routine is None or routine_id in seen:
            return []
        seen.add(routine_id)
        out = [routine]
        for callee in initialisers(routine) + [c for s in everything(routine) for c in callees(routine_id, s)]:
            out += reachable(callee, seen)
        return out

    def run(routine_id: str, sql_id: str, callee_ids: list[str], written: set[str]) -> None:
        """The callees' statements, as if they were the caller's: they run inside its transaction."""
        for callee_id in callee_ids:
            for callee in reachable(callee_id, set()):
                for inner in everything(callee):
                    # reported on the caller's statement: that is the one of this routine which does the scanning
                    found.extend((routine_id, table, sql_id) for table in scans(inner, written))
                    written.update(inner.write_set)

    def visit(routine_id: str, statements: list[M.Statement], written: set[str]) -> None:
        for statement in statements:
            query = getattr(statement, "query", None)
            if query is not None:
                # a cursor FOR loop opens its query once, before the first iteration: the body's writes come
                # after it, so it is checked against what was written before the loop only
                found.extend((routine_id, table, query.id) for table in scans(query, written))
            if statement.kind == "Loop":
                # the back edge: the second iteration runs the top of the body after the writes at its bottom
                for inner in _walk(statement.body):
                    written.update(inner.write_set)
                    written.update(t for c in callees(routine_id, inner) for r in reachable(c, set())
                                   for s in everything(r) for t in s.write_set)
            found.extend((routine_id, table, statement.id) for table in scans(statement, written))
            run(routine_id, statement.id, callees(routine_id, statement), written)
            written.update(statement.write_set)
            for branch in getattr(statement, "branches", []) or []:
                visit(routine_id, branch.body, written)
            visit(routine_id, getattr(statement, "else_body", []) or [], written)
            visit(routine_id, getattr(statement, "body", []) or [], written)
            for handler in getattr(statement, "exception_handlers", []) or []:
                visit(routine_id, handler.body, written)

    for module in program.modules:
        for routine in module.routines:
            written: set[str] = set()
            if routine.body:
                run(routine.id, routine.body[0].id, initialisers(routine), written)
            visit(routine.id, routine.body, written)
            # a handler runs after whatever part of the body ran before the exception
            for handler in routine.exception_handlers:
                visit(routine.id, handler.body, written)
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
        if statement.at_most_one_row:
            continue   # `LIMIT 1` or a bare aggregate: there is no second row to raise TOO_MANY_ROWS with
        if any(d.code == "MULTI_ROW_INTO" for d in statement.diagnostics):
            continue
        statement.add("WARN", "MULTI_ROW_INTO",
                      "SELECT INTO does not reach its row by key, so it can match several and raise "
                      "TOO_MANY_ROWS")
