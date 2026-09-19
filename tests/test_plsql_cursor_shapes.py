"""#11: the explicit-cursor shapes that are not a scan -- B (the first row) and C (counting).

`docs/plsql-cursor-patterns.md` B and C are not loops over rows; they are a query each. The tests here fix
what the rewrite has to keep, which is more than the SQL: the value the original wrote for "no row", the fact
that a failed FETCH leaves its targets alone, and that `COUNT` returning a row at zero matches is the same
answer the loop gave. They also fix what must *not* be rewritten, because a shape recognised too eagerly is a
silently different routine.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from plsql.gen_java.repository import generate_module as generate_repository
from plsql.gen_java.service import generate_module as generate_service
from plsql.lower import _walk, lower_source
from plsql.report import analyse as build_analysis
from plsql.symbols import OracleSchema

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
SCALARDB = FIXTURES / "scalardb-schema.json"


@pytest.fixture(scope="module")
def corpus():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=SCALARDB)


def routine(corpus, routine_id: str):
    return next(r for _, r in corpus.routines() if r.id == routine_id)


def kinds(routine) -> list[str]:
    return [s.kind for s in _walk(routine.body)]


# --- C. counting --------------------------------------------------------------------------------------

def test_a_counting_cursor_becomes_one_aggregate(corpus):
    counting = routine(corpus, "pkg_order_report.count_by_status")
    assert "OpenCursor" not in kinds(counting) and "Fetch" not in kinds(counting)
    query = next(s for s in _walk(counting.body) if s.kind == "SqlOperation")
    assert query.original_sql == "SELECT COUNT(*) FROM orders WHERE status = p_status"
    assert query.into_targets == ["p_count"]
    assert query.target_status == "OK"


def test_the_cursors_own_parameter_is_substituted_by_what_open_passed(corpus):
    """`OPEN c_open_orders(p_status)` -- the cursor's parameter is its own name, not the caller's."""
    query = next(s for s in _walk(routine(corpus, "pkg_order_report.count_by_status").body)
                 if s.kind == "SqlOperation")
    assert [b.plsql_variable for b in query.binds] == ["p_status"]
    assert query.target_sql == ["SELECT COUNT(*) FROM orders WHERE status = :p_status"]


def test_counting_says_why_no_no_data_found_is_introduced(corpus):
    """SEM-004: COUNT returns a row at zero matches, which is the answer the loop gave (it counted nothing)."""
    query = next(s for s in _walk(routine(corpus, "pkg_order_report.count_by_status").body)
                 if s.kind == "SqlOperation")
    assert any(d.code == "CUR_COUNT" for d in query.diagnostics)
    assert query.at_most_one_row, "an aggregate returns exactly one row; TOO_MANY_ROWS is unreachable"
    assert not [d for d in query.diagnostics if d.code == "MULTI_ROW_INTO"]


def test_the_isopen_guard_of_a_rewritten_cursor_goes_with_it(corpus):
    """`IF c%ISOPEN THEN CLOSE c; END IF;` guarded a cursor that no longer exists."""
    counting = routine(corpus, "pkg_order_report.count_by_status")
    handler = counting.exception_handlers[0]
    assert [s.kind for s in handler.body] == ["Raise"]


# --- B. the first row ---------------------------------------------------------------------------------

def test_a_first_row_cursor_becomes_a_bounded_query(corpus):
    first = routine(corpus, "pkg_order_report.largest_order")
    query = next(s for s in _walk(first.body) if s.kind == "SqlOperation")
    assert query.target_sql == ["SELECT total_amount FROM orders WHERE customer_id = :p_customer_id "
                               "ORDER BY total_amount DESC LIMIT 1"]
    assert query.cardinality == "AT_MOST_ONE"
    assert query.into_targets == ["v_amount"]
    assert query.not_found_flag == "c_amounts"


def test_the_not_found_branch_is_kept_where_it_was_written(corpus):
    """The value for "no row" is in the original. Losing it is the one way this rewrite can be wrong."""
    first = routine(corpus, "pkg_order_report.largest_order")
    branch = next(s for s in first.body if s.kind == "If")
    assert branch.branches[0].condition == "c_amounts%NOTFOUND"
    assert branch.branches[0].body[0].target == "v_amount"
    assert branch.branches[0].body[0].expression == "0"


def test_a_bounded_query_is_not_warned_about_too_many_rows(corpus):
    query = next(s for s in _walk(routine(corpus, "pkg_order_report.largest_order").body)
                 if s.kind == "SqlOperation")
    assert not [d for d in query.diagnostics if d.code == "MULTI_ROW_INTO"]


# --- what is not rewritten ----------------------------------------------------------------------------

NOT_A_SHAPE = {
    # MR !52: the shape matched, but the rewrite would not have asked the same question. Each of these is a
    # case where the recognised sequence is genuinely B or C and the rewrite is still declined -- what the
    # rewrite does not carry over decides it, not what the sequence looks like.
    "a cursor parameter shares its name with a column": """\
        CREATE OR REPLACE PROCEDURE p(p_want IN VARCHAR2, p_count OUT NUMBER) IS
          CURSOR c(status IN VARCHAR2) IS SELECT order_id FROM orders WHERE status = status;
          v NUMBER; 
        BEGIN
          p_count := 0;
          OPEN c(p_want);
          LOOP FETCH c INTO v; EXIT WHEN c%NOTFOUND; p_count := p_count + 1; END LOOP;
          CLOSE c;
        END;
        /
    """,
    "the loop fetches twice in one turn": """\
        CREATE OR REPLACE PROCEDURE p IS
          CURSOR c IS SELECT qty FROM order_lines;
          v NUMBER; w NUMBER; n NUMBER := 0;
        BEGIN
          OPEN c;
          LOOP FETCH c INTO v; EXIT WHEN c%NOTFOUND; FETCH c INTO w; n := n + v; END LOOP;
          CLOSE c;
        END;
        /
    """,
    "the loop leaves on something else": """\
        CREATE OR REPLACE PROCEDURE p IS
          CURSOR c IS SELECT qty FROM order_lines;
          v NUMBER; n NUMBER := 0;
        BEGIN
          OPEN c;
          LOOP FETCH c INTO v; EXIT WHEN n > 100; n := n + v; END LOOP;
          CLOSE c;
        END;
        /
    """,
    "the fetch is not followed by a close": """\
        CREATE OR REPLACE PROCEDURE p IS
          CURSOR c IS SELECT qty FROM order_lines;
          v NUMBER; w NUMBER;
        BEGIN
          OPEN c;
          FETCH c INTO v;
          FETCH c INTO w;
          CLOSE c;
        END;
        /
    """,
}


@pytest.mark.parametrize("why", sorted(NOT_A_SHAPE))
def test_a_sequence_that_is_not_one_of_the_shapes_is_left_alone(tmp_path, why):
    """Left as OPEN / FETCH / CLOSE, which the generator refuses -- visibly, rather than guessed at."""
    source = tmp_path / "p.prc"
    source.write_text(textwrap.dedent(NOT_A_SHAPE[why]), encoding="utf-8")
    modules, _ = lower_source(source, OracleSchema.from_ddl(SRC / "schema.sql"))
    body = _walk(modules[0].routines[0].body)
    assert [s.kind for s in body if s.kind in ("OpenCursor", "Fetch", "CloseCursor")][0] == "OpenCursor"
    assert "Fetch" in [s.kind for s in body]


def test_a_cursor_parameter_is_substituted_on_the_tree_not_in_the_text(tmp_path):
    """A qualified column keeps its name; only a bare name that is the parameter is replaced."""
    source = tmp_path / "p.prc"
    source.write_text(textwrap.dedent("""\
        CREATE OR REPLACE PROCEDURE p(p_want IN VARCHAR2, p_count OUT NUMBER) IS
          CURSOR c(p_status IN VARCHAR2) IS
            SELECT o.order_id FROM orders o WHERE o.status = p_status ORDER BY o.order_id;
          v NUMBER;
        BEGIN
          p_count := 0;
          OPEN c(p_want);
          LOOP FETCH c INTO v; EXIT WHEN c%NOTFOUND; p_count := p_count + 1; END LOOP;
          CLOSE c;
        END;
        /
    """), encoding="utf-8")
    modules, _ = lower_source(source, OracleSchema.from_ddl(SRC / "schema.sql"))
    query = next(s for s in _walk(modules[0].routines[0].body) if s.kind == "SqlOperation")
    assert query.original_sql == "SELECT COUNT(*) FROM orders o WHERE o.status = p_want"


CAPPED = """\
CREATE OR REPLACE PROCEDURE p(p_count OUT NUMBER) IS
  CURSOR c IS SELECT order_id FROM orders FETCH FIRST 5 ROWS ONLY;
  v_id NUMBER;
