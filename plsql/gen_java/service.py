"""P2-6: the routine body as a Java method.

The statements the IR models become Java; the ones it cannot, or must not, become a `TODO` the compiler will not
let anyone forget. Three rules shape the output:

* **The transaction boundary is the caller's.** A generated method never begins or commits. The decision (plan §9)
  is that no Spring annotation appears either: the boundary is a `try`-with-resources in one helper, and the
  application decides where to put it. A method that committed on its own would make the surrounding use case
  impossible to compose.
* **Every statement keeps its line.** A comment carrying `file:line` sits on each translated statement, because
  the value of the output is not the code -- it is being able to check the code against the PL/SQL it came from
  (design document §17-8).
* **What cannot be translated is a compile error, not a silent gap.** An unsupported construct becomes a
  `throw new UnsupportedOperationException` next to the original source text. The plan's non-functional
  requirements forbid hiding a warning behind a success, and a routine that quietly does less than the original
  is the worst possible outcome of a migration.
"""

from __future__ import annotations

import contextvars
import dataclasses
import re

from dataclasses import dataclass, field

from ..ir import model as M
from ..lower import _walk
from . import split
from .dto import loop_component_type
from .emit import JavaFile
from .expr import SEQUENCES_IMPORT, translate
from .types import signature_type, record_class, java_class_name, java_name, java_type, record_columns, result_record_name, routine_stem

# the module being generated, so an expression can resolve a sibling routine without threading it through
# every statement helper
_MODULE: "contextvars.ContextVar[M.Module | None]" = contextvars.ContextVar("module", default=None)
_DOMAIN: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("domain", default=None)
_INFRA: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("infra", default=None)
# #12: trigger の本体は別の module にある。呼ぶ側はその routine を見て、引数と audit の有無を決める
_PROGRAM: "contextvars.ContextVar[M.Program | None]" = contextvars.ContextVar("program", default=None)
# names a nested block declares (#18). They are in scope for its body and nowhere else, which is what the
# PL/SQL says -- reading them off the routine would make a block-local visible to the whole method.
_BLOCK_LOCALS: "contextvars.ContextVar[dict[str, str]]" = contextvars.ContextVar("block_locals", default={})
# the declarations of the enclosing nested blocks, innermost last: what `_holder` finds before the routine's (#71)
_BLOCK_HOLDERS: "contextvars.ContextVar[tuple]" = contextvars.ContextVar("block_holders", default=())
_ROWCOUNT_SEEN: "contextvars.ContextVar[bool]" = contextvars.ContextVar("rowcount", default=False)
_READS_ROWCOUNT: "contextvars.ContextVar[bool]" = contextvars.ContextVar("reads_rowcount", default=False)
# この routine が途中で拒否することが分かっているか（#25）。採番の前で止めるために要る
_REFUSES_LATER: "contextvars.ContextVar[bool]" = contextvars.ContextVar("refuses", default=False)
# P4-5: `FOR r IN (SELECT qty, ...)` puts `r` in scope for the body, and the translator turns `r.qty` into the
# record accessor `r.qty()`. Kept apart from the routine's own names so a nested loop restores the outer one.
_LOOP_ROWS: "contextvars.ContextVar[dict[str, str]]" = contextvars.ContextVar("loop_rows", default={})
# handler の中だけで意味を持つ名前（`SQLCODE`）。catch が束ねている例外から読む
# いま内側にいる、Java のラベルとして出したループ。`EXIT outer_loop` を `break outerLoop;` にしてよいのは、
# その名前のループを実際に出したときだけである
_LOOP_LABELS: "contextvars.ContextVar[frozenset[str]]" = contextvars.ContextVar("loop_labels",
                                                                                default=frozenset())
# `_HANDLER_ERROR` のキー。値は、いま中にいる catch の変数名（`e`、入れ子なら `e2` ...）。PL/SQL の名前と
# ぶつからないよう、識別子にならない文字を入れてある
_CAUGHT = "<caught>"
# inside a loop whose DML adds to `rowCount` (a multi-row UPDATE split into a loop, a fused BULK COLLECT + FORALL):
# the count was set to 0 where that loop starts, and a loop nested in it must not start it again (#109)
_COUNTING: "contextvars.ContextVar[bool]" = contextvars.ContextVar("counting", default=False)
_HANDLER_ERROR: "contextvars.ContextVar[dict[str, str]]" = contextvars.ContextVar("handler", default={})


@dataclass
class ServiceFile:
    file: JavaFile
    routines: list[str] = field(default_factory=list)
    untranslated: list[str] = field(default_factory=list)
    unknown_names: list[str] = field(default_factory=list)


def generate_module(module: M.Module, package: str, repository_package: str,
                    domain_package: str, program: "M.Program | None" = None) -> ServiceFile:
    """One Java class per PL/SQL module. Public routines become public methods, private ones private.

    `program` is needed when this module calls a trigger (#12): the trigger's body lives in another
    module, and its signature is what decides the argument order.
    """
    if program is not None:
        _PROGRAM.set(program)
    elif (current := _PROGRAM.get()) is not None and not any(m is module for m in current.modules):
        # a program left behind by an earlier call (a ContextVar outlives it): resolving this module's calls
        # against another program's routines gave different Java depending on what ran before
        _PROGRAM.set(None)
    name = java_class_name(module.name) + "Service"
    file = JavaFile(package=package, name=name,
                    source=module.source_range.file if module.source_range else module.name)
    result = ServiceFile(file=file)
    file.add_import(f"{repository_package}.{java_class_name(module.name)}Repository")
    file.add_import(f"{domain_package}.MigratedException")

    _MODULE.set(module)
    _DOMAIN.set(domain_package)
    from .types import set_object_package
    set_object_package(domain_package)
    _INFRA.set(repository_package)
    # #12: この module が呼ぶ trigger。**呼ぶ側に注入する**——移行先に trigger は無いので、
    # 掛けるには書き込む側が呼ぶしかない。誰が呼んでいるかが constructor に出るのは、
    # 「掛かるのはこの経路だけ」という事実がそこに見えるということでもある
    injected = trigger_services(module)
    # #49: a routine that runs in a transaction of its own (transactions.separate) is not injected -- an instance
    # bound to this transaction's connection would join it. The caller takes a port that opens another
    # transaction, and builds the callee on that connection; Sequences travel with it when the callee numbers
    separate = separate_callees(module)
    separate_sequences = any(_module_of(owner) is not None and _uses_sequences(_module_of(owner)) for owner in separate)
    file.comment(
        f"{module.name} ({module.module_kind}).\n"
        "The transaction boundary belongs to the caller: no method here begins, commits or rolls back.")
    with file.block(f"public class {name}") as f:
        f.line(f"private final {java_class_name(module.name)}Repository repository;")
        for trigger in injected:
            f.line(f"private final {java_class_name(trigger)}Service {java_name(trigger)};")
        if separate:
            file.add_import("com.scalar.migrate.plsql.SeparateTransactions")
            f.line("private final SeparateTransactions separate;")
        if separate_sequences:
            file.add_import("com.scalar.migrate.plsql.Sequences")
            f.line("private final Sequences sequences;")
        _constants(f, module)
        f.line()
        parameters = [f"{java_class_name(module.name)}Repository repository"] + \
            [f"{java_class_name(t)}Service {java_name(t)}" for t in injected] + \
            (["SeparateTransactions separate"] if separate else []) + \
            (["Sequences sequences"] if separate_sequences else [])
        with f.block(f"public {name}({', '.join(parameters)})") as g:
            g.line("this.repository = repository;")
            for trigger in injected:
                g.line(f"this.{java_name(trigger)} = {java_name(trigger)};")
            if separate:
                g.line("this.separate = separate;")
            if separate_sequences:
                g.line("this.sequences = sequences;")
        for routine in module.routines:
            f.line()
            # 1 反復 = 1 トランザクションに割ると**決めてある** routine は、1 つの method ではなく
            # トランザクション単位の部品として出る（#24 / #14）。決めていなければ False が返り、
            # いままでどおり 1 つの method になる
            if not split.emit(f, module, routine, result, domain_package):
                _method(f, module, routine, result, domain_package)
    return result


def trigger_services(module: M.Module) -> list[str]:
    """この module が呼ぶ**他の module** の名前。constructor に出る順（名前順）で返す。

    trigger（#12）と、PL/SQL がそう書いている別 package の呼び出しの両方である。どちらも
    「誰に依存しているか」が constructor に出る——移行先で何を配線するかが、そこで分かる。
    """
    out: set[str] = set()
    for routine in module.routines:
        for statement in _walk(routine.body) + [s for h in routine.exception_handlers
                                                for s in _walk(h.body)]:
            owner = _trigger_owner(statement, module) or _sibling_owner(statement, module)
            if owner:
                out.add(owner)
        # a function of another module called inside an expression is a dependency too (#48)
        out.update(owner for owner, _ in _expression_callees(routine, module).values())
    return sorted(out)


# `pkg.name` inside an expression, outside string literals: a call into another module (#48)
_QUALIFIED = re.compile(r"(?<![\w$#.:])([A-Za-z][\w$#]*)\.([A-Za-z][\w$#]*)(?![\w$#.])")
_STRING = re.compile(r"'(?:[^']|'')*'")


def _expression_callees(routine: M.Routine, module: "M.Module | None") -> dict[str, tuple[str, M.Routine]]:
    """`pkg.fn` names inside the routine's expressions that resolve to a routine of another module of the
    program: {"pkg.fn": (module name, routine)}. Overloads are left out (which one is meant is not resolved
    from an expression), as is anything the program does not contain.

    A statement-level call is a `Call` node the generator already resolves; a call inside an expression
    (`v := emp_api.hire(...)`, `'…' || pkg.count`) only exists as text, so it is found here (#48,
    samples/oracle-samples b05_3, 2026-09-25)."""
    from ..lower import overload_of

    program = _PROGRAM.get()
    if program is None:
        return {}
    modules = {m.name.lower(): m for m in program.modules}
    found: dict[str, tuple[str, M.Routine]] = {}
    texts = [text for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
             for text in _expression_texts(statement)]
    # a declaration's initialiser calls too: `r My_Types.My_Rec := My_Types.Init_My_Rec();` (5-33)
    texts += [d.initial for d in routine.declarations if d.initial and d.declaration_kind != "cursor"]
    for text in texts:
        for owner, name in _QUALIFIED.findall(_STRING.sub("''", text or "")):
            target = modules.get(owner.lower())
            if target is None or (module is not None and target is module):
                continue
            callee = next((r for r in target.routines if r.name.lower() == name.lower()
                           and overload_of(r) is None), None)
            if callee is not None:
                found.setdefault(f"{owner.lower()}.{name.lower()}", (target.name, callee))
        # a standalone function called by its bare name, `i || '! = ' || factorial(i)` (8-35, #86): its
        # module is itself. Not when the caller's own module or its locals already mean that name
        own = {r.name.lower() for r in (module.routines if module is not None else [])} | \
            {h.name.lower() for h in list(routine.parameters) + list(routine.declarations)}
        for name in re.findall(r"(?<![\w$#.])([A-Za-z][\w$#]*)\s*\(", _STRING.sub("''", text or "")):
            target = modules.get(name.lower())
            if target is None or target is module or name.lower() in own \
                    or target.module_kind not in ("function", "procedure"):
                continue
            callee = next((r for r in target.routines if r.name.lower() == name.lower()
                           and overload_of(r) is None), None)
            if callee is not None:
                found.setdefault(name.lower(), (target.name, callee))
    return found


def separate_callees(module: M.Module) -> list[str]:
    """The modules whose routines this module calls in a transaction of their own (transactions.separate, #49)."""
    out: set[str] = set()
    for routine in module.routines:
        for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
            if statement.kind == "Call" and statement.resolved_to and split.runs_separately(statement.resolved_to):
                owner = _owner_module(statement.resolved_to, module)
                if owner:
                    out.add(owner)
    return sorted(out)


def _module_of(name: str) -> "M.Module | None":
    program = _PROGRAM.get()
    return next((m for m in (program.modules if program else []) if m.name.lower() == name.lower()), None)


def _uses_sequences(module: M.Module) -> bool:
    from .repository import _uses_sequences as uses

    return uses(module)


def _sibling_owner(statement: M.Statement, module: M.Module) -> str | None:
    """別の module の routine を呼ぶ文なら、その module 名。"""
    if statement.kind != "Call" or not getattr(statement, "resolved_to", None):
        return None
    if split.runs_separately(statement.resolved_to):
        return None   # #49: built on the connection SeparateTransactions opens, not injected
    owner = _owner_module(statement.resolved_to, module)
    if not owner or owner == module.name or _routine(statement.resolved_to) is None:
        return None
    return owner


def _trigger_owner(statement: M.Statement, module: M.Module) -> str | None:
    """その文が trigger 本体を呼んでいるなら、その module 名。

    判断は**その文が自分で言っていること**（`TRIGGER_CALL`）で行う。呼ばれる側の routine を
    引いて確かめる形にすると、program が渡っていない呼び出し（module 1 つだけを生成する経路）で
    **黙って普通の呼び出しとして扱われ、名前付き引数が式として翻訳される**——実際そうなった。
    """
    if statement.kind != "Call" or not getattr(statement, "resolved_to", None):
        return None
    if not any(d.code == "TRIGGER_CALL" for d in statement.diagnostics):
        return None
    owner, _, _ = statement.resolved_to.rpartition(".")
    return owner or None


def _routine(routine_id: str) -> "M.Routine | None":
    program = _PROGRAM.get()
    for module in (program.modules if program else []):
        for routine in module.routines:
            if routine.id == routine_id:
                return routine
    return None


# text a statement carries that is not an expression: ids and classifications, and the SQL, whose own values
# are the repository's business (`repository.needs_audit` reads the binds lifted out of it)
NOT_AN_EXPRESSION = {"id", "kind", "sql_kind", "loop_kind", "cardinality", "label", "direction", "name",
                     "callee", "resolved_to", "original_sql", "target_sql", "cursor", "not_found_flag",
                     "exception_name", "variable", "source_range"}


def needs_audit(routine: M.Routine) -> bool:
    """Whether anything in this routine reads a value the caller supplies (#1, #8).

    Both places count: an expression the routine evaluates itself (`v := SYSTIMESTAMP`), and one lifted out
    of a statement's SQL (P4-4), which the repository computes but the service has to hand it the context for.

    The answer comes from **translating** the text, not from searching it. A regular expression cannot tell
    `USER` from `'USER'`, and it grew a signature parameter no one read out of a string literal. It also has
    to be asked of the right attributes, and the list of them was short by one -- `RAISE`'s message -- which
    made a body reference `audit` that the signature did not provide: Java that does not compile. Every text
    a statement carries is translated instead, less the ones that are not expressions, so a node added to the
    IR is covered the day it arrives rather than the day someone remembers to add it here.
    """
    return _needs_audit(routine, set())


def _needs_audit(routine: M.Routine, visiting: set[str]) -> bool:
    if routine.id in visiting:
        return False   # recursion: the answer comes from the rest of the body
    visiting.add(routine.id)
    from .. import builtins
    for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
        mapped = builtins.lookup(statement.callee) if statement.kind == "Call" and not statement.resolved_to else None
        if mapped is not None and mapped.java is None:
            continue   # a no-op built-in (DBMS_STATS, SET_MODULE, #55): its arguments are never evaluated
        for text in _expression_texts(statement):
            if translate(text).audit:
                return True
        # #12: trigger を呼ぶなら、その trigger が要る値も呼び出し側から来る。ここを見ないと、
        # 本体が `audit` を使うのに signature がそれを受け取らない Java になる。trigger だけでなく
        # ふつうの routine も同じ: 呼び先が audit を受け取るなら、呼ぶ側はそれを渡す引数を持つ
        # （b05_1 → raise_salary → trigger、samples/oracle-samples 2026-09-25。呼び先だけ見ていて
        # 呼ぶ側の signature に audit が無い Java が出た）
        if statement.kind == "Call" and getattr(statement, "resolved_to", None):
            callee = _routine(statement.resolved_to)
            if callee is not None and callee is not routine and _needs_audit(callee, visiting):
                return True
    # a function of another module called inside an expression takes the audit too (#48)
    for _, callee in _expression_callees(routine, _MODULE.get()).values():
        if callee is not routine and _needs_audit(callee, visiting):
            return True
    return False


def _expression_texts(statement: M.Statement):
    for field in dataclasses.fields(statement):
        if field.name in NOT_AN_EXPRESSION:
            continue
        value = getattr(statement, field.name, None)
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (item for item in value if isinstance(item, str))
    for bind in getattr(statement, "binds", None) or []:
        if bind.expression:
            yield bind.expression
    for branch in getattr(statement, "branches", []) or []:
        if branch.condition:
            yield branch.condition


def correlation_row(routine: M.Routine) -> dict[str, "M.BindVariable"]:
    """`NEW.status` / `OLD.status` -> それを渡す bind。trigger の行は呼び出し側から来る（#12）。

    **並びを決めているのは `plsql/triggers.py` である。** 呼ぶ側（書き込む文のところ）と呼ばれる側
    （この method の signature）が別々に数えると、引数が静かにずれる。使われている参照だけを返すのは
    `AuditContext` と同じ理由で、読んでいない列まで呼び出し側に用意させないためである。
    """
    from ..triggers import EVENT_OF_COLUMN_PREFIX, EVENTS, correlation_row as correlations

    module = _MODULE.get()
    out: dict[str, M.BindVariable] = {}
    for variable, bind in correlations(routine, getattr(module, "trigger_when", None)).items():
        # 条件や式だけが読む列には型が付いていない。付いていないことを `Object` として出すほうが、
        # 条件ごと拒んで「なぜ読めないのか」を隠すよりよい
        # `INSERTING` / `UPDATING` / `DELETING`: どのイベントの文のところで呼んでいるかは呼ぶ側が知っている
        # ので、相関行と同じく引数で受け取る（#29 の 25）
        event = "BOOLEAN" if variable in EVENTS or variable.startswith(EVENT_OF_COLUMN_PREFIX) else None
        out[variable] = bind or M.BindVariable(name=variable.replace(".", "_"), direction="IN",
                                               oracle_type=event, plsql_variable=variable)
    return out


_LITERAL = re.compile(r"^(?:-?\d+(?:\.\d+)?|'(?:[^']|'')*'|TRUE|FALSE|NULL)$", re.IGNORECASE)


def _constants(file: JavaFile, module: M.Module) -> None:
    """A package's constants (`c_max_raise_pct CONSTANT NUMBER := 20`, in the specification or the body) as
    fields (#46). A constant is the same for every caller, so a static field keeps its meaning; a constant whose
    value is an expression is left out and its name stays unknown, which the statement that reads it reports."""
    if module.module_kind != "package":
        return
    for declaration in module.declarations:
        if declaration.declaration_kind != "constant" or not declaration.initial \
                or not _LITERAL.match(declaration.initial.strip()):
            continue
        mapped = java_type(declaration.type.resolved if declaration.type else None)
        file.add_import(*mapped.imports)
        value = declaration.initial.strip()
        if value.startswith("'"):
            value = '"' + value[1:-1].replace("''", "'").replace('"', '\\"') + '"'
        elif mapped.name == "BigDecimal":
            file.add_import("com.scalar.migrate.plsql.Plsql")
            value = f"Plsql.dec({value})"
        elif mapped.name == "Long" and "." not in value:
            value = f"{value}L"
        file.line(f"private static final {mapped.name} {_local(declaration.name)} = {value.lower() if value.upper() in ('TRUE', 'FALSE', 'NULL') else value};")


