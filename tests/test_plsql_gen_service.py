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
    ("v NOT IN ('A')", 'Plsql.notIn(v, "A")'),   # `!` は NULL を TRUE にしてしまう
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


@pytest.mark.parametrize("plsql,java", [
    ("-n", "Plsql.neg(n)"),
    ("+n", "n"),                       # Oracle の単項プラスは値を変えない
    ("- -n", "Plsql.neg(Plsql.neg(n))"),
    ("n - 1", "Plsql.sub(n, 1)"),      # 二項のままであること
    ("n - -1", "Plsql.sub(n, Plsql.neg(1))"),
])
def test_a_leading_sign_is_unary_not_a_binary_operator_missing_its_left(plsql: str, java: str):
    """`-n` は `Plsql.sub(, n)` になっていた——**コンパイルできない Java** である。

    corpus のどの文も単項マイナスを持って生成器まで来なかったので、ずっと隠れていた。#14 が
    `-v_qtys(i)` を `-r.qty` に書き換えて初めて表に出た（`--verify-compile` が捕まえた）。
    """
    result = translate(plsql, {"n": "n"})
    assert result.java.replace(" ", "") == java.replace(" ", "")
    assert result.translatable


def test_cast_as_date_drops_the_sub_second_part():
    """Oracle の DATE は秒までしか持たない。**切り捨て**であることを 23ai で実測して確かめた
    （`.999999` を渡しても繰り上がらない）。"""
    result = translate("CAST(v AS DATE)", {"v": "v"})
    assert result.java == "Plsql.castDate(v)" and result.translatable


def test_a_cast_whose_behaviour_was_not_measured_is_refused():
    """確かめていない型に名前を与えると、何をするか誰も知らない変換が黙って通る。"""
    assert not translate("CAST(v AS NUMBER)", {"v": "v"}).translatable


def test_precedence_is_parsed_not_pattern_matched():
    """Regression: marker substitution mis-split `a > 1 AND b = 'x'` because a marker cannot see its operands."""
    result = translate("n > 1 AND v = 'x'", {"v": "v", "n": "n"})
    assert result.java == 'Plsql.gt(n, 1) && Plsql.eq(v, "x")'


def test_a_parenthesised_group_is_parsed_again():
    assert translate("NOT (v = 'x')", {"v": "v"}).java == '(Plsql.ne(v, "x"))'


@pytest.mark.parametrize("plsql,java", [
    ("a + b * c", "Plsql.add(a, Plsql.mul(b, c))"),
    ("a - b / c - d", "Plsql.sub(Plsql.sub(a, Plsql.div(b, c)), d)"),
    ("a * b + c * d", "Plsql.add(Plsql.mul(a, b), Plsql.mul(c, d))"),
    ("(a + b) * c", "Plsql.mul((Plsql.add(a, b)), c)"),
    ("a - -b * c", "Plsql.sub(a, Plsql.mul(Plsql.neg(b), c))"),
])
def test_multiplication_binds_tighter_than_addition(plsql: str, java: str):
    """Regression (#27-10): one left-to-right loop over `+ - * /` read `a + b * c` as `(a + b) * c`. It compiled,
    ran, and wrote the wrong number -- in a lifted SQL bind, into the database."""
    assert translate(plsql, {n: n for n in "abcd"}).java == java


def test_exponentiation_is_refused_not_read_as_two_multiplications():
    assert translate("a ** 2", {"a": "a"}).unknown == ["**"]


