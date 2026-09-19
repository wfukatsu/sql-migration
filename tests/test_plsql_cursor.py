"""P4-5: the cursor FOR loop, generated rather than refused.

Oracle's cursor is a position held across the transaction and ScalarDB has none, so the rows are read before
the loop runs. That is a different thing, and the tests here fix the two places it matters: the loop's query is
a statement like any other, and a body that writes what its own query reads is refused rather than reordered.
"""

from __future__ import annotations

import pytest

from plsql.lower import _walk
from plsql.report import analyse

SRC = "fixtures/plsql/src"
DDL = "fixtures/plsql/src/schema.sql"
SCALARDB = "fixtures/plsql/scalardb-schema.json"


@pytest.fixture(scope="module")
def corpus():
    return analyse(SRC, DDL, scalardb_schema=SCALARDB)


def loops(corpus):
    return [(routine, s) for _, routine in corpus.routines()
            for s in _walk(routine.body) + [x for h in routine.exception_handlers for x in _walk(h.body)]
            if s.kind == "Loop" and (s.loop_kind or "") == "cursor-for"]


def test_an_inline_cursor_for_loop_carries_its_query_as_a_statement(corpus):
    """Before P4-5 the query existed only as text, which is why the loop could not be generated at all."""
    inline = [(r, s) for r, s in loops(corpus) if s.query is not None]
    assert inline, "no cursor FOR loop carried a query"
    for _, loop in inline:
        assert loop.query.kind == "SqlOperation"
        assert loop.query.original_sql.upper().startswith("SELECT")
        assert loop.variable, "the loop variable is what the body reads columns from"


def test_the_loop_query_goes_through_the_converter_like_any_other(corpus):
    for _, loop in [(r, s) for r, s in loops(corpus) if s.query is not None]:
        assert loop.query.target_status in {"OK", "WARN", "PLANNED", "ERROR"}
        assert loop.query.into_columns, "the select list is what the row record is built from"


def test_the_loop_query_binds_the_routines_variables(corpus):
    """`WHERE order_id = p_order_id` binds the parameter, not a column of that name."""
    by_routine = {r.id: s for r, s in loops(corpus) if s.query is not None}
    total = by_routine["pkg_order_pricing.order_total"]
    assert [b.plsql_variable for b in total.query.binds] == ["p_order_id"]


def test_a_named_cursor_loop_gets_the_query_from_its_declaration(corpus):
    """`FOR r IN c` names a cursor declared elsewhere; the query is resolved out of the declaration (#11).

    Before that, the loop arrived with nothing to convert and was refused for the shape of its header rather
    than for anything about what it does.
    """
    named = [(r, s) for r, s in loops(corpus)
             if s.cursor and "(" not in s.cursor.split(" IN ", 1)[-1]]
    assert named, "the corpus has a named-cursor FOR loop"
    routine, loop = next((r, s) for r, s in named if r.id == "pkg_stock_reserve.claim_batch")
    assert loop.query is not None
    assert loop.query.original_sql.upper().startswith("SELECT ORDER_ID FROM ORDERS")
    # the lock is written in the declaration, and comes with it: it is why this routine is a redesign
    assert loop.query.locking_mode == "FOR UPDATE SKIP LOCKED"
    assert any(d.code == "ROW_LOCK" for d in loop.query.diagnostics)


# ---------------------------------------------------------------- generation
def generated(name: str) -> str:
    from pathlib import Path
    return Path("generated/src/main/java/com/example/migrated/application", name).read_text(encoding="utf-8")


@pytest.mark.skipif(not __import__("pathlib").Path("generated/src/main/java").is_dir(),
                    reason="run `python -m plsql.generate fixtures/plsql/src --out-dir generated` first")
def test_a_read_only_loop_is_generated_as_a_java_for(corpus):
    source = generated("PkgOrderPricingService.java")
    assert "for (OrderTotalLoop1Row r : repository.orderTotalLoop1(pOrderId))" in source
    assert "r.qty()" in source and "r.unitPrice()" in source


@pytest.mark.skipif(not __import__("pathlib").Path("generated/src/main/java").is_dir(),
                    reason="needs the generated tree")
def test_the_generated_loop_says_the_rows_were_read_first(corpus):
    """The difference from Oracle is written where the reader is, not only in the documentation."""
    assert "read before the loop runs" in generated("PkgOrderPricingService.java")


@pytest.mark.skipif(not __import__("pathlib").Path("generated/src/main/java").is_dir(),
                    reason="needs the generated tree")
def test_a_loop_writing_what_its_query_reads_reads_first_and_says_why(corpus):
    """パターン D は先に読む形で移す（2026-09-19 / #20 の決定 A）。以前は一律に拒否していた。

    Oracle の cursor は OPEN の時点で読み取りが一貫しているので、先に全部読む形と回す行が同じであり、
    読むのは書くより前の 1 回だけなので「同じトランザクションで書いた物の走査」にも当たらない。
    拒否が残るのは、ループより前に同じ表を書いている routine である（test_plsql_claims が見る）。
    """
    source = generated("PkgOrderReportService.java")
    assert "cursor FOR loop whose body writes" not in source
    # 2026-09-19 / #19: mark_reviewed はさらに 1 注文 = 1 トランザクションへ割ったので、対象は別の
    # トランザクションで読まれ、同じ表を書く問題そのものが無くなった
    assert "markReviewedOne(" in source


@pytest.mark.skipif(not __import__("pathlib").Path("generated/src/main/java").is_dir(),
                    reason="needs the generated tree")
def test_the_loop_row_record_holds_numbers_as_bigdecimal(corpus):
    """It models the PL/SQL loop variable, not the column: `r.qty` is a NUMBER passed to NUMBER parameters."""
    from pathlib import Path
    record = Path("generated/src/main/java/com/example/migrated/domain/OrderTotalLoop1Row.java")
    assert record.exists()
    assert "BigDecimal qty" in record.read_text(encoding="utf-8")


def test_the_generator_leaves_no_file_from_an_earlier_run():
    """A renamed class used to leave its old file behind, and javac compiled both."""
    from pathlib import Path
    names = {p.stem for p in Path("generated/src/main/java").rglob("*.java")}
    for name in names:
        assert name[:1].isupper(), name
    assert not any(n.lower() == m.lower() and n != m for n in names for m in names), \
        "two generated files differ only in case; one is stale"