def _collection_kind(holder) -> str | None:
    """`list` for a nested table / VARRAY, `map` for an INDEX BY table -- by text (#45) or by integer (#93)."""
    if holder.type is None or holder.type.origin != "collection":
        return None
    resolved = (holder.type.resolved or "").upper()
    return "map" if re.search(r"INDEX\s+BY\s+(?:N?VARCHAR2?|PLS_INTEGER|BINARY_INTEGER|SIMPLE_INTEGER)\b", resolved) else "list"


def _scope(routine: M.Routine, module: M.Module | None = None) -> dict[str, str]:
    """PL/SQL name -> Java name for everything visible inside the method, siblings included.

    A function call inside an expression (`v := order_total(id)`) resolves to a sibling method, so the sibling
    names belong in the scope; without them every such call is reported as unknown.
    """
    names = {"SQL%ROWCOUNT": "rowCount", "sql%rowcount": "rowCount",
             # the implicit cursor's other attributes: the last DML touched no row / some row
             "SQL%NOTFOUND": "(rowCount == 0)", "sql%notfound": "(rowCount == 0)",
             "SQL%FOUND": "(rowCount > 0)", "sql%found": "(rowCount > 0)",
             # SQL%BULK_ROWCOUNT(i): a collection the FORALL fills, element by element (#51)
             "sql%bulk_rowcount#collection": "bulkRowCount"}
    # trigger の相関名。`:NEW.status` は文が走る前から Java が値として持っているもので、
    # cursor FOR ループの行と同じ扱いになる（#10 / #12）
    for variable, bind in correlation_row(routine).items():
        # PL/SQL の本文は `:NEW.status`、SQL から起こした bind は `NEW.status`。同じものなので
        # 両方の綴りを置く
        names[variable] = java_name(bind.name)
        names[f":{variable}"] = java_name(bind.name)
    names.update({p.name: java_name(p.name) for p in routine.parameters})
    # a TYPE is not a value: `t_names('a', 'b')` (a collection constructor) rendered as a call to a method that
    # does not exist. Left out, the constructor is reported instead (#40)
    names.update({d.name: java_name(d.name) for d in routine.declarations if d.declaration_kind != "type"})
    # collections (#45): `v#collection` says a local is one (list / map), `t#constructor` that a TYPE builds one
    trigger_locals = list(module.declarations) if module is not None and module.module_kind == "trigger" else []
    for holder in list(routine.parameters) + list(routine.declarations) + trigger_locals:
        kind = _collection_kind(holder)
        if kind and getattr(holder, "declaration_kind", None) == "type":
            # a TYPE, not a variable: only its constructor (`tv1(3, 5)`, 5-12, #82)
            if kind == "list":
                element = re.sub(r"^(?:List|Map)<(?:[^,]+,\s*)?(.+)>$", r"\1", java_type(holder.type.resolved).name)
                names[f"{holder.name.lower()}#constructor"] = kind
                names[f"{holder.name.lower()}#element"] = element
            continue
        if kind:
            names[f"{holder.name.lower()}#collection"] = kind
            element = re.sub(r"^(?:List|Map)<(?:[^,]+,\s*)?(.+)>$", r"\1", java_type(holder.type.resolved).name)
            names[f"{holder.name.lower()}#element"] = element
            bound = re.search(r"\bLIMIT\s+(\d+)$", holder.type.resolved or "")
            if bound:
                names[f"{holder.name.lower()}#limit"] = bound.group(1)   # `v.LIMIT` of a VARRAY(n)
            if kind == "list" and holder.type is not None and "%" not in (holder.type.oracle or ""):
                names[f"{(holder.type.oracle or '').strip().lower()}#constructor"] = kind
                names[f"{(holder.type.oracle or '').strip().lower()}#element"] = element
    # CHAR(n) locals hold blank-padded text (#62); comparing one with a literal or another CHAR is blank-padded too.
    # PLS_INTEGER locals: arithmetic between two of them is 32-bit (#60)
    for holder in list(routine.parameters) + list(routine.declarations) + trigger_locals:
        declared = ((holder.type.resolved or holder.type.oracle) or "").strip() if holder.type is not None else ""
        if _CHAR_CONSTRAINT.fullmatch(declared):
            names[f"{holder.name.lower()}#blank_padded"] = "1"
        if declared.upper() in _PLS_INTEGER_TYPES:
            names[f"{holder.name.lower()}#pls_integer"] = "1"
        text = _text_rendering(declared)
        if text:
            names[f"{holder.name.lower()}#text"] = text
        if holder.type is not None and record_class(holder.type):
            # its field names: `name1.first` is the field, not the collection method FIRST (5-45, #82)
            names[f"{holder.name.lower()}#fields_of"] = ",".join(
                f.lower() for f, _ in record_columns(holder.type.resolved or ""))
    # a schema object type's constructor `emp_grade_t(a, b, 'X')` builds its record (#54)
    from .types import object_types
    for name, resolved in object_types().items():
        names[f"{name}#record"] = java_class_name(name)
        names[f"{name}#fields"] = ",".join(java_type(t).name for _, t in record_columns(resolved))
        if _DOMAIN.get():
            names[f"{name}#import"] = f"{_DOMAIN.get()}.{java_class_name(name)}"
    if module is not None:
        # an overloaded name is several Java methods (`put1`, `put2`); which one an expression means is not
        # resolved, so the name is left out and the expression is reported instead of compiled against nothing
        from ..lower import overload_of
        names.update({r.name: java_name(r.name) for r in module.routines if overload_of(r) is None})
        # the Java types of a sibling's IN parameters, so that a call can hand a NUMBER parameter a BigDecimal.
        # `rank_of(v_balance)` with `v_balance members.balance%TYPE` (NUMBER(10) -> Long) did not compile: the
        # method takes BigDecimal (samples/tutorial, 2026-09-20). The key cannot clash with a PL/SQL name
        for r in module.routines:
            # the routine itself too: a recursive call (`fibonacci(n - 2)`) needs its own parameter types (#75)
            if overload_of(r) is None:
                names[f"{r.name.lower()}#parameters"] = ",".join(
                    signature_type(p.type).name for p in r.parameters
                    if not (r.enclosing and p.carried))
                carried = [p.carried_from or p.name for p in r.parameters if r.enclosing and p.carried]
                if carried:
                    # a lifted local subprogram takes the enclosing variables it reads after its own (#80)
                    names[f"{r.name.lower()}#extra"] = ",".join(carried)
        # a trigger declares its locals on the module, not on the body, and a package-level cursor is visible
        # to every routine; leaving them out reports real names as unknown
        # a package-level variable is session state (STATE-001): there is no field to assign, so a reference
        # is reported rather than compiled against nothing (#40). Constants and a trigger's locals stay
        names.update({d.name: java_name(d.name) for d in module.declarations
                      if d.declaration_kind != "type"
                      and not (module.module_kind == "package" and d.declaration_kind == "variable")})
    # #48: a function of another module inside an expression: `emp_api.hire(...)` -> `empApi.hire(...)`. The
    # service is injected (trigger_services). One with OUT / IN OUT arguments -- a carried package variable
    # (#46) included -- hands values back in a result record, which an expression has nowhere to put, so it is
    # reported with the reason instead of compiled against nothing
    for qualified, (owner, callee) in _expression_callees(routine, module).items():
        if any(p.direction in ("OUT", "IN OUT") for p in callee.parameters):
            names[f"{qualified}#refused"] = ("OUT / IN OUT 引数（運ぶ package 変数を含む）のある routine は式の中では"
                                             "呼べない。文に分けて Result record から受ける")
            continue
        names[qualified] = f"{java_name(owner)}.{java_name(routine_stem(callee))}"
        names[f"{qualified}#parameters"] = ",".join(
            signature_type(p.type).name for p in callee.parameters)
        if needs_audit(callee):
            names[f"{qualified}#extra"] = "audit"
    return names


def _method(file: JavaFile, module: M.Module, routine: M.Routine, result: ServiceFile,
            domain_package: str, *, method_name: str | None = None,
            extra_parameters: "list[tuple[str, str]]" = (), extra_scope: "dict[str, str] | None" = None,
            notes: "list[str]" = ()) -> None:
    """One routine, one method -- unless it was split into transaction-sized parts (#24), in which case
    this emits one of the parts: `method_name` names it, `extra_parameters` carry what the caller's loop
    holds (the row, the element, the exception) and `extra_scope` binds the PL/SQL references to them."""
    outer_scope = _LOOP_ROWS.get()
    _LOOP_ROWS.set({**outer_scope, **(extra_scope or {})})
    try:
        _emit_method(file, module, routine, result, domain_package, method_name=method_name,
                     extra_parameters=extra_parameters, notes=notes)
    finally:
        _LOOP_ROWS.set(outer_scope)


def _emit_method(file: JavaFile, module: M.Module, routine: M.Routine, result: ServiceFile,
                 domain_package: str, *, method_name: str | None = None,
                 extra_parameters: "list[tuple[str, str]]" = (), notes: "list[str]" = ()) -> None:
    returns = "void"
    returned_rows = next((s for s in _walk(routine.body) if s.kind == "Loop" and getattr(s, "returns_rows", False)), None)
    if returned_rows is not None:
        # `OPEN rc FOR q; RETURN rc;` (#44): the caller gets the rows
        from .repository import loop_record
        returns = f"List<{loop_record(routine, returned_rows)}>"
        file.add_import("java.util.List", f"{domain_package}.{loop_record(routine, returned_rows)}")
    elif routine.return_type is not None:
        mapped = signature_type(routine.return_type)
        file.add_import(*mapped.imports)
        if record_class(routine.return_type) is not None:
            file.add_import(f"{domain_package}.{mapped.name}")
        returns = mapped.name
    outs = [p for p in routine.parameters if p.direction in ("OUT", "IN OUT")]
    if outs:
        returns = result_record_name(routine)
        file.add_import(f"{domain_package}.{returns}")

    parameters = []
    for parameter in routine.parameters:
        if parameter.direction == "OUT":
            continue  # an OUT argument comes back in the result, not through the signature
        parameters.append(f"{_holder_java_type(file, parameter.type)} {java_name(parameter.name)}")

    for variable, bind in correlation_row(routine).items():
        # #12: trigger の行は呼び出し側が渡す。移行先に trigger は無いので、「この表へのすべての
        # 書き込み」に掛かっていたものが、この method を呼ぶ経路にだけ掛かる——網羅性は呼び出し側の
        # 設計（trigger-patterns §0）であって、生成器が保証できることではない
        # 列の幅ではなく PL/SQL の NUMBER として受ける（repository が `:NEW.qty` の bind をそう型付けするのと
        # 同じ規則）。NUMBER(10) の列を Long にすると、呼ぶ側の NUMBER の引数も repository の引数も合わない
        mapped = loop_component_type(bind.oracle_type)
        file.add_import(*mapped.imports)
        parameters.append(f"{mapped.name} {java_name(bind.name)}")

    # 割った部品が呼び出し側のループから受け取るもの（行・要素・例外）。audit より前に置くのは、
    # audit が「最後に足しても位置引数がずれない」ためにそこにいるからである（#24）
    parameters.extend(f"{kind} {name}" for kind, name in extra_parameters)

    if needs_audit(routine):
        # #1 / #8: who and when come from the caller. Last, so adding it does not renumber the parameters a
        # caller already passes positionally.
        file.add_import("com.scalar.migrate.plsql.AuditContext")
        parameters.append("AuditContext audit")

    visibility = "public" if routine.visibility == "public" else "private"
    _ROWCOUNT_SEEN.set(False)
    _REFUSES_LATER.set(_refuses_somewhere(routine, result))
    _source_comment(file, routine)
    for note in notes:
        file.comment(note)
    with file.block(f"{visibility} {returns} {java_name(method_name or routine_stem(routine))}"
                    f"({', '.join(parameters)}) throws Exception") as f:
        if routine.routine_kind == "trigger-body":
            for declaration in (_MODULE.get().declarations if _MODULE.get() else []):
                _declaration(f, declaration, routine, result)
            if _trigger_when(f, routine, result):
                # 条件が読めないので本体は出さない。Java は throw のあとの文を受け付けないし、
                # 出したところで動かない
                f.comment("the body is not emitted while the firing condition is unresolved")
                return
        if routine.call_spec:
            # the body is Java or C inside the database (`AS LANGUAGE JAVA NAME ...`): nothing to translate, and an
            # empty method would return as if it had done its work (8-43, #69)
            f.comment(f"not translated: a call specification (LANGUAGE {routine.call_spec}); its body is outside PL/SQL")
            f.line(f'throw new UnsupportedOperationException("call specification (LANGUAGE {routine.call_spec}): '
                   f'move its body into the application");')
            if routine.id not in result.untranslated:
                result.untranslated.append(routine.id)
            return
        clashes = _name_clashes(routine)
        if clashes:
            # `p_id` and `p__id`, or a local called `row_count` beside the generated `rowCount`: two declarations
            # of one Java name do not compile, and renaming one would have to reach every place that reads it
            f.comment(f"not translated: {'; '.join(clashes)}")
            f.line(f'throw new UnsupportedOperationException("names that collide in Java: '
                   f'{"; ".join(clashes)}");')
            if routine.id not in result.untranslated:
                result.untranslated.append(routine.id)
            return
        written = _walk(routine.body) + [x for h in routine.exception_handlers for x in _walk(h.body)]
        # 畳んだ動的 SQL の variant も DML でありうる。walk には出てこないので、ここで足す——
        # 足さないと `rowCount` を使う文だけが出て、宣言が無い Java になる（P4-7 の生成で判明）
        written += [v for s in written for v in (getattr(s, "variant_statements", None) or [])]
        _READS_ROWCOUNT.set(bool(re.search(r"SQL%ROWCOUNT", repr(routine), re.IGNORECASE)))
        if any((s.sql_kind or "").upper() in ("INSERT", "UPDATE", "DELETE", "MERGE")
               for s in written if s.kind == "SqlOperation") \
                or (_READS_ROWCOUNT.get() and any(_sets_rowcount_to_one(s) for s in written)):
            # one declaration per method: a routine may hold several DML statements, in different blocks
            f.line("int rowCount = 0;")
            if _reads_bulk_rowcount(routine):
                f.add_import("java.util.List")
                f.add_import("java.util.ArrayList")
                f.add_import("java.math.BigDecimal")
                f.line("List<BigDecimal> bulkRowCount = new ArrayList<>();")
        for parameter in outs:
            # an OUT argument becomes a local, and comes back in the result record rather than through the
            # signature: a caller must not see a half-updated set when an exception interrupts the routine
            if parameter.direction == "IN OUT":
                # the method parameter itself is the local: `String pName = pName;` redeclared it and javac
                # refused the whole class (2026-09-24, samples/oracle-samples normalize_name)
                continue
            f.line(f"{_holder_java_type(file, parameter.type)} {java_name(parameter.name)} = "
                   f"{_record_default(parameter.type, f) or 'null'};")
        chunked = {(loop.variable or "").lower() for loop in _walk(routine.body)
                   if loop.kind == "Loop" and getattr(loop, "chunk", None)}
        # a `%ROWTYPE` record that a rewritten scan made its loop variable (#39): the for declares it
        chunked |= {(loop.variable or "").lower() for loop in _walk(routine.body)
                    if loop.kind == "Loop" and loop.loop_kind == "cursor-for" and loop.variable
                    and any(d.name.lower() == loop.variable.lower() and d.type is not None
                            and (d.type.oracle or "").upper().endswith("%ROWTYPE") for d in routine.declarations)}
        for declaration in routine.declarations:
            if declaration.name.lower() in chunked:
                # 分割読みのループ変数（#14）。PL/SQL では宣言された配列だが、Java では塊そのもの
                # が for の変数になる。ここでも宣言すると同じ名前が 2 つになる
                continue
            _declaration(f, declaration, routine, result)
        for cursor, variable in _general_cursors(routine).items():
            f.add_import("com.scalar.migrate.plsql.Plsql")
            f.line(f"Plsql.Cursor {_cursor_state(cursor)} = new Plsql.Cursor({'true' if variable else 'false'});"
                   f"   // {cursor}")
        for flag in _not_found_flags(routine):
            # `c%NOTFOUND` after an explicit cursor's first FETCH (#11). It is declared with the locals, not at
            # the read, because the branch that asks may sit in a different block from the read that answers.
            f.line(f"boolean {_flag_name(flag)} = false;   // {flag}%NOTFOUND")
        if routine.declarations or outs:
            f.line()
        if _emits_a_catch(routine.exception_handlers, routine):
            with f.block("try") as body:
                _guarded(body, routine.exception_handlers, routine.body, routine, result, domain_package)
            _handlers(f, routine.exception_handlers, routine, result, domain_package)
        else:
            # handler が 1 つも出ないなら `try` も出さない。`catch` の無い `try` は Java にならない
            # （`--verify-compile` が捕まえた）
            _handlers(f, routine.exception_handlers, routine, result, domain_package)
            _statements(f, routine.body, routine, result)
        if outs and not _always_exits(routine, result):
            components = (["null"] if routine.return_type is not None else []) + \
                [java_name(p.name) for p in outs]
            f.line(f"return new {returns}({', '.join(components)});")
        elif returns != "void" and not outs and not _always_exits(routine, result):
            f.line("// the PL/SQL falls through here; Oracle raises ORA-06503 when a function does")
            f.line('throw new IllegalStateException("function reached its end without RETURN");')
    if routine.id not in result.routines:
        # 割った部品は同じ routine から出た複数の method である（#24）。数えるのは routine のほう
        result.routines.append(routine.id)