BEGIN
  p_count := 0;
  OPEN c;
  LOOP FETCH c INTO v_id; EXIT WHEN c%NOTFOUND; p_count := p_count + 1; END LOOP;
  CLOSE c;
END;
/
"""


def test_a_counting_cursor_whose_rows_are_capped_does_not_become_a_count(tmp_path):
    """`COUNT(*)` は 1 行返すので、`FETCH FIRST 5` が**何も上限しなくなる**——別の数になる。

    走査（A）としては書き換わる。そちらは上限を持ったままの問い合わせを回すので、数は変わらない。
    """
    source = tmp_path / "p.prc"
    source.write_text(textwrap.dedent(CAPPED), encoding="utf-8")
    modules, _ = lower_source(source, OracleSchema.from_ddl(SRC / "schema.sql"))
    body = _walk(modules[0].routines[0].body)
    query = next(s for s in body if s.kind == "SqlOperation")
    assert "COUNT(*)" not in query.original_sql
    assert "FETCH FIRST 5" in query.original_sql
    assert [s.loop_kind for s in body if s.kind == "Loop"] == ["cursor-for"]


# --- A. 走査（2026-09-18 / `prc_reprice_all`）----------------------------------------------------------

SCAN = """\
CREATE OR REPLACE PROCEDURE p(p_count OUT NUMBER, p_last OUT NUMBER) IS
  CURSOR c IS SELECT order_id FROM orders;
  v_id NUMBER;
