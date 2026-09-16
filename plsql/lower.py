"""P1-5: parse tree -> Migration IR.

This is where the shape of the source stops mattering and the meaning starts. The design document (§5.1) forbids
going from a parse tree straight to Java, and this module is the reason that is possible: everything downstream
reads IR, and the ANTLR contexts stop here.

    modules = lower_file(parse_file("pkg_order.pkb"), symbols=table)
    program = lower_program([...])

## Nothing is dropped quietly

A construct the lowering does not model becomes an `Unsupported` node carrying its source text, not an omission.
That matters twice over: the plan's non-functional requirements say a warning must never be hidden behind a
success, and `ruleCoverage` (docs/plsql-kpi.md §3) counts nodes the rules can decide -- so a routine holding one
cannot reach AUTO by accident.

## What the IR records that the tree does not

Transaction and external effects are summarised per routine while walking, because that is the evidence the
safety rules (P2-3) work from: a `COMMIT` anywhere in a body makes the whole routine a REDESIGN candidate, and
finding that later would mean walking the IR again for something the lowering already saw.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from antlr4 import ParserRuleContext

from .frontend import ParsedFile
from .ir import model as M
from .preprocess import Unit
from .source import SourceRange
from .symbols import OracleSchema, SymbolTable, _child, _children, _descend, _text

RAISE_APPLICATION_ERROR = re.compile(
    r"RAISE_APPLICATION_ERROR\s*\(\s*(-?\d+)\s*,\s*(.*)\)\s*$", re.IGNORECASE | re.DOTALL)
DB_LINK = re.compile(r"@\s*([A-Za-z][\w$#]*)")
EXTERNAL_PACKAGES = re.compile(
    r"\b(UTL_HTTP|UTL_SMTP|UTL_FILE|UTL_TCP|DBMS_SCHEDULER|DBMS_JOB|DBMS_AQ|DBMS_PIPE|DBMS_ALERT|DBMS_LOB)\b",
    re.IGNORECASE)

# statement context -> the lowering method that handles it
STATEMENT_CONTEXTS = (
    "Assignment_statementContext", "If_statementContext", "Case_statementContext", "Loop_statementContext",
    "Forall_statementContext", "Exit_statementContext", "Continue_statementContext", "Goto_statementContext",
    "Null_statementContext", "Raise_statementContext", "Return_statementContext", "Call_statementContext",
    "Commit_statementContext", "Rollback_statementContext", "Savepoint_statementContext",
    "Data_manipulation_language_statementsContext", "Execute_immediateContext",
    "Open_statementContext", "Fetch_statementContext", "Close_statementContext",
    "Open_for_statementContext", "Pipe_row_statementContext", "Sql_statementContext",
    "Cursor_manipulation_statementsContext", "Transaction_control_statementsContext",
)


@dataclass
class _Context:
    unit: Unit
    module: str
    symbols: SymbolTable | None
    schema: OracleSchema | None


def lower_file(parsed: ParsedFile, symbols: SymbolTable | None = None,
               schema: OracleSchema | None = None, public: set[str] | None = None) -> list[M.Module]:
    """Every module a file defines. A unit that did not parse is skipped; its diagnostic already exists."""
    modules: list[M.Module] = []
    for parsed_unit in parsed.units:
        if parsed_unit.tree is None:
            continue
        lowerer = _Lowerer(parsed_unit.unit, symbols, schema, public or set())
        modules.extend(lowerer.modules(parsed_unit.tree))
    return modules


def lower_program(files: list[ParsedFile], symbols: SymbolTable | None = None,
                  schema: OracleSchema | None = None, public: set[str] | None = None,
                  program_id: str = "program") -> M.Program:
    program = M.Program(id=program_id, kind="Program",
                        schema_snapshot=schema.snapshot if schema else None)
    for parsed in files:
        program.modules.extend(lower_file(parsed, symbols, schema, public))
        program.unresolved.extend(i for i in parsed.all_issues() if i.severity != "INFO")
    if symbols is not None:
        program.unresolved.extend(symbols.unresolved)
    return program


def lower_source(path: str | Path, schema: OracleSchema | None = None) -> tuple[list[M.Module], SymbolTable]:
    """Parse, resolve and lower one source file, picking up its package specification when there is one.

    The specification is what says which routines are public, and a body lowered without it would mark every
    routine private -- a difference that matters as soon as the rules look at visibility.
    """
    from .frontend import parse_file
    from .symbols import build as build_symbols, public_routines

    path = Path(path)
    spec_path = path.with_suffix(".pks")
    public = public_routines(parse_file(spec_path)) if spec_path.exists() and spec_path != path else set()
    parsed = parse_file(path)
    symbols = build_symbols(parsed, schema, public)
    return lower_file(parsed, symbols, schema, public), symbols


class _Lowerer:
    def __init__(self, unit: Unit, symbols: SymbolTable | None, schema: OracleSchema | None,
                 public: set[str]) -> None:
        self.unit = unit
        self.symbols = symbols
        self.schema = schema
        self.public = public
        self.module_name = ""

    # -- modules ------------------------------------------------------------------------------------------
    def modules(self, tree: ParserRuleContext) -> list[M.Module]:
        out: list[M.Module] = []
        for context in _descend(tree, {"Create_package_bodyContext", "Create_packageContext",
                                       "Create_triggerContext", "Create_procedure_bodyContext",
                                       "Create_function_bodyContext"}):
            name = type(context).__name__
            if name in ("Create_package_bodyContext", "Create_packageContext"):
                out.append(self._package(context, spec=name == "Create_packageContext"))
            elif name == "Create_triggerContext":
                out.append(self._trigger(context))
            else:
                out.append(self._standalone(context))
        return out

    def _package(self, context: ParserRuleContext, spec: bool) -> M.Module:
        name = _text(_child(context, "Package_nameContext")).split(".")[-1].lower() or "<package>"
        self.module_name = name
        module = M.Module(id=name, kind="Module", name=name, module_kind="package",
                          source_range=self._range(context))
        ids = M.IdFactory(name)
        for declaration in _descend(context, {"Declare_specContext"}, stop={"Procedure_bodyContext",
                                                                            "Function_bodyContext"}):
            module.declarations.extend(self._declarations(declaration, ids))
        if not spec:
            for body in _descend(context, {"Procedure_bodyContext", "Function_bodyContext"}):
                module.routines.append(self._routine(body, module=name))
        return module

    def _standalone(self, context: ParserRuleContext) -> M.Module:
        routine = self._routine(context, module=None)
        self.module_name = routine.name
        module = M.Module(id=routine.name, kind="Module", name=routine.name,
                          module_kind=routine.routine_kind, source_range=self._range(context),
                          routines=[routine])
        return module

    def _trigger(self, context: ParserRuleContext) -> M.Module:
        name = _text(_child(context, "Trigger_nameContext")).split(".")[-1].lower() or "<trigger>"
        self.module_name = name
        text = _text(context)
        table = _first(re.search(r"\bON\s+([\w$#.]+)", text, re.IGNORECASE))
        timing = _first(re.search(r"\b(BEFORE|AFTER|INSTEAD\s+OF)\b", text, re.IGNORECASE))
        event = _first(re.search(r"\b(INSERT|UPDATE|DELETE)(\s+OF\s+[\w$#,\s]+)?\b", text, re.IGNORECASE))
        module = M.Module(id=name, kind="Module", name=name, module_kind="trigger",
                          source_range=self._range(context), trigger_table=table,
                          trigger_timing=timing, trigger_event=event)
        body = _child(context, "Trigger_bodyContext") or context
        routine = M.Routine(id=f"{name}.body", kind="Routine", name="body", routine_kind="trigger-body",
                            source_range=self._range(context), visibility="private")
        ids = M.IdFactory(routine.id)
        routine.body = self._statements(body, ids)
        self._effects(routine, _text(context))
        module.routines.append(routine)
        return module

    # -- routines ------------------------------------------------------------------------------------------
    def _routine(self, context: ParserRuleContext, module: str | None) -> M.Routine:
        identifier = _child(context, "IdentifierContext") or _child(context, "Procedure_nameContext") \
            or _child(context, "Function_nameContext")
        name = _text(identifier).split(".")[-1].lower() if identifier is not None else "<anonymous>"
        routine_id = f"{module}.{name}" if module else name
        is_function = "Function" in type(context).__name__
        routine = M.Routine(
            id=routine_id, kind="Routine", name=name,
            routine_kind="function" if is_function else "procedure",
            source_range=self._range(context),
            visibility="public" if (module is None or name in self.public) else "private")
        ids = M.IdFactory(routine_id)

        for parameter in _descend(context, {"ParameterContext"}, stop={"BodyContext"}):
            routine.parameters.append(self._parameter(parameter, ids))
        if is_function:
            spec = next(iter(_children(context, "Type_specContext")), None)
            if spec is not None:
                routine.return_type = self._type(routine_id, _text(spec))

        for declaration in _descend(context, {"Declare_specContext"}, stop={"BodyContext"}):
            routine.declarations.extend(self._declarations(declaration, ids))

        text = _text(context)
        if re.search(r"\bAUTHID\s+CURRENT_USER\b", text, re.IGNORECASE):
            routine.auth_id = "CURRENT_USER"
        elif re.search(r"\bAUTHID\s+DEFINER\b", text, re.IGNORECASE):
            routine.auth_id = "DEFINER"
        routine.deterministic = bool(re.search(r"\bDETERMINISTIC\b", text, re.IGNORECASE))

        body = _child(context, "BodyContext")
        if body is not None:
            routine.body = self._statements(_child(body, "Seq_of_statementsContext") or body, ids)
            for handler in _descend(body, {"Exception_handlerContext"}):
                routine.exception_handlers.append(self._handler(handler, ids))
        self._effects(routine, text)
        return routine

    def _parameter(self, context: ParserRuleContext, ids: M.IdFactory) -> M.Parameter:
        text = _text(context)
        direction = "IN OUT" if re.search(r"\bIN\s+OUT\b", text, re.I) else \
            "OUT" if re.search(r"\bOUT\b", text, re.I) else "IN"
        spec = _child(context, "Type_specContext")
        default = _first(re.search(r"(?::=|\bDEFAULT\b)\s*(.+)$", text, re.IGNORECASE | re.DOTALL))
        return M.Parameter(
            id=ids.next("param"), kind="Parameter", name=_text(_child(context, "Parameter_nameContext")),
            direction=direction, source_range=self._range(context),
            type=self._type(None, _text(spec)) if spec is not None else None,
            default=default.strip() if default else None,
            nocopy=bool(re.search(r"\bNOCOPY\b", text, re.IGNORECASE)))

    def _declarations(self, context: ParserRuleContext, ids: M.IdFactory) -> list[M.Declaration]:
        out: list[M.Declaration] = []
        for kind, context_name in (("variable", "Variable_declarationContext"),
                                   ("exception", "Exception_declarationContext"),
                                   ("cursor", "Cursor_declarationContext"),
                                   ("type", "Type_declarationContext")):
            for declaration in _descend(context, {context_name}):
                identifier = _child(declaration, "IdentifierContext")
                if identifier is None:
                    continue
                text = _text(declaration)
                spec = _child(declaration, "Type_specContext")
                initial = _first(re.search(r":=\s*(.+?);?\s*$", text, re.DOTALL))
                out.append(M.Declaration(
                    id=ids.next("decl"), kind="Declaration", name=_text(identifier),
                    declaration_kind="constant" if re.search(r"\bCONSTANT\b", text, re.I) else kind,
                    source_range=self._range(declaration), initial=initial.strip() if initial else None,
                    type=self._type(None, _text(spec)) if spec is not None else None))
        return out

    def _handler(self, context: ParserRuleContext, ids: M.IdFactory) -> M.ExceptionHandler:
        names = [_text(n) for n in _descend(context, {"Exception_nameContext"})]
        handler = M.ExceptionHandler(id=ids.next("handler"), kind="ExceptionHandler",
                                     exceptions=names or ["OTHERS"], source_range=self._range(context))
        handler.body = self._statements(_child(context, "Seq_of_statementsContext") or context, ids)
        return handler

    # -- statements ------------------------------------------------------------------------------------------
    def _statements(self, context: ParserRuleContext, ids: M.IdFactory) -> list[M.Statement]:
        return [self._statement(c, ids) for c in _descend(context, set(STATEMENT_CONTEXTS),
                                                          stop={"Exception_handlerContext"})]

    def _statement(self, context: ParserRuleContext, ids: M.IdFactory) -> M.Statement:
        name = type(context).__name__
        text = _text(context)
        source = self._range(context)
        handler = {
            "Assignment_statementContext": self._assignment,
            "If_statementContext": self._if,
            "Case_statementContext": self._case,
            "Loop_statementContext": self._loop,
            "Forall_statementContext": self._forall,
            "Raise_statementContext": self._raise,
            "Return_statementContext": self._return,
            "Call_statementContext": self._call,
            "Data_manipulation_language_statementsContext": self._sql,
            "Sql_statementContext": self._sql_statement,
            "Cursor_manipulation_statementsContext": self._sql_statement,
            "Transaction_control_statementsContext": self._sql_statement,
            "Execute_immediateContext": self._dynamic,
        }.get(name)
        if handler is not None:
            return handler(context, ids, text, source)
        if name in ("Commit_statementContext", "Rollback_statementContext", "Savepoint_statementContext"):
            return self._transaction(context, ids, text, source)
        if name in ("Open_statementContext", "Fetch_statementContext", "Close_statementContext"):
            return self._cursor(context, ids, text, source)
        if name in ("Exit_statementContext", "Continue_statementContext",
                    "Goto_statementContext", "Null_statementContext"):
            return self._control(context, ids, text, source)
        node = M.Unsupported(id=ids.next("stmt"), kind="Unsupported", source_range=source,
                             text=text, construct=name.removesuffix("Context"))
        node.add("WARN", "UNSUPPORTED_CONSTRUCT",
                 f"{node.construct} is not lowered yet; the routine cannot be AUTO while it is present")
        return node

    def _assignment(self, context, ids, text, source) -> M.Statement:
        target, _, expression = text.partition(":=")
        return M.Assignment(id=ids.next("stmt"), kind="Assignment", source_range=source,
                            target=target.strip(), expression=expression.strip().rstrip(";").strip())

    def _if(self, context, ids, text, source) -> M.Statement:
        node = M.If(id=ids.next("stmt"), kind="If", source_range=source)
        conditions = _children(context, "ConditionContext")
        sequences = _children(context, "Seq_of_statementsContext")
        for index, condition in enumerate(conditions):
            body = self._statements(sequences[index], ids) if index < len(sequences) else []
            node.branches.append(M.Branch(condition=_text(condition), body=body))
        for elsif in _children(context, "Elsif_partContext"):
            node.branches.append(M.Branch(
                condition=_text(_child(elsif, "ConditionContext")),
                body=self._statements(_child(elsif, "Seq_of_statementsContext") or elsif, ids)))
        else_part = _child(context, "Else_partContext")
        if else_part is not None:
            node.else_body = self._statements(else_part, ids)
        return node

    def _case(self, context, ids, text, source) -> M.Statement:
        node = M.Case(id=ids.next("stmt"), kind="Case", source_range=source)
        selector = _child(context, "ExpressionContext")
        node.selector = _text(selector) if selector is not None else None
        for when in _descend(context, {"Case_when_partContext", "Simple_case_when_partContext",
                                       "Searched_case_when_partContext"}):
            condition = _child(when, "ConditionContext") or _child(when, "ExpressionContext")
            node.branches.append(M.Branch(
                condition=_text(condition), body=self._statements(when, ids)))
        else_part = _child(context, "Case_else_partContext")
        if else_part is not None:
            node.else_body = self._statements(else_part, ids)
        return node

    def _loop(self, context, ids, text, source) -> M.Statement:
        cursor_param = _child(context, "Cursor_loop_paramContext")
        kind = "basic"
        condition = cursor = None
        if re.match(r"^\s*WHILE\b", text, re.IGNORECASE):
            kind = "while"
            condition = _text(_child(context, "ConditionContext"))
        elif cursor_param is not None:
            inner = _text(cursor_param)
            kind = "cursor-for" if re.search(r"\b(IN\s*\(|IN\s+[\w$#.]+\s*(\(|$))", inner) and \
                not re.search(r"\.\.", inner) else "for"
            cursor = inner
        return M.Loop(id=ids.next("stmt"), kind="Loop", source_range=source, loop_kind=kind,
                      label=_first(re.match(r"^\s*<<\s*([\w$#]+)\s*>>", text)), condition=condition,
                      cursor=cursor, body=self._statements(
                          _child(context, "Seq_of_statementsContext") or context, ids))

    def _forall(self, context, ids, text, source) -> M.Statement:
        node = M.Loop(id=ids.next("stmt"), kind="Loop", source_range=source, loop_kind="forall",
                      cursor=_text(_child(context, "Bounds_clauseContext")),
                      body=self._statements(context, ids))
        if re.search(r"\bSAVE\s+EXCEPTIONS\b", text, re.IGNORECASE):
            node.add("WARN", "FORALL_SAVE_EXCEPTIONS",
                     "SAVE EXCEPTIONS lets some items fail and the rest commit; the atomicity is a "
                     "business decision, not a translation")
        return node

    def _raise(self, context, ids, text, source) -> M.Statement:
        name_context = _child(context, "Exception_nameContext")
        return M.Raise(id=ids.next("stmt"), kind="Raise", source_range=source,
                       exception=_text(name_context) if name_context is not None else None)

    def _return(self, context, ids, text, source) -> M.Statement:
        expression = _child(context, "ExpressionContext" if _child(context, "ExpressionContext") else "")
        return M.Return(id=ids.next("stmt"), kind="Return", source_range=source,
                        expression=_text(expression).strip() or None if expression is not None else None)

    def _call(self, context, ids, text, source) -> M.Statement:
        application_error = RAISE_APPLICATION_ERROR.search(text)
        if application_error:
            return M.Raise(id=ids.next("stmt"), kind="Raise", source_range=source,
                           error_code=int(application_error.group(1)),
                           message=application_error.group(2).strip().rstrip(")").strip())
        callee = _text(_child(context, "Routine_nameContext") or context).strip().rstrip(";")
        arguments = [_text(a) for a in _descend(context, {"ArgumentContext"})]
        return M.Call(id=ids.next("stmt"), kind="Call", source_range=source,
                      callee=callee.split("(")[0].strip(), arguments=arguments)

    def _transaction(self, context, ids, text, source) -> M.Statement:
        name = type(context).__name__
        kind = {"Commit_statementContext": "Commit", "Rollback_statementContext": "Rollback",
                "Savepoint_statementContext": "Savepoint"}[name]
        savepoint = _text(_child(context, "Savepoint_nameContext")) or None
        node = M.TransactionStatement(id=ids.next("stmt"), kind=kind, source_range=source,
                                      savepoint=savepoint or None)
        node.add("WARN", "TRANSACTION_IN_ROUTINE",
                 f"{kind} inside a routine cannot be translated literally; the transaction boundary belongs "
                 "to the application service")
        return node

    def _cursor(self, context, ids, text, source) -> M.Statement:
        name = type(context).__name__
        kind = {"Open_statementContext": "OpenCursor", "Fetch_statementContext": "Fetch",
                "Close_statementContext": "CloseCursor"}[name]
        into = [_text(v) for v in _descend(context, {"Variable_nameContext"})]
        return M.CursorStatement(id=ids.next("stmt"), kind=kind, source_range=source,
                                 cursor=_text(_child(context, "Cursor_nameContext")),
                                 into_targets=into[1:] if kind == "Fetch" else [],
                                 arguments=[_text(a) for a in _descend(context, {"ArgumentContext"})])

    def _control(self, context, ids, text, source) -> M.Statement:
        kind = {"Exit_statementContext": "Exit", "Continue_statementContext": "Continue",
                "Goto_statementContext": "Goto", "Null_statementContext": "Null"}[type(context).__name__]
        condition = _child(context, "ConditionContext")
        node = M.ControlStatement(id=ids.next("stmt"), kind=kind, source_range=source,
                                  label=_text(_child(context, "Label_nameContext")) or None,
                                  condition=_text(condition) if condition is not None else None)
        if kind == "Goto":
            node.add("WARN", "GOTO", "GOTO has no structured equivalent; the control flow has to be rebuilt")
        return node

    # `Sql_statement`, `Cursor_manipulation_statements` and `Transaction_control_statements` are grouping rules,
    # not statements. Treating one as a leaf turns every COMMIT in the corpus into an Unsupported node -- which
    # is exactly what happened before this unwrapping existed.
    WRAPPERS = {"Sql_statementContext", "Cursor_manipulation_statementsContext",
                "Transaction_control_statementsContext"}
    WRAPPED = {"Data_manipulation_language_statementsContext", "Execute_immediateContext",
               "Open_statementContext", "Fetch_statementContext", "Close_statementContext",
               "Open_for_statementContext", "Commit_statementContext", "Rollback_statementContext",
               "Savepoint_statementContext", "Set_transaction_commandContext"}

    def _sql_statement(self, context, ids, text, source) -> M.Statement:
        inner = _descend(context, self.WRAPPED | self.WRAPPERS)
        if inner:
            return self._statement(inner[0], ids)
        return M.Unsupported(id=ids.next("stmt"), kind="Unsupported", source_range=source,
                             text=text, construct=type(context).__name__.removesuffix("Context"))

    def _sql(self, context, ids, text, source) -> M.Statement:
        kind = "UNKNOWN"
        for context_name, sql_kind in (("Select_statementContext", "SELECT"), ("Insert_statementContext", "INSERT"),
                                       ("Update_statementContext", "UPDATE"), ("Delete_statementContext", "DELETE"),
                                       ("Merge_statementContext", "MERGE")):
            if _descend(context, {context_name}):
                kind = sql_kind
                break
        node = M.SqlOperation(id=ids.next("stmt"), kind="SqlOperation", source_range=source,
                              sql_kind=kind, original_sql=text.strip().rstrip(";").strip())
        locking = re.search(r"\bFOR\s+UPDATE\b(\s+(NOWAIT|SKIP\s+LOCKED|WAIT\s+\d+))?", text, re.IGNORECASE)
        if locking:
            node.locking_mode = " ".join(locking.group(0).split()).upper()
            node.add("WARN", "ROW_LOCK",
                     f"{node.locking_mode} is row locking; the target has to provide the same guarantee another way")
        if re.search(r"\bBULK\s+COLLECT\b", text, re.IGNORECASE):
            node.cardinality = "MANY"
            node.add("WARN", "BULK_COLLECT", "BULK COLLECT needs a row limit and a memory bound")
        return node

    def _dynamic(self, context, ids, text, source) -> M.Statement:
        expression = _text(_child(context, "ExpressionContext")).strip()
        constant = None
        literal = re.fullmatch(r"'((?:[^']|'')*)'", expression)
        if literal:
            constant = literal.group(1).replace("''", "'")
        node = M.DynamicSql(id=ids.next("stmt"), kind="DynamicSql", source_range=source,
                            expression=expression, constant_sql=constant,
                            into_targets=[_text(v) for v in _descend(context, {"Into_clauseContext"})])
        node.add("INFO" if constant else "WARN", "DYNAMIC_SQL",
                 "the statement folds to a constant and can be analysed" if constant
                 else "the statement is built at run time; only a finite set of variants can be converted")
        return node

    # -- effects ---------------------------------------------------------------------------------------------
    def _effects(self, routine: M.Routine, text: str) -> None:
        for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
            if statement.kind == "Commit":
                routine.transaction_effects.commits += 1
            elif statement.kind == "Rollback":
                routine.transaction_effects.rollbacks += 1
            elif statement.kind == "Savepoint":
                routine.transaction_effects.savepoints += 1
            elif statement.kind == "DynamicSql":
                routine.external_effects.dynamic_sql = True
        if re.search(r"\bPRAGMA\s+AUTONOMOUS_TRANSACTION\b", text, re.IGNORECASE):
            routine.transaction_effects.autonomous = True
        routine.external_effects.db_links = sorted({m.lower() for m in DB_LINK.findall(text)})
        routine.external_effects.packages = sorted({m.upper() for m in EXTERNAL_PACKAGES.findall(text)})

    # -- helpers ---------------------------------------------------------------------------------------------
    def _type(self, scope: str | None, written: str) -> M.TypeRef:
        written = written.strip()
        if self.symbols is not None and scope is not None:
            symbol = self.symbols.resolve(scope, "<return>")
            if symbol is not None and symbol.type is not None and symbol.type.oracle == written:
                return symbol.type
        return M.TypeRef(oracle=written, resolved=written if "%" not in written else None,
                         origin="declared" if "%" not in written else "unresolved")

    def _range(self, context: ParserRuleContext) -> SourceRange:
        start = self.unit.origin(context.start.line, context.start.column + 1)
        stop = self.unit.origin(context.stop.line, context.stop.column + 1) if context.stop else start
        return SourceRange(start.file, start.line, stop.line, start.column, stop.column)


def _walk(statements: list[M.Statement]) -> list[M.Statement]:
    """Every statement, including the ones nested inside branches and loops."""
    out: list[M.Statement] = []
    for statement in statements:
        out.append(statement)
        for branch in getattr(statement, "branches", []) or []:
            out.extend(_walk(branch.body))
        out.extend(_walk(getattr(statement, "else_body", []) or []))
        out.extend(_walk(getattr(statement, "body", []) or []))
    return out


def _first(match) -> str | None:
    if match is None:
        return None
    return match.group(1) if match.groups() else match.group(0)


def _write_golden(src: Path, out: Path) -> int:
    """Regenerate the golden IR the tests compare against: `python -m plsql.lower --write-golden`."""
    from .symbols import OracleSchema as _Schema

    schema = _Schema.from_ddl(src / "schema.sql")
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for body in sorted(p for p in src.rglob("*") if p.suffix in {".pkb", ".prc", ".trg", ".pks"}):
        if body.suffix == ".pks" and body.with_suffix(".pkb").exists():
            continue  # a specification is folded into its body's module
        modules, symbols = lower_source(body, schema)
        program = M.Program(id=body.stem, kind="Program", schema_snapshot=schema.snapshot,
                            modules=modules, unresolved=symbols.unresolved)
        from .ir import serde

        serde.validate(program)
        (out / f"{body.stem}.ir.json").write_text(serde.dumps(program), encoding="utf-8")
        written += 1
    return written


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-golden", action="store_true", help="regenerate fixtures/plsql/golden-ir")
    parser.add_argument("--src", default="fixtures/plsql/src")
    parser.add_argument("--out", default="fixtures/plsql/golden-ir")
    args = parser.parse_args()
    if args.write_golden:
        print(f"wrote {_write_golden(Path(args.src), Path(args.out))} golden IR files to {args.out}/")
    else:
        parser.print_help()
