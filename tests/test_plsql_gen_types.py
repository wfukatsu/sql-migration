"""P2-5: types, DTOs and exceptions.

The design document (§5.3) keeps `OracleType` and `TargetType` apart so that an unresolved precision stays
visible instead of becoming a `long` that happens to fit today's data. Most of these tests defend that line, and
the rest defend two contracts the generated code has to keep: column mapping by name, and Oracle's error codes.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest
import sqlglot
from sqlglot import exp

from plsql.gen_java import dto, exception
from plsql.gen_java.types import java_class_name, java_name, java_type, record_columns
from plsql.ir import model as M
from plsql.report import analyse as build_analysis
from scalardb_migrate.schema import SchemaRegistry

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
PACKAGE = "com.example.generated.domain"


@pytest.fixture(scope="module")
def program():
    return build_analysis(SRC, SRC / "schema.sql").program


# --- the NUMBER rule does the most work ---------------------------------------------------------------

@pytest.mark.parametrize("oracle,java,storage", [
    ("NUMBER(9)", "Integer", "INT"),
    ("NUMBER(18)", "Long", "BIGINT"),
    ("NUMBER(19)", "BigDecimal", "BIGINT"),
    ("NUMBER(10)", "Long", "BIGINT"),
    ("NUMBER(14,2)", "BigDecimal", "BIGINT"),
    ("NUMBER", "BigDecimal", "TEXT"),
    ("NUMBER(*)", "BigDecimal", "TEXT"),
])
def test_number_is_mapped_by_precision_and_scale(oracle: str, java: str, storage: str):
    mapped = java_type(oracle)
    assert (mapped.name, mapped.storage) == (java, storage)


def test_a_number_without_precision_is_never_narrowed():
    """§5.3: an unresolved precision stays visible rather than becoming a long that fits today."""
    mapped = java_type("NUMBER")
    assert mapped.name == "BigDecimal"
    assert "precision" in mapped.note


def test_a_scaled_number_says_how_it_is_stored():
    mapped = java_type("NUMBER(14,2)")
    assert mapped.scale == 2
    assert mapped.is_scaled
    assert "half-up" in mapped.note, "the reason P0-3 rejected DOUBLE belongs with the decision"


@pytest.mark.parametrize("oracle,java", [
    ("VARCHAR2(20)", "String"),
    ("DATE", "LocalDateTime"),
    ("TIMESTAMP(6)", "LocalDateTime"),
    ("TIMESTAMP(6) WITH TIME ZONE", "OffsetDateTime"),
    ("RAW(16)", "byte[]"),
    ("BOOLEAN", "Boolean"),
    ("PLS_INTEGER", "Integer"),
    ("CLOB", "String"),
])
def test_the_rest_of_the_table(oracle: str, java: str):
    assert java_type(oracle).name == java


def test_oracle_date_is_not_local_date():
    """Oracle DATE carries a time of day; LocalDate would drop it silently."""
    assert java_type("DATE").name == "LocalDateTime"
    assert "time of day" in java_type("DATE").note


def test_a_plsql_boolean_is_the_boxed_type():
    assert java_type("BOOLEAN").name == "Boolean", "a PL/SQL BOOLEAN can be NULL"


def test_an_unknown_type_is_not_guessed():
    mapped = java_type("SOME_OBJECT_TYPE")
    assert mapped.name == "Object"
    assert "no mapping" in mapped.note
    assert java_type(None).name == "Object"


def test_imports_follow_the_type():
    assert java_type("NUMBER").imports == {"java.math.BigDecimal"}
    assert java_type("TIMESTAMP(6) WITH TIME ZONE").imports == {"java.time.OffsetDateTime"}
    assert java_type("VARCHAR2(10)").imports == set()


# --- the generator and the deployed schema must agree ---------------------------------------------------

def test_the_storage_choice_matches_the_schema_that_was_loaded():
    """A generator that disagrees with the schema writes values the table cannot hold.

    This check found a real defect: `NUMBER(10)` had been hand-written as `INT` in the ScalarDB schema, and
    9,999,999,999 does not fit a 32-bit integer.
    """
    registry = SchemaRegistry.from_schema_loader_json(str(FIXTURES / "scalardb-schema.json"))
    mismatches = []
    for statement in sqlglot.parse((SRC / "schema.sql").read_text(encoding="utf-8"), dialect="oracle"):
        if not isinstance(statement, exp.Create) or statement.kind != "TABLE":
            continue
        meta = registry.get(statement.find(exp.Table).name)
        for column in statement.find_all(exp.ColumnDef):
            oracle = column.args["kind"].sql(dialect="oracle")
            actual = meta.columns.get(column.name.lower())
            generated = java_type(oracle).storage
            if actual != generated:
                mismatches.append((meta.name, column.name, oracle, actual, generated))
    assert mismatches == [], mismatches


# --- names ---------------------------------------------------------------------------------------------

def test_plsql_names_become_java_names():
    assert java_name("v_order_id") == "vOrderId"
    assert java_name("P_CUSTOMER_ID") == "pCustomerId"
    assert java_class_name("pkg_order_status") == "PkgOrderStatus"
    assert java_name("") == "value"


# --- %ROWTYPE ---------------------------------------------------------------------------------------------

def test_a_rowtype_is_split_into_columns():
    columns = record_columns("RECORD(customer_id NUMBER(19), name VARCHAR2(100), tier VARCHAR2(10))")
    assert columns == [("customer_id", "NUMBER(19)"), ("name", "VARCHAR2(100)"), ("tier", "VARCHAR2(10)")]


def test_a_rowtype_with_a_parameterised_type_is_not_split_inside_the_parentheses():
    columns = record_columns("RECORD(amount NUMBER(14,2), note VARCHAR2(400))")
    assert columns == [("amount", "NUMBER(14,2)"), ("note", "VARCHAR2(400)")]


def test_a_row_record_maps_columns_by_name(program):
    records = [d for m in program.modules for d in dto.dtos_for(m, PACKAGE) if d.kind == "row"]
    assert records
    rendered = records[0].file.render()
    assert "public record" in rendered
    assert "Components follow the column names" in rendered


def test_a_row_record_carries_the_imports_its_columns_need(program):
    for record in [d for m in program.modules for d in dto.dtos_for(m, PACKAGE) if d.kind == "row"]:
        rendered = record.file.render()
        for java in ("BigDecimal", "LocalDateTime", "OffsetDateTime"):
            if re.search(rf"\b{java}\b", rendered.split("public record")[1]):
                assert f"import java." in rendered, f"{record.file.name} uses {java} without importing it"


# --- OUT parameters become a result ---------------------------------------------------------------------------

def test_a_routine_with_out_parameters_gets_a_result_record(program):
    results = {d.file.name for m in program.modules for d in dto.dtos_for(m, PACKAGE) if d.kind == "result"}
    assert "StatusForCustomerResult" in results


def test_a_routine_without_out_parameters_gets_none():
    routine = M.Routine(id="r", kind="Routine", name="r",
                        parameters=[M.Parameter(id="p", kind="Parameter", name="p_id", direction="IN")])
    assert dto.result_record(routine, PACKAGE) is None


def test_the_result_record_says_why_it_exists():
    routine = M.Routine(id="r", kind="Routine", name="do_it", parameters=[
        M.Parameter(id="p", kind="Parameter", name="p_status", direction="OUT",
                    type=M.TypeRef("VARCHAR2(20)", "VARCHAR2(20)"))])
    rendered = dto.result_record(routine, PACKAGE).file.render()
    assert "half-updated" in rendered, "the reason not to write through arguments belongs in the code"


# --- error codes ------------------------------------------------------------------------------------------------

def test_business_error_codes_are_preserved(program):
    registry = exception.collect(program)
    codes = {e.code for e in registry.codes.values()}
    assert {-20010, -20020, -20030, -20040, -20060, -20070} <= codes


def test_the_registry_records_who_raises_each_code(program):
    registry = exception.collect(program)
    entry = registry.codes[-20020]
    assert "pkg_order_status.status_of" in entry.routines


def test_predefined_exceptions_keep_their_oracle_numbers(program):
    registry = exception.collect(program)
    assert registry.codes[100].class_name == "NoDataFoundException"
    assert registry.codes[-1422].class_name == "TooManyRowsException"


def test_two_meanings_on_one_code_is_a_conflict_not_a_silent_choice():
    registry = exception.Registry()
    registry.add(-20001, "AException", "RAISE_APPLICATION_ERROR(-20001)", "a", "r1")
    registry.add(-20001, "BException", "RAISE_APPLICATION_ERROR(-20001)", "b", "r2")
    assert registry.conflicts == [(-20001, "AException", "BException")]


def test_the_corpus_has_no_code_conflicts(program):
    assert exception.collect(program).conflicts == []


def test_a_user_exception_gets_a_stable_code():
    """`hash()` is salted per process; a code that changes every run would make any golden comparison flake."""
    first = subprocess.run(
        [sys.executable, "-c",
         "from plsql.gen_java.exception import _user_code; print(_user_code('E_X'))"],
        capture_output=True, text=True, cwd=str(FIXTURES.parent.parent))
    second = subprocess.run(
        [sys.executable, "-c",
         "from plsql.gen_java.exception import _user_code; print(_user_code('E_X'))"],
        capture_output=True, text=True, cwd=str(FIXTURES.parent.parent))
    assert first.stdout.strip() == second.stdout.strip() != ""


def test_generated_exceptions_extend_one_base(program):
    files, _ = exception.generate(program, PACKAGE)
    base = [f for f in files if f.name == "MigratedException"]
    assert base
    for file in files:
        if file.name == "MigratedException":
            continue
        assert "extends MigratedException" in file.render()
        assert "public static final int CODE" in file.render()


def test_the_registry_serialises_for_the_report(program):
    data = exception.collect(program).to_dict()
    json.dumps(data)
    assert data["codes"] and "raisedBy" in data["codes"][0]


# --- the emitted file ---------------------------------------------------------------------------------------------

def test_every_generated_file_says_not_to_edit_it(program):
    files, _ = exception.generate(program, PACKAGE)
    for file in files:
        rendered = file.render()
        assert rendered.startswith("// Generated by")
        assert "Do not edit" in rendered
        assert any(l.startswith("package ") for l in rendered.splitlines()[:6])


def test_the_path_follows_the_package(program):
    files, _ = exception.generate(program, PACKAGE)
    assert files[0].path == "com/example/generated/domain/MigratedException.java"
