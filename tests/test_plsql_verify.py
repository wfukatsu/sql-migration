"""#21: `--verify-compile` -- the AUTO gate asks javac whether the code it called AUTO compiles.

Until this, "AUTO" was a claim about code nobody had compiled. The gate read the IR, which cannot see a body
that reads a name its own signature does not provide; two defects of that shape reached review (MR !53) and
both were reported as `AUTO` with `untranslated statements 0`.

The compile itself needs a JVM and Gradle, so the end-to-end run is opt-in (`PLSQL_COMPILE=1`). What is
checked here without one is the part this codebase actually wrote: reading javac's output, and naming the
routine each error came from -- because "PkgXService.java:184" is not something anyone can act on.
"""

from __future__ import annotations

import os
import pathlib
import textwrap

import pytest

from plsql.analysis import analyse as analyse_program
from plsql.gen_java.project import generate, write
from plsql.generate import main
from plsql.report import analyse as build_analysis
from plsql.rules.engine import Evidence, RuleSet, decide
from plsql.verify import CompileReport, _attribute, _errors, verify

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "fixtures" / "plsql" / "src"
SCALARDB = ROOT / "fixtures" / "plsql" / "scalardb-schema.json"

TWO_BLOCKS = """\
CREATE OR REPLACE PROCEDURE prc_twice IS
BEGIN
  DECLARE
    v_tmp VARCHAR2(10);
  BEGIN
    v_tmp := 'a';
  END;
END prc_twice;
/
"""


@pytest.fixture(scope="module")
def corpus_project(tmp_path_factory):
    out = tmp_path_factory.mktemp("verify")
    analysis = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=SCALARDB)
    decisions = decide(analysis.program, analyse_program(analysis.program), RuleSet.load(), Evidence())
    project = generate(analysis.program, out, "com.example.migrated", decisions)
    write(project, decisions)
    return project


# --- reading what javac said ---------------------------------------------------------------------------

def test_an_error_is_read_off_the_build_output():
    errors = _errors("/x/PkgAService.java:12: error: cannot find symbol\n")
    assert [(e.file, e.line, e.message) for e in errors] == [("/x/PkgAService.java", 12,
                                                              "cannot find symbol")]


def test_the_same_error_is_not_counted_twice():
    """Gradle repeats the compiler's output on both streams; two copies read as two defects."""
    line = "/x/PkgAService.java:12: error: cannot find symbol\n"
    assert len(_errors(line + line)) == 1


def test_the_name_javac_could_not_find_is_kept():
    """`cannot find symbol` alone is not actionable. The name is on the next line, indented -- which is the
    one marker that does not change with the compiler's language."""
    errors = _errors("/x/PkgAService.java:12: error: cannot find symbol\n"
                     "  symbol:   variable audit\n"
                     "  location: class PkgAService\n")
    assert errors[0].message == "cannot find symbol / symbol:   variable audit / location: class PkgAService"


def test_the_echoed_source_line_and_its_caret_are_dropped():
    """They say nothing the file and line do not already say, and they are the longest part of the message."""
    errors = _errors("/x/PkgAService.java:12: error: cannot find symbol\n"
                     "        throw new MigratedException(-1, audit.user());\n"
                     "                                        ^\n"
                     "  symbol:   variable audit\n")
    assert errors[0].message == "cannot find symbol / symbol:   variable audit"


def test_an_unindented_line_ends_the_run_of_qualifiers():
    """Gradle prints its own notes between diagnostics; appending one to the error above it would misdescribe
    the defect."""
    errors = _errors("/x/PkgAService.java:12: error: cannot find symbol\n"
                     "  symbol:   variable audit\n"
                     "note: /runtime/Bench.java uses unchecked operations\n"
                     "  note: /runtime/Bench.java uses unchecked operations\n")
    assert errors[0].message == "cannot find symbol / symbol:   variable audit"


def test_a_diagnostic_the_reader_could_not_parse_blames_the_locale_not_the_build(monkeypatch):
    """The build pins the compiler's language; if that did not take, saying 'the build is broken' would send
    the reader to the wrong place."""
    class Finished:
        returncode = 1
        stdout = "/x/PkgAService.java:28: エラー: シンボルを見つけられません\n"
        stderr = ""

    monkeypatch.setattr("plsql.verify.shutil.which", lambda _: "/usr/bin/gradle")
    monkeypatch.setattr("plsql.verify.subprocess.run", lambda *a, **k: Finished())
    report = verify("out")
    assert not report.ok and "locale" in (report.unavailable or "")


def test_a_build_that_failed_without_a_javac_error_is_not_a_verdict_on_the_routines(monkeypatch):
    """A missing dependency or no network is the build being broken, not the routines not compiling."""
    class Finished:
        returncode = 1
        stdout = "FAILURE: Could not resolve all dependencies\n"
        stderr = ""

    monkeypatch.setattr("plsql.verify.shutil.which", lambda _: "/usr/bin/gradle")
    monkeypatch.setattr("plsql.verify.subprocess.run", lambda *a, **k: Finished())
    report = verify("out")
    assert not report.ran and not report.ok and "build itself is broken" in (report.unavailable or "")


def test_not_being_able_to_run_is_not_a_pass(monkeypatch):
    monkeypatch.setattr("plsql.verify.shutil.which", lambda _: None)
    report = verify("out")
    assert not report.ran and not report.ok and report.unavailable == "gradle is not on PATH"