def _handlers(file: JavaFile, handlers: list[M.ExceptionHandler], routine: M.Routine,
              result: ServiceFile, domain_package: str) -> None:
    """Exception handlers become catch blocks, in the order PL/SQL would try them.

    Takes the handlers rather than reading them off the routine: a nested block has its own (#18), and they
    become the catches of that block's `try`, reaching exactly as far as the block does.

    `WHEN OTHERS` is last whatever the source order, because Java resolves catches in order and a broad one first
    would swallow the specific ones. Keeping the PL/SQL order for everything else matters: two handlers can both
    match, and PL/SQL takes the first.
    """
    from .exception import NEVER_RAISED_BY_TARGET, PREDEFINED, class_of, user_class

    ordered = sorted(handlers,
                     key=lambda h: 1 if any(e.upper() == "OTHERS" for e in h.exceptions) else 0)
    caught_already: set[str] = set()
    for handler in ordered:
        names = [e.upper() for e in handler.exceptions]
        if "OTHERS" in names:
            caught = "MigratedException"
            comment = "WHEN OTHERS: only migrated exceptions, so a bug does not look like a business error"
        elif _cannot_happen_on_the_target(names, routine):
            # `PRAGMA EXCEPTION_INIT(e_locked, -54)` のように、**移行先では起こりえない** Oracle の
            # 誤りだけを捕まえる handler である。ScalarDB では「待たない」が既定で ORA-54 に相当する
            # 出来事が無く、衝突は commit で分かる（#9 §B の決定）。
            #
            # catch を出すと `MigratedException` を広く捕まえてしまい、**関係のない業務例外まで
            # 「ロックされている」に付け替える**。Oracle では他の例外は素通りしていたので、
            # 出さないほうが元に近い。
            file.comment(f"WHEN {', '.join(names)}: 捕まえていた Oracle の誤りは移行先では"
                         f"起こらないので、この handler は出さない。衝突は commit で分かり、"
                         f"再試行は呼び出し側の責務である（#9 §B）")
            # 未変換には数えない。**決めて出していない**ものであって、翻訳できなかったものではない
            # ——数えると、決定の結果が KPI では失敗のように見える
            continue
        else:
            # A PL/SQL-declared exception is its own class. It used to become `catch (MigratedException e)`, which
            # also caught a NO_DATA_FOUND raised in the same block and relabelled it as this handler's error --
            # in Oracle that NO_DATA_FOUND goes past a handler that does not name it.
            classes = list(dict.fromkeys(class_of(n, routine, _MODULE.get(), program=_PROGRAM.get())[1] for n in names))
            caught = " | ".join(classes)
            comment = f"WHEN {', '.join(names)}"
            for class_name in classes:
                file.add_import(f"{domain_package}.{class_name}")
            if "VALUE_ERROR" in names:
                comment += ("\nVALUE_ERROR: the size errors of a constrained declaration (Plsql.fit) and text that "
                            "is not a number (Plsql.dec / toNumber / arithmetic) reach this handler. CHAR(n) "
                            "padding and a conversion inside a SQL statement do not (rule EXC-001)")
            unreachable = [n for n in names if n in NEVER_RAISED_BY_TARGET]
            if unreachable:
                comment += (f"\n{', '.join(unreachable)}: nothing on the target raises this by itself, so this "
                            f"handler only runs for an explicit RAISE. In Oracle it also ran for the database's "
                            f"own error -- decide what the target should do instead (rule EXC-001)")
        if caught in caught_already:
            # Two PL/SQL exceptions can map to one Java class, and Java rejects two catches of one type.
            # Which handler applies is then a question about the mapping, so the second is reported rather
            # than silently merged into the first. (A nested block's handlers no longer arrive here: they
            # belong to the block and become its own catches -- #18.)
            file.comment(f"{comment}: a second handler for a type already caught")
            file.comment("    two PL/SQL exceptions map to one Java class here; this needs a human")
            result.untranslated.append(handler.id)
            continue
        caught_already.add(caught)
        file.comment(comment)
        # a handler may hold a block with handlers of its own; Java does not let the inner `e` shadow the outer
        outer = _HANDLER_ERROR.get()
        depth = outer.get(_CAUGHT, "")
        variable = "e" if not depth else f"e{int(depth[1:] or 1) + 1}"
        with file.block(f"catch ({caught} {variable})") as f:
            # handler の中の `SQLCODE` は「いま処理している例外の番号」である。catch が束ねている
            # 例外がそれを持っているので、そこから読む。handler の外では 0 なので、外では置かない
            # ——「いつでも 0」を名前として与えると、handler の外の `SQLCODE` が黙って通る
            # SQLERRM is the message with its ORA- prefix, and FORMAT_ERROR_BACKTRACE the "ORA-06512: at" lines:
            # both read from the caught exception (#38, samples/oracle-samples b04_6_2_user_exceptions)
            f.add_import("com.scalar.migrate.plsql.Plsql")
            errm = f"Plsql.sqlerrm({variable}.code(), {variable}.getMessage())"
            backtrace = f"Plsql.errorBacktrace({variable})"
            _HANDLER_ERROR.set({"SQLCODE": f"{variable}.code()", "sqlcode": f"{variable}.code()",
                                "SQLERRM": errm, "sqlerrm": errm,
                                "DBMS_UTILITY.FORMAT_ERROR_BACKTRACE": backtrace,
                                "dbms_utility.format_error_backtrace": backtrace,
                                "DBMS_UTILITY.FORMAT_ERROR_STACK": errm, "dbms_utility.format_error_stack": errm,
                                _CAUGHT: variable})
            try:
                _statements(f, handler.body, routine, result)
            finally:
                _HANDLER_ERROR.set(outer)


def _guarded(file: JavaFile, handlers: list[M.ExceptionHandler], body: list[M.Statement],
             routine: M.Routine, result: ServiceFile, domain_package: str) -> None:
    """The body of a `try`. Where a handler could catch a division by zero -- ZERO_DIVIDE by name, or OTHERS --
    the helper's `Plsql.ZeroDivide` becomes the migrated `ZeroDivideException` first, so that the catch is one it
    can reach. `catch (ZeroDivideException e)` on its own was dead code: the helper threw ArithmeticException.
    The same goes for VALUE_ERROR and the `Plsql.ValueError` a constrained declaration raises (`Plsql.fit`)."""
    names = {e.upper() for h in handlers for e in h.exceptions}
    raised = [(helper, migrated, variable) for oracle, helper, migrated, variable in _HELPER_ERRORS
              if names & {oracle, "OTHERS"}]
    if not raised:
        _statements(file, body, routine, result)
        return
    file.add_import("com.scalar.migrate.plsql.Plsql")
    with file.block("try") as inner:
        _statements(inner, body, routine, result)
    for helper, migrated, variable in raised:
        if domain_package:
            file.add_import(f"{domain_package}.{migrated}")
        with file.block(f"catch (Plsql.{helper} {variable})") as translated:
            translated.line(f"throw new {migrated}({variable}.getMessage());")


# Oracle's name, the runtime helper's own exception, the migrated class a handler names, the catch variable
_HELPER_ERRORS = (("ZERO_DIVIDE", "ZeroDivide", "ZeroDivideException", "zero"),
                  ("VALUE_ERROR", "ValueError", "ValueErrorException", "size"))


# 移行先では起こりえない Oracle の誤り。いまのところ行ロックが取れないこと（ORA-54）だけである
UNREACHABLE_ORACLE_ERRORS = {"-54"}


def _emits_a_catch(handlers: list[M.ExceptionHandler], routine: M.Routine) -> bool:
    """`catch` が 1 つでも出るか。移行先で起こりえない誤りだけの handler は出ないので、
    `try` を書くかどうかはこれで決まる。"""
    return any(not _cannot_happen_on_the_target([e.upper() for e in h.exceptions], routine)
               for h in handlers)


def _cannot_happen_on_the_target(names: list[str], routine: M.Routine) -> bool:
    """handler が捕まえているのが、移行先では起こりえない誤りだけか。

    判断は `PRAGMA EXCEPTION_INIT` が結びつけた**番号**で行う。名前で判断すると、同じ名前の
    別の例外に当たる。
    """
    bound = {d.name.upper(): (d.initial or "") for d in routine.declarations
             if d.declaration_kind == "exception"}
    codes = [bound.get(name) for name in names]
    return bool(codes) and all(code in UNREACHABLE_ORACLE_ERRORS for code in codes)


def _always_throws(statement: M.Statement, result: ServiceFile) -> bool:
    """Does the Java emitted for this statement always leave by throwing?

    A refused statement and RAISE do. So does an if / else (or CASE, whose missing ELSE throws CASE_NOT_FOUND)
    every branch of which does: javac then rejects whatever follows as unreachable, which is what happened when
    both arms of an IF opened a REF CURSOR the generator could not translate (#36, samples/oracle-samples).
    """
    if statement.id in result.untranslated or statement.kind == "Raise":
        return True
    if statement.kind == "Loop" and statement.loop_kind == "basic":
        # `while (true)` whose body throws before any EXIT never completes, so javac rejects what follows the
        # loop (samples/oracle-plsql-docs 12-30: an untranslated FETCH at the top of the loop, #77)
        for s in statement.body:
            if s.kind == "Exit" or (s.kind in ("If", "Case", "Block", "Loop") and _mentions_exit(s)):
                return False
            if _always_throws(s, result):
                return True
        return False
    if statement.kind in ("If", "Case"):
        branches = all(any(_always_throws(s, result) for s in b.body) for b in statement.branches)
        if statement.else_body:
            return branches and any(_always_throws(s, result) for s in statement.else_body)
        return branches and statement.kind == "Case"
    return False


def _mentions_exit(statement: M.Statement) -> bool:
    """Whether an EXIT is anywhere under this statement (it may leave the enclosing loop)."""
    for child in list(getattr(statement, "body", None) or []) + [s for b in getattr(statement, "branches", None) or []
                                                              for s in b.body] + list(getattr(statement, "else_body", None) or []):
        if child.kind == "Exit" or _mentions_exit(child):
            return True
    return False


def _always_leaves(statement: M.Statement) -> bool:
    """Does this statement always leave the block it is in without falling through: RETURN, an EXIT or CONTINUE
    without WHEN, or a nested block that returns (a RETURN is not an exception, so no handler stops it)?"""
    if statement.kind == "Return":
        return True
    if statement.kind in ("Exit", "Continue"):
        return not getattr(statement, "condition", None)
    if statement.kind == "Block":
        return any(s.kind == "Return" or (s.kind == "Block" and _always_leaves(s)) for s in statement.body)
    return False


def _always_exits(routine: M.Routine, result: ServiceFile) -> bool:
    """Does every path out of the generated body already return or throw?

    Java rejects a statement after one, so both the fall-through guard and the result return have to ask. A
    statement the generator refused counts as a throw: that is exactly what it emits.
    """
    def exits(statements: list[M.Statement]) -> bool:
        if not statements:
            return False
        for statement in statements:
            if statement.id in result.untranslated or _always_throws(statement, result):
                return True   # the refusal throws, and the rest of the block was dropped
            if statement.kind == "Loop" and statement.loop_kind == "basic" and not _mentions_exit(statement):
                return True   # `LOOP ... END LOOP` with no EXIT is `while (true)` with no break (12-34, #77)
        last = statements[-1]
        if last.kind == "Loop" and getattr(last, "returns_rows", False):
            return True   # `OPEN rc FOR q; RETURN rc;` became `return repository...(...)`
        if last.kind == "Block":
            # a block leaves by falling out of it unless its body and every handler leave for good
            return exits(last.body) and all(exits(h.body) for h in last.exception_handlers)
        if last.kind in ("If", "Case") and last.else_body:
            # Java sees that nothing follows an if / else whose every branch leaves, and rejects what is put there
            return exits(last.else_body) and all(exits(b.body) for b in last.branches)
        return last.kind in ("Return", "Raise")

    if not exits(routine.body):
        return False
    if not routine.exception_handlers:
        return True
    return all(exits(h.body) for h in routine.exception_handlers)


def _holder_java_type(file: JavaFile, type_ref: "M.TypeRef | None") -> str:
    """A parameter's Java type: the generated record for a record type (#74), else the mapped type."""
    record = record_class(type_ref)
    if record is not None:
        file.add_import(f"{_DOMAIN.get()}.{record}" if _DOMAIN.get() else record)
        return record
    mapped = signature_type(type_ref)
    file.add_import(*mapped.imports)
    return mapped.name


def _rebuilt(file: JavaFile, current: str, resolved: str, record: str, path: list[str], value: str) -> str | None:
    """`current` -- a record of type `resolved`, class `record` -- rebuilt with the field at `path` set to `value`.

    Generated records are immutable, so `v_rec.id := 1` builds a new one with that component replaced (#45). A path
    through a nested record (`friend.name.first`, 5-35) rebuilds each level on the way down (#82). None when a step
    of the path is not a field, or a nested field's record has no class to build."""
    columns = record_columns(resolved)
    if not any(c.lower() == path[0].lower() for c, _ in columns):
        return None
    components = []
    for column, declared in columns:
        read = f"{current}.{java_name(column)}()"
        if column.lower() != path[0].lower():
            components.append(read)
        elif len(path) == 1:
            # the new value takes the component's type: `default_week.week := i` put an int into a BigDecimal
            components.append(_coerce(file, value, java_type(declared).name))
        else:
            nested = java_type(declared).name
            if not RECORD_TEXT.match(declared) or nested == "Object":
                return None
            file.add_import(*java_type(declared).imports)
            inner = _rebuilt(file, read, declared, nested, path[1:], value)
            if inner is None:
                return None
            components.append(inner)
    return f"new {record}({', '.join(components)})"


RECORD_TEXT = re.compile(r"^\s*RECORD\(", re.IGNORECASE)


def _element_record(resolved: str | None) -> str | None:
    """The `RECORD(...)` a collection holds, when it holds records."""
    element = re.match(r"^\s*TABLE\s+OF\s+(RECORD\(.*\))(?:\s+INDEX\s+BY\s+.+)?$", resolved or "",
                       re.IGNORECASE | re.DOTALL)
    return element.group(1) if element else None


def _empty_record(resolved: str, record: str, file: "JavaFile | None" = None) -> str:
    """A record whose fields are all NULL -- and whose nested records are records of NULLs, as Oracle's are: a
    nested field left null made `friend.name.first := 'John'` read a component of nothing (#82)."""
    if file is not None:
        file.add_import(*java_type(resolved).imports)   # the record's own class, when it is a nested one (5-51)
    components = []
    for _, declared in record_columns(resolved):
        nested = java_type(declared).name
        if RECORD_TEXT.match(declared) and nested != "Object":
            if file is not None:
                file.add_import(*java_type(declared).imports)
            components.append(_empty_record(declared, nested, file))
        else:
            components.append(_literal_default(declared) or "null")
    return f"new {record}({', '.join(components)})"


def _literal_default(declared: str) -> str | None:
    """A field's literal default (`:= 'abcde'` / `DEFAULT 0`) as Java, in the field's own Java type: a
    `years INTEGER DEFAULT 35` field is an Integer, and a BigDecimal there did not compile (5-51)."""
    text = re.search(r"(?::=|\bDEFAULT\b)\s*'((?:[^']|'')*)'\s*$", declared, re.IGNORECASE)
    number = re.search(r"(?::=|\bDEFAULT\b)\s*(-?\d+(?:\.\d+)?)\s*$", declared, re.IGNORECASE)
    if text:
        return '"' + text.group(1).replace("''", "'").replace('"', '\\"') + '"'
    if number:
        literal = number.group(1)
        kind = java_type(re.split(r"\s*(?::=|\bDEFAULT\b)", declared, maxsplit=1, flags=re.IGNORECASE)[0]).name
        if kind == "Integer" and "." not in literal:
            return literal
        if kind == "Long" and "." not in literal:
            return f"{literal}L"
        if kind == "Double":
            return f"{float(literal)!r}"
        if kind == "Float":
            return f"{float(literal)!r}f"
        return f'new java.math.BigDecimal("{literal}")'
    return None


def _record_default(type_ref: "M.TypeRef | None", file: "JavaFile | None" = None) -> str | None:
    """An OUT record starts as a record with every field NULL (or its TYPE's default), as a PL/SQL one does:
    `x.f` read before anything is assigned is 'abcde' for `RECORD (f VARCHAR2(5) := 'abcde')` (8-16)."""
    record = record_class(type_ref)
    if record is None:
        return None
    components = []
    for _, declared in record_columns(type_ref.resolved or ""):
        # `f VARCHAR2(5) := 'abcde'` and `f VARCHAR2(5) DEFAULT 'abcde'` are the same default (5-45, #82); a
        # nested record's text holds its own fields' defaults, so it is looked at first
        if RECORD_TEXT.match(declared) and java_type(declared).name != "Object":
            if file is not None:
                file.add_import(*java_type(declared).imports)
            components.append(_empty_record(declared, java_type(declared).name, file))   # a nested record (#82)
        else:
            components.append(_literal_default(declared) or "null")
    return f"new {record}({', '.join(components)})"


def _row_type(declaration: M.Declaration) -> str | None:
    """A local whose type is a record has the record P2-5 generated for it, not `Object`.

    Both shapes land here: a `%ROWTYPE` of a table, and a package-local `TYPE t IS RECORD (...)`. They are the
    same thing -- a named list of typed fields -- and the generator names them apart only so that a table's row
    and a package's record cannot collide.
    """
    if declaration.type is None:
        return None
    if declaration.type.origin == "rowtype":
        return java_class_name(declaration.type.oracle.split("%")[0]) + "Row"
    if declaration.type.origin == "record":
        return java_class_name(declaration.type.oracle.rpartition(".")[2])
    return None


