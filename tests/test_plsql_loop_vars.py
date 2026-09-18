"""#10: a cursor FOR loop's `r.xxx` is the loop's row, not a column of the statement's table.

`FOR r IN (SELECT order_id FROM orders ...) LOOP` binds `r`, and the generated Java is already holding that row
as a value before any statement in the body runs. Reading `r.order_id` as a column is what made
`WHERE order_id = r.order_id` a column-to-column comparison ScalarDB refuses, and what kept `TO_CHAR(r.order_id)`
from being lifted out of the SQL (P4-4). The tests here fix both, and the two limits that keep it honest: a
qualifier that is not a loop variable is still a column, and the variable is not in scope inside the query that
binds it.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from plsql.gen_java.repository import generate_module as generate_repository
from plsql.gen_java.service import generate_module
from plsql.lower import _walk, walk_scoped
from plsql.report import analyse as build_analysis

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
SCALARDB = FIXTURES / "scalardb-schema.json"


@pytest.fixture(scope="module")
def corpus():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=SCALARDB)


def statements(corpus):
    return [s for _, routine in corpus.routines()
            for s in _walk(routine.body) + [x for h in routine.exception_handlers for x in _walk(h.body)]]


def test_a_loop_row_in_a_nested_blocks_handler_is_a_bind_too(corpus):
    """The handler of a `BEGIN ... EXCEPTION ... END` inside the loop is inside the loop (#18).

    While it was hoisted onto the routine this was the one `r.order_id` #10 could not reach.
    """
    insert = at_line(corpus, "prc_nightly_close.prc", 26)
    assert "r.order_id" in [b.plsql_variable for b in insert.binds]
    assert "TO_CHAR(r.order_id)" in [b.expression for b in insert.binds if b.expression]
    assert [d.code for d in insert.diagnostics if d.severity == "ERROR"] == []


def by_id(corpus, sql_id: str):
    return next(s for s in statements(corpus) if s.id == sql_id)


def at_line(corpus, file: str, line: int, kind: str | None = None):
    """A statement by where it is written, not by its id: an id moves when the lowering changes (#18).

    1 行に 2 文あることがある——#12 で trigger を掛けるようになってから、書き込みの前に `:OLD` を
    読む文が**同じ行として**挟まる。どちらが欲しいかは `kind` で言う。
    """
    return next(s for s in statements(corpus)
                if s.source_range and s.source_range.file == file and s.source_range.start_line == line
                and (kind is None or (s.sql_kind or "").upper() == kind))


# --- the walk that carries the scope ------------------------------------------------------------------

def test_the_loop_variable_is_in_scope_for_the_body_and_not_for_its_own_query(corpus):
    """The query is what binds `r`; inside it, `r` is not a name yet."""
    routine = next(r for _, r in corpus.routines() if r.id == "prc_nightly_close")
    scoped = {}
    for statement, loops in walk_scoped(routine.body):
        if statement.source_range:
            scoped[statement.source_range.start_line] = sorted(loops)
    assert scoped[11] == []      # the loop's own query, which is what binds `r`
    assert scoped[14] == ["r"]   # UPDATE ... WHERE order_id = r.order_id
    assert scoped[26] == ["r"]   # the INSERT in the nested block's handler, still inside the loop (#18)
    assert scoped[8] == []       # before the loop


def test_walk_scoped_visits_the_same_statements_as_walk(corpus):
    """The scope is added to the walk, not substituted for it: `capability.check` relies on both orders."""
    for _, routine in corpus.routines():
        assert [s.id for s, _ in walk_scoped(routine.body)] == [s.id for s in _walk(routine.body)]


# --- what it fixes ------------------------------------------------------------------------------------

def test_a_loop_row_reference_in_a_where_clause_becomes_a_bind(corpus):
    """`WHERE order_id = r.order_id` was COL_COL: two columns compared, which ScalarDB refuses."""
    update = at_line(corpus, "prc_nightly_close.prc", 14, kind="UPDATE")
    assert update.target_status == "OK"
    assert [b.plsql_variable for b in update.binds] == ["r.order_id"]
    assert update.target_sql == ["UPDATE orders SET status = 'CLOSED' WHERE order_id = :r_order_id"]
    assert not [d for d in update.diagnostics if d.code == "COL_COL"]


def test_the_bind_is_typed_from_the_query_the_loop_iterates(corpus):
    """Not from the DDL: the row record is generated from the query's select list, and the two must agree."""
    update = at_line(corpus, "prc_nightly_close.prc", 14, kind="UPDATE")
    bind = update.binds[0]
    assert bind.oracle_type == "NUMBER(19)"     # what `SELECT order_id FROM orders` declared
    assert bind.column == "order_id" and bind.scalardb_type == "BIGINT"


def test_an_expression_over_a_loop_row_is_lifted_into_the_application(corpus):
    """`TO_CHAR(r.order_id)` is computable in Java once `r.order_id` is a value rather than a column."""
    insert = at_line(corpus, "prc_nightly_close.prc", 15)
    lifted = [b for b in insert.binds if b.expression]
    assert "TO_CHAR(r.order_id)" in [b.expression for b in lifted]
    # `USER` used to stop this statement as well; it is now the caller's argument too (#1)
    assert "USER" in [b.expression for b in lifted]
    assert [d.code for d in insert.diagnostics if d.severity == "ERROR"] == []


def test_the_corpus_has_no_column_to_column_comparison_left(corpus):
    codes = [d.code for s in statements(corpus) for d in s.diagnostics]
    assert "COL_COL" not in codes


# --- the limits ---------------------------------------------------------------------------------------

def test_a_qualifier_that_is_not_a_loop_variable_is_still_a_column(corpus):
    """`o.order_id` in a join is a real column; rewriting it to a bind would change what the query means."""
    joined = [s for s in statements(corpus)
              if s.kind == "SqlOperation" and " JOIN " in (s.original_sql or "").upper()]
    assert joined, "the corpus has a join"
    for statement in joined:
        assert not [b for b in statement.binds if b.plsql_variable and "." in b.plsql_variable]


# --- end to end ---------------------------------------------------------------------------------------

LOOP_SOURCE = """\
CREATE OR REPLACE PROCEDURE prc_loop_note(p_status IN VARCHAR2) IS
BEGIN
  FOR r IN (SELECT order_id FROM orders WHERE status = p_status) LOOP
    UPDATE customers SET note = TO_CHAR(r.order_id) WHERE customer_id = r.order_id;
  END LOOP;
END prc_loop_note;
/
"""


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """One routine whose loop body both binds and computes over the loop row, generated end to end.

    It writes a table its own query does not read, which is the one shape the cursor generator accepts
    (P4-5) -- the corpus routines that carry `r.xxx` are all stopped for other reasons, so without this the
    Java side of #10 would never be exercised.
    """
    root = tmp_path_factory.mktemp("loopvars")
    (root / "schema.sql").write_text((SRC / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "prc_loop_note.prc").write_text(textwrap.dedent(LOOP_SOURCE), encoding="utf-8")
    analysis = build_analysis(root, root / "schema.sql", scalardb_schema=SCALARDB)
    module = next(m for m in analysis.program.modules if m.name == "prc_loop_note")
    return (generate_module(module, "g.app", "g.infra", "g.domain"),
            generate_repository(module, "g.infra", "g.domain"))


def test_the_service_passes_the_loop_rows_accessor(generated):
    """`r.order_id` is not a Java name: the row record's component is, and that is what must be passed.

    One argument, not two: both references are the same value, so they share one bind (`_unique`).
    """
    java = generated[0].file.render()
    assert "for (PrcLoopNoteLoop1Row r : repository.prcLoopNoteLoop1(pStatus))" in java
    assert "repository.prcLoopNoteStmt2(r.orderId())" in java
    assert not generated[0].untranslated


def test_the_repository_computes_the_lifted_expression_from_that_parameter(generated):
    """The lifted `TO_CHAR(r.order_id)` is computed in the repository, from its parameter.

    The repository has no row record to read; it has the one value the service passed. Rendering the lift as
    `r.orderId()` there would be a call on a name that does not exist.
    """
    java = generated[1].file.render()
    assert "public int prcLoopNoteStmt2(BigDecimal rOrderId)" in java
    assert "Plsql.text(rOrderId)" in java


def test_a_loop_row_parameter_is_typed_like_the_record_it_comes_out_of(corpus):
    """`r.qty` is a NUMBER in PL/SQL, and the loop record says so; a parameter typed from the column's own
    width (`NUMBER(10)` -> Long) is one the caller cannot pass `r.qty()` to. The generated tree caught this as
    a compile error; this catches it here.
    """
    from plsql.gen_java.dto import loop_component_type
    from plsql.gen_java.types import java_type

    checked = 0
    for _, routine in corpus.routines():
        for statement, loops in walk_scoped(routine.body):
            for bind in getattr(statement, "binds", None) or []:
                head = (bind.plsql_variable or "").split(".")[0].lower()
                if bind.expression or head not in loops:
                    continue
                loop = loops[head]
                field = bind.plsql_variable.split(".", 1)[1].lower()
                columns = list(zip(loop.query.into_columns or [], loop.query.into_oracle_types or []))
                declared = next(t for name, t in columns if (name or "").lower() == field)
                assert java_type(bind.oracle_type) == java_type(declared)
                assert loop_component_type(bind.oracle_type).name == loop_component_type(declared).name
                checked += 1
    assert checked, "the corpus has a loop-row bind"