BEGIN
  p_count := 0;
  OPEN c;
  LOOP FETCH c INTO v_id; EXIT WHEN c%NOTFOUND; p_count := p_count + 1; END LOOP;
  CLOSE c;
  p_last := v_id;
END;
/
"""


def _scan(tmp_path):
    source = tmp_path / "p.prc"
    source.write_text(textwrap.dedent(SCAN), encoding="utf-8")
    modules, _ = lower_source(source, OracleSchema.from_ddl(SRC / "schema.sql"))
    return _walk(modules[0].routines[0].body)


def test_a_scan_becomes_a_cursor_for_loop(tmp_path):
    """読むだけのループは cursor FOR ループと同じものである。本体が何をしていてもよい——
    「読んだ行で何かする」という形そのものだからである。"""
    body = _scan(tmp_path)
    assert not [s for s in body if s.kind in ("OpenCursor", "Fetch", "CloseCursor")]
    loop = next(s for s in body if s.kind == "Loop")
    assert loop.loop_kind == "cursor-for" and loop.query is not None


def test_the_fetch_targets_are_still_assigned(tmp_path):
    """参照を書き換えず、**行の値を元の変数へ代入する文を足す**。ループの後で読んでいる routine
    でも、Oracle と同じ値が入っている（最後の FETCH は空振りしても INTO を変えない）。"""
    body = _scan(tmp_path)
    loop = next(s for s in body if s.kind == "Loop")
    first = loop.body[0]
    assert (first.kind, first.target, first.expression) == ("Assignment", "v_id", "r.order_id")


def test_the_change_of_shape_is_recorded(tmp_path):
    """行を先に読む形になったことは、黙って起きてよい変化ではない（動く行数がメモリで決まる）。"""
    body = _scan(tmp_path)
    loop = next(s for s in body if s.kind == "Loop")
    assert [d.code for d in loop.diagnostics] == ["CUR_SCAN"]


# --- the Java -----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def generated(corpus):
    module = next(m for m in corpus.program.modules if m.name == "pkg_order_report")
    return (generate_service(module, "g.app", "g.infra", "g.domain").file.render(),
            generate_repository(module, "g.infra", "g.domain").file.render())


def test_the_count_is_read_straight_into_the_out_parameter(generated):
    service, repository = generated
    assert "pCount = Plsql.dec(repository.countByStatusStmt3(pStatus));" in service
    assert '"SELECT COUNT(*) FROM orders WHERE status = :p_status"' in repository


def test_the_first_row_read_answers_notfound_from_the_rows_absence(generated):
    """Not from the value: a row whose only column is NULL is a row, and `%NOTFOUND` must stay false."""
    service, repository = generated
    assert "boolean cAmountsNotFound = false;" in service
    assert "cAmountsNotFound = largestOrderStmt2Row == null;" in service
    assert "if (cAmountsNotFound) {" in service
    assert "return null;   // %NOTFOUND" in repository


def test_a_fetch_that_found_nothing_leaves_its_target_alone(generated):
    """Oracle leaves the INTO target as it was; writing null would be a different routine."""
    service, _ = generated
    assert "if (largestOrderStmt2Row != null) {" in service
