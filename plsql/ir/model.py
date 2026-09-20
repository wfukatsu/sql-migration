"""P1-4: the Migration IR.

The IR is the contract between the PL/SQL side and everything downstream: analysis (P2-1), the rules (P2-2) and
the generators (P2-5..P2-7) read it, and none of them may need the parse tree. Two design rules from the design
document (§5.1) are load-bearing and every node here obeys them:

* **Oracle meaning is kept, not erased.** A `COMMIT` is a node, not a missing feature; `%TYPE` keeps the column it
  came from. Deciding what to do about them is the rule engine's job, later, with the evidence still attached.
* **Nothing points into sqlglot or ANTLR.** A `SqlOperation` holds the SQL text and a stable, serialised
  description of what was found in it, never a library object. The IR has to survive being written to disk and
  read back by a different version of the tool -- that is what `SCHEMA_VERSION` is for.

Every node carries `id`, `source_range`, `type`, `confidence` and `diagnostics`, so any of them can be pointed at
in a report and traced back to the line the developer wrote.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..source import Issue, SourceRange

# 1.1.0: P3-1 added what each bind and select item was attributed to (column, ScalarDB type, declared Oracle
# type) and whether the SELECT asked for a star. All optional, so a 1.0.0 reader still reads a 1.1.0 document.
SCHEMA_VERSION = "1.1.0"

# --- verdict / capability vocabularies (shared with docs/design/plsql-kpi.md) -------------------------------
VERDICTS = ("AUTO", "REVIEW", "REDESIGN")
CARDINALITIES = ("EXACTLY_ONE", "AT_MOST_ONE", "MANY", "NONE", "UNKNOWN")
SQL_KINDS = ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "DDL", "UNKNOWN")


@dataclass
class TypeRef:
    """A type as the source wrote it, plus what it resolved to.

    `NUMBER` without precision is deliberately not collapsed to a Java type here: the design document (§5.3) keeps
    `OracleType` and `TargetType` apart so that an unresolved precision stays visible instead of silently becoming
    a `long`. `origin` records how the type was reached, which is what makes a `%TYPE` auditable.
    """

    oracle: str                      # as written: "NUMBER(19)", "orders.status%TYPE", "customers%ROWTYPE"
    resolved: str | None = None      # after %TYPE / %ROWTYPE resolution: "VARCHAR2(20)"
    origin: str = "declared"         # declared | rowtype | record | collection | column-type |
                                     # inferred | unresolved
    schema_snapshot: str | None = None  # which DDL snapshot resolved it (design doc §5.3)
    nullable: bool | None = None

    def is_resolved(self) -> bool:
        return self.resolved is not None


@dataclass
class Node:
    """Common shape. `kind` is the discriminator; `type` is the data type where the node has one."""

    id: str
    kind: str
    source_range: SourceRange | None = None
    type: TypeRef | None = None
    confidence: float | None = None
    diagnostics: list[Issue] = field(default_factory=list)

    def add(self, severity: str, code: str, message: str) -> None:
        self.diagnostics.append(Issue(severity, code, message, self.source_range))


# --- declarations -----------------------------------------------------------------------------------

@dataclass
class Parameter(Node):
    name: str = ""
    direction: str = "IN"        # IN | OUT | IN OUT
    default: str | None = None
    nocopy: bool = False


@dataclass
class Declaration(Node):
    """A local variable, constant, cursor, exception or nested type."""

    name: str = ""
    declaration_kind: str = "variable"  # variable | constant | cursor | exception | type | record
    initial: str | None = None


# --- statements -------------------------------------------------------------------------------------

@dataclass
class Statement(Node):
    """Base for every statement. `kind` says which one.

    `read_set` / `write_set` live here rather than on `SqlOperation` because a cursor FOR loop reads tables too:
    its query is part of the loop, not a separate statement, and leaving the fields off the base made every table
    such a loop iterates invisible to the analysis.
    """

    read_set: list[str] = field(default_factory=list)
    write_set: list[str] = field(default_factory=list)


@dataclass
class Assignment(Statement):
    target: str = ""
    expression: str = ""


@dataclass
class Call(Statement):
    callee: str = ""
    arguments: list[str] = field(default_factory=list)
    resolved_to: str | None = None   # the routine id, once the call graph is built (P2-1)


@dataclass
class Raise(Statement):
    exception: str | None = None
    error_code: int | None = None    # RAISE_APPLICATION_ERROR(-20001, ...)
    message: str | None = None


@dataclass
class Return(Statement):
    expression: str | None = None


@dataclass
class TransactionStatement(Statement):
    """COMMIT / ROLLBACK / SAVEPOINT / ROLLBACK TO. Kept as a node: the rules decide, not the lowering."""

    savepoint: str | None = None


@dataclass
class If(Statement):
    branches: list["Branch"] = field(default_factory=list)
    else_body: list[Statement] = field(default_factory=list)


@dataclass
class Branch:
    condition: str
    body: list[Statement] = field(default_factory=list)


@dataclass
class Case(Statement):
    selector: str | None = None
    branches: list[Branch] = field(default_factory=list)
    else_body: list[Statement] = field(default_factory=list)


@dataclass
class Loop(Statement):
    loop_kind: str = "basic"     # basic | while | for | cursor-for | forall
    label: str | None = None
    condition: str | None = None
    cursor: str | None = None
    # P4-5: the query a cursor FOR loop iterates, as a statement in its own right. Kept as one so that the
    # converter, the capability check and the generator all see it the way they see any other query -- before
    # this it lived only as the text in `cursor`, which is why the loop could not be generated at all.
    query: "SqlOperation | None" = None
    # the loop variable: `FOR r IN (...)` binds `r`, and the body reads `r.column`
    variable: str | None = None
    # #14: `FETCH c BULK COLLECT INTO v LIMIT n` が回していた**1 回分の件数**。これが入っている
    # ループは、行をまとめて読んでから n 件ずつ配る——移行先に跨トランザクションの cursor が無い
    # ので、`n` はもう読み込む量ではなく、**1 回に配る量**である。その違いを残すために、
    # 「ただの走査」に潰さずに持つ
    chunk: str | None = None
    # #19: 処理対象を件数つきで繰り返し読むとき、次のページの起点になるキー列（keyset）
    paged_key: str | None = None
    body: list[Statement] = field(default_factory=list)


@dataclass
class BindVariable:
    name: str
    direction: str = "IN"
    oracle_type: str | None = None
    plsql_variable: str | None = None
    # the ScalarDB column this value lands in, when exactly one can be named (P3-1). The generator needs it to
    # convert at the bind boundary: ScalarDB's driver refuses a BigDecimal, and a scaled money column has to be
    # scaled on the way in. None means "could not be attributed", and the value is bound unchanged.
    column: str | None = None
    scalardb_type: str | None = None
    # what the Oracle DDL declares that column as. The variable's own type does not decide the storage scale:
    # `v_total NUMBER` assigned into a `NUMBER(14,2)` column is still cents in a scaled BIGINT.
    column_oracle_type: str | None = None
    # P4-4: the PL/SQL expression whose value this bind carries. ScalarDB SQL evaluates almost nothing, so an
    # expression in SET or VALUES is computed in the application and bound as a value instead. None means the
    # bind is a plain variable reference.
    expression: str | None = None


@dataclass
class SqlOperation(Statement):
    """A SQL statement inside PL/SQL. The bridge (P1-6) fills the target-side fields."""

    sql_kind: str = "UNKNOWN"
    original_sql: str = ""
    binds: list[BindVariable] = field(default_factory=list)
    into_targets: list[str] = field(default_factory=list)
    # the ScalarDB column and type behind each select item, positionally (P3-1). None where there is not
    # exactly one -- an expression, or a column the schema does not describe.
    into_columns: list[str | None] = field(default_factory=list)
    into_types: list[str | None] = field(default_factory=list)
    into_oracle_types: list[str | None] = field(default_factory=list)
    # whether the SELECT asks for every column. One INTO target and a star means a %ROWTYPE read, which the
    # repository cannot build from a single result column (P3-1).
    selects_star: bool = False  # serialised as selectsStar
    cardinality: str = "UNKNOWN"          # UNKNOWN | NONE | EXACTLY_ONE | AT_MOST_ONE | MANY
    # the cursor whose `%NOTFOUND` this read answers (#11). A `FETCH` that found nothing is not the same as a
    # row whose column is NULL, so the branch the original wrote is answered from a flag the read sets, not
    # from the value it assigned.
    not_found_flag: str | None = None
    # whether the query cannot return a second row by its own shape -- `LIMIT 1`, or a bare aggregate. The
    # access path cannot say this, and it is what decides whether TOO_MANY_ROWS is reachable.
    at_most_one_row: bool = False
    locking_mode: str | None = None       # FOR UPDATE / NOWAIT / SKIP LOCKED / WAIT n
    target_status: str | None = None      # OK | WARN | PLANNED | ERROR, from scalardb_migrate
    target_sql: list[str] = field(default_factory=list)
    plan_id: str | None = None            # the plan.json this statement needs at run time


@dataclass
class DynamicSql(Statement):
    """EXECUTE IMMEDIATE / DBMS_SQL. The expression is kept unevaluated; P4 does partial evaluation."""

    expression: str = ""
    constant_sql: str | None = None       # set when the expression folds to a literal
    # P4-7: every statement this can run, when that is a finite knowable set. Each entry is
    # {"guard": <the branch conditions that produce it>, "sql": <the statement>}. Empty when the set is not
    # knowable, which is the honest answer for a table name decided at run time.
    variants: list[dict] = field(default_factory=list)
    # each variant, converted and checked like any other statement. Not serialised: it is derived, and a
    # reader of the IR gets the same information from `variants` plus the diagnostics.
    variant_statements: list["SqlOperation"] = field(default_factory=list, repr=False, compare=False)
    using: list[BindVariable] = field(default_factory=list)
    into_targets: list[str] = field(default_factory=list)


@dataclass
class CursorStatement(Statement):
    """OPEN / FETCH / CLOSE. The cursor's lifetime crosses statements, which is why it is not a SqlOperation."""

    cursor: str = ""
    into_targets: list[str] = field(default_factory=list)
    arguments: list[str] = field(default_factory=list)
    # `FETCH ... BULK COLLECT INTO v LIMIT n` の `n`。INTO の対象ではないので分けて持つ——
    # 一緒くたにすると、**代入先が 1 つ増えたように見える**
    bulk_limit: str | None = None


