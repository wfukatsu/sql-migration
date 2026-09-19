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


def test_an_unlocked_scan_then_write_now_reads_first(corpus):
    """ロックの無い cursor（`mark_reviewed`）も、#20 の決定 A で先に読む形へ移した。Oracle の cursor は
    OPEN の時点で読み取りが一貫しているので、回す行はロックが無くても同じである。"""
    from plsql.gen_java.service import generate_module

    module = next(m for m in corpus.program.modules if m.name == "pkg_order_report")
    java = generate_module(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    body = java[java.index("public void markReviewed("):].split("\n    }")[0]
    assert "for (MarkReviewedLoop" in body and "UnsupportedOperationException" not in body


def test_the_order_nobody_reads_is_dropped(corpus):
    """全行に同じことをするだけなので、どの順で回しても残る行は同じ。Oracle で順序が効くのはロックを
    取る順だけで、移行先に行ロックは無い。落とすと `status` の索引で読める。"""
    routine = next(r for _, r in corpus.routines() if r.id == "pkg_order_report.mark_reviewed")
    loop = next(s for s in _walk(routine.body) if s.kind == "Loop")
    assert "ORDER BY" not in loop.query.original_sql
    assert any(d.code == "ORDER_DROPPED" for d in loop.query.diagnostics)
    assert loop.query.target_status == "OK"


WRITES_FIRST = """\
CREATE OR REPLACE PROCEDURE prc_touch_then_scan(p_status IN VARCHAR2) IS
BEGIN
  UPDATE orders SET note = 'start' WHERE order_id = 1;
  FOR r IN (SELECT order_id FROM orders WHERE status = p_status) LOOP
    UPDATE orders SET note = 'reviewed' WHERE order_id = r.order_id;
  END LOOP;
END prc_touch_then_scan;
/
"""


def _generate_one(tmp_path, name: str, source: str) -> str:
    from plsql.analysis import analyse as analyse_program
    from plsql.gen_java.service import generate_module

    root = tmp_path / "src"
    root.mkdir()
    (root / "schema.sql").write_text((SRC / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
    (root / f"{name}.prc").write_text(source, encoding="utf-8")
    analysis = build_analysis(root, root / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    analyse_program(analysis.program)
    module = next(m for m in analysis.program.modules if m.name == name)
    return generate_module(module, "g.app", "g.infra", "g.domain", analysis.program).file.render()


def test_a_routine_that_wrote_the_table_before_the_loop_is_still_refused(tmp_path):
    """ループより前に同じ表を書いていたら、走査は「同じトランザクションで書いた物の走査」になり、
    ScalarDB が拒否する（P2-4 / DB-CORE-10106）。これは実行時に落ちるので、生成の時点で拒む。"""
    java = _generate_one(tmp_path, "prc_touch_then_scan", WRITES_FIRST)
    assert "which its own query reads" in java


@pytest.mark.parametrize("body,why", [
    ("UPDATE orders SET note = v_last WHERE order_id = r.order_id; v_last := r.order_id;",
     "前の反復が残した値を読む"),
    ("UPDATE orders SET note = 'x' WHERE customer_id = 1;", "読んだ行を主キーで指していない"),
    ("UPDATE orders SET note = 'x' WHERE order_id = r.order_id; EXIT;", "途中で抜ける"),
])
def test_an_order_that_could_matter_is_kept(tmp_path, body, why):
    source = f"""\
CREATE OR REPLACE PROCEDURE prc_ordered(p_status IN VARCHAR2) IS
  v_last VARCHAR2(40);
BEGIN
  FOR r IN (SELECT order_id FROM orders WHERE status = p_status ORDER BY ordered_at) LOOP
    {body}
  END LOOP;
END prc_ordered;
/
"""
    root = tmp_path / "src"
    root.mkdir()
    (root / "schema.sql").write_text((SRC / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "prc_ordered.prc").write_text(source, encoding="utf-8")
    analysis = build_analysis(root, root / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    routine = next(r for _, r in analysis.routines() if r.id == "prc_ordered")
    loop = next(s for s in _walk(routine.body) if s.kind == "Loop")
    assert "ORDER BY" in loop.query.original_sql, why