@pytest.mark.parametrize("plsql,java", [
    ("NOT (a = b)", "(Plsql.ne(a, b))"),
    ("NOT a < b", "Plsql.ge(a, b)"),
    ("a NOT IN (1, 2)", "Plsql.notIn(a, 1, 2)"),
    ("NOT a IN (1, 2)", "Plsql.notIn(a, 1, 2)"),
    ("a NOT BETWEEN 1 AND b", "Plsql.notBetween(a, 1, b)"),
    ("v NOT LIKE 'A%'", 'Plsql.notLike(v, "A%")'),
    ("NOT (a = 1 AND (b <> 2 OR v IS NULL))", "((Plsql.ne(a, 1) || (Plsql.eq(b, 2) && Plsql.isNotNull(v))))"),
    ("NOT (a = 1 OR b = 2) AND v = 'x'", '(Plsql.ne(a, 1) && Plsql.ne(b, 2)) && Plsql.eq(v, "x")'),
    ("NOT NOT a = b", "Plsql.eq(a, b)"),
    ("NOT ok", "Plsql.isFalse(ok)"),
    ("NOT (a + b) > 3", "Plsql.le((Plsql.add(a, b)), 3)"),
])
def test_not_is_pushed_into_the_operator_never_emitted_as_a_bang(plsql: str, java: str):
    """Regression (#27-11): the helper's comparisons answer "is it TRUE", so `!(eq(a, b))` is true for a NULL `a`
    -- where Oracle's `NOT (a = b)` is UNKNOWN and the IF does not fire. De Morgan holds in three-valued logic,
    so the negation goes down to the operator, whose opposite is again "TRUE only"."""
    rendered = translate(plsql, {"a": "a", "b": "b", "v": "v", "ok": "ok"}).java
    assert rendered == java
    assert "!" not in rendered


@pytest.mark.parametrize("plsql,java", [
    ("a > 1", "Plsql.bool3(Plsql.gt(a, 1), Plsql.le(a, 1))"),
    ("NOT ok", "Plsql.bool3(Plsql.isFalse(ok), Plsql.isTrue(ok))"),
    ("a = b OR v IS NOT NULL", "Plsql.bool3(Plsql.eq(a, b) || Plsql.isNotNull(v), Plsql.ne(a, b) && Plsql.isNull(v))"),
    ("v IN ('NEW', 'CONFIRMED')", 'Plsql.bool3(Plsql.in(v, "NEW", "CONFIRMED"), Plsql.notIn(v, "NEW", "CONFIRMED"))'),
    ("ok", "ok"),
    ("TRUE", "true"),
])
def test_a_condition_stored_in_a_boolean_keeps_unknown_as_null(plsql: str, java: str):
    """`v_ok := a > 1` with a NULL `a` leaves `v_ok` NULL. Collapsing it to false would make a later `NOT v_ok`
    fire where Oracle's does not."""
    assert translate(plsql, {"a": "a", "b": "b", "v": "v", "ok": "ok"}, boolean_value=True).java == java


def _service_of(source: str) -> str:
    from plsql.frontend import parse_text
    from plsql.lower import lower_file
    from plsql.symbols import build
    parsed = parse_text(source, "p.prc")
    module = lower_file(parsed, build(parsed, None), None)[0]
    return generate_module(module, APP, INFRA, DOMAIN).file.render()


NESTED_LOOPS = """CREATE OR REPLACE PROCEDURE p(p_n NUMBER) IS
  i NUMBER := 0; j NUMBER := 0;
BEGIN
  <<outer_loop>>
  LOOP
    i := i + 1;
    LOOP
      j := j + 1;
      CONTINUE outer_loop WHEN j = 2;
      EXIT outer_loop WHEN j > p_n;
      EXIT WHEN j > 100;
    END LOOP;
  END LOOP;
END;
/
"""


def test_a_labelled_exit_leaves_the_loop_it_names():
    """Regression (#27-12): the label was dropped, `break;` left the inner loop only, and the outer
    `while (true)` never ended."""
    java = _service_of(NESTED_LOOPS)
    assert "outerLoop: while (true)" in java
    assert "if (Plsql.gt(j, pN)) break outerLoop;" in java
    assert "if (Plsql.eq(j, 2)) continue outerLoop;" in java
    assert "if (Plsql.gt(j, 100)) break;" in java


def test_an_exit_to_a_label_no_emitted_loop_carries_is_refused():
    java = _service_of(NESTED_LOOPS.replace("<<outer_loop>>", ""))
    assert "break outerLoop" not in java
    assert "UnsupportedOperationException" in java


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


# --- handler の中だけで意味を持つ名前（#13） ------------------------------------------------------

def test_sqlcode_reads_the_exception_the_handler_caught():
    """`SQLCODE` は「いま処理している例外の番号」である。catch が束ねている例外が持っている。"""
    import pathlib

    from plsql.gen_java.service import generate_module
    from plsql.report import analyse as build_analysis

    fixtures = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    corpus = build_analysis(fixtures / "src", fixtures / "src" / "schema.sql",
                            scalardb_schema=fixtures / "scalardb-schema.json")
    module = next(m for m in corpus.program.modules if m.name == "pkg_bulk_load")
    java = generate_module(module, "g.app", "g.infra", "g.domain").file.render()
    assert "Plsql.eq(e.code(), Plsql.neg(24381))" in java


