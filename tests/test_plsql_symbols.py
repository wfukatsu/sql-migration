"""P1-3: the symbol table and type resolution.

`symbolResolution` and `typeResolution` are factors of the confidence (docs/design/plsql-kpi.md §3), and a factor of 0
keeps a routine out of AUTO. So the property that matters is not "resolves a lot" but "never claims to have
resolved something it did not": these tests push unresolvable types through and check they come back marked.
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.frontend import parse_file, parse_text
from plsql.symbols import OracleSchema, Scope, Symbol, build, public_routines

SRC = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql" / "src"


@pytest.fixture(scope="module")
def schema() -> OracleSchema:
    return OracleSchema.from_ddl(SRC / "schema.sql")


def table_for(name: str, schema: OracleSchema):
    body = SRC / name
    spec = body.with_suffix(".pks")
    public = public_routines(parse_file(spec)) if spec.exists() else set()
    return build(parse_file(body), schema, public)


# --- the Oracle schema snapshot -----------------------------------------------------------------------

def test_the_ddl_snapshot_has_an_id_that_changes_with_the_ddl(tmp_path: pathlib.Path, schema: OracleSchema):
    assert schema.snapshot and schema.snapshot.startswith("schema.sql@")
    other = tmp_path / "schema.sql"
    other.write_text("CREATE TABLE t (id NUMBER(9));\n", encoding="utf-8")
    assert OracleSchema.from_ddl(other).snapshot != schema.snapshot


def test_columns_keep_their_oracle_types(schema: OracleSchema):
    """Not the ScalarDB types: %TYPE has to resolve to what Oracle says, or the semantics shift early."""
    assert schema.column("orders", "status") == "VARCHAR2(20)"
    assert schema.column("orders", "total_amount") == "NUMBER(14, 2)"
    assert schema.column("customers", "registered_on") == "DATE"


def test_an_unknown_table_or_column_is_simply_absent(schema: OracleSchema):
    assert schema.column("orders", "no_such_column") is None
    assert schema.columns("no_such_table") is None


# --- declarations -------------------------------------------------------------------------------------

def test_parameters_are_recorded_with_their_direction(schema: OracleSchema):
    table = table_for("pkg_order_status.pkb", schema)
    out = table.resolve("pkg_order_status.status_for_customer", "p_status")
    assert out.kind == "parameter"
    assert out.direction == "OUT"
    assert table.resolve("pkg_order_status.status_for_customer", "p_customer_id").direction == "IN"


def test_local_variables_are_scoped_to_their_routine(schema: OracleSchema):
    table = table_for("pkg_order_status.pkb", schema)
    assert table.resolve("pkg_order_status.status_of", "v_status") is not None
    assert table.resolve("pkg_order_status.status_for_customer", "v_status") is None


def test_a_routine_sees_the_package_scope_above_it(schema: OracleSchema):
    table = table_for("pkg_bulk_load.pkb", schema)
    scope = table.scopes["pkg_bulk_load.restock"]
    assert scope.parent is not None and scope.parent.id == "pkg_bulk_load"


def test_visibility_comes_from_the_package_specification(schema: OracleSchema):
    """`order_total` is declared in the spec; `tier_discount` only exists in the body."""
    table = table_for("pkg_order_pricing.pkb", schema)
    package = table.scopes["pkg_order_pricing"]
    assert package.symbols["order_total"].visibility == "public"
    assert package.symbols["tier_discount"].visibility == "private"


def test_source_ranges_point_back_at_the_original_file(schema: OracleSchema):
    table = table_for("pkg_order_status.pkb", schema)
    symbol = table.resolve("pkg_order_status.status_of", "v_status")
    assert symbol.source_range.file == "pkg_order_status.pkb"
    assert symbol.source_range.start_line > 1


# --- %TYPE / %ROWTYPE -----------------------------------------------------------------------------------

def test_a_column_type_resolves_and_records_the_snapshot(schema: OracleSchema):
    symbol = table_for("pkg_order_status.pkb", schema).resolve("pkg_order_status.status_of", "v_status")
    assert symbol.type.oracle == "orders.status%TYPE"
    assert symbol.type.resolved == "VARCHAR2(20)"
    assert symbol.type.origin == "column-type"
    assert symbol.type.schema_snapshot == schema.snapshot


def test_a_rowtype_resolves_to_the_shape_of_the_table(schema: OracleSchema):
    symbol = table_for("pkg_customer_view.pkb", schema).resolve("pkg_customer_view.load", "v_row")
    assert symbol.type.origin == "rowtype"
    assert symbol.type.resolved.startswith("RECORD(")
    assert "tier VARCHAR2(10)" in symbol.type.resolved


def test_a_plain_type_is_marked_as_declared(schema: OracleSchema):
    symbol = table_for("prc_nightly_close.prc", schema).resolve("prc_nightly_close", "v_processed")
    assert symbol.type.origin == "declared"
    assert symbol.type.resolved == "NUMBER"


@pytest.mark.parametrize("declaration,reason", [
    ("v orders.no_such_column%TYPE;", "no column"),
    ("v no_such_table%ROWTYPE;", "no table"),
    ("v v_undeclared%TYPE;", "not a declared variable"),
])
def test_an_unresolvable_type_is_reported_not_guessed(schema: OracleSchema, declaration: str, reason: str):
    parsed = parse_text(f"CREATE OR REPLACE PROCEDURE p IS\n  {declaration}\nBEGIN NULL; END;\n/\n", "p.prc")
    table = build(parsed, schema)
    assert len(table.unresolved) == 1
    assert table.unresolved[0].code == "UNRESOLVED_TYPE"
    assert reason in table.unresolved[0].message
    assert not table.resolve("p", "v").type.is_resolved()


def test_without_a_schema_every_attribute_type_is_unresolved():
    """No DDL snapshot means no resolution. Guessing would be worse than saying so."""
    parsed = parse_text("CREATE OR REPLACE PROCEDURE p IS\n  v orders.status%TYPE;\nBEGIN NULL; END;\n/\n", "p.prc")
    table = build(parsed, None)
    assert table.unresolved
    assert table.resolution_rate() == 0.0


def test_a_variable_referencing_another_variable_resolves_through_it(schema: OracleSchema):
    parsed = parse_text(
        "CREATE OR REPLACE PROCEDURE p IS\n  a orders.status%TYPE;\n  b a%TYPE;\nBEGIN NULL; END;\n/\n", "p.prc")
    table = build(parsed, schema)
    assert table.resolve("p", "b").type.resolved == "VARCHAR2(20)"
    assert table.resolve("p", "b").type.origin == "inferred"


# --- overloads -------------------------------------------------------------------------------------------

def test_routines_with_the_same_name_are_collected_as_an_overload_set(schema: OracleSchema):
    parsed = parse_text(
        "CREATE OR REPLACE PACKAGE BODY pkg AS\n"
        "  PROCEDURE do_it(p IN NUMBER) IS BEGIN NULL; END;\n"
        "  PROCEDURE do_it(p IN VARCHAR2) IS BEGIN NULL; END;\n"
        "END pkg;\n/\n", "pkg.pkb")
    table = build(parsed, schema)
    assert len(table.overloads["pkg.do_it"]) == 2
    assert {s.signature for s in table.overloads["pkg.do_it"]} == {"IN NUMBER", "IN VARCHAR2"}


# --- KPI-2 ------------------------------------------------------------------------------------------------

def test_the_corpus_resolution_rate_meets_the_phase_1_target(schema: OracleSchema):
    typed = resolved = 0
    unresolved: list[str] = []
    for body in sorted(list(SRC.rglob("*.pkb")) + list(SRC.rglob("*.prc")) + list(SRC.rglob("*.trg"))):
        table = table_for(str(body.relative_to(SRC)), schema)
        symbols = [s for s in table.all_symbols() if s.type is not None]
        typed += len(symbols)
        resolved += sum(1 for s in symbols if s.type.is_resolved())
        unresolved += [i.message for i in table.unresolved]
    assert typed > 100, "the corpus should exercise a meaningful number of typed symbols"
    assert resolved / typed >= 0.95, f"{resolved}/{typed} resolved; unresolved: {unresolved[:5]}"


def test_the_corpus_exercises_both_attribute_forms(schema: OracleSchema):
    origins: set[str] = set()
    for body in sorted(list(SRC.rglob("*.pkb")) + list(SRC.rglob("*.prc"))):
        for symbol in table_for(str(body.relative_to(SRC)), schema).all_symbols():
            if symbol.type:
                origins.add(symbol.type.origin)
    assert {"declared", "column-type", "rowtype"} <= origins


# --- scope mechanics ----------------------------------------------------------------------------------------

def test_an_inner_declaration_shadows_an_outer_one():
    outer = Scope(id="pkg", kind="module")
    inner = Scope(id="pkg.p", kind="routine", parent=outer)
    outer.declare(Symbol(name="x", kind="variable", scope="pkg"))
    inner.declare(Symbol(name="x", kind="variable", scope="pkg.p"))
    assert inner.resolve("x").scope == "pkg.p"
    assert outer.resolve("x").scope == "pkg"


def test_resolution_is_case_insensitive():
    scope = Scope(id="p", kind="routine")
    scope.declare(Symbol(name="V_Status", kind="variable", scope="p"))
    assert scope.resolve("v_status") is not None
    assert scope.resolve("V_STATUS") is not None


def test_resolving_an_unknown_scope_returns_nothing(schema: OracleSchema):
    assert table_for("pkg_order_status.pkb", schema).resolve("no.such.scope", "x") is None


def test_parameters_keep_their_source_order(schema: OracleSchema):
    """The signature is an ordered thing; a tree walk that reorders matches would corrupt it silently."""
    parsed = parse_text(
        "CREATE OR REPLACE PACKAGE BODY pkg AS\n"
        "  PROCEDURE do_it(p_a IN NUMBER, p_b OUT VARCHAR2, p_c IN OUT DATE) IS BEGIN NULL; END;\n"
        "END pkg;\n/\n", "pkg.pkb")
    table = build(parsed, schema)
    assert table.overloads["pkg.do_it"][0].signature == "IN NUMBER,OUT VARCHAR2,IN OUT DATE"
