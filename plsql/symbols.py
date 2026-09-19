"""P1-3: the symbol table, and what `%TYPE` / `%ROWTYPE` actually resolve to.

Nothing downstream can decide anything without knowing what a name refers to and what type it has. Two properties
matter more than completeness:

* **An unresolved name is recorded, never guessed.** `docs/plsql-kpi.md` makes `symbolResolution` and
  `typeResolution` factors of the confidence, and a factor of 0 keeps a routine out of AUTO. Silently inventing a
  type would convert an unknown into a wrong answer.
* **A resolved `%TYPE` remembers where it came from.** The design document (§5.3) asks for the DDL snapshot id to
  be recorded, so a type that changes when the schema changes can be found again.

    schema = OracleSchema.from_ddl(Path("fixtures/plsql/src/schema.sql"))
    table = build(parse_file("pkg_order.pkb"), schema)
    table.resolve("pkg_order.create_order", "v_status").type.resolved   # 'VARCHAR2(20)'
    table.unresolved                                                    # Issue(WARN, 'UNRESOLVED_TYPE', ...)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from antlr4 import ParserRuleContext

from .frontend import ParsedFile
from .ir.model import TypeRef
from .source import Issue, SourceRange

# contexts we care about, by class name -- the grammar is vendored, so matching on names keeps this readable
ROUTINE_BODIES = {"Procedure_bodyContext", "Function_bodyContext",
                  "Create_procedure_bodyContext", "Create_function_bodyContext"}
PACKAGE_BODY = "Create_package_bodyContext"
PACKAGE_SPEC = "Create_packageContext"
TRIGGER = "Create_triggerContext"

def _first_group(match) -> str | None:
    return match.group(1) if match else None


TYPE_ATTRIBUTE = re.compile(r"^\s*(?P<base>[\w$#.]+)\s*%\s*(?P<attr>TYPE|ROWTYPE)\s*$", re.IGNORECASE)


# --- the Oracle side of the schema ------------------------------------------------------------------

@dataclass
class OracleSchema:
    """Column types as the Oracle DDL declares them, plus an id for the snapshot they came from.

    The ScalarDB schema (P0-3) is not usable here: it holds target types, and `%TYPE` has to resolve to what
    Oracle says, or the semantics change before anyone has decided to change them.
    """

    tables: dict[str, dict[str, str]] = field(default_factory=dict)
    # 表ごとの主キー。**1 行に絞れるかは Oracle の話**なので、移行先のスキーマに聞かない
    # （#12: trigger を掛けてよい書き込みかどうかがこれで決まる）
    keys: dict[str, list[str]] = field(default_factory=dict)
    snapshot: str | None = None

    @classmethod
    def from_ddl(cls, path: str | Path) -> "OracleSchema":
        import sqlglot
        from sqlglot import exp

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        schema = cls(snapshot=f"{path.name}@{digest}")
        for statement in sqlglot.parse(text, dialect="oracle"):
            if not isinstance(statement, exp.Create) or statement.kind != "TABLE":
                continue
            table = statement.find(exp.Table)
            if table is None:
                continue
            columns: dict[str, str] = {}
            for column in statement.find_all(exp.ColumnDef):
                columns[column.name.lower()] = column.args["kind"].sql(dialect="oracle")
            schema.tables[table.name.lower()] = columns
            schema.keys[table.name.lower()] = _primary_key(statement)
        return schema

    def column(self, table: str, column: str) -> str | None:
        return self.tables.get(table.lower(), {}).get(column.lower())

    def columns(self, table: str) -> dict[str, str] | None:
        return self.tables.get(table.lower())

    def primary_key(self, table: str) -> list[str]:
        return self.keys.get(table.lower(), [])


def _primary_key(statement) -> list[str]:
    """`CONSTRAINT pk_orders PRIMARY KEY (order_id)` と、列に付いた `PRIMARY KEY` の両方を見る。"""
    from sqlglot import exp

    for constraint in statement.find_all(exp.PrimaryKey):
        columns = [c.name.lower() for c in constraint.find_all(exp.Identifier)]
        if columns:
            return columns
    for column in statement.find_all(exp.ColumnDef):
        if any(isinstance(c.kind, exp.PrimaryKeyColumnConstraint)
               for c in column.args.get("constraints") or []):
            return [column.name.lower()]
    return []


# --- symbols ------------------------------------------------------------------------------------------

@dataclass
class Symbol:
    name: str
    kind: str                  # parameter | variable | constant | cursor | exception | type | routine
    scope: str
    type: TypeRef | None = None
    source_range: SourceRange | None = None
    visibility: str = "private"
    direction: str | None = None      # parameters only
    signature: str | None = None      # routines only, for overload sets
    # cursors only: the query the cursor is defined as, and the names of its own parameters, in order. A
    # cursor declared in a package specification exists nowhere else -- the IR lowers the body, not the
    # specification -- so without this a routine that opens it cannot be told what it reads (#11).
    query: str | None = None
    parameters: list[str] = field(default_factory=list)


@dataclass
class Scope:
    id: str
    kind: str                  # module | routine
    parent: "Scope | None" = None
    symbols: dict[str, Symbol] = field(default_factory=dict)

    def declare(self, symbol: Symbol) -> None:
        self.symbols.setdefault(symbol.name.lower(), symbol)

    def resolve(self, name: str) -> Symbol | None:
        scope: Scope | None = self
        while scope is not None:
            found = scope.symbols.get(name.lower())
            if found is not None:
                return found
            scope = scope.parent
        return None


@dataclass
class SymbolTable:
    scopes: dict[str, Scope] = field(default_factory=dict)
    overloads: dict[str, list[Symbol]] = field(default_factory=dict)
    unresolved: list[Issue] = field(default_factory=list)
    schema_snapshot: str | None = None
    # the Oracle DDL this table was built against. The SQL bridge needs it to say what a column's declared type
    # is -- which decides a money column's scale, and the ScalarDB schema cannot answer because it only holds
    # the target type the column was mapped to.
    oracle_schema: "OracleSchema | None" = None

    def resolve(self, scope_id: str, name: str) -> Symbol | None:
        scope = self.scopes.get(scope_id)
        return scope.resolve(name) if scope else None

    def all_symbols(self) -> list[Symbol]:
        return [s for scope in self.scopes.values() for s in scope.symbols.values()]

    def resolution_rate(self) -> float:
        """KPI-2 over the symbols that carry a type: how many of them resolved."""
        typed = [s for s in self.all_symbols() if s.type is not None]
        if not typed:
            return 1.0
        return sum(1 for s in typed if s.type.is_resolved()) / len(typed)


# --- building -------------------------------------------------------------------------------------------

def build(parsed: ParsedFile, schema: OracleSchema | None = None,
          public_names: set[str] | None = None, spec: ParsedFile | None = None) -> SymbolTable:
    """Walk the parse trees of one file and record what it declares.

    `public_names` carries the routine names a package specification exposed, so a body can mark visibility.

    `spec` is that specification's parse tree. What a package declares in its specification -- a RECORD type, a
    constant -- is visible throughout its body, so the specification is walked first and its module scopes are
    left in place for the body to add to. Without it a body referring to its own package's type resolves
    nothing, and the type silently becomes an opaque name.
    """
    table = SymbolTable(schema_snapshot=schema.snapshot if schema else None, oracle_schema=schema)
    for source in ([spec] if spec is not None else []) + [parsed]:
        for unit in source.units:
            if unit.tree is None:
                continue
            _Builder(table, schema, unit.unit, public_names or set()).walk(unit.tree)
    return table


def public_routines(parsed: ParsedFile) -> set[str]:
    """Routine names a package specification declares. Everything else in the body is private."""
    names: set[str] = set()
    for unit in parsed.units:
        if unit.tree is None:
            continue
        for context in _descend(unit.tree, {"Procedure_specContext", "Function_specContext"}):
            identifier = _child(context, "IdentifierContext")
            if identifier is not None:
                names.add(_text(identifier).lower())
    return names


class _Builder:
    def __init__(self, table: SymbolTable, schema: OracleSchema | None, unit, public: set[str]) -> None:
        self.table = table
        self.schema = schema
        self.unit = unit
        self.public = public

    # -- traversal ---------------------------------------------------------------------------------------
    def walk(self, tree: ParserRuleContext) -> None:
        for context in _descend(tree, {PACKAGE_BODY, PACKAGE_SPEC, TRIGGER} | ROUTINE_BODIES):
            name = type(context).__name__
            if name in (PACKAGE_BODY, PACKAGE_SPEC):
                self._package(context)
            elif name == TRIGGER:
                self._trigger(context)
            elif name in ("Create_procedure_bodyContext", "Create_function_bodyContext"):
                self._standalone(context)

    def _package(self, context: ParserRuleContext) -> None:
        name = _text(_child(context, "Package_nameContext") or context).split(".")[-1].lower()
        scope = self._scope(name, "module", None)
        # a body declares under Declare_spec, a specification under Package_obj_spec. Walking only the first
        # means a package's own RECORD type -- declared in the specification, used throughout the body -- is
        # never recorded, and every reference to it resolves to an opaque name.
        for declaration in _descend(context, {"Declare_specContext", "Package_obj_specContext"},
                                    stop=ROUTINE_BODIES):
            self._declaration(scope, declaration)
        from .lower import _routine_name, overload_ordinals

        bodies = list(_descend(context, ROUTINE_BODIES))
        for body, ordinal in zip(bodies, overload_ordinals([_routine_name(b) for b in bodies])):
            self._routine(body, parent=scope, module=name, ordinal=ordinal)

    def _standalone(self, context: ParserRuleContext) -> None:
        self._routine(context, parent=None, module=None)

    def _trigger(self, context: ParserRuleContext) -> None:
        name = _text(_child(context, "Trigger_nameContext") or context).split(".")[-1].lower()
        scope = self._scope(name, "module", None)
        for declaration in _descend(context, {"Declare_specContext"}):
            self._declaration(scope, declaration)

    def _routine(self, context: ParserRuleContext, parent: Scope | None, module: str | None,
                 ordinal: int | None = None) -> None:
        identifier = _child(context, "IdentifierContext") or _child(context, "Procedure_nameContext") \
            or _child(context, "Function_nameContext")
        name = _text(identifier).split(".")[-1].lower() if identifier is not None else "<anonymous>"
        from .lower import routine_id_of

        # the same id the lowering gives the routine: overloads used to share one scope, and the second one's
        # parameters replaced the first one's
        scope_id = routine_id_of(module, name, ordinal)
        scope = self._scope(scope_id, "routine", parent)

        parameters = []
        # 入れ子の subprogram の引数は、外側の routine の引数ではない
        for parameter in _descend(context, {"ParameterContext"},
                                  stop={"BodyContext", "Procedure_bodyContext", "Function_bodyContext"}):
            symbol = self._parameter(scope, parameter)
            if symbol is not None:
                parameters.append(symbol)

        returns = None
        for spec in _children(context, "Type_specContext"):
            returns = self._type(scope, _text(spec))
            break
        if returns is not None:
            scope.declare(Symbol(name="<return>", kind="variable", scope=scope_id, type=returns,
                                 source_range=self._range(context)))

        for declaration in _descend(context, {"Declare_specContext"}, stop={"BodyContext"}):
            self._declaration(scope, declaration)

        routine_symbol = Symbol(
            name=name, kind="routine", scope=module or "", type=returns, source_range=self._range(context),
            visibility="public" if (module is None or name in self.public) else "private",
            signature=",".join(f"{p.direction} {p.type.oracle if p.type else '?'}" for p in parameters))
        if parent is not None:
            parent.declare(routine_symbol)
        key = f"{module}.{name}" if module else name
        self.table.overloads.setdefault(key, []).append(routine_symbol)

    # -- declarations -------------------------------------------------------------------------------------
    def _parameter(self, scope: Scope, context: ParserRuleContext) -> Symbol | None:
        name_context = _child(context, "Parameter_nameContext")
        if name_context is None:
            return None
        text = _text(context)
        direction = "IN OUT" if re.search(r"\bIN\s+OUT\b", text, re.I) else \
            "OUT" if re.search(r"\bOUT\b", text, re.I) else "IN"
        spec = _child(context, "Type_specContext")
        symbol = Symbol(name=_text(name_context), kind="parameter", scope=scope.id, direction=direction,
                        type=self._type(scope, _text(spec)) if spec is not None else None,
                        source_range=self._range(context))
        scope.declare(symbol)
        return symbol

    def _declaration(self, scope: Scope, context: ParserRuleContext) -> None:
        for kind, context_name, name_name in (
                ("variable", "Variable_declarationContext", "Identifier"),
                ("exception", "Exception_declarationContext", "Identifier"),
                ("cursor", "Cursor_declarationContext", "Identifier"),
                ("type", "Type_declarationContext", "Identifier")):
            for declaration in _descend(context, {context_name},
                                        stop={"Procedure_bodyContext", "Function_bodyContext"}):
                identifier = _child(declaration, "IdentifierContext")
                if identifier is None:
                    continue
                text = _text(declaration)
                spec = _child(declaration, "Type_specContext")
                actual = "constant" if re.search(r"\bCONSTANT\b", text, re.I) else kind
                resolved = None
                if kind == "type":
                    resolved = self._record_type(scope, declaration) \
                        or self._collection_type(scope, declaration)
                elif spec is not None:
                    resolved = self._type(scope, _text(spec))
                query = parameters = None
                if kind == "cursor":
                    query = _first_group(re.search(r"\bIS\b\s*(.+?);?\s*$", text, re.DOTALL | re.IGNORECASE))
                    # a cursor's own parameters are `Parameter_spec`, not the `Parameter` a routine uses
                    parameters = [_text(_child(p, "Parameter_nameContext") or p)
                                  for p in _descend(declaration, {"Parameter_specContext"})]
                scope.declare(Symbol(
                    name=_text(identifier), kind=actual, scope=scope.id, type=resolved,
                    source_range=self._range(declaration),
                    query=query.strip() if query else None, parameters=parameters or []))

    def _record_type(self, scope: Scope, declaration: ParserRuleContext) -> TypeRef | None:
        """`TYPE t IS RECORD (a customers.name%TYPE, ...)` resolved to the same shape a %ROWTYPE resolves to.

        Written in the `RECORD(name TYPE, ...)` form the %ROWTYPE path already produces, so that everything
        downstream -- the DTO generator, the Java type mapper -- treats the two the same. They are the same
        thing: a named list of typed fields. Each field's own type goes through the ordinary resolution, which
        is what lets a `%TYPE` field reach the DDL.
        """
        definition = _child(declaration, "Record_type_defContext")
        if definition is None:
            return None
        fields = []
        for field in _descend(definition, {"Field_specContext"}):
            written = _text(field).split(None, 1)
            if len(written) != 2:
                continue
            resolved = self._type(scope, written[1])
            fields.append(f"{written[0]} {resolved.resolved or written[1]}")
        if not fields:
            return None
        return TypeRef(_text(declaration).split()[1], f"RECORD({', '.join(fields)})", "record",
                       self.table.schema_snapshot)

    def _collection_type(self, scope: Scope, declaration: ParserRuleContext) -> TypeRef | None:
        """`TYPE t IS TABLE OF NUMBER(19) INDEX BY PLS_INTEGER` を、要素の型まで解決して残す。

        要素の型が無いと、その型の引数は Java で `Object` にしかならない——`List<BigDecimal>` と
        書けない。`RECORD` を解決しているのと同じ理由で、**名前だけでは移行先の型を決められない**。
        """
        match = re.search(r"\bIS\s+TABLE\s+OF\s+(?P<element>.+?)(?:\s+INDEX\s+BY\b.*)?;?\s*$",
                          _text(declaration), re.IGNORECASE | re.DOTALL)
        if match is None:
            return None
        element = self._type(scope, match.group("element").strip())
        return TypeRef(_text(declaration).split()[1], f"TABLE OF {element.resolved or element.oracle}",
                       "collection", self.table.schema_snapshot)

    # -- types ---------------------------------------------------------------------------------------------
    def _type(self, scope: Scope, written: str) -> TypeRef:
        written = written.strip()
        attribute = TYPE_ATTRIBUTE.match(written)
        if attribute is None:
            # a package-local RECORD type named here resolves to its shape, the same as a %ROWTYPE would
            declared = scope.resolve(written.rpartition(".")[2])
            if declared is not None and declared.kind == "type" and declared.type is not None \
                    and declared.type.origin in ("record", "collection"):
                return TypeRef(written, declared.type.resolved, declared.type.origin,
                               self.table.schema_snapshot)
            return TypeRef(oracle=written, resolved=written, origin="declared")

        base, kind = attribute.group("base"), attribute.group("attr").upper()
        if kind == "ROWTYPE":
            return self._rowtype(base, written)
        return self._coltype(scope, base, written)

    def _coltype(self, scope: Scope, base: str, written: str) -> TypeRef:
        if "." in base:
            table, _, column = base.rpartition(".")
            resolved = self.schema.column(table, column) if self.schema else None
            if resolved is not None:
                return TypeRef(written, resolved, "column-type", self.table.schema_snapshot)
            self._unresolved("UNRESOLVED_TYPE",
                             f"{written}: no column {table}.{column} in the DDL snapshot", scope)
            return TypeRef(written, None, "unresolved", self.table.schema_snapshot)

        referenced = scope.resolve(base)
        if referenced is not None and referenced.type is not None and referenced.type.is_resolved():
            return TypeRef(written, referenced.type.resolved, "inferred", self.table.schema_snapshot)
        self._unresolved("UNRESOLVED_TYPE", f"{written}: {base} is not a declared variable in scope", scope)
        return TypeRef(written, None, "unresolved", self.table.schema_snapshot)

    def _rowtype(self, base: str, written: str) -> TypeRef:
        columns = self.schema.columns(base.rpartition(".")[2]) if self.schema else None
        if columns is None:
            self._unresolved("UNRESOLVED_TYPE", f"{written}: no table {base} in the DDL snapshot", None)
            return TypeRef(written, None, "unresolved", self.table.schema_snapshot)
        shape = ", ".join(f"{name} {type_}" for name, type_ in columns.items())
        return TypeRef(written, f"RECORD({shape})", "rowtype", self.table.schema_snapshot)

    # -- helpers --------------------------------------------------------------------------------------------
    def _scope(self, scope_id: str, kind: str, parent: Scope | None) -> Scope:
        scope = self.table.scopes.get(scope_id)
        if scope is None:
            scope = Scope(id=scope_id, kind=kind, parent=parent)
            self.table.scopes[scope_id] = scope
        return scope

    def _unresolved(self, code: str, message: str, scope: Scope | None) -> None:
        self.table.unresolved.append(Issue("WARN", code, message, self.unit.range))

    def _range(self, context: ParserRuleContext) -> SourceRange:
        start = self.unit.origin(context.start.line, context.start.column + 1)
        stop = self.unit.origin(context.stop.line, context.stop.column + 1) if context.stop else start
        return SourceRange(start.file, start.line, stop.line, start.column, stop.column)


# --- parse-tree helpers ----------------------------------------------------------------------------------

def _text(context) -> str:
    """The original source text of a context, whitespace intact. getText() would glue tokens together."""
    if context is None:
        return ""
    stream = context.start.getInputStream()
    return stream.getText(context.start.start, context.stop.stop) if context.stop else context.getText()


def _children(context, name: str) -> list:
    return [context.getChild(i) for i in range(context.getChildCount())
            if type(context.getChild(i)).__name__ == name]


def _child(context, name: str):
    found = _children(context, name)
    return found[0] if found else None


def _descend(context, names: set[str], stop: set[str] | None = None) -> list:
    """Matching contexts in source order, not descending into a match or into a `stop` context.

    Source order is not cosmetic: a declaration may refer to one written above it (`b a%TYPE`), and a routine's
    parameters are a signature. Returning matches in any other order silently breaks both.
    """
    out: list = []

    def visit(node) -> None:
        for i in range(node.getChildCount()):
            child = node.getChild(i)
            if not hasattr(child, "getChildCount"):
                continue
            name = type(child).__name__
            if name in names:
                out.append(child)
                continue
            if stop and name in stop:
                continue
            visit(child)

    visit(context)
    return out
