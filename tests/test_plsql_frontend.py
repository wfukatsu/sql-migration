"""P1-2: the ANTLR front end.

The acceptance conditions are behavioural, and each has a test here: 90% of the corpus parses, a syntax error
comes back as data with the line of the original file, and one file's failure never stops another.
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.frontend import Coverage, coverage, parse_directory, parse_file, parse_text, parse_unit
from plsql.preprocess import preprocess

SRC = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql" / "src"


# --- results are data -----------------------------------------------------------------------------

def test_a_syntax_error_is_reported_not_raised():
    result = parse_text("BEGIN IF THEN END;\n/\n", "broken.pkb")
    assert not result.ok
    errors = [i for i in result.all_issues() if i.code == "PARSE"]
    assert errors, "a malformed block must produce a PARSE issue"
    assert errors[0].severity == "ERROR"


def test_the_error_names_the_line_of_the_original_file():
    """The offset from the dropped directives has to be undone, or the message points at the wrong line."""
    text = "SET SERVEROUTPUT ON\nSHOW ERRORS\nBEGIN\n  IF THEN\nEND;\n/\n"
    result = parse_text(text, "x.pkb")
    lines = {i.range.start_line for i in result.all_issues() if i.code == "PARSE"}
    assert lines, "no PARSE issue was produced"
    assert min(lines) >= 3, f"the error was reported at {sorted(lines)}, before the block even starts"
    assert max(lines) <= 6


def test_a_good_unit_carries_its_tree():
    result = parse_text("BEGIN\n  NULL;\nEND;\n/\n", "ok.pkb")
    assert result.ok
    assert result.units[0].tree is not None
    assert type(result.units[0].tree).__name__ == "Sql_scriptContext"


def test_parsing_an_empty_file_is_not_an_error():
    assert parse_text("\n\n", "empty.sql").units == []


def test_a_file_with_only_directives_reports_rather_than_crashes():
    result = parse_text("SET ECHO ON\nSHOW ERRORS\n", "only-directives.sql")
    assert result.units == []
    assert not [i for i in result.all_issues() if i.severity == "ERROR"]


# --- one failure never stops the rest ---------------------------------------------------------------

def test_one_broken_file_does_not_stop_the_others(tmp_path: pathlib.Path):
    (tmp_path / "good.pkb").write_text("BEGIN\n  NULL;\nEND;\n/\n", encoding="utf-8")
    (tmp_path / "bad.pkb").write_text("BEGIN IF THEN END;\n/\n", encoding="utf-8")
    (tmp_path / "also_good.prc").write_text(
        "CREATE OR REPLACE PROCEDURE p IS BEGIN NULL; END;\n/\n", encoding="utf-8")
    results = parse_directory(tmp_path)
    assert len(results) == 3
    assert coverage(results).parsed == 2
    assert coverage(results).failed == ["bad.pkb"]


def test_an_unreadable_file_is_a_diagnostic_not_an_exception(tmp_path: pathlib.Path):
    result = parse_file(tmp_path / "does-not-exist.pkb")
    assert not result.ok
    assert [i.code for i in result.all_issues()] == ["READ"]


def test_parse_unit_never_raises():
    """Whatever the input, the caller gets a ParsedUnit back."""
    for text in ["", "???", "BEGIN", "/", "'unterminated", "CREATE PACKAGE", "\x00\x01"]:
        for unit in preprocess(text or "\n", "weird.sql").units:
            assert parse_unit(unit) is not None


# --- the two-stage strategy -------------------------------------------------------------------------

def test_units_the_fast_strategy_rejects_still_parse():
    """SLL can fail on input LL accepts, so its failure must trigger a retry, never a verdict."""
    result = parse_file(SRC / "pkg_bulk_load.pkb")
    assert result.ok
    assert result.units[0].used_fallback, "this unit is the regression case: SLL bails, LL succeeds"


def test_the_fallback_is_not_used_for_most_units():
    """If everything fell back the fast path would be pointless; this pins that it is actually doing work."""
    results = parse_directory(SRC)
    units = [u for r in results for u in r.units]
    fell_back = [u for u in units if u.used_fallback]
    assert len(fell_back) < len(units) / 2, f"{len(fell_back)}/{len(units)} units needed the full strategy"


# --- KPI-1 ------------------------------------------------------------------------------------------

def test_corpus_parse_rate_meets_the_phase_1_target():
    result = coverage(parse_directory(SRC))
    assert result.rate >= 0.90, f"parse rate {result.rate:.0%}, failures: {result.failed}"


def test_coverage_counts_and_names_failures():
    assert Coverage(total=0, parsed=0).rate == 1.0
    partial = Coverage(total=4, parsed=3, failed=["b.pkb"])
    assert partial.rate == 0.75
    assert partial.failed == ["b.pkb"]


def test_preprocessing_issues_reach_the_result():
    result = parse_text("@@other.sql\nBEGIN\n  NULL;\nEND;\n/\n", "x.pkb")
    assert any(i.code == "SQLPLUS_INCLUDE" for i in result.all_issues())
    assert result.ok, "an include is a warning, not a parse failure"


@pytest.mark.parametrize("path", sorted(p for p in SRC.rglob("*") if p.suffix in {".pks", ".pkb", ".prc", ".trg"}),
                         ids=lambda p: p.name)
def test_every_corpus_file_parses(path: pathlib.Path):
    result = parse_file(path)
    assert result.ok, [str(i) for i in result.all_issues() if i.severity == "ERROR"][:3]
