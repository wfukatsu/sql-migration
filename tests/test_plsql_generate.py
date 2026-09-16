"""P2-7 / P2-8 / P2-10: the generated project, and the promise that AUTO compiles.

`gradle compileJava` is the acceptance condition and it needs a JVM, so it is an opt-in test. Everything that can
be checked without one is checked here: the repository reproduces the `SELECT INTO` semantics, the plan path is
used where ScalarDB refuses, nothing edits the caller's transaction, and every AUTO routine comes out whole.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from plsql.analysis import analyse as analyse_program
from plsql.gen_java.project import generate, write
from plsql.generate import _dirty_auto, main
from plsql.report import analyse as build_analysis
from plsql.rules.engine import Evidence, RuleSet, decide

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "plsql"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("generated")
    analysis = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    decisions = decide(analysis.program, analyse_program(analysis.program), RuleSet.load(), Evidence())
    project = generate(analysis.program, out, "com.example.migrated", decisions)
    write(project, decisions)
    return project, decisions, out


def sources(out: pathlib.Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in out.rglob("*.java")}


# --- every AUTO routine comes out whole ------------------------------------------------------------------

def test_no_auto_routine_is_left_incomplete(generated):
    """The rules cleared these for unattended generation; anything the generator refused is a disagreement."""
    project, decisions, _ = generated
    assert _dirty_auto(project, decisions) == []


def test_the_corpus_produces_all_three_layers(generated):
    _, _, out = generated
    names = sources(out)
    assert any(n.endswith("Service.java") for n in names)
    assert any(n.endswith("Repository.java") for n in names)
    assert "MigratedException.java" in names


def test_the_report_records_the_verdicts_next_to_the_code(generated):
    _, decisions, out = generated
    report = json.loads((out / "generation-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["files"] > 0
    assert set(report["verdicts"]) == set(decisions)
    entry = report["verdicts"]["prc_nightly_close"]
    assert entry["ruleVerdict"] == "REDESIGN"
    assert entry["reasons"]


# --- the repository keeps Oracle's semantics ----------------------------------------------------------------

def test_select_into_raises_on_no_row_and_on_many(generated):
    """A JDBC result set does neither; without this a migrated routine takes a different branch."""
    _, _, out = generated
    text = sources(out)["PkgOrderStatusRepository.java"]
    assert "NoDataFoundException" in text
    assert "TooManyRowsException" in text
    assert text.index("matched no row") < text.index("matched more than one row")


def test_dml_returns_the_affected_row_count(generated):
    """`SQL%ROWCOUNT` is behaviour: update_email raises when it is zero."""
    _, _, out = generated
    assert "executeUpdate()" in sources(out)["PkgCustomerCrudRepository.java"]
    assert "int rowCount" in sources(out)["PkgCustomerCrudService.java"]


def test_named_placeholders_are_rewritten_for_jdbc(generated):
    """The converter emits `:name`; JDBC understands only `?`, and the runtime already owns that rewrite."""
    _, _, out = generated
    text = sources(out)["PkgOrderStatusRepository.java"]
    assert "Residual.bindNamed" in text
    assert "prepareStatement(bound)" in text


def test_statements_scalardb_refuses_are_not_written_as_sql(generated):
    project, _, out = generated
    assert project.unsupported_sql, "this corpus contains statements ScalarDB cannot run"
    text = "\n".join(sources(out).values())
    assert "UnsupportedOperationException" in text


def test_a_planned_statement_goes_through_the_plan_runner(generated):
    project, _, out = generated
    if not project.planned_sql:
        pytest.skip("no PLANNED statement in this corpus")
    text = "\n".join(sources(out).values())
    assert "PlanRunner.join(connection" in text


# --- the transaction stays with the caller --------------------------------------------------------------------

def test_nothing_generated_opens_or_ends_a_transaction(generated):
    _, _, out = generated
    for name, text in sources(out).items():
        for forbidden in ("connection.commit", "connection.rollback", "setAutoCommit", "@Transactional"):
            assert forbidden not in text, f"{name} contains {forbidden}"


def test_the_repository_takes_the_connection_it_is_given(generated):
    _, _, out = generated
    text = sources(out)["PkgOrderStatusRepository.java"]
    assert "public PkgOrderStatusRepository(Connection connection)" in text
    assert "DriverManager" not in text


# --- generated code is not hand-editable ------------------------------------------------------------------------

def test_every_file_says_it_is_generated(generated):
    _, _, out = generated
    for name, text in sources(out).items():
        assert text.startswith("// Generated by"), name
        assert "Do not edit" in text


# --- the command --------------------------------------------------------------------------------------------------

def test_the_command_writes_and_reports(tmp_path, capsys):
    assert main([str(SRC), "--out-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "AUTO" in out and "REVIEW" in out and "REDESIGN" in out
    assert (tmp_path / "generation-report.json").exists()


# --- the acceptance condition itself --------------------------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("PLSQL_COMPILE"),
                    reason="needs a JVM and Gradle; opt in with PLSQL_COMPILE=1")
def test_the_generated_sources_compile():
    """P2-8: `gradle compileJava` over the generated tree.

        python -m plsql.generate fixtures/plsql/src --out-dir generated
        PLSQL_COMPILE=1 .venv/bin/python -m pytest tests/test_plsql_generate.py -k compile
    """
    assert (ROOT / "generated" / "src" / "main" / "java").exists(), \
        "run: python -m plsql.generate fixtures/plsql/src --out-dir generated"
    gradle = shutil.which("gradle")
    if gradle is None:
        pytest.skip("gradle is not on PATH")
    finished = subprocess.run([gradle, "compileJava", "-q"], cwd=str(ROOT / "runtime-java"),
                              capture_output=True, text=True)
    assert finished.returncode == 0, finished.stdout[-3000:] + finished.stderr[-3000:]
