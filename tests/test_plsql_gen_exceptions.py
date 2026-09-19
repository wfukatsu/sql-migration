"""Review #27 (13, 14, 16, 17 and what turned up beside them): exceptions, literals and stale files in the
generated Java."""

from __future__ import annotations

import json
import re

from plsql.frontend import parse_text
from plsql.gen_java.emit import JavaFile
from plsql.gen_java.project import _remove_stale
from plsql.gen_java.repository import _escape
from plsql.gen_java.service import generate_module
from plsql.lower import lower_file
from plsql.symbols import build

APP, INFRA, DOMAIN = "x.application", "x.infrastructure", "x.domain"

SOURCE = """CREATE OR REPLACE PACKAGE BODY pkg_e AS
  FUNCTION ratio(p_a NUMBER, p_b NUMBER) RETURN NUMBER IS
    e_bad EXCEPTION;
    v NUMBER;
  BEGIN
    IF p_a < 0 THEN
      RAISE e_bad;
    END IF;
    IF p_a = 0 THEN
      RAISE NO_DATA_FOUND;
    END IF;
    v := p_a / p_b;
    RETURN v;
  EXCEPTION
    WHEN ZERO_DIVIDE THEN
      RETURN 0;
    WHEN e_bad THEN
      BEGIN
        v := 1;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE;
      END;
      RAISE;
    WHEN DUP_VAL_ON_INDEX THEN
      RETURN -1;
  END ratio;
END pkg_e;
/
"""


def lowered(source: str = SOURCE):
    parsed = parse_text(source, "pkg_e.pkb")
    return lower_file(parsed, build(parsed, None), None)[0]


def java(source: str = SOURCE) -> str:
    return generate_module(lowered(source), APP, INFRA, DOMAIN).file.render()


def test_a_handler_is_named_by_its_when_clause_only():
    """The names were collected from the whole handler, body included: `WHEN e_bad THEN BEGIN ... EXCEPTION WHEN
    OTHERS` became a handler for OTHERS, and any `RAISE e_x;` in a body added e_x to the handler's names."""
    handlers = lowered().routines[0].exception_handlers
    assert [[e.upper() for e in h.exceptions] for h in handlers] == [["ZERO_DIVIDE"], ["E_BAD"], ["DUP_VAL_ON_INDEX"]]


def test_a_declared_exception_is_raised_and_caught_as_its_own_class():
    """Regression (#27-13): `WHEN e_bad` was `catch (MigratedException e)`, so it also caught a NO_DATA_FOUND from
    the same block -- which Oracle lets past a handler that does not name it."""
    text = java()
    assert 'throw new EBadException("e_bad");' in text
    assert "catch (EBadException e)" in text
    assert 'throw new NoDataFoundException("NO_DATA_FOUND");' in text
    assert "new MigratedException(0" not in text
    assert text.count("catch (MigratedException") == 1, "only the nested WHEN OTHERS"


def test_a_bare_raise_rethrows_what_the_handler_caught():
    """It used to throw a new `MigratedException(0, "RAISE")`: another class, and SQLCODE 0."""
    text = java()
    assert "catch (MigratedException e2)" in text and "throw e2;" in text, "the inner handler has its own variable"
    assert "throw e;" in text


def test_zero_divide_reaches_its_handler():
    """Regression (#27-14): the helper threw ArithmeticException, so `catch (ZeroDivideException e)` was dead."""
    text = java()
    assert "catch (Plsql.ZeroDivide zero)" in text
    assert "throw new ZeroDivideException(zero.getMessage());" in text
    assert text.index("catch (Plsql.ZeroDivide zero)") < text.index("catch (ZeroDivideException e)")


def test_a_handler_the_target_never_reaches_says_so():
    assert "DUP_VAL_ON_INDEX: nothing on the target raises this by itself" in java()


def test_every_exception_a_handler_names_has_a_class():
    from plsql.gen_java.exception import collect
    from plsql.ir import model as M

    program = M.Program(id="p", kind="Program", modules=[lowered()])
    classes = {e.class_name for e in collect(program).codes.values()}
    assert {"EBadException", "ZeroDivideException", "DuplicateValueException", "NoDataFoundException"} <= classes


# --- 16: literals ------------------------------------------------------------------------------------------
def test_whitespace_inside_a_sql_literal_is_data():
    """Regression (#27-16): `" ".join(text.split())` turned 'X  Y' into 'X Y' -- another value."""
    sql = "UPDATE t\n   SET note = 'a   b\tc'\n WHERE status = 'X  Y'   AND  q = 'it''s  ok'"
    assert _escape(sql) == "UPDATE t SET note = 'a   b\\tc' WHERE status = 'X  Y' AND q = 'it''s  ok'"
    assert _escape('SELECT "Col" FROM t WHERE p = \'C:\\dir\'') == 'SELECT \\"Col\\" FROM t WHERE p = \'C:\\\\dir\''