def _declaration(file: JavaFile, declaration: M.Declaration, routine: M.Routine,
                 result: ServiceFile) -> None:
    if declaration.declaration_kind in ("cursor", "exception", "type"):
        return  # cursors live in the repository; exceptions and types are generated elsewhere
    row = _row_type(declaration)
    if row is not None:
        file.add_import(f"{_DOMAIN.get()}.{row}" if _DOMAIN.get() else row)
        # a PL/SQL record is born with every field NULL, or the default its TYPE gave the field (#45); the
        # Java record is immutable, so it is built here and rebuilt on each field assignment
        if declaration.initial:
            # `r CONSTANT My_Types.My_Rec := My_Types.Init_My_Rec();`: the record the initialiser gives, not one
            # of NULL fields (5-33)
            try:
                rendered = _expr(file, declaration.initial, routine, result)
            except Untranslatable as e:
                file.comment(f"not translated: {declaration.name} := {e.text.strip()[:120]}")
                file.line(f"{row} {_local(declaration.name)} = null;")
                file.line(f'if (true) throw new UnsupportedOperationException("unresolved in declaration '
                          f'{declaration.name}: {", ".join(e.names)}");')
                if routine.id not in result.untranslated:
                    result.untranslated.append(routine.id)
                return
            file.line(f"{row} {_local(declaration.name)} = ({row}) {rendered};")
            return
        components = []
        for field_name, declared in record_columns(declaration.type.resolved if declaration.type else ""):
            nested = java_type(declared).name
            if RECORD_TEXT.match(declared):
                # a nested record: a record of its own defaults, never null (#82). Its text holds its fields'
                # defaults, which are not this field's
                components.append(_empty_record(declared, nested, file) if nested != "Object" else "null")
                continue
            # `DEFAULT 'John'` is the same default as `:= 'John'` (5-45, #82)
            default = re.search(r"(?::=|\bDEFAULT\b)\s*(.+)$", declared, re.IGNORECASE)
            try:
                components.append(_expr(file, default.group(1).strip(), routine, result) if default else "null")
            except Untranslatable as e:
                # a field default the translator has no Java for stopped the whole generation (#70, 5-36); it
                # gets the initialiser's treatment: the record is declared, the routine stops here
                file.comment(f"not translated: default of {declaration.name}.{field_name}: {e.text.strip()[:120]}")
                file.line(f"{row} {_local(declaration.name)} = null;")
                file.line(f'if (true) throw new UnsupportedOperationException("unresolved in declaration '
                          f'{declaration.name}.{field_name}: {", ".join(e.names)}");')
                if routine.id not in result.untranslated:
                    result.untranslated.append(routine.id)
                return
        file.line(f"{row} {_local(declaration.name)} = new {row}({', '.join(components)});")
        return
    if _collection_kind(declaration) and not declaration.initial \
            and "INDEX BY" in (declaration.type.resolved or "").upper():
        # an associative array exists from its declaration, empty; only a nested table / VARRAY starts NULL
        mapped = java_type(declaration.type.resolved)
        file.add_import(*mapped.imports, "com.scalar.migrate.plsql.Plsql")
        constructor = "Plsql.indexBy()" if _collection_kind(declaration) == "map" else "Plsql.table()"
        file.line(f"{mapped.name} {_local(declaration.name)} = {constructor};")
        return
    mapped = java_type(declaration.type.resolved if declaration.type else None)
    file.add_import(*mapped.imports)
    # PL/SQL initialises a declared variable to NULL; Java leaves it definitely-unassigned, and a handler that
    # reads it then fails to compile. Writing the NULL out keeps the two the same.
    initial = " = null" if mapped.name not in ("int", "long", "double", "boolean") else ""
    if declaration.initial:
        try:
            rendered = _expr(file, declaration.initial, routine, result, boolean_value=mapped.name == "Boolean")
        except Untranslatable as e:
            # `c INTEGER := DBMS_SQL.OPEN_CURSOR` -- an initialiser the translator has no Java for. Statements
            # already came out as a comment plus a throw; a declaration crashed the whole run instead
            # (2026-09-24, samples/oracle-samples). Same treatment: the variable is declared, the routine stops here.
            file.comment(f"not translated: {declaration.name} := {e.text.strip()[:120]}")
            file.comment(f"    unresolved: {', '.join(e.names)}")
            file.line(f"{mapped.name} {_local(declaration.name)}{initial};")
            file.line(f'if (true) throw new UnsupportedOperationException("unresolved in declaration '
                      f'{declaration.name}: {", ".join(e.names)}");')
            if routine.id not in result.untranslated:
                result.untranslated.append(routine.id)
            return
        if mapped.name == "BigDecimal" and rendered.lstrip("-").isdigit():
            file.add_import("com.scalar.migrate.plsql.Plsql")
            rendered = f"Plsql.number({rendered})"
        elif mapped.name == "BigDecimal" and rendered.lstrip("-").replace(".", "", 1).isdigit():
            # `Plsql.number` takes a long: `v NUMBER := 1.005` came out as Java that does not compile. The text
            # constructor keeps the literal exact, which a double would not
            rendered = f'new BigDecimal("{rendered}")'
        rendered = _convert_variable(file, routine, declaration.initial, rendered, mapped.name)
        initial = f" = {_constrain(file, _coerce(file, rendered, mapped.name), declaration.type)}"
    file.line(f"{mapped.name} {_local(declaration.name)}{initial};")


def _statements(file: JavaFile, statements: list[M.Statement], routine: M.Routine,
                result: ServiceFile) -> None:
    if not statements:
        file.line("// the PL/SQL body is empty")
        return
    for index, statement in enumerate(statements):
        _statement(file, statement, routine, result)
        if _always_throws(statement, result):
            # Java rejects a statement after one that always throws. Stopping here is also honest: the rest of
            # the block cannot run, and pretending otherwise would hide how much of the routine is missing.
            file.comment("the rest of this block is unreachable while the statement above is unresolved")
            break
        if index + 1 < len(statements) and _always_leaves(statement):
            # PL/SQL compiles a statement after RETURN / EXIT and never runs it; Java refuses to compile it (#77,
            # samples/oracle-plsql-docs 8-7). Left out, with a note, the method does what the routine did
            file.comment(f"{len(statements) - index - 1} statement(s) after this one never run in PL/SQL either "
                         "(unreachable); not emitted")
            break


def _refuses_somewhere(routine: M.Routine, result: ServiceFile) -> bool:
    """この routine のどこかで翻訳を拒むか。**本番の生成に入る前に、捨てる紙で 1 回書いてみる。**

    予測ではなく実行で答えるのは、「翻訳できない」の定義が生成器のあちこちに分かれているからである
    （未知の名前・扱えない文の種類・ScalarDB が拒む SQL）。同じことを 2 か所で判断すると、片方が
    増えたときにもう片方が黙って古くなる——この repo が何度も直してきた形である（#12 / #21）。
    """
    probe = ServiceFile(file=JavaFile(package="probe", name="Probe", source=""))
    seen = _REFUSES_LATER.get()
    _REFUSES_LATER.set(False)   # 下書きの中でこの規則を効かせない（数えたいのは元の拒否だけ）
    try:
        _statements(probe.file, routine.body, routine, probe)
        for handler in routine.exception_handlers:
            _statements(probe.file, handler.body, routine, probe)
    except Exception:
        return True   # 書いてみて落ちるなら、それも拒否である
    finally:
        _REFUSES_LATER.set(seen)
    return bool(probe.untranslated)


def draws_a_sequence(statement: M.Statement) -> bool:
    """この文が採番するか。採番は**トランザクションの外へ出る**（#25）。

    `CACHE n` の sequence は hi/lo に移す（計画 §9）ので、引いた番号は呼び出し側が rollback しても
    戻らない。DML はトランザクションの中なので戻る——だから止めるべきはここだけである。
    """
    return any((bind.expression or "").upper().endswith(".NEXTVAL")
               for bind in getattr(statement, "binds", None) or [])


def _statement(file: JavaFile, statement: M.Statement, routine: M.Routine, result: ServiceFile) -> None:
    _source_comment(file, statement)
    if _REFUSES_LATER.get() and draws_a_sequence(statement):
        # この routine は完走できない。採番だけはトランザクションを抜けるので、**引く前に止める**
        # ——拒否された routine が欠番を作らないようにする（#25 / 2026-09-18 の決定）。
        # ここより前の文はそのまま出す: どこまで移行できているかが見え、コンパイル検査も受ける。
        file.comment("この routine には翻訳できない文がある。採番はトランザクションを抜けるので、"
                     "引く前に止める")
        file.line('throw new UnsupportedOperationException("routine が完走できないので、'
                  'この文の採番は行わない");')
        if statement.id not in result.untranslated:
            result.untranslated.append(statement.id)
        return
    try:
        _translate_statement(file, statement, routine, result)
    except Untranslatable as e:
        file.comment(f"not translated: {e.text.strip()[:120]}")
        file.comment(f"    unresolved: {', '.join(e.names)}")
        file.line(f'throw new UnsupportedOperationException("unresolved in {statement.kind}: '
                  f'{", ".join(e.names)}");')
        if statement.id not in result.untranslated:
            result.untranslated.append(statement.id)


def _folded_by_writer(routine: M.Routine) -> bool:
    """Whether every `:NEW.x := ...` of this trigger body is one the writer folds into its values (#47). A body
    whose assignments cannot be folded (a sequence read, a conditional assignment) is never called, and an
    assignment to its own argument would only look as if the row were changed."""
    from ..triggers import Trigger

    module = _MODULE.get()
    if module is None or routine.routine_kind != "trigger-body":
        return False
    shape = Trigger(module=module, routine=routine, table=(module.trigger_table or "").lower(),
                    timing=(module.trigger_timing or "BEFORE").upper(), event=(module.trigger_event or "").upper())
    return shape.foldable_assignments() is not None


def _translate_statement(file: JavaFile, statement: M.Statement, routine: M.Routine,
                         result: ServiceFile) -> None:
    kind = statement.kind

    if kind == "Assignment":
        if (statement.target or "").lstrip(":").upper().startswith(("NEW.", "OLD.")):
            # `:NEW.email := UPPER(:NEW.email)` は、**これから書き込まれる行を書き換える**もので、Java の引数への
            # 代入では呼び出し側に返らない。書く側が同じ式を書く値に畳み込む（triggers._fold、#47）ので、本体では
            # 自分の引数に代入して、あとの検査が書き換え後の値を読めるようにする。畳み込めない代入（条件つき、
            # 局所変数を読む）の trigger は書く側が呼ばない（TRIGGER_REDESIGN）ので、ここは通らない
            target = (statement.target or "").strip()
            if target.lstrip(":").upper().startswith("OLD.") or not _folded_by_writer(routine):
                raise Untranslatable([f"assignment to {statement.target}"], statement.target or "")
            mapped = next((java for name, java in _scope(routine, _MODULE.get()).items()
                           if name.lower() == target.lower()), None)
            if mapped is None:
                raise Untranslatable([f"assignment to {statement.target}"], statement.target or "")
            file.comment(f"{target}: 書く側は同じ式を書く値に畳み込んでいる（#47）。ここでは後続の検査のために引数へ代入する")
            file.line(f"{mapped} = {_expr(file, statement.expression, routine, result)};")
            return
        written = (statement.target or "").strip()
        element_field = re.fullmatch(r"(?P<collection>[\w$#]+)\s*\((?P<key>[^()]*)\)\s*\.\s*(?P<path>[\w$#.]+)", written)
        if element_field:
            # `v1(1).f1 := 1`: a field of an element that is a record (6-30, #82). The element is rebuilt with that
            # field replaced -- or built, when there is none yet, as Oracle creates it
            holder = _holder(routine, element_field.group("collection"))
            element = re.sub(r"^(?:List|Map)<(?:[^,]+,\s*)?(.+)>$", r"\1", java_type(holder.type.resolved).name) \
                if holder is not None and _collection_kind(holder) else None
            shape = _element_record(holder.type.resolved) if element else None
            if shape is None:
                raise Untranslatable([f"assignment to collection element {written}"], written)
            if _DOMAIN.get():
                file.add_import(f"{_DOMAIN.get()}.{element}")
            file.add_import("com.scalar.migrate.plsql.Plsql")
            collection = _local(holder.name)
            key = _expr(file, element_field.group("key"), routine, result)
            current = (f"(Plsql.exists({collection}, {key}) ? ({element}) Plsql.at({collection}, {key}) : "
                       f"{_empty_record(shape, element, file)})")
            value = _expr(file, statement.expression, routine, result)
            rebuilt = _rebuilt(file, current, shape, element, element_field.group("path").split("."), value)
            if rebuilt is None:
                raise Untranslatable([f"assignment to collection element {written}"], written)
            file.line(f"Plsql.set({collection}, {key}, {rebuilt});")
            return
        if "(" in written:
            # `v_sal(r.last_name) := r.salary`: an element of a collection (#45). `Plsql.set` grows a nested
            # table only through EXTEND, as Oracle does; an associative array takes any key
            head, _, subscript = written.partition("(")
            holder = _holder(routine, head.strip())
            if holder is None or not _collection_kind(holder):
                raise Untranslatable([f"assignment to collection element {written}"], written)
            file.add_import("com.scalar.migrate.plsql.Plsql")
            subscripts = _subscripts("(" + subscript)
            if subscripts is None:
                raise Untranslatable([f"assignment to collection element {written}"], written)
            # `nva(4)(4) := 1`: an element of an element (a varray of varrays, 5-11 / 5-13, #76). The outer ones
            # are read; the innermost is set -- the inner collection is the same object inside the outer one
            container = _local(holder.name)
            for inner in subscripts[:-1]:
                container = f"Plsql.at({container}, {_expr(file, inner, routine, result)})"
            key = _expr(file, subscripts[-1], routine, result)
            element = re.sub(r"^(?:List|Map)<(?:[^,]+,\s*)?(.+)>$", r"\1", java_type(holder.type.resolved).name)
            value = _expr(file, statement.expression, routine, result)
            if len(subscripts) == 1:
                value = _coerce(file, value, element)
            file.line(f"Plsql.set({container}, {key}, {value});")
            return
        if "." in written and (_holder(routine, written.partition(".")[0]) is not None
                               or written.partition(".")[0].lower() in {k.lower() for k in _LOOP_ROWS.get()}):
            # `v_rec.id := 1`: a field of a record. The generated records are immutable, so the record is
            # rebuilt with that one component replaced (#45)
            head, _, path = written.partition(".")
            holder = _holder(routine, head)
            record = _row_type(holder) if holder is not None else None
            if record is None:
                raise Untranslatable([f"assignment to record field {written}"], written)
            if _DOMAIN.get():
                file.add_import(f"{_DOMAIN.get()}.{record}")
            value = _expr(file, statement.expression, routine, result)
            variable = java_name(holder.name)
            rebuilt = _rebuilt(file, variable, holder.type.resolved or "", record, path.split("."), value)
            if rebuilt is None:
                raise Untranslatable([f"assignment to record field {written}"], written)
            file.line(f"{variable} = {rebuilt};")
            return
        # the target goes through the translator too: `:NEW.col` is not a Java name, and rendering it anyway
        # produced code that did not compile
        copied = _record_copy(file, routine, statement.target, statement.expression)
        if copied is not None:
            file.line(copied)
            return
        target = _expr(file, statement.target, routine, result)
        target_type = _local_type(routine, statement.target)
        value = _expr(file, statement.expression, routine, result, boolean_value=target_type == "Boolean")
        value = _convert_variable(file, routine, statement.expression, value, target_type)
        holder = _holder(routine, statement.target)
        file.line(f"{target} = {_constrain(file, _coerce(file, value, target_type), holder.type if holder else None)};")
    elif kind == "Return":
        returns = signature_type(routine.return_type).name \
            if routine.return_type is not None else "void"
        outs = [java_name(p.name) for p in routine.parameters if p.direction in ("OUT", "IN OUT")]
        value = None
        if statement.expression:
            value = _coerce(file, _expr(file, statement.expression, routine, result,
                                        boolean_value=returns == "Boolean"), returns)
        if outs:
            # the OUT arguments travel in the result record, so a RETURN in the middle has to build it too:
            # a bare `return;` in a method that returns the record did not compile
            components = ([value or "null"] if routine.return_type is not None else []) + outs
            file.line(f"return new {result_record_name(routine)}({', '.join(components)});")
        elif value is not None:
            file.line(f"return {value};")
        elif returns != "void":
            # `RETURN;` in a function: a PIPELINED function ends its stream this way (#40)
            file.line("return null;")
        else:
            file.line("return;")
    elif kind == "If":
        _if(file, statement, routine, result)
    elif kind == "Case":
        _case(file, statement, routine, result)
    elif kind == "Loop":
        _loop(file, statement, routine, result)
    elif kind == "Block":
        _block(file, statement, routine, result)
    elif kind == "Raise":
        _raise(file, statement, routine, result)
    elif kind in ("Exit", "Continue"):
        # `EXIT outer_loop WHEN ...` names the loop it leaves. Dropping the label left the inner loop only, and
        # the outer `while (true)` then never ended.
        jump = "break" if kind == "Exit" else "continue"
        if statement.label:
            if statement.label.lower() not in _LOOP_LABELS.get():
                raise Untranslatable([f"{kind.upper()} {statement.label}"], statement.label)
            jump = f"{jump} {java_name(statement.label)}"
        file.line(f"if ({_expr(file, statement.condition, routine, result, condition=True)}) {jump};"
                  if statement.condition else f"{jump};")
    elif kind == "Null":
        file.line("// NULL;")
    elif kind == "Call":
        _call(file, statement, routine, result)
    elif kind in ("Fetch", "CloseCursor") and \
            (statement.cursor or "").split("(")[0].strip().lower() in _general_cursors(routine):
        if kind == "Fetch":
            _fetch(file, statement, routine)
        else:
            file.line(f"{_cursor_state((statement.cursor or '').split('(')[0].strip().lower())}.close();")
    elif kind == "SqlOperation":
        _sql(file, statement, routine)
    elif kind == "DynamicSql":
        _dynamic(file, statement, routine, result)
    elif kind in ("Commit", "Rollback", "Savepoint") and routine.id in split._BOUNDARIES.get().caller:
        # transactions.callerBoundary: the caller begins, commits and rolls back. The statement is left out,
        # visibly. A ROLLBACK that undid earlier writes now undoes nothing unless the caller rolls back
        file.comment(f"{kind.upper()} は呼び出し側の境界に移した（limits.yaml transactions.callerBoundary: "
                     f"{split._BOUNDARIES.get().caller[routine.id]}）")
    else:
        _untranslated(file, statement, result)


def _trigger_when(file: JavaFile, routine: M.Routine, result: ServiceFile) -> bool:
    """`WHEN (OLD.status <> NEW.status)` を、本体の前の番人として出す。

    発火条件は「変わったときだけ」であって、落とすと**記録される量が変わる**——監査 trigger なら
    更新のたびに 1 行増える。条件の中の名前が 1 つでも解決しなければ、本体ごと拒む: 条件を落として
    本体だけ動かすのは、**元より多く実行する**ということである。
    """
    module = _MODULE.get()
    condition = getattr(module, "trigger_when", None) if module else None
    if not condition:
        return False
    file.comment(f"WHEN ({condition})")
    try:
        file.line(f"if (!({_expr(file, condition, routine, result, condition=True)})) return;")
    except Untranslatable as e:
        # 条件が読めないまま本体を動かすと、**元より多く実行する**。拒むほうを選ぶ。
        file.comment(f"    unresolved: {', '.join(e.names)}")
        file.line(f'throw new UnsupportedOperationException("unresolved in trigger WHEN: '
                  f'{", ".join(e.names)}");')
        if routine.id not in result.untranslated:
            result.untranslated.append(routine.id)
        return True
    file.line()
    return False


def _local(name: str) -> str:
    """The Java name of a PL/SQL variable where it is used: the block's own when a nested `DECLARE` (or a FOR
    loop's index) gave it a new one because the name was taken further out (#71), else the plain one."""
    for plsql_name, java in _BLOCK_LOCALS.get().items():
        if plsql_name.lower() == name.lower():
            return java
    return java_name(name)


def _taken(routine: M.Routine) -> set[str]:
    """Java names already declared in the method at this point: the routine's parameters and declarations, a
    trigger's locals, and every enclosing block's and loop's."""
    module = _MODULE.get()
    names = [p.name for p in routine.parameters] + [d.name for d in routine.declarations]
    if module is not None and module.module_kind == "trigger":
        names += [d.name for d in module.declarations]
    return {java_name(n) for n in names} | set(_BLOCK_LOCALS.get().values())


def _fresh(java: str, taken: set[str]) -> str:
    """`a` when free, else `a_2`, `a_3`... Java cannot declare a name its enclosing scope already has, which PL/SQL
    allows in a nested block; generated names are camelCase, so one with an underscore is never among them."""
    if java not in taken:
        return java
    n = 2
    while f"{java}_{n}" in taken:
        n += 1
    return f"{java}_{n}"


