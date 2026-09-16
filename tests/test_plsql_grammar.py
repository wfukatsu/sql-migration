"""P0-6: the vendored PL/SQL grammar parses without any build step.

The acceptance condition is that a clean `pip install -r requirements.txt` is enough -- no Java, no
ANTLR tool. These tests therefore import the committed parser and parse source directly.
"""

from __future__ import annotations

import pytest
from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from plsql.grammar import PlSqlLexer, PlSqlParser

PACKAGE_BODY = """
CREATE OR REPLACE PACKAGE BODY pkg_order AS
  PROCEDURE create_order(p_id IN NUMBER, p_status OUT VARCHAR2) IS
    v_status orders.status%TYPE;
    v_row    orders%ROWTYPE;
  BEGIN
    SELECT status INTO v_status FROM orders WHERE id = p_id;
    IF v_status IS NULL THEN
      RAISE_APPLICATION_ERROR(-20001, 'no status');
    END IF;
    FOR r IN (SELECT id FROM order_lines WHERE order_id = p_id) LOOP
      UPDATE order_lines SET checked = 1 WHERE id = r.id;
    END LOOP;
    p_status := v_status;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      p_status := 'UNKNOWN';
  END create_order;
END pkg_order;
/
"""


class _Collector(ErrorListener):
    """Syntax errors as data. The front end reports them as diagnostics, never as exceptions."""

    def __init__(self) -> None:
        self.errors: list[tuple[int, int, str]] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append((line, column, msg))


def parse(source: str) -> tuple[object, list[tuple[int, int, str]]]:
    collector = _Collector()
    lexer = PlSqlLexer(InputStream(source))
    lexer.removeErrorListeners()
    lexer.addErrorListener(collector)
    parser = PlSqlParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(collector)
    return parser.sql_script(), collector.errors


def test_package_body_parses_without_errors():
    tree, errors = parse(PACKAGE_BODY)
    assert errors == []
    assert type(tree).__name__ == "Sql_scriptContext"


@pytest.mark.parametrize("source", [
    "begin null; end;\n/\n",
    "BEGIN NULL; END;\n/\n",
    "Begin Null; End;\n/\n",
])
def test_keywords_are_case_insensitive(source):
    """The caller must not have to upper-case the input: that would destroy literal casing."""
    _, errors = parse(source)
    assert errors == []


def test_string_literal_keeps_its_case():
    source = "BEGIN dbms_output.put_line('MixedCase Text'); END;\n/\n"
    stream = CommonTokenStream(PlSqlLexer(InputStream(source)))
    PlSqlParser(stream).sql_script()
    stream.fill()
    assert "'MixedCase Text'" in [t.text for t in stream.tokens]


def test_syntax_error_is_reported_as_data_not_raised():
    _, errors = parse("BEGIN IF THEN END;\n/\n")
    assert errors, "a malformed block must produce diagnostics"
    assert all(isinstance(line, int) for line, _, _ in errors)


def test_visitor_and_listener_are_importable():
    from plsql.grammar import PlSqlParserListener, PlSqlParserVisitor

    assert hasattr(PlSqlParserVisitor, "visitSql_script")
    assert hasattr(PlSqlParserListener, "enterSql_script")