@dataclass
class Block(Statement):
    """A nested `BEGIN ... EXCEPTION ... END`, kept where it was written (#18).

    Before this, the handlers of a nested block were hoisted onto the routine and the block itself vanished.
    Three things went with it: which loop iteration a statement in the handler belonged to, which block a
    `ROLLBACK TO savepoint` unwound, and how far the handler actually reached. The generator could only notice
    the third, and only when two hoisted handlers caught the same Java type.
    """

    declarations: list[Declaration] = field(default_factory=list)
    body: list[Statement] = field(default_factory=list)
    exception_handlers: list["ExceptionHandler"] = field(default_factory=list)


@dataclass
class ControlStatement(Statement):
    """EXIT / CONTINUE / GOTO / NULL. `label` and `condition` are kept: EXIT WHEN is not the same as EXIT."""

    label: str | None = None
    condition: str | None = None


@dataclass
class Unsupported(Statement):
    """A construct the lowering does not model yet.

    It is a node rather than a silent omission on purpose: the plan's non-functional requirements say a warning
    must never be hidden behind a success, and a routine holding one of these cannot reach AUTO because
    `ruleCoverage` counts nodes the rules can decide.
    """

    text: str = ""
    construct: str = ""


@dataclass
class ExceptionHandler(Node):
    exceptions: list[str] = field(default_factory=list)   # names, or OTHERS
    body: list[Statement] = field(default_factory=list)