def _block(file: JavaFile, statement: M.Block, routine: M.Routine, result: ServiceFile) -> None:
    """A nested `BEGIN ... EXCEPTION ... END` (#18), as the Java block it is.

    Its handlers catch only what its own body raises, which is what the PL/SQL said and what the hoisting
    this replaces could not express. Without handlers it is a bare block, which still matters: a `DECLARE`
    inside it scopes its names the way PL/SQL does.

    The declarations go **inside** the braces. A `DECLARE` name belongs to its block and nowhere else, so
    writing it outside gave it the whole method instead, and two blocks declaring the same name produced a
    Java duplicate declaration -- code that does not compile, reported as `AUTO`. The braces are the block's
    own, not the `try`'s: a handler reads the block's variables, and a name declared inside `try` is not
    visible from `catch`.
    """
    outer = _BLOCK_LOCALS.get()
    taken, renamed = _taken(routine), {}
    for d in statement.declarations:
        renamed[d.name] = _fresh(java_name(d.name), taken)
        taken.add(renamed[d.name])
    _BLOCK_LOCALS.set({**outer, **renamed})
    outer_holders = _BLOCK_HOLDERS.get()
    _BLOCK_HOLDERS.set(outer_holders + tuple(statement.declarations))
    try:
        with file.block("") as scope:
            for declaration in statement.declarations:
                _declaration(scope, declaration, routine, result)
            if not _emits_a_catch(statement.exception_handlers, routine):
                _handlers(scope, statement.exception_handlers, routine, result, _DOMAIN.get() or "")
                _statements(scope, statement.body, routine, result)
                return
            with scope.block("try") as f:
                _guarded(f, statement.exception_handlers, statement.body, routine, result, _DOMAIN.get() or "")
            _handlers(scope, statement.exception_handlers, routine, result, _DOMAIN.get() or "")
    finally:
        _BLOCK_LOCALS.set(outer)
        _BLOCK_HOLDERS.set(outer_holders)


def _if(file: JavaFile, statement: M.If, routine: M.Routine, result: ServiceFile) -> None:
    for index, branch in enumerate(statement.branches):
        keyword = "if" if index == 0 else "} else if"
        condition = _expr(file, branch.condition, routine, result, condition=True)
        with file.block(("if" if index == 0 else "else if") + f" ({condition})") as f:
            _statements(f, branch.body, routine, result)
    if statement.else_body:
        with file.block("else") as f:
            _statements(f, statement.else_body, routine, result)


def _case(file: JavaFile, statement: M.Case, routine: M.Routine, result: ServiceFile) -> None:
    file.comment("CASE lowered to if/else: PL/SQL CASE without ELSE raises CASE_NOT_FOUND, "
                 "which the final else preserves")
    for index, branch in enumerate(statement.branches):
        # a searched CASE branches on its WHEN like an IF (#65); a simple CASE's WHEN is a value to compare
        condition = _expr(file, branch.condition, routine, result, condition=not statement.selector)
        if statement.selector:
            condition = f"Plsql.eq({_expr(file, statement.selector, routine, result)}, {condition})"
            file.add_import("com.scalar.migrate.plsql.Plsql")
        with file.block(("if" if index == 0 else "else if") + f" ({condition})") as f:
            _statements(f, branch.body, routine, result)
    with file.block("else") as f:
        if statement.else_body:
            _statements(f, statement.else_body, routine, result)
        else:
            f.line('throw new IllegalStateException("CASE_NOT_FOUND");')


def _loop(file: JavaFile, statement: M.Loop, routine: M.Routine, result: ServiceFile) -> None:
    label = f"{java_name(statement.label)}: " if statement.label else ""
    index_name = None   # a numeric FOR loop's index: a local of the body only
    if statement.loop_kind == "while":
        opening = f"{label}while ({_expr(file, statement.condition, routine, result, condition=True)})"
    elif statement.loop_kind == "cursor-for" and statement.query is not None:
        counting = _COUNTING.get()
        if not counting and _accumulates(statement):
            # SQL%ROWCOUNT after the loop is the rows of every iteration -- and 0 when it ran none, not what the
            # statement before it left (#109)
            file.line("rowCount = 0;")
        token = _COUNTING.set(counting or _accumulates(statement))
        try:
            _cursor_for(file, statement, routine, result)
        finally:
            _COUNTING.reset(token)
        return
    elif statement.loop_kind == "forall" and _forall_collection(statement, routine) is not None:
        _forall(file, statement, routine, result)
        return
    elif statement.loop_kind == "for" and (numeric := NUMERIC_FOR.match(statement.cursor or "")):
        # `FOR i IN [REVERSE] low .. high`: PL/SQL evaluates the bounds once, so the end is held in a second
        # loop variable; the index is a PLS_INTEGER (#37, samples/oracle-samples b04_2_control_flow)
        file.add_import("com.scalar.migrate.plsql.Plsql")
        index_name = numeric.group("index")
        # `FOR i IN ...` where the routine also declares an `i`: the loop's own i hides it in PL/SQL (4-20, #71)
        index = _fresh(java_name(index_name), _taken(routine))
        low = _expr(file, numeric.group("low"), routine, result)
        high = _expr(file, numeric.group("high"), routine, result)
        if numeric.group("reverse"):
            opening = (f"{label}for (int {index} = Plsql.toInt({high}), {index}End = Plsql.toInt({low}); "
                       f"{index} >= {index}End; {index}--)")
        else:
            opening = (f"{label}for (int {index} = Plsql.toInt({low}), {index}End = Plsql.toInt({high}); "
                       f"{index} <= {index}End; {index}++)")
    elif statement.loop_kind in ("cursor-for", "forall", "for"):
        # A named cursor's query is still not modelled as a statement, so there is nothing to iterate. Emitting
        # a call to a repository method that does not exist would give code that cannot compile; refusing keeps
        # the gap where a reviewer sees it.
        raise Untranslatable([f"{statement.loop_kind} loop"], statement.cursor or statement.kind)
    else:
        opening = f"{label}while (true)"
    outer = _LOOP_LABELS.get()
    outer_locals = _BLOCK_LOCALS.get()
    if statement.label:
        _LOOP_LABELS.set(outer | {statement.label.lower()})
    if index_name:
        _BLOCK_LOCALS.set({**outer_locals, index_name: index})
    try:
        with file.block(opening) as f:
            _statements(f, statement.body, routine, result)
    finally:
        _LOOP_LABELS.set(outer)
        _BLOCK_LOCALS.set(outer_locals)


# `j IN REVERSE 1 .. 6`, `a IN 1 .. v_max`: what the lowering keeps of a numeric FOR loop
NUMERIC_FOR = re.compile(r"^\s*(?P<index>[\w$#]+)\s+IN\s+(?P<reverse>REVERSE\s+)?(?P<low>.+?)\s*\.\.\s*(?P<high>.+?)\s*$",
                         re.IGNORECASE | re.DOTALL)

FORALL_BOUND = re.compile(r"^\s*1\s*\.\.\s*(?P<collection>[\w$#]+)\s*\.\s*COUNT\s*$", re.IGNORECASE)


def _accumulates(loop: M.Loop) -> bool:
    """Whether the loop's own DML adds to `rowCount` rather than setting it."""
    return any(getattr(s, "accumulates_rowcount", False) for s in _walk(loop.body))


def _forall_collection(statement: M.Loop, routine: M.Routine) -> tuple[str, str] | None:
    """`FORALL i IN 1 .. p_ids.COUNT` の (コレクション, 添字)。回せない形なら None。

    添字の名前は本体の参照から採る——`FORALL` の索引は IR に残っていないが、`p_ids(i)` の `i` が
    それである。本体がコレクションの要素を読んでいなければ、ループにしても意味が無い。
    """
    bound = FORALL_BOUND.match(statement.cursor or "")
    if bound is None:
        return None
    collection = bound.group("collection").lower()
    for inner in _forall_statements(statement):
        for bind in getattr(inner, "binds", None) or []:
            reference = COLLECTION_ELEMENT.match(bind.plsql_variable or "")
            if reference and reference.group("collection").lower() == collection:
                return collection, reference.group("index")
    return None


COLLECTION_ELEMENT = re.compile(r"^(?P<collection>[\w$#]+)\s*\(\s*(?P<index>[\w$#]+)\s*\)$")


def _forall_statements(statement: M.Loop) -> list[M.Statement]:
    """The FORALL body's statements, and the query of a loop inside it: the RMW split of a multi-row UPDATE (#9)
    turns the DML into a loop over the rows it touches, whose query is where `v_depts(i)` is read (#51)."""
    out = []
    for inner in _walk(statement.body):
        out.append(inner)
        if getattr(inner, "query", None) is not None:
            out.append(inner.query)
    return out


def _forall(file: JavaFile, statement: M.Loop, routine: M.Routine, result: ServiceFile) -> None:
    """`FORALL i IN 1 .. p_ids.COUNT <DML>` を、その要素を回す Java のループにする。

    **FORALL は 1 往復、ループは要素ごとに 1 回**である。答えは変わらないが性能は変わる——#14 で
    `BULK COLLECT` + `FORALL` を走査ループにしたときと同じ代償で、そこと同じく隠さずに書く。

    `SAVE EXCEPTIONS`（部分失敗を許す原子性）はここでは扱わない。`BULK-002` が REDESIGN として
    捕まえ続ける。
    """
    collection, index = _forall_collection(statement, routine)
    java = _expr(file, collection, routine, result)
    bound = _holder(routine, collection)
    # an INDEX BY PLS_INTEGER table is a Map keyed as PL/SQL keys it (#93): walked 1 .. COUNT, and an element
    # read through Plsql.at, so a missing key raises as Oracle's FORALL does rather than reading null
    keyed = bound is not None and _collection_kind(bound) == "map"
    # 本体が読むコレクションは 1 つとは限らない（`p_ids(i)` と `p_names(i)` が並ぶ）。回す長さは
    # 境界が名指したものから採り、要素の読み方は**本体が読んでいるすべて**について用意する
    scope = {}
    for inner in _forall_statements(statement):
        for bind in getattr(inner, "binds", None) or []:
            reference = COLLECTION_ELEMENT.match(bind.plsql_variable or "")
            if reference is None or reference.group("index").lower() != index.lower():
                continue
            name = reference.group("collection")
            holder = _holder(routine, name)
            if keyed and holder is not None and _collection_kind(holder) == "map":
                element = re.sub(r"^Map<[^,]+,\s*(.+)>$", r"\1", java_type(holder.type.resolved).name)
                scope[f"{name}({index})".lower()] = \
                    f"(({element}) Plsql.at({_expr(file, name, routine, result)}, {java_name(index)}))"
                continue
            scope[f"{name}({index})".lower()] = \
                f"{_expr(file, name, routine, result)}.get({java_name(index)})"
    outer = _LOOP_ROWS.get()
    _LOOP_ROWS.set({**outer, **scope})
    try:
        file.comment("FORALL は 1 往復、ここでは要素ごとに 1 回。答えは同じで、性能が変わる")
        counted = _reads_bulk_rowcount(routine)
        if counted:
            # SQL%BULK_ROWCOUNT(i): the rows the i-th element's statement touched, for the last FORALL (#51)
            file.line("bulkRowCount.clear();")
        opening = (f"for (int {java_name(index)} = 1; {java_name(index)} <= Plsql.count({java}); {java_name(index)}++)"
                   if keyed else
                   f"for (int {java_name(index)} = 0; {java_name(index)} < {java}.size(); {java_name(index)}++)")
        if keyed:
            file.add_import("com.scalar.migrate.plsql.Plsql")
        # SQL%ROWCOUNT after a FORALL is the rows every element's statement touched, not the last one's (#109):
        # each element's count starts at 0 and is added up here. A bare block, so two FORALLs can sit side by side
        total = _fresh("forallRows", _taken(routine))
        token = _COUNTING.set(False)
        try:
            with file.block("") as b:
                b.line(f"int {total} = 0;")
                with b.block(opening) as f:
                    f.line("rowCount = 0;")
                    _statements(f, statement.body, routine, result)
                    if counted:
                        f.line("bulkRowCount.add(Plsql.dec(rowCount));")
                    f.line(f"{total} += rowCount;")
                b.line(f"rowCount = {total};")
        finally:
            _COUNTING.reset(token)
    finally:
        _LOOP_ROWS.set(outer)


def _reads_bulk_rowcount(routine: M.Routine) -> bool:
    return bool(re.search(r"SQL\s*%\s*BULK_ROWCOUNT", repr(routine), re.IGNORECASE))


def _cursor_for(file: JavaFile, statement: M.Loop, routine: M.Routine, result: ServiceFile) -> None:
    """`FOR r IN (SELECT ...) LOOP ... END LOOP` over rows the repository read.

    The rows are read first and iterated afterwards, which is not what Oracle does -- a cursor there is a
    position held open across the transaction. Two consequences are deliberate and visible rather than hidden:
    the row count is bounded by memory, and a write inside the body does not change what the loop iterates.
    The second is why a body that writes to a table the query reads is refused instead: ScalarDB forbids
    scanning what the same transaction wrote (P2-4), and Oracle's answer there is its own, not reproducible by
    reading first.
    """
    from .repository import loop_method, loop_record

    query = statement.query
    if query.target_status == "ERROR":
        raise Untranslatable(["cursor FOR loop whose query ScalarDB cannot run"], query.original_sql)
    written = {t for s in _walk(statement.body) for t in (getattr(s, "write_set", None) or [])}
    conflict = written & set(query.read_set or [])
    locked_undecided = bool(query.locking_mode) and not _locked_and_decided(query)
    if conflict and (locked_undecided or not _scan_precedes_writes(routine, statement, conflict)):
        raise Untranslatable(
            [f"cursor FOR loop whose body writes {sorted(conflict)}, which its own query reads"],
            query.original_sql)
    if conflict:
        # パターン D（走査しながら同じ表を更新）を、先に読む形で移す（2026-09-19 / #20 の決定 A）。
        #
        # Oracle の cursor は OPEN の時点で読み取りが一貫しているので、先に全部読む形と回す行が同じで
        # ある（`FOR UPDATE` ならロックで、無くても読み取り一貫性で）。読むのは書くより前の 1 回だけ
        # なので、「同じトランザクションで書いた物の走査」（P2-4）にも当たらない——**この routine が
        # ループより前に同じ表を書いていなければ**。書いていたら拒否のままにする。
        #
        # 残る違いは、OPEN のあとで他が行を変えたときである。Oracle はそのまま上書きし、こちらは
        # commit で弾かれる（再試行は呼び出し側の責務）。弾かれる分だけ安全側である
        file.comment("先に読んでから書く: Oracle も OPEN の時点で回す行が決まる。他からの変更は commit で"
                     "弾かれる（パターン D / #20）")

    variable = java_name(statement.variable or "r")
    # the translator renders `head.tail` as `scope[head].tail()`, so the loop variable itself is what goes in
    columns = {statement.variable or "r": variable}
    arguments = _arguments(file, query, routine, result)
    record = loop_record(routine, statement)
    file.add_import(f"{_DOMAIN.get()}.{record}")
    file.comment("the rows are read before the loop runs: ScalarDB has no cursor held across a transaction")
    rows = f"repository.{loop_method(routine, statement)}({arguments})"
    if statement.returns_rows:
        file.line(f"return {rows};")
        return
    if statement.chunk:
        # #14: `FETCH ... BULK COLLECT INTO v LIMIT n` が回していた分割読み。行は先にまとめて読む
        # ので、`n` はもう**読み込む量ではなく配る量**である。メモリを守るのは走査行数の上限である
        size = _expr(file, statement.chunk, routine, result)
        file.add_import("java.util.List", "com.scalar.migrate.plsql.Plsql")
        file.comment(f"{statement.chunk} は 1 回に**配る**件数である。読み込む量を決めていた値が、"
                     f"配る量しか決めなくなる（走査行数の上限が守るのはメモリのほう）")
        # `v_ids.COUNT` は塊の件数。`v_ids` は Java の List なので、その読み方を名前として置く
        columns[f"{statement.variable}.count"] = f"{variable}.size()"
        opening = f"for (List<{record}> {variable} : Plsql.chunks({rows}, {size}))"
    else:
        opening = f"for ({record} {variable} : {rows})"
    outer = _LOOP_ROWS.get()
    _LOOP_ROWS.set({**outer, **columns})
    try:
        with file.block(opening) as f:
            _statements(f, statement.body, routine, result)
    finally:
        _LOOP_ROWS.set(outer)


def _scan_precedes_writes(routine: M.Routine, loop: M.Loop, tables: set[str]) -> bool:
    """この routine が、ループより前に `tables` を書いていないか。

    書いていたら、ループの走査は「同じトランザクションで書いた物の走査」になり、ScalarDB が拒否する
    （P2-4 / DB-CORE-10106）。呼び出し側が同じトランザクションで先に書いていた場合は、ここからは
    見えない——それはどの走査にも言えることで、呼び出し側の境界の設計である。
    """
    for statement in _walk(routine.body):
        if statement is loop:
            return True
        if set(getattr(statement, "write_set", None) or []) & tables:
            return False
    return True


def _locked_and_decided(query: M.SqlOperation) -> bool:
    """走査が行ロックを持っていて、それを楽観制御へ移すと記録されているか（capability が付けた印）。"""
    return bool(query.locking_mode) and any(d.code == "OPTIMISTIC" for d in query.diagnostics)


_CODE_CLASSES: dict[int, dict[int, str]] = {}


def _code_class(code: int) -> str | None:
    """The class the exception registry gave `code` for this program (the first name wins; exception.collect)."""
    from .exception import collect

    program = _PROGRAM.get()
    if program is None:
        return None
    classes = _CODE_CLASSES.get(id(program))
    if classes is None:
        classes = {c: entry.class_name for c, entry in collect(program).codes.items()}
        _CODE_CLASSES.clear()
        _CODE_CLASSES[id(program)] = classes
    return classes.get(code)


def _raise(file: JavaFile, statement: M.Raise, routine: M.Routine, result: ServiceFile) -> None:
    from .exception import bound_class, class_of

    if statement.error_code is not None:
        message = _expr(file, statement.message, routine, result) if statement.message else '""'
        bound = bound_class(statement.error_code, _PROGRAM.get())
        coded = bound or _code_class(statement.error_code)
        if coded and _DOMAIN.get():
            # the class generated for this number: one bound with PRAGMA EXCEPTION_INIT (so `WHEN e_x` catches it),
            # or the one RAISE_APPLICATION_ERROR's number got (`PkgPointsError20103Exception`). Throwing the base
            # class left that class in the domain package for callers to catch, and nothing ever threw it
            file.add_import(f"{_DOMAIN.get()}.{coded}")
            file.line(f"throw new {coded}({message or chr(34) * 2});")
        else:
            file.line(f"throw new MigratedException({statement.error_code}, {message or chr(34) * 2});")
    elif not statement.exception:
        # `RAISE;` re-raises what the handler caught. It used to throw a new `MigratedException(0, "RAISE")`: the
        # caller's `WHEN NO_DATA_FOUND` no longer matched, and SQLCODE was 0
        caught = _HANDLER_ERROR.get().get(_CAUGHT)
        if caught is None:
            raise Untranslatable(["RAISE outside a handler"], "RAISE")
        file.line(f"throw {caught};")
    else:
        # by its own class, so that `WHEN e_unknown_status` catches this and nothing else
        name = statement.exception.upper()
        class_name = class_of(name, routine, _MODULE.get(), program=_PROGRAM.get())[1]
        if _DOMAIN.get():
            file.add_import(f"{_DOMAIN.get()}.{class_name}")
        file.line(f'throw new {class_name}("{statement.exception}");')


