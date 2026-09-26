"""#55: Oracle-supplied packages the generator knows (plsql/builtins.py).

A call into a package that is not in the program has no signature to read. For the ones in the table, named
arguments are put in Oracle's order, the target's counterpart is emitted, and the call no longer counts as a call
into code nobody analysed. What is not in the table is refused as before.
"""

from __future__ import annotations

import pathlib

import pytest

from plsql import builtins
from plsql.analysis import analyse as analyse_program
from plsql.report import analyse
from plsql.rules.engine import Evidence, RuleSet, decide

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SCHEMA = FIXTURES / "src" / "schema.sql"


@pytest.mark.parametrize("arguments,expected", [
    (["module_name => 'B'", "action_name => 'S1'"], ["'B'", "'S1'"]),
    (["action_name => 'S1'", "module_name => 'B'"], ["'B'", "'S1'"]),
    (["'B'", "action_name => 'S1'"], ["'B'", "'S1'"]),
    (["NULL", "NULL"], ["NULL", "NULL"]),
])
def test_named_arguments_are_put_in_oracle_order(arguments, expected):
    assert builtins.ordered(builtins.lookup("dbms_application_info.set_module"), arguments) == expected


@pytest.mark.parametrize("name,arguments", [
    ("DBMS_APPLICATION_INFO.SET_MODULE", ["module => 'B'", "action_name => 'S'"]),   # no such parameter
    ("DBMS_APPLICATION_INFO.SET_MODULE", ["action_name => 'S'"]),                   # module_name is missing
    ("DBMS_APPLICATION_INFO.SET_MODULE", ["module_name => 'B'", "'S'"]),             # positional after named
    ("DBMS_RANDOM.VALUE", ["1"]),                                                    # VALUE() or VALUE(low, high)
])
def test_arguments_oracle_would_refuse_are_refused(name, arguments):
    with pytest.raises(builtins.BuiltinArgumentError):
        builtins.ordered(builtins.lookup(name), arguments)


def _generate(tmp_path, body: str, declare: str = ""):
    from plsql.gen_java.service import generate_module

    (tmp_path / "p.prc").write_text(f"CREATE OR REPLACE PROCEDURE p IS\n{declare}\nBEGIN\n{body}\nEND;\n/\n",
                                    encoding="utf-8")
    analysis = analyse(tmp_path, SCHEMA)
    module = next(m for m in analysis.program.modules if any(r.id == "p" for r in m.routines))
    java = generate_module(module, "g.app", "g.infra", "g.domain", analysis.program).file.render()
    perfect = Evidence(captures={"p": (1, 1)})
    decision = decide(analysis.program, analyse_program(analysis.program), RuleSet.load(), perfect)["p"]
    return java, decision


def test_the_sample_block_generates_and_is_not_an_external_call(tmp_path):
    body = """
  DBMS_APPLICATION_INFO.SET_MODULE(module_name => 'SAMPLE_BATCH', action_name => 'STEP1');
  DBMS_SESSION.SLEEP(1);
  DBMS_OUTPUT.PUT_LINE('乱数: ' || ROUND(DBMS_RANDOM.VALUE(1, 100)));
  DBMS_OUTPUT.PUT_LINE('hsecs: ' || DBMS_UTILITY.GET_TIME);
  DBMS_APPLICATION_INFO.SET_MODULE(NULL, NULL);"""
    java, decision = _generate(tmp_path, body)
    assert "UnsupportedOperationException" not in java
    assert "Plsql.sleep(1);" in java
    assert "Plsql.round(Plsql.randomValue(1, 100))" in java
    assert "Plsql.getTime()" in java
    assert java.count("DBMS_APPLICATION_INFO.SET_MODULE: V$SESSION") == 2, "a no-op says why it is one"
    assert "CALL-001" not in {m.rule.id for m in decision.matches}
    assert decision.confidence.symbol_resolution == 1.0


def test_a_package_not_in_the_table_is_still_refused(tmp_path):
    java, decision = _generate(tmp_path, "DBMS_SESSION.SET_IDENTIFIER('batch');")
    assert 'throw new UnsupportedOperationException("external call: DBMS_SESSION.SET_IDENTIFIER")' in java
    assert "CALL-001" in {m.rule.id for m in decision.matches}


def test_output_keeps_working_with_a_named_argument(tmp_path):
    java, _ = _generate(tmp_path, "DBMS_OUTPUT.PUT_LINE(item => 'x');\nDBMS_OUTPUT.NEW_LINE;")
    assert 'Plsql.putLine("x");' in java and "Plsql.newLine();" in java


def test_every_helper_the_table_names_exists_in_the_runtime():
    runtime = (pathlib.Path(__file__).resolve().parent.parent / "runtime-java/src/main/java/com/scalar/migrate/plsql"
               / "Plsql.java").read_text(encoding="utf-8")
    for builtin in builtins.BUILTINS.values():
        if builtin.java:
            method = builtin.java.split(".")[1]
            assert f" {method}(" in runtime, f"{builtin.name}: Plsql.{method} is not in runtime-java"
