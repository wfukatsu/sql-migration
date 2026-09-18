"""P1-5: parse tree -> Migration IR.

The acceptance condition is a golden comparison over the corpus, and that is the last test here. The ones before
it pin the meanings that a golden file alone would not explain: which constructs become which nodes, what the
lowering records that the tree does not, and that nothing is dropped silently.
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.frontend import parse_text
from plsql.ir import model as M, serde
from plsql.lower import _walk, lower_file, lower_program, lower_source
from plsql.symbols import OracleSchema, build

SRC = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql" / "src"
GOLDEN = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql" / "golden-ir"
BODIES = sorted([p for p in SRC.rglob("*") if p.suffix in {".pkb", ".prc", ".trg", ".pks"}
                 and not (p.suffix == ".pks" and p.with_suffix(".pkb").exists())])


@pytest.fixture(scope="module")
def schema() -> OracleSchema:
    return OracleSchema.from_ddl(SRC / "schema.sql")


def lower_text(body: str, name: str = "p.prc", schema: OracleSchema | None = None) -> M.Routine:
    parsed = parse_text(body, name)
    modules = lower_file(parsed, build(parsed, schema), schema)
    return modules[0].routines[0]


def kinds(routine: M.Routine) -> list[str]:
    return [s.kind for s in _walk(routine.body)]


# --- statements become nodes --------------------------------------------------------------------------

def test_an_assignment_keeps_both_sides():
    routine = lower_text("CREATE OR REPLACE PROCEDURE p IS\n  v NUMBER;\nBEGIN\n  v := 1 + 2;\nEND;\n/\n")
    assignment = routine.body[0]
    assert assignment.kind == "Assignment"
    assert assignment.target == "v"
    assert assignment.expression == "1 + 2"


def test_an_if_keeps_its_branches_and_else():
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p IS\n  v NUMBER;\nBEGIN\n"
        "  IF v = 1 THEN v := 2;\n  ELSIF v = 2 THEN v := 3;\n  ELSE v := 4;\n  END IF;\nEND;\n/\n")
    node = routine.body[0]
    assert node.kind == "If"
    assert len(node.branches) == 2
    assert node.branches[0].condition == "v = 1"
    assert node.else_body and node.else_body[0].kind == "Assignment"


def test_raise_application_error_keeps_its_code_and_message():
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  RAISE_APPLICATION_ERROR(-20010, 'nope');\nEND;\n/\n")
    node = routine.body[0]
    assert node.kind == "Raise"
    assert node.error_code == -20010
    assert "nope" in node.message


def test_a_named_raise_keeps_the_exception_name():
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p IS\n  e EXCEPTION;\nBEGIN\n  RAISE e;\nEND;\n/\n")
    assert routine.body[0].kind == "Raise"
    assert routine.body[0].exception == "e"


def test_transaction_statements_are_nodes_not_omissions():
    """A COMMIT that disappeared during lowering could never be flagged by a rule."""
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  SAVEPOINT s;\n  COMMIT;\n  ROLLBACK TO s;\nEND;\n/\n")
    assert kinds(routine) == ["Savepoint", "Commit", "Rollback"]
    assert all(s.diagnostics for s in routine.body), "each one carries its own warning"


def test_cursor_statements_keep_the_cursor_they_touch():
    """A sequence that is none of the recognised shapes (#11) stays the OPEN / FETCH / CLOSE it was.

    The loop body does more than count, so it is not shape C, and the fetch is not a first row followed by a
    close, so it is not shape B. What the lowering must keep is which cursor each statement touches.
    """
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p IS\n  CURSOR c IS SELECT 1 FROM dual;\n  v NUMBER;\n  n NUMBER;\n"
        "BEGIN\n  OPEN c;\n  LOOP\n    FETCH c INTO v;\n    EXIT WHEN c%NOTFOUND;\n"
        "    n := n + v;\n  END LOOP;\n  CLOSE c;\nEND;\n/\n")
    cursor_statements = [s for s in _walk(routine.body) if s.kind in ("OpenCursor", "Fetch", "CloseCursor")]
    assert [s.kind for s in cursor_statements] == ["OpenCursor", "Fetch", "CloseCursor"]
    assert {s.cursor for s in cursor_statements} == {"c"}


def test_a_fetch_keeps_every_variable_it_assigns():
    """`FETCH c INTO a, b, c` assigns three variables. Dropping the first is what this used to do."""
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p IS\n  CURSOR c IS SELECT 1, 2, 3 FROM dual;\n"
        "  a NUMBER; b NUMBER; d NUMBER;\n"
        "BEGIN\n  OPEN c;\n  LOOP\n    FETCH c INTO a, b, d;\n    EXIT WHEN c%NOTFOUND;\n"
        "    a := a + b;\n  END LOOP;\n  CLOSE c;\nEND;\n/\n")
    fetch = next(s for s in _walk(routine.body) if s.kind == "Fetch")
    assert fetch.into_targets == ["a", "b", "d"]


def test_a_for_update_is_recorded_with_its_mode():
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p IS\n  v NUMBER;\nBEGIN\n"
        "  SELECT stock_qty INTO v FROM products WHERE product_id = 1 FOR UPDATE NOWAIT;\nEND;\n/\n")
    node = routine.body[0]
    assert node.kind == "SqlOperation"
    assert node.locking_mode == "FOR UPDATE NOWAIT"
    assert any(d.code == "ROW_LOCK" for d in node.diagnostics)


def test_a_constant_execute_immediate_is_folded():
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  EXECUTE IMMEDIATE 'DELETE FROM t';\nEND;\n/\n")
    node = routine.body[0]
    assert node.kind == "DynamicSql"
    assert node.constant_sql == "DELETE FROM t"
    assert [d.severity for d in node.diagnostics] == ["INFO"]


def test_a_built_execute_immediate_is_left_unevaluated_and_warned():
    routine = lower_text(
        "CREATE OR REPLACE PROCEDURE p(p_t IN VARCHAR2) IS\nBEGIN\n"
        "  EXECUTE IMMEDIATE 'DELETE FROM ' || p_t;\nEND;\n/\n")
    node = routine.body[0]
    assert node.constant_sql is None
    assert [d.severity for d in node.diagnostics] == ["WARN"]


def test_grouping_rules_are_unwrapped_rather_than_treated_as_leaves():
    """Regression: treating `Transaction_control_statements` as a leaf turned every COMMIT into Unsupported."""
    routine = lower_text("CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  COMMIT;\nEND;\n/\n")
    assert kinds(routine) == ["Commit"]


# --- nothing is dropped silently -------------------------------------------------------------------------

def test_an_unmodelled_construct_becomes_a_node_with_a_warning():
    node = M.Unsupported(id="x", kind="Unsupported", text="PIPE ROW(x);", construct="Pipe_row_statement")
    node.add("WARN", "UNSUPPORTED_CONSTRUCT", "not lowered yet")
    assert node.text and node.construct and node.diagnostics


def test_the_corpus_lowers_with_no_unsupported_nodes(schema: OracleSchema):
    leftovers: list[str] = []
    for body in BODIES:
        modules, _ = lower_source(body, schema)
        for module in modules:
            for routine in module.routines:
                for statement in _walk(routine.body):
                    if statement.kind == "Unsupported":
                        leftovers.append(f"{body.name}: {statement.construct}")
    assert leftovers == [], f"constructs still to model: {sorted(set(leftovers))}"


# --- what the IR records that the tree does not -------------------------------------------------------------

@pytest.mark.parametrize("name,commits,rollbacks,savepoints,autonomous", [
    ("prc_nightly_close.prc", 4, 2, 1, False),
    ("prc_audit_autonomous.prc", 1, 1, 0, True),
    ("holdout/prc_reprice_all.prc", 2, 0, 0, False),
])
def test_transaction_effects_are_summarised_per_routine(schema, name, commits, rollbacks, savepoints, autonomous):
    """P2-3 works from this summary; recomputing it later would mean walking the IR again for what was seen once."""
    routine = lower_source(SRC / name, schema)[0][0].routines[0]
    effects = routine.transaction_effects
    assert (effects.commits, effects.rollbacks, effects.savepoints, effects.autonomous) == \
        (commits, rollbacks, savepoints, autonomous)
    assert effects.controls_transaction


def test_a_db_link_is_recorded_as_an_external_effect(schema: OracleSchema):
    routine = lower_source(SRC / "prc_remote_sync.prc", schema)[0][0].routines[0]
    assert routine.external_effects.db_links == ["warehouse_link"]


def test_dynamic_sql_is_recorded_as_an_external_effect(schema: OracleSchema):
    module = lower_source(SRC / "pkg_dynamic_search.pkb", schema)[0][0]
    assert any(r.external_effects.dynamic_sql for r in module.routines)


def test_visibility_comes_from_the_specification(schema: OracleSchema):
    module = lower_source(SRC / "pkg_order_pricing.pkb", schema)[0][0]
    by_name = {r.name: r.visibility for r in module.routines}
    assert by_name["order_total"] == "public"
    assert by_name["tier_discount"] == "private"


def test_a_trigger_records_what_it_fires_on(schema: OracleSchema):
    module = lower_source(SRC / "trg_orders_audit.trg", schema)[0][0]
    assert module.module_kind == "trigger"
    assert module.trigger_table == "orders"
    assert module.trigger_timing.upper() == "AFTER"
    assert "UPDATE" in module.trigger_event.upper()


def test_every_node_can_be_traced_back_to_the_file(schema: OracleSchema):
    modules, _ = lower_source(SRC / "pkg_order_status.pkb", schema)
    for routine in modules[0].routines:
        assert routine.source_range.file == "pkg_order_status.pkb"
        for statement in _walk(routine.body):
            assert statement.source_range is not None
            assert statement.source_range.start_line >= routine.source_range.start_line


def test_statement_ids_are_scoped_to_their_routine(schema: OracleSchema):
    modules, _ = lower_source(SRC / "pkg_order_status.pkb", schema)
    for routine in modules[0].routines:
        for statement in _walk(routine.body):
            assert statement.id.startswith(routine.id + "#")


# --- the golden comparison (the acceptance condition) --------------------------------------------------------

@pytest.mark.parametrize("body", BODIES, ids=lambda p: p.name)
def test_the_lowered_ir_matches_the_golden(body: pathlib.Path, schema: OracleSchema):
    modules, symbols = lower_source(body, schema)
    program = M.Program(id=body.stem, kind="Program", schema_snapshot=schema.snapshot,
                        modules=modules, unresolved=symbols.unresolved)
    golden = (GOLDEN / f"{body.stem}.ir.json").read_text(encoding="utf-8")
    assert serde.dumps(program) == golden, f"regenerate: python -m plsql.lower --write-golden ({body.name})"


@pytest.mark.parametrize("body", BODIES, ids=lambda p: p.name)
def test_every_golden_validates_against_the_schema(body: pathlib.Path):
    program = serde.loads((GOLDEN / f"{body.stem}.ir.json").read_text(encoding="utf-8"))
    serde.validate(program)


def test_a_program_gathers_the_unresolved_issues(schema: OracleSchema):
    from plsql.frontend import parse_file

    program = lower_program([parse_file(SRC / "pkg_order_status.pkb")], schema=schema)
    assert program.schema_snapshot == schema.snapshot
    assert program.modules


def test_the_ir_carries_the_types_the_symbol_table_resolved(schema: OracleSchema):
    """The IR is what P2-5 reads to pick Java types.

    Regression: the lowering built its own TypeRef and never consulted the symbol table, so every `%TYPE` in the
    IR said `unresolved` while P1-3 had resolved it. KPI-2 looked fine because it is measured on the symbol table.
    """
    modules, _ = lower_source(SRC / "pkg_order_status.pkb", schema)
    declaration = modules[0].routines[0].declarations[0]
    assert declaration.type.oracle == "orders.status%TYPE"
    assert declaration.type.resolved == "VARCHAR2(20)"
    assert declaration.type.origin == "column-type"
    assert declaration.type.schema_snapshot == schema.snapshot


def test_no_corpus_declaration_is_left_unresolved_in_the_ir(schema: OracleSchema):
    unresolved: list[str] = []
    for body in BODIES:
        modules, _ = lower_source(body, schema)
        for module in modules:
            for routine in module.routines:
                for declaration in list(routine.declarations) + list(routine.parameters):
                    if declaration.type is not None and not declaration.type.is_resolved():
                        unresolved.append(f"{routine.id}.{declaration.name}: {declaration.type.oracle}")
    assert unresolved == [], unresolved