def _call(file: JavaFile, statement: M.Call, routine: M.Routine, result: ServiceFile) -> None:
    if _trigger_call(file, statement, routine, result):
        return
    target = statement.resolved_to or statement.callee
    if statement.resolved_to:
        module = _MODULE.get()
        owner = _owner_module(statement.resolved_to, module)
        callee = _routine(statement.resolved_to) or \
            next((r for r in (module.routines if module else []) if r.id == statement.resolved_to), None)
    else:
        callee = None
    if callee is None and not statement.resolved_to and _builtin_call(file, statement, routine, result):
        return
    ordered = _positional(statement, callee)
    if callee is not None and any(p.direction in ("OUT", "IN OUT") for p in callee.parameters):
        # the callee hands its OUT / IN OUT arguments back in its result record (#40): the values go in
        # by position, the record comes back, and each OUT argument is assigned from the matching field
        ins = [a for p, a in zip(callee.parameters, ordered) if p.direction != "OUT"]
        outs = [(p, a) for p, a in zip(callee.parameters, ordered) if p.direction in ("OUT", "IN OUT")]
        for p, a in outs:
            if _holder(routine, a) is None and a.lower() not in _BLOCK_LOCALS.get():
                raise Untranslatable([f"OUT argument {p.name} => {a} is not a local"], statement.callee)
        arguments = ", ".join(_fit_argument(file, _argument(file, a, routine, result), p)
                              for p, a in zip([p for p in callee.parameters if p.direction != "OUT"], ins))
    else:
        outs = []
        expected = list(callee.parameters) if callee is not None else []
        arguments = ", ".join(_fit_argument(file, _argument(file, a, routine, result), expected[i] if i < len(expected) else None)
                              for i, a in enumerate(ordered))
    if statement.resolved_to:
        if callee is not None and needs_audit(callee):
            arguments = ", ".join(a for a in [arguments, "audit"] if a)
        if split.runs_separately(statement.resolved_to):
            # 自律トランザクションの routine を**同じトランザクションの中で**呼ぶと、親が rollback した
            # ときに一緒に消える（#3 §G）。別のトランザクションを開く口（SeparateTransactions）を呼び出し側が
            # 受け取り、その connection の上に呼び先を組み立てて呼ぶ（#49）。OUT 引数と戻り値は、その境界の
            # 外へ持ち出す形をまだ持っていない
            if outs or statement.into or callee is None or owner is None or _module_of(owner) is None:
                raise Untranslatable([f"{statement.resolved_to} は別トランザクションで呼ぶ routine で、OUT 引数 / 戻り値を"
                                      f"境界の外へ持ち出す形が無い"], statement.callee)
            repository_class = java_class_name(owner) + "Repository"
            if _INFRA.get():
                file.add_import(f"{_INFRA.get()}.{repository_class}")
            built = f"new {repository_class}(connection{', sequences' if _uses_sequences(_module_of(owner)) else ''})"
            why = split._BOUNDARIES.get().why(statement.resolved_to) or ""
            file.comment(f"{statement.resolved_to} は別のトランザクションで呼ぶ（limits.yaml transactions.separate: {why}）。"
                         f"親が rollback しても残る（#49）")
            file.line(f"separate.run(connection -> {{ new {java_class_name(owner)}Service({built})"
                      f".{java_name(routine_stem(callee))}({arguments}); return null; }});")
            return
        if module is not None and owner is not None and owner != module.name:
            # 別の module の routine を呼ぶ。**PL/SQL がそう書いてある**ので、誰を呼ぶかは決定では
            # ない——注入するのは trigger と同じ形である（#12）。以前はここで拒んでいたが、
            # そのために「呼ばれる側が REVIEW なだけの routine」まで動かせなかった
            if callee is None:
                raise Untranslatable([statement.resolved_to], f"call into {owner}")
            invocation = f"{java_name(owner)}.{java_name(routine_stem(callee))}({arguments})"
        else:
            # the Java name comes from the routine, not from the id: `pkg.put~2` is the method `put2`
            invocation = f"{java_name(routine_stem(callee) if callee is not None else target.split('.')[-1])}({arguments})"
        if not outs:
            if statement.into:
                file.line(f"{_expr(file, statement.into, routine, result)} = "
                          f"{_coerce(file, invocation, _local_type(routine, statement.into))};")
            else:
                file.line(f"{invocation};")
            return
        record = result_record_name(callee)
        if _DOMAIN.get():
            file.add_import(f"{_DOMAIN.get()}.{record}")
        holder = f"{java_name(routine_stem(callee))}Result"
        # a block of its own, so that the same routine called twice does not declare the holder twice
        with file.block("") as f:
            f.line(f"{record} {holder} = {invocation};")
            for p, a in outs:
                target_type = _local_type(routine, a)
                f.line(f"{_expr(f, a, routine, result)} = {_coerce(f, f'{holder}.{java_name(p.name)}()', target_type)};")
            if statement.into:
                # a function hoisted out of an expression (plsql.hoist, #48): its return value is in the record too
                f.line(f"{_expr(f, statement.into, routine, result)} = "
                       f"{_coerce(f, f'{holder}.returned()', _local_type(routine, statement.into))};")
    elif _collection_call(file, statement, routine, result):
        return
    else:
        file.comment(f"external call: {statement.callee}")
        file.line(f'throw new UnsupportedOperationException("external call: {statement.callee}");')
        if statement.id not in result.untranslated:
            result.untranslated.append(statement.id)   # it throws, so what follows in the block is unreachable


def _builtin_call(file: JavaFile, statement: M.Call, routine: M.Routine, result: ServiceFile) -> bool:
    """An Oracle-supplied procedure the generator knows (plsql/builtins.py, #55): its arguments in Oracle's order,
    named ones placed by name, and the target's counterpart -- a helper, or nothing, with the reason as a comment."""
    from .. import builtins

    builtin = builtins.lookup(statement.callee)
    if builtin is None or builtin.function:
        return False
    try:
        arguments = builtins.ordered(builtin, list(statement.arguments))
    except builtins.BuiltinArgumentError as e:
        raise Untranslatable([str(e)], statement.callee)
    if builtin.java is None:
        file.comment(f"{builtin.name}: {builtin.note}（#55）")
        return True
    file.add_import("com.scalar.migrate.plsql.Plsql")
    # DBMS_OUTPUT writes its argument as text: a TIMESTAMP or BINARY_DOUBLE local as TO_CHAR would (#91, #94)
    as_text = builtin.name in ("DBMS_OUTPUT.PUT_LINE", "DBMS_OUTPUT.PUT")
    file.line(f"{builtin.java}({', '.join(_expr(file, a, routine, result, as_text=as_text) for a in arguments)});")
    return True


def _collection_call(file: JavaFile, statement: M.Call, routine: M.Routine, result: ServiceFile) -> bool:
    """`v_names.EXTEND;`, `v_names.DELETE(2);`, `v_top3.EXTEND(3);`: a collection method as a statement (#45)."""
    from .expr import COLLECTION_METHODS

    receiver, _, tail = (statement.callee or "").rpartition(".")
    head, _, subscript = receiver.partition("(")
    holder = _holder(routine, head.strip())
    if holder is None or not _collection_kind(holder) or tail.upper() not in COLLECTION_METHODS:
        return False
    file.add_import("com.scalar.migrate.plsql.Plsql")
    # `vntb1(2).DELETE(1)`: the method of an element that is itself a collection -- the same object inside the
    # outer one, so changing it changes the outer (5-12, #82)
    target = java_name(holder.name)
    for inner in (_subscripts("(" + subscript) or [] if subscript else []):
        target = f"Plsql.at({target}, {_expr(file, inner, routine, result)})"
    arguments = [target] + [_expr(file, a, routine, result) for a in statement.arguments]
    file.line(f"Plsql.{COLLECTION_METHODS[tail.upper()]}({', '.join(arguments)});")
    return True


def _argument(file: JavaFile, text: str, routine: M.Routine, result: "ServiceFile") -> str:
    """One argument of a call statement. A comparison can only be handed to a BOOLEAN parameter, and there it is a
    value, not a branch: `print_boolean('x', 100 IN (a, b))` with a NULL `a` passes NULL, where the "is TRUE"
    rendering passed FALSE (samples/oracle-plsql-docs 2-48, #61). `translate` wraps it in `Plsql.bool3` only when
    the argument is a comparison or a logical operator, so every other argument is rendered as before."""
    return _expr(file, text, routine, result, boolean_value=True)


def _positional(statement: M.Call, callee: M.Routine | None) -> list[str]:
    """The arguments in the callee's parameter order (OUT parameters included, so that `_call` can pair them).
    `put(p_note => 'x', p_id => 1)` was rendered as it stood, which is not Java -- and named notation is what
    tells two overloads apart. A parameter left to its DEFAULT is filled with the default when it is a literal."""
    if not any(NAMED_ARGUMENT.match(a) for a in statement.arguments) and (
            callee is None or len(statement.arguments) >= len(callee.parameters)):
        return list(statement.arguments)
    if callee is None:
        raise Untranslatable(["named arguments of a routine that is not in the program"], statement.callee)
    positional = [a for a in statement.arguments if not NAMED_ARGUMENT.match(a)]
    named = {a.partition("=>")[0].strip().lower(): a.partition("=>")[2].strip()
             for a in statement.arguments if NAMED_ARGUMENT.match(a)}
    taken = list(callee.parameters)[len(positional):]
    out = list(positional)
    for parameter in taken:
        if parameter.name.lower() in named:
            out.append(named.pop(parameter.name.lower()))
        elif getattr(parameter, "carried", False):
            # #46: carried package state. The caller carries it too, under the same name -- or under its own, for
            # a lifted subprogram that names it otherwise (`check_credit.rating`, #90)
            out.append(getattr(parameter, "carried_from", None) or parameter.name)
        elif parameter.default is not None and LITERAL_DEFAULT.match(parameter.default.strip()):
            out.append(parameter.default.strip())
        else:
            # a parameter left to its default: the generated method has no default to fall back on
            raise Untranslatable([f"{parameter.name} is left to its default"], statement.callee)
    if named:
        raise Untranslatable([f"no parameter named {', '.join(named)}"], statement.callee)
    return out


def _fit_argument(file: JavaFile, rendered: str, parameter: "M.Parameter | None") -> str:
    """A rendered argument, shaped for the callee's Java parameter: `raise_salary(104, 10)` handed the literal 10
    to a `BigDecimal pPct` and javac refused it (#40). The expression translator does this for calls inside
    expressions; a call statement goes through here."""
    if parameter is None or parameter.type is None:
        return rendered
    expected = signature_type(parameter.type).name
    if expected in ("Integer", "Double", "Float") and rendered.startswith("Plsql.") \
            and not rendered.startswith(("Plsql.toInt(", "Plsql.toDouble(", "Plsql.toFloat(")):
        return _coerce(file, rendered, expected)   # `p(1, (bb + 3) * 4, ff)` to an INTEGER parameter (8-14, #75)
    literal = re.fullmatch(r"-?\d+(?:\.\d+)?", rendered)
    if expected == "BigDecimal" and not rendered.startswith("Plsql.dec("):
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.dec({rendered})"
    if expected == "Long" and literal and "." not in rendered:
        return f"{rendered}L"
    return rendered


# `p_id => 1`: named notation starts with the parameter name. Looking for `=>` anywhere took the string literal
# `' => '` inside `k || ' => ' || v(k)` for one (samples/oracle-samples b04_5, 2026-09-25)
NAMED_ARGUMENT = re.compile(r"^\s*[\w$#]+\s*=>")

# a DEFAULT the call can spell out itself: NULL, a number, a quoted string, TRUE / FALSE
LITERAL_DEFAULT = re.compile(r"^(?:NULL|TRUE|FALSE|[+-]?\d+(?:\.\d+)?|'(?:[^']|'')*')$", re.IGNORECASE)


def _owner_module(routine_id: str, module: "M.Module | None") -> str | None:
    """The module a resolved call lands in: `pkg.put` -> `pkg`; a standalone `log_msg` -> its own module
    `log_msg` (there was no owner for it, so the call was rendered as if it were a sibling: #40)."""
    if "." in routine_id:
        return routine_id.rsplit(".", 1)[0]
    program = _PROGRAM.get()
    for candidate in (program.modules if program else []):
        if any(r.id == routine_id for r in candidate.routines):
            return None if module is not None and candidate.name == module.name else candidate.name
    return None


def _trigger_call(file: JavaFile, statement: M.Call, routine: M.Routine,
                  result: ServiceFile) -> bool:
    """`trg_orders_audit.body(...)` を、注入された trigger service への呼び出しとして出す（#12）。

    引数は**名前で来る**（`NEW.status => 'CLOSED'`）。並べ替えるのは呼ばれる側の signature に
    合わせるためで、その並びを決めているのは `plsql/triggers.py` である——呼ぶ側と呼ばれる側が
    別々に数えると、引数が静かにずれる。
    """
    from ..triggers import correlation_row as correlations

    owner = _trigger_owner(statement, _MODULE.get())
    if owner is None:
        return False
    callee = _routine(statement.resolved_to)
    module = next((m for m in (_PROGRAM.get().modules if _PROGRAM.get() else []) if m.name == owner),
                  None)
    if callee is None:
        # 呼ばれる側が見つからない。引数の並びはその signature が決めるので、推測で出さない
        raise Untranslatable([f"{statement.resolved_to} の本体が見つからない"], statement.callee)
    given = {}
    for argument in statement.arguments:
        name, _, value = argument.partition("=>")
        given[name.strip()] = value.strip()
    wanted = list(correlations(callee, getattr(module, "trigger_when", None)))
    missing = [name for name in wanted if name not in given]
    if missing:
        # 呼ばれる側が読む列を、呼ぶ側が渡していない。黙って null を渡すと**条件の意味が変わる**
        raise Untranslatable([f"{owner} needs {', '.join(missing)}"], statement.callee)
    # each value is shaped for the body's parameter: the SET expression of a split RMW (`Plsql.add(vRmw1, 1)`)
    # is an Object and the body takes the column's BigDecimal (samples/oracle-samples b05_4, 2026-09-25)
    types = correlation_row(callee)
    arguments = [_coerce(file, _expr(file, given[name], routine, result),
                         java_type(types[name].oracle_type).name if name in types else "Object")
                 for name in wanted]
    if needs_audit(callee):
        arguments.append("audit")
    file.line(f"{java_name(owner)}.{java_name(routine_stem(callee))}({', '.join(arguments)});")
    return True


def _sets_rowcount_to_one(statement) -> bool:
    """An implicit `SELECT ... INTO` that returns sets SQL%ROWCOUNT to 1 (no row and many rows raise instead).
    An explicit cursor's FETCH (AT_MOST_ONE) does not touch the implicit cursor's attributes."""
    return statement.kind == "SqlOperation" and bool(statement.into_targets) \
        and statement.cardinality not in ("MANY", "AT_MOST_ONE") and not statement.plan_id \
        and statement.target_status != "PLANNED"


def _sql(file: JavaFile, statement: M.SqlOperation, routine: M.Routine) -> None:
    _sql_statement(file, statement, routine)
    if _READS_ROWCOUNT.get() and _sets_rowcount_to_one(statement):
        # `IF SQL%ROWCOUNT = 0` after a SELECT INTO used to read the count of the DML before it
        file.line("rowCount = 1;")


def _sql_statement(file: JavaFile, statement: M.SqlOperation, routine: M.Routine) -> None:
    method = f"{java_name(routine_stem(routine))}{_sql_suffix(statement)}"
    # a bind lifted out of the SQL (P4-4) is computed inside the repository from the other binds, so it is not
    # passed in. The repository's parameter list is built from the same rule; the two have to agree.
    arguments = _arguments(file, statement, routine, None)
    if getattr(statement, "opens_cursor", None):
        # an explicit cursor's OPEN (#81): its rows are read now, and each FETCH takes the next. A plan hands its
        # rows back as lists; the cursor takes either
        planned = statement.plan_id or statement.target_status == "PLANNED"
        file.add_import("com.scalar.migrate.plsql.Plsql")
        file.line(f"{_cursor_state(statement.opens_cursor)}.open(repository.{method}({arguments})"
                  f"{'.rows()' if planned else ''});")
        return
    targets = statement.into_targets
    if getattr(statement, "returns_deleted", None) and targets:
        # `DELETE ... RETURNING ... INTO` of scalars, read before the DELETE (#87): none leaves the targets, a
        # second raises TOO_MANY_ROWS -- before the DELETE, as Oracle undoes its statement
        planned = statement.plan_id or statement.target_status == "PLANNED"
        file.add_import("com.scalar.migrate.plsql.Plsql")
        if _DOMAIN.get():
            file.add_import(f"{_DOMAIN.get()}.TooManyRowsException")
        with file.block("") as f:
            f.comment(f"DELETE ... RETURNING INTO {', '.join(targets)}: the rows it deletes, read first")
            rows = f"repository.{method}({arguments})" + (".rows()" if planned else "")
            f.line(f"var rows_ = Plsql.arrays({rows});")
            with f.block("if (rows_.size() > 1)") as g:
                g.line('throw new TooManyRowsException("DML Returning: Too many rows");')
            with f.block("if (!rows_.isEmpty())") as g:
                _assign_row(g, routine, targets, "rows_.get(0)", constrained=True)
        return
    if statement.plan_id or statement.target_status == "PLANNED":
        _planned_into(file, statement, routine, method, arguments, targets)
        return
    if targets and statement.cardinality == "MANY":
        # BULK COLLECT fills collections from every matching row (#66). Treating it as a one-row SELECT INTO turns
        # "no rows" and "many rows" into exceptions the original never raised. The repository returns every row;
        # collection i takes column i. A collection of records, or a target that is not a local collection, is
        # refused rather than guessed.
        _bulk_into(file, statement, routine, targets, f"repository.{method}({arguments})")
        return
    if targets and statement.cardinality == "AT_MOST_ONE":
        _first_row(file, statement, routine, method, arguments, targets)
    elif targets and len(targets) == 1 and "." not in targets[0]:
        # (`INTO rec.dept_name` -- one field of a record -- goes the way of several fields below: 8-37, #74)
        holder = _holder(routine, targets[0])
        value = _into(file, f"repository.{method}({arguments})", _local_type(routine, targets[0]))
        file.line(f"{_local(targets[0])} = {_constrain(file, value, holder.type if holder else None)};")
    elif targets:
        # the repository returns the columns positionally, in the order the SELECT names them
        if any("." in target for target in targets):
            _record_into(file, routine, targets, method, arguments, statement)
            return
        file.comment(f"SELECT INTO {', '.join(targets)}")
        file.line(f"var row = repository.{method}({arguments});")
        for index, target in enumerate(targets):
            holder = _holder(routine, target)
            value = _into(file, f"row[{index}]", _local_type(routine, target))
            file.line(f"{_local(target)} = {_constrain(file, value, holder.type if holder else None)};")
    elif (statement.sql_kind or "").upper() in ("INSERT", "UPDATE", "DELETE", "MERGE"):
        import json   # a Java string literal: the message quotes the PL/SQL, which may hold quotes itself
        for guard in getattr(statement, "key_guards", None) or []:
            # the key left the SET (#92): the same key is a no-op in Oracle; another one would move the row, which
            # ScalarDB cannot do -- refused here rather than updated under the old key in silence
            value = _expr(file, guard["value"], routine, None)
            where = _expr(file, guard["where"], routine, None)
            message = (f"SET {guard['column']} = {guard['value']} would move the row from {guard['column']} = "
                       f"{guard['where']} to another key; ScalarDB cannot update a key")
            with file.block(f"if (!Plsql.eq({value}, {where}))") as g:
                g.line(f"throw new UnsupportedOperationException({json.dumps(message, ensure_ascii=False)});")
        # SQL%ROWCOUNT is part of the behaviour: `update_email` raises when it is zero. One variable per
        # statement, because a routine may hold several DML statements in one scope.
        file.line(f"rowCount {'+' if getattr(statement, 'accumulates_rowcount', False) else ''}= repository.{method}({arguments});")
    else:
        file.line(f"repository.{method}({arguments});")


