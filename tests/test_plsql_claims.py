"""#9 C 型: `FOR UPDATE SKIP LOCKED` で取り合う `claim_batch` を楽観制御で移す（2026-09-19 の決定）。

担当者列は足さない。ScalarDB では担当者列を立てても「読んでから書く」ことに変わりはなく、同じ行を
取った 2 人の片方が commit で弾かれるのは同じである。`claim_batch` は取ること自体が 1 トランザクション
で完結するので、列が無くても二重取りは起きない。

動かすのに要ったのは 3 つで、どれも**行の指し方と数え方**の話である——行ロックを落とす判断は記録が持つ。
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.limits import RowLocks
from plsql.lower import _walk
from plsql.report import analyse as build_analysis

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def corpus():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json",
                          row_locks=RowLocks.load(FIXTURES / "limits.yaml"))


def claim(corpus):
    routine = next(r for _, r in corpus.routines() if r.id == "pkg_stock_reserve.claim_batch")
    loop = next(s for s in _walk(routine.body) if s.kind == "Loop")
    return routine, loop


def test_where_current_of_points_at_the_same_row_by_its_key(corpus):
    """`CURRENT OF` は「いま FETCH した行」。cursor が主キーを読んでいれば、その値で同じ行を指せる。"""
    _, loop = claim(corpus)
    update = next(s for s in loop.body if s.kind == "SqlOperation" and s.sql_kind == "UPDATE")
    assert update.original_sql == "UPDATE orders SET status = 'CLAIMED' WHERE order_id = r.order_id"
    assert any(d.code == "CURRENT_OF" for d in update.diagnostics)
    assert update.target_status == "OK"


def test_rownum_with_a_bind_becomes_a_limit(corpus):
    """件数は bind でも件数である。`<` は n-1 が要るので bind では作れず、拒否のまま。"""
    _, loop = claim(corpus)
    assert loop.query.target_sql == ["SELECT order_id FROM orders WHERE status = 'NEW' LIMIT :p_limit"]


def test_the_count_is_bound_as_an_integer(corpus):
    """列ではないが整数である。言わずに渡すと NUMBER が BigDecimal のまま届き、ドライバが拒否した。"""
    _, loop = claim(corpus)
    assert [(b.name, b.scalardb_type, b.column) for b in loop.query.binds] == [("p_limit", "INT", None)]


def test_the_lock_drop_is_the_recorded_decision(corpus):
    _, loop = claim(corpus)
    assert any(d.code == "OPTIMISTIC" for d in loop.query.diagnostics)


def test_the_locked_scan_then_write_is_generated(corpus):
    """Oracle も OPEN の時点で行をロックして集合を固定する。先に読む形と同じ行を回し、読むのは
    書くより前の 1 回だけなので、同じトランザクションで書いた物の走査（P2-4）にも当たらない。"""
    from plsql.gen_java.service import generate_module

    module = next(m for m in corpus.program.modules if m.name == "pkg_stock_reserve")
    java = generate_module(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    body = java[java.index("public void claimBatch("):].split("\n    }")[0]
    assert "for (ClaimBatchLoop" in body and "UnsupportedOperationException" not in body


def test_an_unlocked_scan_then_write_is_still_refused(corpus):
    """ロックが無い cursor（`mark_reviewed`）には上の理由が立たない。#20 で決めるまで拒否のまま。"""
    from plsql.gen_java.service import generate_module

    module = next(m for m in corpus.program.modules if m.name == "pkg_order_report")
    java = generate_module(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    body = java[java.index("public void markReviewed("):].split("\n    }")[0]
    assert "which its own query reads" in body
