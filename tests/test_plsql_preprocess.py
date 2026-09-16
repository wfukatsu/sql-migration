"""P1-1: SQL*Plus preprocessing and the source map.

Two properties matter downstream and are what these tests defend:

* a unit's text is exactly the PL/SQL the developer wrote -- nothing dropped, nothing case-folded
* every line of that text maps back to the line of the original file, so a later diagnostic can name it
"""

from __future__ import annotations

import pathlib

import pytest
from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from plsql.grammar import PlSqlLexer, PlSqlParser
from plsql.preprocess import preprocess

SRC = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql" / "src"
SUFFIXES = {".pks", ".pkb", ".prc", ".trg"}


def corpus_files() -> list[pathlib.Path]:
    return sorted([p for p in SRC.rglob("*") if p.suffix in SUFFIXES] + [SRC / "schema.sql"])


class _Collector(ErrorListener):
    def __init__(self) -> None:
        self.errors: list[str] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append(f"{line}:{column} {msg}")


def parse_errors(text: str) -> list[str]:
    collector = _Collector()
    lexer = PlSqlLexer(InputStream(text))
    lexer.removeErrorListeners()
    lexer.addErrorListener(collector)
    parser = PlSqlParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(collector)
    parser.sql_script()
    return collector.errors


# --- splitting ------------------------------------------------------------------------------------

def test_a_block_ends_at_a_lone_slash():
    result = preprocess("BEGIN\n  NULL;\nEND;\n/\nBEGIN\n  NULL;\nEND;\n/\n")
    assert [u.kind for u in result.units] == ["block", "block"]


def test_a_plain_statement_ends_at_a_semicolon():
    result = preprocess("CREATE TABLE t (id NUMBER);\nCREATE TABLE u (id NUMBER);\n")
    assert len(result.units) == 2
    assert all(u.kind == "statement" for u in result.units)


def test_semicolons_inside_a_block_do_not_split_it():
    result = preprocess("BEGIN\n  NULL;\n  NULL;\n  NULL;\nEND;\n/\n")
    assert len(result.units) == 1
    assert result.units[0].text.count(";") == 4


def test_a_slash_inside_a_string_is_not_a_terminator():
    result = preprocess("BEGIN\n  v := 'a\n/\nb';\nEND;\n/\n")
    assert len(result.units) == 1, "the '/' belongs to the literal"


def test_a_semicolon_inside_a_comment_does_not_end_a_statement():
    result = preprocess("CREATE TABLE t ( -- a ; here\n  id NUMBER\n);\n")
    assert len(result.units) == 1


def test_a_semicolon_inside_a_block_comment_does_not_end_a_statement():
    result = preprocess("CREATE TABLE t (\n/* ; still\n   going ; */\n  id NUMBER\n);\n")
    assert len(result.units) == 1


def test_an_escaped_quote_does_not_end_the_literal():
    result = preprocess("BEGIN\n  v := 'it''s; fine';\n  w := 2;\nEND;\n/\n")
    assert len(result.units) == 1


def test_a_q_quoted_literal_hides_its_contents():
    result = preprocess("BEGIN\n  v := q'[a ; b / c]';\nEND;\n/\n")
    assert len(result.units) == 1


# --- SQL*Plus directives --------------------------------------------------------------------------

@pytest.mark.parametrize("directive", [
    "SET SERVEROUTPUT ON", "SHOW ERRORS", "SPOOL out.log", "WHENEVER SQLERROR EXIT",
    "PROMPT loading...", "REM a remark", "EXEC pkg.do_it", "COLUMN name FORMAT A30",
])
def test_directives_between_statements_are_dropped(directive: str):
    result = preprocess(f"{directive}\nCREATE TABLE t (id NUMBER);\n")
    assert len(result.units) == 1
    assert directive.split()[0] not in result.units[0].text


def test_execute_immediate_is_not_treated_as_the_sqlplus_exec_directive():
    """`EXEC` is SQL*Plus, `EXECUTE IMMEDIATE` is PL/SQL. Dropping the latter would corrupt the body."""
    body = "BEGIN\n  EXECUTE IMMEDIATE 'UPDATE t SET c = 1';\nEND;\n/\n"
    result = preprocess(body)
    assert "EXECUTE IMMEDIATE" in result.units[0].text


def test_a_continuation_line_starting_with_set_is_kept():
    """SET is a directive only between statements; inside an UPDATE it is the SET clause."""
    result = preprocess("UPDATE orders\n   SET status = 'X'\n WHERE order_id = 1;\n")
    assert len(result.units) == 1
    assert "SET status" in result.units[0].text


