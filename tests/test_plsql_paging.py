"""#19 の決定（2026-09-19）: 人が行数の上限を決めなくて済む形。

* 割った routine の処理対象は、**キー順に件数つきで繰り返し読む**。上限は「何行来うるか」ではなく
  「1 回に何行ずつ取るか」（運用の調整値）になる
* 問い合わせ自身が件数を絞っている走査には、上限の決定を求めない
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.columns import caps_its_rows
from plsql.ir import model as M
from plsql.limits import Boundaries, RowLocks
from plsql.report import analyse as build_analysis

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
CONFIG = FIXTURES / "limits.yaml"


@pytest.fixture(scope="module")
def corpus():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json",
                          row_locks=RowLocks.load(CONFIG), boundaries=Boundaries.load(CONFIG))


def loop_of(corpus, routine_id):
    routine = next(r for _, r in corpus.routines() if r.id == routine_id)
    return routine, next(s for s in routine.body if s.kind == "Loop")


@pytest.mark.parametrize("routine_id,key", [
    ("prc_nightly_close", "order_id"), ("prc_purge_audit", "audit_id"),
    ("prc_reprice_all", "order_id"), ("pkg_order_report.mark_reviewed", "order_id")])
def test_the_targets_are_read_page_by_page_in_key_order(corpus, routine_id, key):
    """処理すると対象から外れるもの（nightly_close / purge_audit）も外れないもの（reprice_all）も、
    キー順に先へ進める 1 つの形にする。外れないものを同じ問い合わせで繰り返すと、同じ行が返って終わらない。"""
    routine, loop = loop_of(corpus, routine_id)
    sql = loop.query.original_sql
    assert f"> p_after_key" in sql and "FETCH FIRST p_batch ROWS ONLY" in sql
    assert sql.rstrip().split(" ORDER BY ")[1].startswith(("order_id", "o.order_id", "audit_id"))
    assert loop.paged_key == key
    assert [p.name for p in routine.parameters][-2:] == ["p_after_key", "p_batch"]


def test_the_page_size_is_bound_as_an_integer(corpus):
    """`FETCH FIRST p_batch` の件数は識別子として持たれ、列の走査には現れない。見落とすと
    `LIMIT p_batch` がそのまま ScalarDB へ渡る（実際そうなっていた）。"""
    _, loop = loop_of(corpus, "prc_nightly_close")
    assert ("p_batch", "INT") in [(b.name, b.scalardb_type) for b in loop.query.binds]
    assert loop.query.target_sql[0].endswith("LIMIT :p_batch")


def test_a_routine_nobody_decided_to_split_is_not_paged():
    corpus = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    _, loop = loop_of(corpus, "prc_nightly_close")
    assert "p_after_key" not in loop.query.original_sql


@pytest.mark.parametrize("sql,capped", [
    ("SELECT a FROM t WHERE ROWNUM <= 5", True),
    ("SELECT a FROM t FETCH FIRST p_batch ROWS ONLY", True),
    ("SELECT a FROM t", False),
])
def test_a_query_that_caps_its_own_rows_is_recognised(sql, capped):
    """件数が変数でも、絞っていることに変わりはない。最初は `sqlglot` を import しておらず、広い
    except がその誤りを「絞っていない」に化けさせていた。"""
    operation = M.SqlOperation(id="q", kind="SqlOperation", original_sql=sql)
    assert caps_its_rows(operation) is capped


def test_a_converted_limit_counts_as_capping():
    """`ROWNUM <= :n` は変換で `LIMIT :n` になる。元の形では bind との比較にしか見えない。"""
    operation = M.SqlOperation(id="q", kind="SqlOperation", original_sql="SELECT a FROM t WHERE ROWNUM <= :n",
                               target_sql=["SELECT a FROM t LIMIT :n"])
    assert caps_its_rows(operation)


def test_the_generated_targets_start_from_the_smallest_key_and_refuse_a_bad_batch(tmp_path):
    from plsql.generate import main as generate

    generate([str(SRC), "--scalardb-schema", str(FIXTURES / "scalardb-schema.json"),
              "--limits", str(CONFIG), "--out-dir", str(tmp_path), "--quiet"])
    java = (tmp_path / "src/main/java/com/example/migrated/application/PrcNightlyCloseService.java"
            ).read_text(encoding="utf-8")
    assert "if (pAfterKey == null) pAfterKey = BigDecimal.valueOf(Long.MIN_VALUE);" in java, \
        "null のまま渡すと `key > NULL` が偽になり、1 件も返らない"
    assert "if (pBatch == null || pBatch < 1)" in java
    assert "public static BigDecimal prcNightlyCloseAfter(" in java


# --- #27-15: a key that is not unique in the result ---------------------------------------------------------
def _paged(select: str):
    import sqlglot

    from plsql.paging import _joins_keep_one_row_per_key
    from plsql.symbols import OracleSchema

    schema = OracleSchema.from_ddl("fixtures/plsql/src/schema.sql")
    return _joins_keep_one_row_per_key(sqlglot.parse_one(select, dialect="oracle"), schema)


def test_a_join_that_can_repeat_the_key_is_not_paged():
    """Regression: `orders JOIN order_lines` returns several rows per order_id. When a page ended inside one
    order, `order_id > :after` skipped the rest of its lines -- no error, just lines never processed."""
    assert _paged("SELECT o.order_id FROM orders o JOIN customers c ON c.customer_id = o.customer_id"), "N:1"
    assert not _paged("SELECT o.order_id, l.line_no FROM orders o JOIN order_lines l ON l.order_id = o.order_id")
    assert not _paged("SELECT o.order_id FROM orders o JOIN customers c ON c.tier = o.status"), "not on the key"
    assert not _paged("SELECT o.order_id FROM orders o, customers c WHERE c.customer_id = o.customer_id")
    assert not _paged("SELECT o.order_id FROM orders o JOIN (SELECT customer_id FROM customers) c "
                      "ON c.customer_id = o.customer_id")
