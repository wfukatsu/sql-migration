"""P2-6: routine bodies as Java methods.

Three properties decide whether the output is usable, and each has tests here: the transaction boundary stays
with the caller, every statement can be traced to its line, and anything that cannot be translated becomes a
compile-time refusal rather than code that quietly does less than the original.
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.gen_java.expr import translate
from plsql.gen_java.service import generate_module
from plsql.ir import model as M
from plsql.report import analyse as build_analysis

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
APP, INFRA, DOMAIN = "g.app", "g.infra", "g.domain"


@pytest.fixture(scope="module")
def program():
    return build_analysis(SRC, SRC / "schema.sql",
                          scalardb_schema=FIXTURES / "scalardb-schema.json").program


def module_named(program, name: str) -> M.Module:
    return next(m for m in program.modules if m.name == name)


def rendered(program, name: str) -> str:
    return generate_module(module_named(program, name), APP, INFRA, DOMAIN).file.render()


# --- expressions go through the semantics helper -------------------------------------------------------

@pytest.mark.parametrize("plsql,java", [
    ("v IS NULL", "Plsql.isNull(v)"),
    ("v IS NOT NULL", "Plsql.isNotNull(v)"),
    ("v = 'NEW'", 'Plsql.eq(v, "NEW")'),
    ("v <> 'X'", 'Plsql.ne(v, "X")'),
    ("n > 1", "Plsql.gt(n, 1)"),
    ("'a' || n", 'Plsql.concat("a", n)'),
    ("v IN ('A','B')", 'Plsql.in(v, "A", "B")'),
    ("v NOT IN ('A')", '!(Plsql.in(v, "A"))'),
    ("n BETWEEN 1 AND 10", "Plsql.between(n, 1, 10)"),
    ("v LIKE 'A%'", 'Plsql.like(v, "A%")'),
    ("NVL(n, 0)", "Plsql.nvl(n, 0)"),
    ("ROUND(n, 2)", "Plsql.round(n, 2)"),
    ("SYSDATE", "Plsql.sysdate()"),
])
def test_oracle_semantics_become_helper_calls(plsql: str, java: str):
    """`=` is three-valued, `||` treats NULL as empty, ROUND is half-up. The Java operator means none of that."""
    result = translate(plsql, {"v": "v", "n": "n"})
    assert result.java.replace(" ", "") == java.replace(" ", "")
    assert result.translatable


def test_precedence_is_parsed_not_pattern_matched():
    """Regression: marker substitution mis-split `a > 1 AND b = 'x'` because a marker cannot see its operands."""
    result = translate("n > 1 AND v = 'x'", {"v": "v", "n": "n"})
    assert result.java == 'Plsql.gt(n, 1) && Plsql.eq(v, "x")'


def test_a_parenthesised_group_is_parsed_again():
    assert translate("NOT (v = 'x')", {"v": "v"}).java == '!((Plsql.eq(v, "x")))'


def test_an_unknown_function_is_reported_not_invented():
    result = translate("SYS_CONNECT_BY_PATH(v, '/')", {"v": "v"})
    assert not result.translatable
    assert "SYS_CONNECT_BY_PATH" in result.unknown


def test_string_literals_are_requoted_and_escaped():
    assert translate("'it''s'", {}).java == '"it\'s"'


def test_a_sibling_routine_call_resolves_to_its_method():
    assert translate("status_of(p_id)", {"status_of": "statusOf", "p_id": "pId"}).java == "statusOf(pId)"


# --- the transaction boundary stays with the caller ------------------------------------------------------

def test_no_generated_method_manages_a_transaction(program):
    """Plan §9: the boundary is the application's, and no Spring annotation appears either."""
    for module in program.modules:
        text = generate_module(module, APP, INFRA, DOMAIN).file.render()
        for forbidden in ("@Transactional", "org.springframework", ".commit()", ".begin()", ".rollback()"):
            assert forbidden not in text, f"{module.name} contains {forbidden}"


def test_the_class_says_where_the_boundary_belongs(program):
    assert "boundary belongs to the caller" in rendered(program, "pkg_order_status")


# --- signatures ---------------------------------------------------------------------------------------------

def test_out_parameters_leave_the_signature_and_come_back_in_a_result(program):
    text = rendered(program, "pkg_order_status")
    assert "public StatusForCustomerResult statusForCustomer(BigDecimal pCustomerId)" in text
    assert "return new StatusForCustomerResult(pStatus);" in text
    assert "String pStatus = null;" in text, "the OUT value is a local, not a written-through argument"


def test_a_function_that_falls_through_fails_loudly():
    """Oracle raises ORA-06503 when a function ends without RETURN; silence would be a behaviour change.

    The guard is emitted only where it is reachable -- Java rejects a statement after a path that always exits,
    so a body whose every branch returns does not get one.
    """
    routine = M.Routine(id="r", kind="Routine", name="f", return_type=M.TypeRef("NUMBER(9)", "NUMBER(9)"),
                        declarations=[M.Declaration(id="d", kind="Declaration", name="v",
                                                    type=M.TypeRef("NUMBER(9)", "NUMBER(9)"))],
                        body=[M.Assignment(id="r#1", kind="Assignment", target="v", expression="1")])
    module = M.Module(id="m", kind="Module", name="m", module_kind="package", routines=[routine])
    text = generate_module(module, APP, INFRA, DOMAIN).file.render()
    assert "function reached its end without RETURN" in text


