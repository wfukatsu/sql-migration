"""#48: a function of another module called inside an expression (`v := emp_api.hire(...)`, `'…' || pkg.count`).

A statement-level call into another package was already generated (the service is injected); inside an
expression the same call only existed as text and came out as an unknown name. Found with
samples/oracle-samples b05_3 (2026-09-25), together with `WHEN emp_api.e_invalid_raise` naming a class nothing threw.
"""

from __future__ import annotations

import pathlib

from plsql.gen_java.service import generate_module
from plsql.report import analyse

PACKAGE = """
CREATE OR REPLACE PACKAGE emp_api AS
  e_invalid EXCEPTION;
  FUNCTION hire(p_last VARCHAR2, p_salary NUMBER) RETURN NUMBER;
  FUNCTION call_count RETURN NUMBER;
  PROCEDURE give_raise(p_pct NUMBER);
END emp_api;
/
CREATE OR REPLACE PACKAGE BODY emp_api AS
  g_calls PLS_INTEGER := 0;
  FUNCTION hire(p_last VARCHAR2, p_salary NUMBER) RETURN NUMBER IS
  BEGIN
    RETURN p_salary * 2;
  END hire;
  FUNCTION call_count RETURN NUMBER IS
  BEGIN
    RETURN 3;
  END call_count;
  PROCEDURE give_raise(p_pct NUMBER) IS
  BEGIN
    IF p_pct > 20 THEN RAISE e_invalid; END IF;
  END give_raise;
END emp_api;
/
"""

CALLER = """
CREATE OR REPLACE PROCEDURE b05_3 AS
  v_id NUMBER;
BEGIN
  v_id := emp_api.hire('Sato', 8000);
  DBMS_OUTPUT.PUT_LINE('count: ' || emp_api.call_count);
  BEGIN
    emp_api.give_raise(50);
  EXCEPTION
    WHEN emp_api.e_invalid THEN DBMS_OUTPUT.PUT_LINE('invalid');
  END;
END;
/
"""


def generated(tmp_path: pathlib.Path) -> tuple[str, str]:
    (tmp_path / "schema.sql").write_text("CREATE TABLE t (id NUMBER(6) PRIMARY KEY);\n", encoding="utf-8")
    (tmp_path / "emp_api.pks").write_text(PACKAGE.split("/\n")[0], encoding="utf-8")
    (tmp_path / "emp_api.pkb").write_text(PACKAGE.split("/\n")[1], encoding="utf-8")
    (tmp_path / "b05_3.prc").write_text(CALLER, encoding="utf-8")
    analysis = analyse(str(tmp_path), tmp_path / "schema.sql")
    caller = next(m for m in analysis.program.modules if m.name == "b05_3")
    package = next(m for m in analysis.program.modules if m.name == "emp_api")
    return (generate_module(caller, "g.app", "g.infra", "g.domain", program=analysis.program).file.render(),
            generate_module(package, "g.app", "g.infra", "g.domain", program=analysis.program).file.render())


def test_a_function_of_another_module_is_called_through_the_injected_service(tmp_path):
    caller, _ = generated(tmp_path)
    assert "private final EmpApiService empApi;" in caller, caller
    assert "vId = Plsql.dec(empApi.hire(\"Sato\", Plsql.dec(8000)))" in caller, caller


def test_a_function_without_arguments_gets_its_parentheses(tmp_path):
    caller, _ = generated(tmp_path)
    assert "empApi.callCount()" in caller, caller


def test_the_package_exception_is_caught_as_the_class_the_package_throws(tmp_path):
    caller, package = generated(tmp_path)
    assert "throw new EInvalidException(" in package
    assert "catch (EInvalidException e)" in caller, caller
    assert "EmpApiEInvalidException" not in caller