def test_the_check_tells_the_build_it_is_a_check(monkeypatch):
    """`plsql.verify` is what turns the silent fallback below into an error, and what pins the locale."""
    seen = {}

    class Finished:
        returncode = 0
        stdout = stderr = ""

    def record(command, **kwargs):
        seen["command"] = command
        return Finished()

    monkeypatch.setattr("plsql.verify.shutil.which", lambda _: "/usr/bin/gradle")
    monkeypatch.setattr("plsql.verify.subprocess.run", record)
    verify("/tmp/out")
    assert "-Pplsql.verify=1" in seen["command"]
    assert any(a.startswith("-Pplsql.generatedDir=") for a in seen["command"])


def test_python_and_gradle_still_agree_on_the_property_names():
    """The fallback in build.gradle is silent on purpose -- an ordinary `gradle compileJava` has to keep
    working -- so if the two sides stop agreeing on a name, the check compiles the PREVIOUS run's output and
    reports it as this one's success. Nothing else notices: the end-to-end run is opt-in."""
    gradle = (ROOT / "runtime-java" / "build.gradle").read_text(encoding="utf-8")
    python = (ROOT / "plsql" / "verify.py").read_text(encoding="utf-8")
    for name in ("plsql.generatedDir", "plsql.verify"):
        assert name in gradle, name
        assert name in python, name


# --- naming the routine --------------------------------------------------------------------------------

def test_an_error_is_attributed_to_the_routine_whose_method_holds_it(corpus_project):
    service = next(f for f in corpus_project.files if f.path.endswith("PkgOrderReportService.java"))
    text = service.render()
    line = next(number for number, content in enumerate(text.splitlines(), start=1)
                if "countByStatus(" in content) + 1
    errors = _errors(f"/g/{pathlib.Path(service.path).name}:{line}: error: cannot find symbol\n")
    _attribute(errors, corpus_project)
    assert errors[0].routine == "pkg_order_report.count_by_status"


def test_a_repository_method_is_attributed_to_the_routine_its_statement_came_from(corpus_project):
    """`countByStatusStmt3` belongs to `count_by_status`: the longest routine name that prefixes it wins."""
    repository = next(f for f in corpus_project.files if f.path.endswith("PkgOrderReportRepository.java"))
    text = repository.render()
    line = next(number for number, content in enumerate(text.splitlines(), start=1)
                if "countByStatusStmt" in content and content.strip().startswith("public")) + 1
    errors = _errors(f"/g/{pathlib.Path(repository.path).name}:{line}: error: cannot find symbol\n")
    _attribute(errors, corpus_project)
    assert errors[0].routine == "pkg_order_report.count_by_status"


def test_an_error_outside_any_method_is_left_unattributed(corpus_project):
    """An import or a class-level line belongs to no routine, and guessing one would send a reader to the
    wrong place."""
    errors = _errors("/g/PkgOrderReportService.java:3: error: cannot find symbol\n")
    _attribute(errors, corpus_project)
    assert errors[0].routine is None


# --- the classes the generator itself raises -------------------------------------------------------------

def test_the_exceptions_the_repository_throws_are_always_written(tmp_path):
    """`SELECT INTO` throws them whether or not the PL/SQL named them. Writing them only when the source
    mentions one left every other repository importing a class that was not there (found by this check)."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "schema.sql").write_text((SRC / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
    (source / "prc_twice.prc").write_text(textwrap.dedent(TWO_BLOCKS), encoding="utf-8")
    out = tmp_path / "out"
    assert main([str(source), "--scalardb-schema", str(SCALARDB), "--out-dir", str(out),
                 "--quiet", "--no-verify-compile"]) == 0
    domain = out / "src" / "main" / "java" / "com" / "example" / "migrated" / "domain"
    assert (domain / "NoDataFoundException.java").exists()
    assert (domain / "TooManyRowsException.java").exists()


# --- the gate ------------------------------------------------------------------------------------------

def test_the_run_fails_when_the_check_was_asked_for_and_could_not_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("plsql.verify.shutil.which", lambda _: None)
    assert main([str(SRC), "--out-dir", str(tmp_path), "--verify-compile"]) == 1
    assert "compile check did not run" in capsys.readouterr().out


def test_the_run_fails_on_a_javac_error_and_names_the_routine(tmp_path, monkeypatch, capsys):
    error = CompileReport(ran=True, errors=_errors(
        "/g/PkgOrderReportService.java:12: error: cannot find symbol\n"))
    error.errors[0].routine = "pkg_order_report.count_by_status"
    monkeypatch.setattr("plsql.generate._verify_compile", lambda project, args: error)
    assert main([str(SRC), "--out-dir", str(tmp_path), "--verify-compile"]) == 1
    out = capsys.readouterr().out
    assert "pkg_order_report.count_by_status" in out and "cannot find symbol" in out


def test_the_environment_variable_asks_for_the_same_check(tmp_path, monkeypatch, capsys):
    """So CI can turn it on without every command in the docs growing a flag."""
    monkeypatch.setenv("PLSQL_VERIFY_COMPILE", "1")
    monkeypatch.setattr("plsql.verify.shutil.which", lambda _: None)
    assert main([str(SRC), "--out-dir", str(tmp_path)]) == 1
    assert "compile check did not run" in capsys.readouterr().out


@pytest.mark.skipif(not os.environ.get("PLSQL_COMPILE"),
                    reason="needs a JVM and Gradle; opt in with PLSQL_COMPILE=1")
def test_the_corpus_compiles_through_the_gate(tmp_path, capsys):
    """P2-8 as the command's own answer:

        PLSQL_COMPILE=1 .venv/bin/python -m pytest tests/test_plsql_verify.py -k corpus_compiles
    """
    assert main([str(SRC), "--scalardb-schema", str(SCALARDB), "--out-dir", str(tmp_path),
                 "--verify-compile"]) == 0
    assert "gradle compileJava succeeded" in capsys.readouterr().out