def test_sqlcode_outside_a_handler_is_still_refused():
    """handler の外では常に 0 である。名前として与えると、外の `SQLCODE` が黙って通る。"""
    assert not translate("SQLCODE = -24381", {}).translatable


# --- #25: 完走できない routine は採番の前で止める ------------------------------------------------

def test_a_routine_that_cannot_finish_does_not_draw_a_sequence_first():
    """採番だけが**トランザクションの外へ出る**。`CACHE n` の sequence は hi/lo に移す（計画 §9）ので、
    引いた番号は呼び出し側が rollback しても戻らない——拒否された routine が欠番を作る。

    DML は戻るので止めない。どこまで移行できているかが見え、コンパイル検査も受ける。
    """
    import pathlib

    from plsql.gen_java.service import generate_module
    from plsql.report import analyse as build_analysis

    fixtures = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    corpus = build_analysis(fixtures / "src", fixtures / "src" / "schema.sql",
                            scalardb_schema=fixtures / "scalardb-schema.json")
    module = next(m for m in corpus.program.modules if m.name == "prc_audit_autonomous")
    java = generate_module(module, "g.app", "g.infra", "g.domain").file.render()
    assert "この文の採番は行わない" in java
    assert "repository.prcAuditAutonomousStmt1" not in java, "採番する文へ到達している"


def test_a_routine_that_finishes_still_draws_its_sequence():
    """止めるのは完走できない routine だけである。"""
    import pathlib

    from plsql.gen_java.service import generate_module
    from plsql.report import analyse as build_analysis

    fixtures = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    corpus = build_analysis(fixtures / "src", fixtures / "src" / "schema.sql",
                            scalardb_schema=fixtures / "scalardb-schema.json")
    module = next(m for m in corpus.program.modules if m.name == "prc_add_product")
    java = generate_module(module, "g.app", "g.infra", "g.domain").file.render()
    assert "この文の採番は行わない" not in java
    assert "repository.prcAddProductStmt" in java


# ---- review #27, 17c: output that did not compile, and was counted as clean ---------------------------------

def test_a_standalone_function_keeps_its_return_type():
    """`Create_function_bodyContext` has a lower-case f: the function came out `void` with `return 1;` in it."""
    java = _service_of("CREATE OR REPLACE FUNCTION f(p NUMBER) RETURN NUMBER IS\nBEGIN\n  RETURN 1;\nEND;\n/\n")
    assert "public BigDecimal f(BigDecimal p)" in java


def test_nothing_is_emitted_after_an_if_whose_every_branch_returns():
    java = _service_of("CREATE OR REPLACE FUNCTION f(p NUMBER) RETURN NUMBER IS\nBEGIN\n"
                       "  IF p > 0 THEN\n    RETURN 1;\n  ELSE\n    RETURN 2;\n  END IF;\nEND;\n/\n")
    assert "reached its end without RETURN" not in java, "Java rejects the unreachable throw"
    fallthrough = _service_of("CREATE OR REPLACE FUNCTION f(p NUMBER) RETURN NUMBER IS\nBEGIN\n"
                              "  IF p > 0 THEN\n    RETURN 1;\n  END IF;\nEND;\n/\n")
    assert "reached its end without RETURN" in fallthrough


def test_a_return_in_a_procedure_with_out_arguments_returns_the_result_record():
    java = _service_of("CREATE OR REPLACE PROCEDURE q(p NUMBER, o OUT NUMBER) IS\nBEGIN\n  o := 1;\n"
                       "  IF p > 0 THEN\n    RETURN;\n  END IF;\n  o := 2;\nEND;\n/\n")
    assert java.count("return new QResult(o);") == 2 and "return;" not in java


def test_a_literal_over_two_lines_stays_one_java_literal():
    java = _service_of("CREATE OR REPLACE PROCEDURE q IS\n  v VARCHAR2(100);\nBEGIN\n  v := 'line1\nline2';\nEND;\n/\n")
    assert '"line1\\nline2"' in java


