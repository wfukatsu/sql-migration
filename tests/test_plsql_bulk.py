"""#14: `SELECT ... BULK COLLECT INTO` と、それを回す `FORALL` を 1 つの走査ループにする。

Oracle のこの形は「表から N 行を配列へ読み、その配列で N 回 DML する」である。書き換えないと
`BULK COLLECT INTO` を持つ SELECT は 1 行の `SELECT INTO` として扱われ、**0 件と複数件が元に無い
例外になる**（実測で `-1422 TOO_MANY_ROWS` が出ていた）。

ここで固定するのは、書き換えが**何を保つか**と、**何を書き換えないか**である。配列の使い方が
「i 番目の値を読む」以外なら、行を回す形は同じことをしない。
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from plsql.lower import _walk, lower_source
from plsql.symbols import OracleSchema

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def schema():
    return OracleSchema.from_ddl(SRC / "schema.sql")


@pytest.fixture(scope="module")
def archive_lines(schema):
    modules, _ = lower_source(SRC / "pkg_bulk_load.pkb", schema)
    return next(r for r in modules[0].routines if r.id.endswith("archive_lines"))


def lower(tmp_path, schema, source: str):
    path = tmp_path / "p.prc"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    modules, _ = lower_source(path, schema)
    return modules[0].routines[0]


# --- 書き換えるもの --------------------------------------------------------------------------------

def test_the_pair_becomes_one_loop_over_the_rows(archive_lines):
    loop = archive_lines.body[0]
    assert loop.kind == "Loop" and loop.loop_kind == "cursor-for" and loop.variable == "r"
    assert loop.query.original_sql == \
        "SELECT product_id, qty FROM order_lines WHERE order_id = p_order_id"
    assert "BULK COLLECT" not in loop.query.original_sql
    assert [s.kind for s in loop.body] == ["SqlOperation"]


def test_the_subscripted_collections_become_the_rows_columns(archive_lines):
    """`v_products(i)` は 1 行目の `product_id`、`v_qtys(i)` は 2 行目の `qty`——位置で対応する。"""
    insert = archive_lines.body[0].body[0].original_sql
    assert "r.product_id" in insert and "-r.qty" in insert
    assert "v_products" not in insert and "v_qtys" not in insert


def test_the_rewrite_says_what_it_changed(archive_lines):
    """FORALL は 1 往復、ループは行ごとに 1 回である。答えは変わらないが、性能は変わる。"""
    codes = [d.code for d in archive_lines.body[0].diagnostics]
    assert "BULK_CHUNKED" in codes


def test_the_statement_that_raised_too_many_rows_is_gone(archive_lines):
    """1 行の SELECT INTO として扱われていたので、2 行目があると -1422 になっていた。"""
    assert not [s for s in _walk(archive_lines.body)
                if s.kind == "SqlOperation" and "BULK COLLECT" in (s.original_sql or "")]


# --- 書き換えないもの ------------------------------------------------------------------------------

NOT_REWRITTEN = {
    "配列を回す FORALL が続かない": """\
        CREATE OR REPLACE PROCEDURE p(p_order_id IN NUMBER, p_first OUT NUMBER) IS
          TYPE t_qty IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
          v_qtys t_qty;
        BEGIN
          SELECT qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
          IF v_qtys.COUNT > 0 THEN p_first := v_qtys(1); END IF;
        END p;
        /
    """,
    "本体が配列そのものを読む": """\
        CREATE OR REPLACE PROCEDURE p(p_order_id IN NUMBER) IS
          TYPE t_qty IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
          v_qtys t_qty;
        BEGIN
          SELECT qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
          FORALL i IN 1 .. v_qtys.COUNT
            INSERT INTO inventory_tx (entry_id, product_id, delta_qty, reason, created_at)
            VALUES (1, 1, v_qtys.COUNT, 'X', SYSDATE);
        END p;
        /
    """,
    "添字が式である": """\
        CREATE OR REPLACE PROCEDURE p(p_order_id IN NUMBER) IS
          TYPE t_qty IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
          v_qtys t_qty;
        BEGIN
          SELECT qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
          FORALL i IN 1 .. v_qtys.COUNT
            INSERT INTO inventory_tx (entry_id, product_id, delta_qty, reason, created_at)
            VALUES (1, 1, v_qtys(i + 1), 'X', SYSDATE);
        END p;
        /
    """,
    "射影と配列の数が合わない": """\
        CREATE OR REPLACE PROCEDURE p(p_order_id IN NUMBER) IS
          TYPE t_qty IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
          v_qtys t_qty;
        BEGIN
          SELECT product_id, qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
          FORALL i IN 1 .. v_qtys.COUNT
            INSERT INTO inventory_tx (entry_id, product_id, delta_qty, reason, created_at)
            VALUES (1, 1, v_qtys(i), 'X', SYSDATE);
        END p;
        /
    """,
}


@pytest.mark.parametrize("why", sorted(NOT_REWRITTEN))
def test_a_shape_the_loop_would_not_reproduce_is_left_alone(tmp_path, schema, why):
    """残せば `BULK-001` が捕まえ、生成器が拒否する——黙って違うことをするより良い。"""
    routine = lower(tmp_path, schema, NOT_REWRITTEN[why])
    assert [s for s in _walk(routine.body)
            if s.kind == "SqlOperation" and "BULK COLLECT" in (s.original_sql or "")], \
        "書き換えてはいけない形を書き換えている"


def test_save_exceptions_is_not_this_rewrites_business(schema):
    """`FORALL ... SAVE EXCEPTIONS` は部分失敗を許す原子性の話で、業務要件である（BULK-002）。"""
    modules, _ = lower_source(SRC / "pkg_bulk_load.pkb", schema)
    restock = next(r for r in modules[0].routines if r.id.endswith("restock"))
    assert [s for s in _walk(restock.body) if s.kind == "Loop" and s.loop_kind == "forall"]