def test_an_assignment_from_a_function_with_out_arguments_is_hoisted(tmp_path):
    """`v := pkg_o.f(1, w)`: the call becomes a statement of its own (plsql.hoist), so the OUT argument and the
    return value both come back through the result record. Before, it was refused as an unknown name."""
    (tmp_path / "schema.sql").write_text("CREATE TABLE t (id NUMBER(6) PRIMARY KEY);\n", encoding="utf-8")
    (tmp_path / "pkg_o.pkb").write_text(
        "CREATE OR REPLACE PACKAGE BODY pkg_o AS\n  FUNCTION f(p_x NUMBER, p_y OUT NUMBER) RETURN NUMBER IS\n"
        "  BEGIN p_y := p_x; RETURN p_x; END f;\nEND pkg_o;\n", encoding="utf-8")
    (tmp_path / "c.prc").write_text(
        "CREATE OR REPLACE PROCEDURE c AS\n  v NUMBER; w NUMBER;\nBEGIN\n  v := pkg_o.f(1, w);\nEND;\n", encoding="utf-8")
    analysis = analyse(str(tmp_path), tmp_path / "schema.sql")
    caller = next(m for m in analysis.program.modules if m.name == "c")
    java = generate_module(caller, "g.app", "g.infra", "g.domain", program=analysis.program).file.render()
    assert "FResult fResult = pkgO.f(Plsql.dec(1));" in java, java
    assert "w = Plsql.dec(fResult.pY());" in java and "vCall1 = Plsql.dec(fResult.returned());" in java, java
    assert "v = Plsql.dec(vCall1);" in java and "UnsupportedOperationException" not in java


# --- a function with OUT / IN OUT arguments inside an expression is hoisted (plsql.hoist) --------------------
OUT_PACKAGE = """
CREATE OR REPLACE PACKAGE BODY pkg_c AS
  FUNCTION count_calls(p_n IN OUT NUMBER) RETURN NUMBER IS
  BEGIN
    p_n := p_n + 1;
    RETURN p_n * 10;
  END count_calls;
END pkg_c;
"""

OUT_CALLER = """
CREATE OR REPLACE PROCEDURE c AS
  v_n NUMBER := 0;
  v_text VARCHAR2(100);
BEGIN
  v_text := 'count: ' || pkg_c.count_calls(v_n);
  DBMS_OUTPUT.PUT_LINE('again: ' || pkg_c.count_calls(v_n));
  IF v_n > 0 AND pkg_c.count_calls(v_n) > 5 THEN
    NULL;
  ELSIF pkg_c.count_calls(v_n) > 7 THEN
    NULL;
  END IF;
END;
/
"""


def test_a_call_with_out_arguments_is_hoisted_in_front_of_the_statement(tmp_path):
    from plsql.lower import _walk

    (tmp_path / "schema.sql").write_text("CREATE TABLE t (id NUMBER(6) PRIMARY KEY);\n", encoding="utf-8")
    (tmp_path / "pkg_c.pkb").write_text(OUT_PACKAGE, encoding="utf-8")
    (tmp_path / "c.prc").write_text(OUT_CALLER, encoding="utf-8")
    analysis = analyse(str(tmp_path), tmp_path / "schema.sql")
    caller = next(m for m in analysis.program.modules if m.name == "c")
    routine = caller.routines[0]
    kinds = [(s.kind, getattr(s, "into", None)) for s in routine.body]
    assert kinds[:4] == [("Call", "v_call_1"), ("Assignment", None), ("Call", "v_call_2"), ("Call", None)]
    assert routine.body[1].expression == "'count: ' || v_call_1"
    assert routine.body[0].arguments == ["v_n"] and routine.body[0].resolved_to == "pkg_c.count_calls"
    assert [d.name for d in routine.declarations[-3:]] == ["v_call_1", "v_call_2", "v_call_3"]
    if_ = routine.body[-1]
    assert if_.branches[0].condition == "v_n > 0 AND v_call_3 > 5", "the first condition runs once, before the IF"
    assert "pkg_c.count_calls" in if_.branches[1].condition, "an ELSIF condition runs conditionally: not hoisted"
    java = generate_module(caller, "g.app", "g.infra", "g.domain", program=analysis.program).file.render()
    assert "CountCallsResult countCallsResult = pkgC.countCalls(Plsql.dec(vN));" in java, java
    assert "vN = " in java and "vCall1 = Plsql.dec(countCallsResult.returned());" in java, java
    assert "OUT / IN OUT 引数" in java, "the ELSIF call stays refused with the reason"