# --- routines and modules ---------------------------------------------------------------------------

@dataclass
class TransactionEffects:
    commits: int = 0
    rollbacks: int = 0
    savepoints: int = 0
    autonomous: bool = False

    @property
    def controls_transaction(self) -> bool:
        return bool(self.commits or self.rollbacks or self.savepoints or self.autonomous)


@dataclass
class ExternalEffects:
    db_links: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)   # UTL_HTTP, DBMS_SCHEDULER, ...
    dynamic_sql: bool = False


@dataclass
class Routine(Node):
    name: str = ""
    routine_kind: str = "procedure"   # procedure | function | trigger-body
    parameters: list[Parameter] = field(default_factory=list)
    return_type: TypeRef | None = None
    declarations: list[Declaration] = field(default_factory=list)
    body: list[Statement] = field(default_factory=list)
    exception_handlers: list[ExceptionHandler] = field(default_factory=list)
    auth_id: str | None = None        # DEFINER | CURRENT_USER
    deterministic: bool = False
    visibility: str = "public"        # public when declared in the package spec
    transaction_effects: TransactionEffects = field(default_factory=TransactionEffects)
    external_effects: ExternalEffects = field(default_factory=ExternalEffects)


@dataclass
class Module(Node):
    """A package (spec + body), a standalone routine, or a trigger."""

    name: str = ""
    module_kind: str = "package"      # package | procedure | function | trigger
    routines: list[Routine] = field(default_factory=list)
    declarations: list[Declaration] = field(default_factory=list)   # package-level state
    trigger_event: str | None = None
    trigger_table: str | None = None
    # `UPDATE OF status` の `status`。**この列が SET に無ければ trigger は掛からない**ので、
    # 落とすと「掛からなかった書き込み」に掛けてしまう（#12）
    trigger_columns: list[str] = field(default_factory=list)
    trigger_timing: str | None = None
    # `WHEN (OLD.status <> NEW.status)`: trigger が発火する条件。落とすと**記録される量が変わる**
    # ので、生成側は本体の前の番人として出す（#12 / trigger-patterns A-2）
    trigger_when: str | None = None

    @property
    def has_package_state(self) -> bool:
        return self.module_kind == "package" and any(
            d.declaration_kind in ("variable", "constant") for d in self.declarations)


@dataclass
class Program(Node):
    """Everything analysed in one run."""

    schema_version: str = SCHEMA_VERSION
    modules: list[Module] = field(default_factory=list)
    unresolved: list[Issue] = field(default_factory=list)
    schema_snapshot: str | None = None


# --- stable identifiers ------------------------------------------------------------------------------

class IdFactory:
    """Deterministic ids: `pkg_order.create_order#stmt-12`, as in the design document §7.1.

    They have to be stable across runs so a golden IR comparison (P1-5) and a decision recorded against a node
    (P3-5) still line up after the tool changes. The counter is per scope, never global.
    """

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self._counters: dict[str, itertools.count] = {}

    def next(self, prefix: str = "stmt") -> str:
        counter = self._counters.setdefault(prefix, itertools.count(1))
        return f"{self.scope}#{prefix}-{next(counter)}"

    def child(self, name: str) -> "IdFactory":
        return IdFactory(f"{self.scope}.{name}" if self.scope else name)