def _dynamic(file: JavaFile, statement: M.DynamicSql, routine: M.Routine,
             result: ServiceFile) -> None:
    """P4-7: 走りうる文が**数えられる**なら、その分だけ書く。数えられないなら拒む。

    `EXECUTE IMMEDIATE v_sql` は、文字列が literal と分岐から組まれているとき、走りうる文の集合が
    有限で分かる。畳んだ結果は静的な文とまったく同じ道を通っている（変換・列への帰属・型）ので、
    ここで出すのは**どの variant を走らせるかの分岐**だけである。

    表名が実行時に決まるもの（`'DELETE FROM ' || p_table_name`）は数えられない。**推測で 1 つに
    決めない**——拒んで、allowlist や専用 Repository という再設計を人に残す（設計書 §6.8）。

    `EXECUTE IMMEDIATE` は呼び出し側の権限で走る（`AUTHID`）。畳んだ文を見た人が「静的な文と同じ
    に検査された」と思わないよう、診断は文に残してある。
    """
    variants = statement.variant_statements or []
    if not variants:
        raise Untranslatable(["EXECUTE IMMEDIATE whose statement is not a knowable set"],
                             statement.expression or "")
    file.comment(f"EXECUTE IMMEDIATE: 走りうる文は {len(variants)} 通り。畳んで静的な文として"
                 f"生成してある（P4-7）")
    branches = list(zip(statement.variants, variants))
    emitted = 0
    for index, (variant, operation) in enumerate(branches):
        guard = (variant or {}).get("guard") or ""
        last = index == len(branches) - 1
        if guard:
            opening = ("if" if emitted == 0 else "else if") + \
                f" ({_expr(file, guard, routine, result)})"
            with file.block(opening) as f:
                _variant(f, operation, statement, routine)
            emitted += 1
        elif emitted:
            with file.block("else") as f:
                _variant(f, operation, statement, routine)
        else:
            _variant(file, operation, statement, routine)
        if not last and not guard:
            # 条件の無い variant のあとに続きは無い。並べると到達しないコードになる
            break
    if branches and all((variant or {}).get("guard") for variant, _ in branches):
        # どの variant の条件にも当たらない。**元なら何かしらの文が走った**が、それが何かは誰も
        # 決めていない——許された表名の一覧に無い表名がここに来る。黙って何もしないのは最悪なので、拒む
        with file.block("else") as f:
            f.line(f'throw new IllegalArgumentException("{routine.id}: 走りうる文のどれにも当たらない'
                   f'（limits.yaml の dynamicTables に無い表名など）");')


DDL = re.compile(r"^\s*(CREATE|DROP|ALTER|RENAME|GRANT|REVOKE|COMMENT|ANALYZE|PURGE|FLASHBACK)\b", re.IGNORECASE)


def _variant(file: JavaFile, operation: M.SqlOperation, statement: M.DynamicSql,
             routine: M.Routine) -> None:
    """畳んだ 1 つの variant。INTO は元の `EXECUTE IMMEDIATE ... INTO` が言っている。"""
    ddl = DDL.match(operation.original_sql or "")
    if ddl:
        from ..dynamic import omitted_ddl

        why = omitted_ddl(routine.id)
        if why:
            file.comment(f"{ddl.group(1).upper()}: 移行先では実行しない（limits.yaml ddl.omit: {why}）。"
                         f"元の文: {' '.join((operation.original_sql or '').split())}")
            return
        # `EXECUTE IMMEDIATE 'CREATE TABLE ...'`: the schema of the target is Schema Loader's, and ScalarDB runs no
        # DDL inside a transaction. Emitting it would run a DROP TABLE from application code (#52)
        raise Untranslatable([f"routine の中の DDL（{ddl.group(1).upper()}）は移行先で実行しない。スキーマは Schema Loader "
                              f"が持つ。一時表の作成と削除なら、その用途を再設計するか、省くと決める"],
                             statement.expression or "")
    if statement.into_targets and (operation.sql_kind or "").upper() != "SELECT":
        # `EXECUTE IMMEDIATE 'UPDATE ... RETURNING x INTO :n' ... RETURNING INTO v`: ScalarDB SQL has no RETURNING,
        # and the repository returns the row count, which is not the value (samples/oracle-samples b06_3, 2026-09-25)
        raise Untranslatable([f"RETURNING INTO of a dynamic {operation.sql_kind}"], statement.expression or "")
    operation = dataclasses.replace(operation, into_targets=list(statement.into_targets))
    _sql(file, operation, routine)


def _arguments(file: JavaFile, statement: M.SqlOperation, routine: M.Routine,
               result: "ServiceFile | None") -> str:
    """The values passed to the repository method, in the order its parameter list was built.

    A bind lifted out of the SQL (P4-4) is computed inside the repository from the other binds, so it is not
    passed; `repository._parameters` applies the same rule and the two have to agree.

    A cursor FOR loop's `r.order_id` (#10) is not a name Java has -- it is a component of the row the loop is
    holding -- so it goes through the expression translator, which renders it as the accessor the record
    generated for that loop actually has.
    """
    from .repository import needs_audit as statement_needs_audit

    out = ["audit"] if statement_needs_audit(statement) else []
    for bind in (b for b in statement.binds if not b.expression):
        name = bind.plsql_variable or bind.name
        # 素の識別子でないものは翻訳に通す: `r.order_id`（ループの行）も `p_ids(i)`（コレクションの
        # 要素）も、名前として Java の変数に落ちるものである。`java_name` に渡すと `pIds(i)` という
        # 存在しない method 呼び出しになる
        # a literal the dynamic SQL's USING wrote (`USING 110, 'DEPARTMENT_ID'`, 7-20) goes as its value
        plain = name.replace("_", "").replace("$", "").replace("#", "").isalnum() and name[:1].isalpha()
        rendered = _local(name) if plain else _expr(file, name, routine, result)
        expected = java_type(bind.oracle_type).name if bind.oracle_type else None
        if plain and expected in ("Integer", "Long", "Double", "Float", "BigDecimal") and \
                _local_type(routine, name) not in (expected, None, "Object"):
            # an INTEGER parameter is a BigDecimal (#75) and the repository takes the column's own type: passing it
            # as it stood did not compile (12-18, #83)
            file.add_import("com.scalar.migrate.plsql.Plsql")
            helper = {"Integer": "toInt", "Long": "toLong", "Double": "toDouble", "Float": "toFloat",
                      "BigDecimal": "dec"}[expected]
            rendered = f"Plsql.{helper}({rendered})"
        if rendered.startswith("Plsql.at(") and "." not in name:
            # a collection element is Object; the repository parameter is typed from the bind's column the way
            # `repository._parameters` types it (`FORALL ... VALUES (pnums(i), ...)`: 12-9, #75)
            expected = java_type(bind.oracle_type).name
            if expected == "String":
                rendered = f"(String) {rendered}"
            elif expected in ("Integer", "Long", "Double", "Float", "BigDecimal", "LocalDateTime"):
                file.add_import("com.scalar.migrate.plsql.Plsql")
            helper = {"Integer": "toInt", "Long": "toLong", "Double": "toDouble", "Float": "toFloat",
                      "BigDecimal": "dec"}[expected]
            rendered = f"Plsql.{helper}({rendered})"
        out.append(rendered)
    return ", ".join(out)


def _first_row(file: JavaFile, statement: M.SqlOperation, routine: M.Routine, method: str,
               arguments: str, targets: list[str]) -> None:
    """An explicit cursor's `OPEN` / first `FETCH` (#11), as the query it was.

    The repository hands back null when there was no row, and the routine's own `%NOTFOUND` branch -- kept
    where it was written -- decides what that means. The flag is set from the row's absence rather than from
    the value assigned: `FETCH` finding a row whose column is NULL is not `%NOTFOUND`.

    A `FETCH` that finds nothing **leaves its targets as they were**. That is what Oracle does, and it is why
    the assignment sits inside the guard rather than writing null: the routine may have put something there
    already, and a sequence with no `%NOTFOUND` branch relies on exactly that.
    """
    row = f"{method}Row"
    file.comment(f"FETCH {statement.not_found_flag} INTO {', '.join(targets)}" if statement.not_found_flag
                 else f"SELECT INTO {', '.join(targets)}（行が無ければ元の値のまま）")
    file.line(f"Object[] {row} = repository.{method}({arguments});")
    if statement.not_found_flag:
        file.line(f"{_flag_name(statement.not_found_flag)} = {row} == null;")
    with file.block(f"if ({row} != null)") as f:
        _assign_row(f, routine, targets, row)


def _bulk_into(file: JavaFile, statement: M.SqlOperation, routine: M.Routine, targets: list[str], rows: str) -> None:
    """BULK COLLECT INTO: every row read, into the targets (#66, #67).

    Several collections take a column each; one collection of records (`TABLE OF c1%ROWTYPE`, 12-23) takes a
    record built from each row. An INDEX BY PLS_INTEGER target is a Map (#93), numbered from 1 as Oracle numbers
    it; a nested table or VARRAY is a List. A target that is not a local collection is refused, not guessed.
    """
    holders = [_holder(routine, t) for t in targets]
    if any(h is None or not _collection_kind(h) for h in holders):
        raise Untranslatable([f"BULK COLLECT INTO {', '.join(targets)}"], statement.original_sql)
    file.add_import("com.scalar.migrate.plsql.Plsql")
    records = _element_record(holders[0].type.resolved) if len(targets) == 1 else None
    if records is None and (len(targets) != len(getattr(statement, "into_columns", None) or targets) or any(
            "RECORD(" in (h.type.resolved or "").upper() for h in holders)):
        raise Untranslatable([f"BULK COLLECT INTO {', '.join(targets)}"], statement.original_sql)
    with file.block("") as f:   # its own scope: a routine may BULK COLLECT more than once
        f.line(f"var bulk_ = {rows};")
        if records is not None:
            holder = holders[0]
            element = re.sub(r"^(?:List|Map)<(?:[^,]+,\s*)?(.+)>$", r"\1", java_type(holder.type.resolved).name)
            if element == "Object":
                raise Untranslatable([f"BULK COLLECT INTO {targets[0]}"], statement.original_sql)
            f.add_import(*java_type(holder.type.resolved).imports, "java.util.List", "java.util.ArrayList")
            f.line(f"List<{element}> rows_ = new ArrayList<>();")
            with f.block("for (Object[] row_ : bulk_)") as g:
                fields = record_columns(records)
                for _, kind in fields:
                    g.add_import(*java_type(kind).imports)
                values = ", ".join(_into(g, f"row_[{i}]", java_type(kind).name) for i, (_, kind) in enumerate(fields))
                g.line(f"rows_.add(new {element}({values}));")
            f.line(f"{_local(targets[0])} = {_collected(holder, 'rows_')};")
            return
        for index, (target, holder) in enumerate(zip(targets, holders)):
            element = re.sub(r"^(?:List|Map)<(?:[^,]+,\s*)?(.+)>$", r"\1", java_type(holder.type.resolved).name)
            f.line(f"{_local(target)} = {_collected(holder, f'Plsql.column(bulk_, {index}, {element}.class)')};")


def _collected(holder, values: str) -> str:
    """A List of values as the target collection's own kind: an INDEX BY table is a Map keyed 1 .. n (#93)."""
    return f"Plsql.indexed({values})" if _collection_kind(holder) == "map" else values


def _planned_into(file: JavaFile, statement: M.SqlOperation, routine: M.Routine, method: str, arguments: str,
                  targets: list[str]) -> None:
    """A query the plan runs (ScalarDB fetches, H2 computes the rest) read INTO variables (#83).

    The plan hands back every row, so the three ways a read can end are decided here as a direct read decides them:
    a `SELECT INTO` raises NO_DATA_FOUND on none and TOO_MANY_ROWS on a second; a cursor's first row (`AT_MOST_ONE`)
    leaves its targets as they were when there is none; BULK COLLECT takes them all. `SELECT salary * 0.10 INTO
    bonus FROM employees WHERE employee_id = 100` -- a key read with arithmetic on it -- stopped here before (2-25).
    """
    kind = (statement.sql_kind or "").upper()
    if not targets or kind != "SELECT" or any("." in t for t in targets):
        raise Untranslatable(["execution plan result"], statement.original_sql)
    file.add_import("com.scalar.migrate.plsql.Plsql")
    rows = f"Plsql.arrays(repository.{method}({arguments}).rows())"
    if statement.cardinality == "MANY":
        _bulk_into(file, statement, routine, targets, rows)
        return
    with file.block("") as f:
        f.comment(f"SELECT INTO {', '.join(targets)}, through the plan")
        f.line(f"var rows_ = {rows};")
        if statement.cardinality == "AT_MOST_ONE":
            if statement.not_found_flag:
                f.line(f"{_flag_name(statement.not_found_flag)} = rows_.isEmpty();")
            with f.block("if (!rows_.isEmpty())") as g:
                _assign_row(g, routine, targets, "rows_.get(0)")
            return
        if _DOMAIN.get():
            f.add_import(f"{_DOMAIN.get()}.NoDataFoundException", f"{_DOMAIN.get()}.TooManyRowsException")
        with f.block("if (rows_.isEmpty())") as g:
            g.line('throw new NoDataFoundException("SELECT INTO matched no row");')
        with f.block("if (rows_.size() > 1)") as g:
            g.line('throw new TooManyRowsException("SELECT INTO matched more than one row");')
        _assign_row(f, routine, targets, "rows_.get(0)", constrained=True)


def _assign_row(file: JavaFile, routine: M.Routine, targets: list[str], row: str,
                constrained: bool = False) -> None:
    """FETCH a row INTO its targets: one value per scalar target, or -- when the only target is a record -- the
    record built from every column. Casting column 0 to the record (`job1 = (CRow) row[0]`) compiled and threw at
    run time (#81)."""
    holder = _holder(routine, targets[0]) if len(targets) == 1 else None
    record = record_class(holder.type) if holder is not None else None
    if record is not None:
        fields = record_columns(holder.type.resolved or "")
        for _, kind in fields:
            file.add_import(*java_type(kind).imports)
        values = ", ".join(_into(file, f"{row}[{index}]", java_type(kind).name) for index, (_, kind) in enumerate(fields))
        file.line(f"{_local(targets[0])} = new {record}({values});")
        return
    for index, target in enumerate(targets):
        value = _into(file, f"{row}[{index}]", _local_type(routine, target))
        if constrained:
            holder = _holder(routine, target)
            value = _constrain(file, value, holder.type if holder else None)
        file.line(f"{_local(target)} = {value};")


def _cursor_state(cursor: str) -> str:
    return java_name(f"{cursor}_cursor")


def _general_cursors(routine: M.Routine) -> dict[str, bool]:
    """The cursors whose OPEN reads the rows its FETCHes take (#81), each with whether it is a cursor variable."""
    out: dict[str, bool] = {}
    for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
        cursor = getattr(statement, "opens_cursor", None)
        if cursor:
            out[cursor.lower()] = out.get(cursor.lower(), False) or bool(getattr(statement, "cursor_variable", False))
    return out


def _fetch(file: JavaFile, statement: M.CursorStatement, routine: M.Routine) -> None:
    """`FETCH c INTO ...` of a cursor whose OPEN read its rows (#81). No row leaves the targets as they were."""
    cursor = (statement.cursor or "").split("(")[0].strip().lower()
    file.add_import("com.scalar.migrate.plsql.Plsql")
    if any(d.code == "BULK_COLLECT" for d in statement.diagnostics):
        # FETCH ... BULK COLLECT INTO ... [LIMIT n]: the next n rows into the collections (#67)
        limit = _expr(file, statement.bulk_limit, routine, None) if statement.bulk_limit else "null"
        _bulk_into(file, statement, routine, list(statement.into_targets),
                   f"{_cursor_state(cursor)}.fetchMany({limit})")
        return
    with file.block("") as f:   # its own scope: a routine fetches more than once
        f.line(f"Object[] row_ = {_cursor_state(cursor)}.fetch();")
        with f.block("if (row_ != null)") as g:
            _assign_row(g, routine, list(statement.into_targets), "row_")


def _into(file: JavaFile, value: str, target_type: str) -> str:
    """Put a value the repository returned into a local of the declared type.

    A cast is not enough for a number. The column a `SELECT INTO` reads decides what JDBC hands back -- a
    ScalarDB `COUNT(*)` arrives as a Long -- while the local's type comes from the PL/SQL declaration, which for
    any `NUMBER` is BigDecimal. Casting one to the other throws at run time, and only for the routines whose
    SELECT happens to return the other kind. Coercing instead is what Oracle does on the same assignment.
    """
    if target_type == "BigDecimal":
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.dec({value})"
    if target_type == "OffsetDateTime":
        # ScalarDB の TIMESTAMPTZ 列は Instant で返る。cast すると落ちる（`last_paid_at` / 2026-09-19）
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.zoned({value})"
    helper = {"Integer": "toInt", "Long": "toLong", "Double": "toDouble", "Float": "toFloat",
              "LocalDateTime": "moment"}.get(target_type)
    if helper:
        # a column read comes back as whatever JDBC or the plan's H2 hands over -- a BigDecimal for an Integer
        # local cast and threw (#81, a cursor read through a plan)
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.{helper}({value})"
    return f"({target_type}) {value}"