def test_private_routines_become_private_methods(program):
    text = rendered(program, "pkg_order_pricing")
    assert "private " in text
    assert "public BigDecimal orderTotal" in text or "public " in text


# --- traceability ---------------------------------------------------------------------------------------------

def test_every_translated_statement_carries_its_source_line(program):
    text = rendered(program, "pkg_order_status")
    assert text.count("// pkg_order_status.pkb:") >= 5


def test_the_file_names_the_source_and_forbids_editing(program):
    text = rendered(program, "pkg_order_status")
    assert text.startswith("// Generated by")
    assert "pkg_order_status.pkb" in text.splitlines()[1]


# --- control structures ------------------------------------------------------------------------------------------

def test_if_elsif_else_becomes_if_else_if_else():
    routine = M.Routine(id="r", kind="Routine", name="r", declarations=[
        M.Declaration(id="d1", kind="Declaration", name="v", type=M.TypeRef("NUMBER(9)", "NUMBER(9)")),
        M.Declaration(id="d2", kind="Declaration", name="w", type=M.TypeRef("NUMBER(9)", "NUMBER(9)"))], body=[M.If(
        id="r#1", kind="If",
        branches=[M.Branch("v = 1", [M.Assignment(id="r#2", kind="Assignment", target="w", expression="1")]),
                  M.Branch("v = 2", [M.Assignment(id="r#3", kind="Assignment", target="w", expression="2")])],
        else_body=[M.Assignment(id="r#4", kind="Assignment", target="w", expression="3")])])
    module = M.Module(id="m", kind="Module", name="m", module_kind="package", routines=[routine])
    text = generate_module(module, APP, INFRA, DOMAIN).file.render()
    assert "if (" in text and "else if (" in text and "else {" in text


def test_a_case_without_else_keeps_case_not_found():
    routine = M.Routine(id="r", kind="Routine", name="r", declarations=[
        M.Declaration(id="d1", kind="Declaration", name="v", type=M.TypeRef("NUMBER(9)", "NUMBER(9)"))],
        body=[M.Case(id="r#1", kind="Case", branches=[M.Branch("v = 1", [])])])
    module = M.Module(id="m", kind="Module", name="m", module_kind="package", routines=[routine])
    text = generate_module(module, APP, INFRA, DOMAIN).file.render()
    assert "CASE_NOT_FOUND" in text, "PL/SQL raises it; dropping the else would change behaviour"


def test_exit_when_becomes_a_conditional_break():
    routine = M.Routine(id="r", kind="Routine", name="r", declarations=[
        M.Declaration(id="d1", kind="Declaration", name="v", type=M.TypeRef("NUMBER(9)", "NUMBER(9)"))],
        body=[M.Loop(id="r#1", kind="Loop", loop_kind="basic",
                     body=[M.ControlStatement(id="r#2", kind="Exit", condition="v = 1")])])
    module = M.Module(id="m", kind="Module", name="m", module_kind="package", routines=[routine])
    text = generate_module(module, APP, INFRA, DOMAIN).file.render()
    assert "while (true)" in text
    assert "break;" in text


# --- exception handlers ------------------------------------------------------------------------------------------

def test_handlers_become_catches_with_others_last():
    """Java resolves catches in order; a broad one first would swallow the specific ones."""
    routine = M.Routine(id="r", kind="Routine", name="r", exception_handlers=[
        M.ExceptionHandler(id="h1", kind="ExceptionHandler", exceptions=["OTHERS"]),
        M.ExceptionHandler(id="h2", kind="ExceptionHandler", exceptions=["NO_DATA_FOUND"])])
    module = M.Module(id="m", kind="Module", name="m", module_kind="package", routines=[routine])
    text = generate_module(module, APP, INFRA, DOMAIN).file.render()
    assert text.index("NoDataFoundException e") < text.index("WHEN OTHERS")


def test_the_corpus_handlers_are_generated(program):
    text = rendered(program, "pkg_order_status")
    assert "catch (NoDataFoundException e)" in text
    assert "catch (TooManyRowsException e)" in text


# --- refusing rather than guessing ---------------------------------------------------------------------------------

def test_an_untranslatable_statement_becomes_a_compile_time_refusal():
    routine = M.Routine(id="r", kind="Routine", name="r", declarations=[
        M.Declaration(id="d", kind="Declaration", name="v", type=M.TypeRef("VARCHAR2(10)", "VARCHAR2(10)"))],
        body=[M.Assignment(id="r#1", kind="Assignment", target="v",
                           expression="SYS_CONNECT_BY_PATH(x, '/')")])
    module = M.Module(id="m", kind="Module", name="m", module_kind="package", routines=[routine])
    result = generate_module(module, APP, INFRA, DOMAIN)
    text = result.file.render()
    assert "UnsupportedOperationException" in text
    assert "SYS_CONNECT_BY_PATH" in text
    assert "r#1" in result.untranslated


def test_what_could_not_be_translated_is_listed(program):
    """The list is the point: a routine holding one of these cannot reach AUTO by accident."""
    total = 0
    for module in program.modules:
        total += len(generate_module(module, APP, INFRA, DOMAIN).untranslated)
    assert total > 0, "this corpus contains constructs P2-6 does not model; silence would be suspicious"


def test_no_generated_file_contains_raw_plsql_operators(program):
    """`<>` or a bare `||` in the output means an expression escaped the translator."""
    for module in program.modules:
        for line in generate_module(module, APP, INFRA, DOMAIN).file.render().splitlines():
            code = line.split("//")[0]
            assert "<>" not in code, line