def test_source_text_in_a_comment_cannot_become_code():
    """Regression (#27-39): javac translates `\\uXXXX` before it knows what a comment is."""
    file = JavaFile(package="x", name="C")
    file.comment("note = '\\u000a static { System.exit(1); } //' and C:\\users\\bob")
    assert "\\u000a" not in file.render() and "\\users" not in file.render()
    assert "\\ u000a" in file.render()


# --- 17: stale files ---------------------------------------------------------------------------------------
def test_only_files_the_generator_owns_are_removed(tmp_path):
    """Regression (#27-17): every `.java` the run did not write was deleted -- hand-written ones too."""
    from plsql.gen_java.emit import HANDOVER_HEADER, HEADER

    root = tmp_path / "src" / "main" / "java"
    (root / "com" / "acme").mkdir(parents=True)
    files = {
        "Current.java": HEADER.format(source="a.pkb") + "class Current {}",
        "Stale.java": HEADER.format(source="old.pkb") + "class Stale {}",
        "Handed.java": HANDOVER_HEADER.format(source="a.pkb", date="2026-09-19") + "class Handed {}",
        "Hand.java": "package com.acme;\nclass Hand {}",
    }
    for name, text in files.items():
        (root / "com" / "acme" / name).write_text(text, encoding="utf-8")
    removed = _remove_stale(root, {(root / "com" / "acme" / "Current.java").resolve()})
    assert [p.name for p in removed] == ["Stale.java"]
    assert sorted(p.name for p in (root / "com" / "acme").iterdir()) == ["Current.java", "Hand.java", "Handed.java"]


# ---- review #27, 17a / 17b ---------------------------------------------------------------------------------

CONSTRAINED = """CREATE OR REPLACE PACKAGE BODY pkg_c AS
  PROCEDURE fit(p_id NUMBER) IS
    v_rate NUMBER(5,2) := 1.005;
    v_code VARCHAR2(3);
    v_name VARCHAR2(10 CHAR);
    v_free NUMBER;
    v_pad  CHAR(4);
  BEGIN
    v_rate := p_id / 3;
    v_code := 'abcd';
    v_name := 'x';
    v_free := p_id / 3;
    v_pad := 'a';
    UPDATE t SET n = 1 WHERE id = p_id;
    SELECT name INTO v_name FROM t WHERE id = p_id;
    IF SQL%ROWCOUNT = 0 THEN
      v_code := NULL;
    END IF;
  EXCEPTION
    WHEN VALUE_ERROR THEN
      v_code := 'E';
  END fit;
END pkg_c;
"""


def test_a_constrained_declaration_constrains_what_is_assigned_to_it():
    """`NUMBER(5,2)` rounds and `VARCHAR2(3)` refuses: BigDecimal and String on their own do neither."""
    text = java(CONSTRAINED)
    assert re.search(r"BigDecimal vRate = Plsql\.fit\(.+, 5, 2\);", text), "the initial value too"
    assert re.search(r"vRate = Plsql\.fit\(.+, 5, 2\);", text)
    assert 'vCode = Plsql.fit("abcd", 3, false);' in text
    assert 'vName = Plsql.fit("x", 10, true);' in text
    assert "vCode = null;" in text, "NULL fits anywhere"
    assert not re.search(r"vFree = Plsql\.fit|vPad = Plsql\.fit", text), "no constraint, or CHAR's padding rule"


def test_value_error_from_a_size_error_reaches_its_handler():
    text = java(CONSTRAINED)
    assert "throw new ValueErrorException(size.getMessage());" in text
    assert text.index("catch (Plsql.ValueError size)") < text.index("catch (ValueErrorException e)")
    assert "only the size errors of a constrained declaration" in text


def test_select_into_sets_rowcount_when_the_routine_reads_it(tmp_path):
    """`SQL%ROWCOUNT` after a SELECT INTO read the count of the UPDATE before it."""
    from plsql.report import analyse

    (tmp_path / "pkg_c.pkb").write_text(CONSTRAINED, encoding="utf-8")
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE t (id NUMBER(9) PRIMARY KEY, n NUMBER(9), name VARCHAR2(10));\n", encoding="utf-8")
    (tmp_path / "scalardb.json").write_text(json.dumps({"ns.t": {
        "transaction": True, "partition-key": ["id"], "clustering-key": [],
        "columns": {"id": "INT", "n": "INT", "name": "TEXT"}}}), encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "pkg_c.pkb").rename(src / "pkg_c.pkb")
    module = analyse(src, tmp_path / "schema.sql", scalardb_schema=tmp_path / "scalardb.json").program.modules[0]
    text = generate_module(module, APP, INFRA, DOMAIN).file.render()
    select = text.index("vName = Plsql.fit(", text.index("rowCount = repository."))
    assert select < text.index("rowCount = 1;") < text.index("Plsql.eq(rowCount")
    assert "rowCount = 1;" not in java(), "not emitted where nothing reads SQL%ROWCOUNT"