@pytest.mark.parametrize("declarations,clash", [
    ("p_id NUMBER; p__id NUMBER;", "p_id / p__id -> pId"),
    ("row_count NUMBER;", "SQL%ROWCOUNT / row_count -> rowCount"),
])
def test_two_plsql_names_that_are_one_java_name_are_refused(declarations, clash):
    from plsql.frontend import parse_text
    from plsql.lower import lower_file
    from plsql.symbols import build
    parsed = parse_text(f"CREATE OR REPLACE PROCEDURE q IS\n  {declarations}\nBEGIN\n  NULL;\nEND;\n/\n", "q.prc")
    module = lower_file(parsed, build(parsed, None), None)[0]
    generated = generate_module(module, APP, INFRA, DOMAIN)
    assert clash in generated.file.render()
    assert generated.untranslated == ["q"], "refused, so it is not counted as clean"


def test_an_unkeyed_select_into_reads_two_rows_and_no_further(tmp_path):
    """#4 (decided 2026-09-19): Oracle's meaning is kept -- 0 rows NO_DATA_FOUND, 2+ rows TOO_MANY_ROWS -- and two
    rows are enough to tell. A key lookup needs no limit, and nothing ever adds LIMIT 1 or an ORDER BY."""
    from plsql.generate import main as generate

    generate(["fixtures/plsql/src", "--scalardb-schema", "fixtures/plsql/scalardb-schema.json",
              "--out-dir", str(tmp_path), "--quiet"])
    text = next(tmp_path.rglob("PkgOrderStatusRepository.java")).read_text(encoding="utf-8")
    assert 'SELECT status FROM orders WHERE customer_id = :p_customer_id LIMIT 2"' in text
    assert 'SELECT status FROM orders WHERE order_id = :p_order_id"' in text, "the key lookup is left alone"
    assert "LIMIT 1" not in text
    assert text.count("TooManyRowsException(") >= 2, "the second row still raises"


# --- a call to a sibling hands its NUMBER parameters a BigDecimal (samples/tutorial, 2026-09-20) --------

SIBLING_CALL = """CREATE OR REPLACE PACKAGE BODY pkg_s AS
  FUNCTION label_of(p_amount IN NUMBER, p_name IN VARCHAR2) RETURN VARCHAR2 IS
  BEGIN
    RETURN p_name;
  END label_of;

  PROCEDURE run(p_name IN VARCHAR2) IS
    v_count PLS_INTEGER := 3;
    v_label VARCHAR2(10);
  BEGIN
    v_label := label_of(v_count, p_name);
  END run;
END pkg_s;
"""


def test_a_sibling_call_converts_the_argument_of_a_number_parameter():
    """`label_of` takes BigDecimal; the local is an Integer. Passing it as is was Java that does not compile, in a
    routine the rules had judged AUTO. The String argument is left alone."""
    java = _service_of(SIBLING_CALL)
    assert "labelOf(Plsql.dec(vCount), pName)" in java


def test_an_if_whose_every_branch_is_refused_ends_the_block(tmp_path):
    """Both arms of the IF open a REF CURSOR the generator cannot translate, so both throw; javac then rejects the
    loop after the IF as unreachable. The block ends at the IF instead (#36, samples/oracle-samples b04_4_4)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "schema.sql").write_text("CREATE TABLE departments (department_id NUMBER(4) PRIMARY KEY, department_name VARCHAR2(30));\n")
    (src / "pick.prc").write_text(
        "CREATE OR REPLACE PROCEDURE pick (p_mode VARCHAR2) AS\n"
        "  rc SYS_REFCURSOR;\n  v_name VARCHAR2(50);\n"
        "BEGIN\n"
        "  IF p_mode = 'DEPT' THEN\n    OPEN rc FOR SELECT department_name FROM departments;\n"
        "  ELSE\n    OPEN rc FOR SELECT department_name FROM departments WHERE department_id = 1;\n  END IF;\n"
        "  LOOP\n    FETCH rc INTO v_name;\n    EXIT WHEN rc%NOTFOUND;\n  END LOOP;\n  CLOSE rc;\n"
        "END;\n/\n")
    program = build_analysis(src, src / "schema.sql").program
    java = generate_module(module_named(program, "pick"), APP, INFRA, DOMAIN).file.render()
    assert java.count("throw new UnsupportedOperationException") >= 2
    assert "the rest of this block is unreachable" in java
    assert "while (true)" not in java