def test_include_directives_are_reported_not_inlined():
    result = preprocess("@@other.sql\nCREATE TABLE t (id NUMBER);\n")
    assert result.includes == ["other.sql"]
    assert any(i.code == "SQLPLUS_INCLUDE" for i in result.issues)


def test_substitution_variables_are_reported():
    result = preprocess("CREATE TABLE &owner..t (id NUMBER);\n")
    assert any(i.code == "SQLPLUS_SUBSTITUTION" for i in result.issues)


# --- the source map -------------------------------------------------------------------------------

def test_the_map_points_back_at_the_original_line():
    text = "SET SERVEROUTPUT ON\nSHOW ERRORS\nBEGIN\n  NULL;\nEND;\n/\n"
    unit = preprocess(text, "x.sql").units[0]
    assert unit.origin(1).line == 3, "the unit starts on the third line of the file"
    assert unit.origin(2).line == 4
    assert unit.range.start_line == 3


def test_columns_are_not_shifted():
    unit = preprocess("SET ECHO ON\nBEGIN\n    NULL;\nEND;\n/\n", "x.sql").units[0]
    assert unit.origin(2, 5) == unit.map.origin(2, 5)
    assert unit.origin(2, 5).column == 5


def test_every_line_of_every_unit_maps_back_to_its_own_text():
    """The round trip: line i of the unit is line origin(i) of the file, character for character."""
    for path in corpus_files():
        original = path.read_text(encoding="utf-8").splitlines()
        for unit in preprocess(path.read_text(encoding="utf-8"), path.name).units:
            for index, line in enumerate(unit.text.splitlines(), start=1):
                assert original[unit.origin(index).line - 1] == line, f"{path.name} unit line {index}"


def test_asking_for_a_line_outside_the_unit_raises():
    unit = preprocess("BEGIN\n  NULL;\nEND;\n/\n").units[0]
    with pytest.raises(IndexError):
        unit.origin(99)


# --- the input is not modified --------------------------------------------------------------------

def test_the_input_is_not_case_folded():
    """P0-6: the grammar is case-insensitive, and upper-casing would destroy literal casing."""
    unit = preprocess("BEGIN\n  dbms_output.put_line('MixedCase');\nEnd;\n/\n").units[0]
    assert "dbms_output" in unit.text
    assert "'MixedCase'" in unit.text
    assert "End;" in unit.text


def test_comments_are_kept_in_the_unit_text():
    """The parser puts comments on a hidden channel; stripping them here would lose them for good."""
    unit = preprocess("-- a leading note\nBEGIN\n  NULL; -- trailing\nEND;\n/\n").units[0]
    assert "a leading note" in unit.text
    assert "trailing" in unit.text


# --- the corpus -----------------------------------------------------------------------------------

@pytest.mark.parametrize("path", corpus_files(), ids=lambda p: p.name)
def test_every_corpus_unit_parses(path: pathlib.Path):
    result = preprocess(path.read_text(encoding="utf-8"), path.name)
    assert result.units, f"{path.name} produced no unit"
    for unit in result.units:
        assert parse_errors(unit.text) == [], f"{path.name} {unit.range}"


def test_the_corpus_splits_into_the_expected_number_of_units():
    """32 corpus files hold one unit each; schema.sql holds 16 statements."""
    counts = {p.name: len(preprocess(p.read_text(encoding="utf-8"), p.name).units) for p in corpus_files()}
    assert counts.pop("schema.sql") == 16
    assert set(counts.values()) == {1}, {k: v for k, v in counts.items() if v != 1}


def test_a_literal_spanning_lines_hides_a_slash_on_its_own_line():
    """A literal may span lines; a '/' inside it is text. This is why the scanner carries state."""
    result = preprocess("BEGIN\n  v := 'a\n/\nb';\n  w := 2;\nEND;\n/\n")
    assert len(result.units) == 1
    assert "w := 2" in result.units[0].text


def test_a_block_comment_spanning_lines_hides_a_slash():
    result = preprocess("BEGIN\n/* note\n/\n still note */\n  NULL;\nEND;\n/\n")
    assert len(result.units) == 1


def test_a_q_quoted_literal_spanning_lines_hides_a_slash():
    result = preprocess("BEGIN\n  v := q'[a\n/\nb]';\n  w := 2;\nEND;\n/\n")
    assert len(result.units) == 1
    assert "w := 2" in result.units[0].text