def _record_into(file: JavaFile, routine: M.Routine, targets: list[str], method: str, arguments: str,
                 statement: M.SqlOperation) -> None:
    """`SELECT a, b INTO v_rec.a, v_rec.b` builds the record in one go.

    PL/SQL fills the fields one at a time; a Java record is immutable, so it is constructed once from all of
    them. That is only the same thing if every field is assigned by this statement, so a statement that fills
    part of a record is refused rather than silently constructed with nulls in the rest.
    """
    holders = {target.rpartition(".")[0] for target in targets}
    if len(holders) != 1:
        raise Untranslatable([f"SELECT INTO fields of more than one record ({', '.join(targets)})"],
                             statement.original_sql)
    holder = holders.pop()
    record = _local_type(routine, holder)
    fields = [target.rpartition(".")[2] for target in targets]
    declared = _record_fields(routine, holder)
    if declared is not None and [f.lower() for f in fields] != [f.lower() for f in declared]:
        raise Untranslatable(
            [f"SELECT INTO only part of {holder} ({', '.join(fields)} of {', '.join(declared)})"],
            statement.original_sql)
    file.comment(f"SELECT INTO {', '.join(targets)}")
    file.line(f"var row = repository.{method}({arguments});")
    casts = ", ".join(_into(file, f"row[{i}]", java_type(kind).name)
                      for i, kind in enumerate(_record_types(routine, holder) or [None] * len(fields)))
    file.line(f"{java_name(holder)} = new {record}({casts});")


def _record_fields(routine: M.Routine, holder: str) -> list[str] | None:
    return [name for name, _ in _record_shape(routine, holder)] or None


def _record_types(routine: M.Routine, holder: str) -> list[str] | None:
    return [kind for _, kind in _record_shape(routine, holder)] or None


def _record_shape(routine: M.Routine, holder: str) -> list[tuple[str, str]]:
    for declaration in routine.declarations:
        if declaration.name.lower() == holder.lower() and declaration.type is not None:
            return record_columns(declaration.type.resolved or "")
    return []


def _coerce(file: JavaFile, value: str, target_type: str) -> str:
    """A numeric literal or a ternary is not a BigDecimal; the helper makes it one."""
    # `(Plsql.mul(pi, …))`: the parentheses PL/SQL wrote hide the helper call from the checks below (2-13, 8-3)
    while value.startswith("(") and value.endswith(")") and _balanced(value[1:-1]):
        value = value[1:-1]
    if target_type == "BigDecimal" and not value.startswith("Plsql.dec("):
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.dec({value})"
    # `i := i + 1` on a PLS_INTEGER: the arithmetic helpers return Object (date arithmetic returns a date), and
    # `Integer i = Plsql.add(i, 1)` did not compile (2026-09-24, samples/oracle-samples b04_2_control_flow)
    # a call of a routine that returns an INTEGER (a BigDecimal since signature_type) into an INTEGER local rounds,
    # as Oracle does on the assignment (`x := f(2)`: 8-3)
    if target_type in ("Integer", "Long") and re.match(r"[A-Za-z_][\w.]*\(", value) and not value.startswith("Plsql."):
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.{'toInt' if target_type == 'Integer' else 'toLong'}({value})"
    if target_type in ("Integer", "Long") and value.startswith("Plsql.") and not value.startswith(("Plsql.fit", "Plsql.to")):
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.{'toInt' if target_type == 'Integer' else 'toLong'}({value})"
    # #75: a REAL / BINARY_DOUBLE / BINARY_FLOAT local takes a literal or helper arithmetic (int, BigDecimal,
    # Object) only through a conversion -- `Double radius = 1;` and `Float d = Plsql.div(10, b);` did not compile
    if target_type in ("Double", "Float") and (re.fullmatch(r"-?\d+(?:\.\d+)?", value) or value.startswith("Plsql.")) \
            and not value.startswith(f"Plsql.to{target_type}("):
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.to{target_type}({value})"
    # date arithmetic comes back as Object (`TRUNC(SYSDATE) - 1` is a DATE, `d1 - d2` a number): 11-14 / 11-15
    if target_type == "LocalDateTime" and value.startswith(("Plsql.sub(", "Plsql.add(", "Plsql.trunc(", "Plsql.nvl(", "Plsql.at(")):
        file.add_import("com.scalar.migrate.plsql.Plsql")
        return f"Plsql.castDate({value})"
    # COALESCE / NVL2 / GREATEST / LEAST hand back whichever argument won, as Object (#84)
    if target_type in ("String", "LocalDateTime", "Boolean") and value.startswith(
            tuple(f"Plsql.{f}(" for f in ("coalesce", "nvl2", "greatest", "least"))):
        return f"({target_type}) {value}"
    # `v_row := v_list(i)`: an element of a collection comes back as Object; a record local needs its class (#54)
    from .types import object_class
    if value.startswith("Plsql.at(") and target_type and any(object_class(n) == target_type for n in _object_names()):
        return f"({target_type}) {value}"
    return value


def _record_copy(file: JavaFile, routine: M.Routine, target: str | None, source: str | None) -> str | None:
    """`target := source` between two records of different types with the same number of fields: PL/SQL copies
    field by field in order; the two Java records are different classes (`CRow` and `NameRec`: 5-46, #75)."""
    if not target or not source or not re.fullmatch(r"\s*[A-Za-z][\w$#]*\s*", source) \
            or not re.fullmatch(r"\s*[A-Za-z][\w$#]*\s*", target):
        return None
    to, of = _holder(routine, target.strip()), _holder(routine, source.strip())
    if to is None or of is None:
        return None
    to_class = _row_type(to) if isinstance(to, M.Declaration) else record_class(to.type)
    of_class = _row_type(of) if isinstance(of, M.Declaration) else record_class(of.type)
    if to_class is None or of_class is None or to_class == of_class:
        return None
    to_columns = record_columns(to.type.resolved or "")
    of_columns = record_columns(of.type.resolved or "")
    if len(to_columns) != len(of_columns) or not to_columns:
        return None
    variable = _local(of.name)
    parts = [_coerce(file, f"{variable}.{java_name(c)}()", java_type(t).name) for (c, _), (_, t) in zip(of_columns, to_columns)]
    return f"{_local(to.name)} = {variable} == null ? null : new {to_class}({', '.join(parts)});"


def _convert_variable(file: JavaFile, routine: M.Routine, written: str | None, value: str, target_type: str) -> str:
    """`q INTEGER := p` with `p NUMBER`: one variable into another of a different Java type. PL/SQL converts
    (rounding into an INTEGER); Java refuses `Integer q = p;` (8-12, #75)."""
    if target_type not in ("Integer", "Long", "Double", "Float") or not written \
            or not re.fullmatch(r"\s*[A-Za-z][\w$#]*\s*", written):
        return value
    source = _local_type(routine, written.strip())
    if source in (target_type, "Object") or source not in ("BigDecimal", "Integer", "Long", "Double", "Float"):
        return value
    file.add_import("com.scalar.migrate.plsql.Plsql")
    helper = {"Integer": "toInt", "Long": "toLong", "Double": "toDouble", "Float": "toFloat"}[target_type]
    return f"Plsql.{helper}({value})"


def _subscripts(text: str) -> "list[str] | None":
    """`(4)(5)` -> ["4", "5"]: the top-level parenthesised groups, in order. None when anything else is there
    (`(1).f1` -- a field of an element -- is not a chain of subscripts)."""
    out, depth, current = [], 0, ""
    for c in text.strip():
        if c == "(":
            if depth > 0:
                current += c
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                out.append(current.strip())
                current = ""
            elif depth < 0:
                return None
            else:
                current += c
        elif depth == 0:
            if not c.isspace():
                return None
        else:
            current += c
    return out if out and depth == 0 else None


def _balanced(text: str) -> bool:
    depth = 0
    for c in text:
        depth += {"(": 1, ")": -1}.get(c, 0)
        if depth < 0:
            return False
    return depth == 0


def _object_names() -> list[str]:
    from .types import object_types
    return list(object_types())


def _local_type(routine: M.Routine, target: str) -> str:
    """The repository hands back Object; the local it lands in has a declared type.

    A trigger declares its locals on the module, not on the routine (`symbols._trigger` registers them
    there and the generator emits them from there), so those count as locals here too. Without this the
    value was cast to `Object` and assigned to a `String` -- Java that does not compile (#12).
    """
    holder = _holder(routine, target)
    if holder is None:
        return "Object"
    if isinstance(holder, M.Declaration):
        row = _row_type(holder)
        if row is not None:
            return row
        return java_type(holder.type.resolved or holder.type.oracle).name
    # a parameter has the Java type its signature gave it: an INTEGER one is a BigDecimal (types.signature_type)
    return signature_type(holder.type).name


def _name_clashes(routine: M.Routine) -> list[str]:
    module = _MODULE.get()
    module_locals = list(module.declarations) if module is not None \
        and routine.routine_kind == "trigger-body" else []
    named: dict[str, list[str]] = {"rowCount": ["SQL%ROWCOUNT"]}
    for holder in list(routine.parameters) + list(routine.declarations) + module_locals:
        if getattr(holder, "declaration_kind", None) in ("cursor", "exception", "type"):
            continue
        named.setdefault(java_name(holder.name), []).append(holder.name)
    return [f"{' / '.join(names)} -> {java}" for java, names in named.items()
            if len({n.lower() for n in names}) > 1]


def _holder(routine: M.Routine, target: str):
    # a nested block's own declaration first: it hides the routine's of the same name (#71)
    for holder in reversed(_BLOCK_HOLDERS.get()):
        if holder.name.lower() == (target or "").lower() and holder.type is not None:
            return holder
    module = _MODULE.get()
    module_locals = list(module.declarations) if module is not None \
        and routine.routine_kind == "trigger-body" else []
    for holder in list(routine.declarations) + module_locals + list(routine.parameters):
        if holder.name.lower() == (target or "").lower() and holder.type is not None:
            return holder
    return None


_NUMBER_CONSTRAINT = re.compile(r"(?:NUMBER|NUMERIC|DECIMAL|DEC)\s*\(\s*(\d+)\s*(?:,\s*(-?\d+)\s*)?\)", re.IGNORECASE)
_TEXT_CONSTRAINT = re.compile(r"(?:VARCHAR2|VARCHAR|NVARCHAR2)\s*\(\s*(\d+)\s*(CHAR|BYTE)?\s*\)", re.IGNORECASE)
# PL/SQL's 32-bit integers and their predefined subtypes: the range and NOT NULL each carries (#59, #60).
# SIMPLE_INTEGER is NOT NULL but wraps on overflow instead of raising, so it has no range here
_TIMESTAMP_DECLARED = re.compile(r"^\s*TIMESTAMP\s*(?:\(\s*(\d+)\s*\))?\s*(WITH\s+(LOCAL\s+)?TIME\s+ZONE)?\s*$",
                                 re.IGNORECASE)


def _text_rendering(declared: str) -> str | None:
    """How a local of this type is written as text, when its Java type alone would write it as something else.

    A BINARY_DOUBLE is a Double, as a REAL local or a NUMBER column read as one is: Oracle writes it 4.0E+000 (#91).
    A TIMESTAMP is a LocalDateTime, as a DATE is: Oracle writes it with the time and as many fraction digits as the
    declared precision, 6 when none is given (#94). A TIMESTAMP WITH TIME ZONE is an OffsetDateTime and the runtime
    knows it, but not its precision. WITH LOCAL TIME ZONE is shown in the session's zone without one, and ScalarDB
    keeps no session zone, so it is left to the runtime.
    """
    if declared.upper() in ("BINARY_DOUBLE", "SIMPLE_DOUBLE"):
        return "Plsql.binaryDouble({})"
    timestamp = _TIMESTAMP_DECLARED.fullmatch(declared)
    if timestamp and not timestamp.group(3):
        return f"Plsql.timestampText({{}}, {int(timestamp.group(1) or 6)})"
    return None


_PLS_INTEGER_TYPES = {"PLS_INTEGER", "BINARY_INTEGER", "NATURAL", "NATURALN", "POSITIVE", "POSITIVEN", "SIGNTYPE"}
_PREDEFINED_RANGES = {"NATURAL": "0..2147483647", "NATURALN": "0..2147483647", "POSITIVE": "1..2147483647",
                      "POSITIVEN": "1..2147483647", "SIGNTYPE": "-1..1"}
_NOT_NULL_TYPES = {"SIMPLE_INTEGER", "NATURALN", "POSITIVEN", "SIMPLE_FLOAT", "SIMPLE_DOUBLE"}
_BOUNDS = re.compile(r"\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*")

# CHAR / NCHAR, with or without a length (CHAR alone is CHAR(1)). NCHAR counts characters whatever the setting
_CHAR_CONSTRAINT = re.compile(r"(N?)CHAR(?:ACTER)?\s*(?:\(\s*(\d+)\s*(CHAR|BYTE)?\s*\))?", re.IGNORECASE)


def _constrain(file: JavaFile, value: str, type_ref: "M.TypeRef | None") -> str:
    """The declared type's checks on a value going into a variable: the size (`_fit`), then a RANGE and NOT NULL,
    each VALUE_ERROR as in Oracle (#59, #60). A PLS_INTEGER's 32 bits are checked by `Plsql.toInt`, which every
    assignment to one already goes through."""
    fitted = _fit(file, value, type_ref)
    if type_ref is None:
        return fitted
    declared = ((type_ref.resolved or type_ref.oracle) or "").strip().upper()
    bounds = _BOUNDS.fullmatch(type_ref.range or _PREDEFINED_RANGES.get(declared, ""))
    if bounds and fitted != "null":
        file.add_import("com.scalar.migrate.plsql.Plsql")
        fitted = f"Plsql.inRange({fitted}, {bounds.group(1)}L, {bounds.group(2)}L)"
    if type_ref.nullable is False or declared in _NOT_NULL_TYPES:
        file.add_import("com.scalar.migrate.plsql.Plsql")
        fitted = f"Plsql.notNull({fitted})"
    return fitted


def _fit(file: JavaFile, value: str, type_ref: "M.TypeRef | None") -> str:
    """A value on its way into a variable declared `NUMBER(5,2)` or `VARCHAR2(3)`.

    The constraint is behaviour: Oracle rounds to the scale, and raises VALUE_ERROR past the precision or the
    length. BigDecimal and String hold anything, so without this 1.005 stayed 1.005 and 'abcd' fitted in three
    bytes. A CHAR(n) is checked the same way and then padded with blanks to n (#62).
    """
    declared = ((type_ref.resolved or type_ref.oracle) if type_ref is not None else "") or ""
    number = _NUMBER_CONSTRAINT.fullmatch(declared.strip())
    text = _TEXT_CONSTRAINT.fullmatch(declared.strip())
    blank_padded = _CHAR_CONSTRAINT.fullmatch(declared.strip())
    if value == "null" or not (number or text or blank_padded):
        return value
    if blank_padded:
        file.add_import("com.scalar.migrate.plsql.Plsql")
        chars = bool(blank_padded.group(1)) or (blank_padded.group(3) or "").upper() == "CHAR"
        return f"Plsql.pad({value}, {int(blank_padded.group(2) or 1)}, {'true' if chars else 'false'})"
    if number:
        # NUMBER(9) is an Integer and NUMBER(18) a Long in the generated code (types.java_type), and the helper
        # has to hand back that type: `Long v = Plsql.fit(...)` with a BigDecimal result did not compile
        helper = {"BigDecimal": "fit", "Long": "fitLong", "Integer": "fitInt"}.get(java_type(declared).name)
        if helper is None:
            return value
        file.add_import("com.scalar.migrate.plsql.Plsql")
        scale = f", {int(number.group(2) or 0)}" if helper == "fit" else ""
        return f"Plsql.{helper}({value}, {int(number.group(1))}{scale})"
    file.add_import("com.scalar.migrate.plsql.Plsql")
    return f"Plsql.fit({value}, {int(text.group(1))}, {'true' if (text.group(2) or '').upper() == 'CHAR' else 'false'})"


def _sql_suffix(statement: M.SqlOperation) -> str:
    from .repository import sql_suffix

    return sql_suffix(statement)


def _untranslated(file: JavaFile, statement: M.Statement, result: ServiceFile) -> None:
    text = getattr(statement, "text", "") or getattr(statement, "expression", "") or statement.kind
    file.comment(f"not translated: {statement.kind}")
    for line in str(text).splitlines()[:6]:
        file.comment(f"    {line}")
    file.line(f'throw new UnsupportedOperationException("{statement.kind} is not translated");')
    result.untranslated.append(statement.id)


def _source_comment(file: JavaFile, node: M.Node) -> None:
    if node.source_range is not None:
        file.comment(f"{node.source_range.file}:{node.source_range.start_line}")


class Untranslatable(Exception):
    """An expression the translator could not place. The statement becomes a throw rather than broken code."""

    def __init__(self, names: list[str], text: str) -> None:
        super().__init__(", ".join(names))
        self.names = names
        self.text = text


def _not_found_flags(routine: M.Routine) -> list[str]:
    """The cursors whose `%NOTFOUND` this routine's rewritten reads answer, in the order they are read."""
    out: list[str] = []
    for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
        flag = getattr(statement, "not_found_flag", None)
        if flag and flag.lower() not in [f.lower() for f in out]:
            out.append(flag)
    return out


def _flag_name(cursor: str) -> str:
    return java_name(f"{cursor}_not_found")


def _expr(file: JavaFile, text: str | None, routine: M.Routine, result: "ServiceFile | None",
          module: M.Module | None = None, boolean_value: bool = False, condition: bool = False,
          as_text: bool = False) -> str:
    """Translate an expression, or refuse.

    An unrecognised name reaching the output would either fail to compile or, worse, resolve to something with
    different semantics. Refusing turns the statement into a `throw` with the original next to it, which the
    compiler accepts and a reviewer can act on. Emitting it anyway is the one outcome that helps nobody.
    """
    names = {**_scope(routine, module or _MODULE.get()), **_BLOCK_LOCALS.get(), **_LOOP_ROWS.get(),
             **_HANDLER_ERROR.get()}
    names.update({f"{flag}%notfound": _flag_name(flag) for flag in _not_found_flags(routine)})
    for cursor in _general_cursors(routine):
        state = _cursor_state(cursor)
        names.update({f"{cursor}%found": f"{state}.found()", f"{cursor}%notfound": f"{state}.notFound()",
                      f"{cursor}%rowcount": f"{state}.rowCount()", f"{cursor}%isopen": f"{state}.isOpen()"})
    rendered = translate(text, names, boolean_value=boolean_value, condition=condition, as_text=as_text)
    for name in rendered.unknown:
        if result is not None and name not in result.unknown_names:
            result.unknown_names.append(name)
    if rendered.unknown:
        raise Untranslatable(rendered.unknown, text or "")
    if rendered.sequences:
        # `v_id := seq.NEXTVAL` outside any SQL. Numbering belongs to the repository -- it is the one that is
        # given a `Sequences` (plan §9) -- so the service asks it, instead of reaching for a field it never had
        # (found 2026-09-20 on the first routine that came from outside the corpus: the corpus only ever took
        # a number inside an INSERT)
        file.add_import(*(i for i in rendered.imports if i != SEQUENCES_IMPORT))
        return rendered.java.replace('sequences.next("', 'repository.nextSequenceValue("')
    file.add_import(*rendered.imports)
    return rendered.java
