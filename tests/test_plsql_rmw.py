"""#9: `UPDATE t SET c = c + :x` を「同じトランザクションの中で読んでから書く」へ移す。

Oracle のこの形は**行ロックの下で原子的**である。ScalarDB SQL は列を読む式を受け付けないので、
置き換えるなら読みを明示するしかない。安全なのは同じトランザクションの中で読んで書くからで、
衝突は Consensus Commit が弾く（P3-4 実測）。だから **A 型と同じ決定が要る**——記録された
routine だけを書き換える。

同じ行を 1 つのトランザクションが 2 回触っても更新が失われないことは `RmwIT` が実クラスタで
測ってある（自分の書き込みは読める）。ここでは書き換えの形と、**書き換えない条件**を固定する。
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.limits import RowLocks
from plsql.lower import _walk
from plsql.report import analyse as build_analysis

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
CONFIG = FIXTURES / "limits.yaml"


@pytest.fixture(scope="module")
def corpus():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json",
                          row_locks=RowLocks.load(CONFIG))


@pytest.fixture(scope="module")
def undecided():
    """記録を渡さずに解析したもの。決めていない世界では何も書き換わらない。"""
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")


def statements(analysis, routine_id):
    routine = next(r for _, r in analysis.routines() if r.id == routine_id)
    return routine, [s for s in _walk(routine.body) if s.kind == "SqlOperation"]


def test_the_column_is_read_before_it_is_written(corpus):
    routine, sql = statements(corpus, "pkg_order_lock.cancel")
    read = next(s for s in sql if s.original_sql.startswith("SELECT stock_qty"))
    write = next(s for s in sql if s.original_sql.startswith("UPDATE products"))
    assert read.into_targets == ["v_rmw_1"]
    assert "v_rmw_1" in write.original_sql and "stock_qty +" not in write.original_sql
    assert sql.index(read) < sql.index(write), "読みが書きより後ろにある"


def test_both_statements_are_runnable_on_the_target(corpus):
    _, sql = statements(corpus, "pkg_order_lock.cancel")
    products = [s for s in sql if "products" in (s.original_sql or "")]
    assert [s.target_status for s in products] == ["OK", "OK"]


def test_the_variable_it_introduced_is_declared_and_typed(corpus):
    """宣言しないと生成コードが宣言していない変数へ代入する。型が無いと代入がコンパイルできない。"""
    routine, _ = statements(corpus, "pkg_order_lock.cancel")
    declaration = next(d for d in routine.declarations if d.name == "v_rmw_1")
    assert declaration.type.resolved == "NUMBER(10)"


def test_the_split_says_why(corpus):
    _, sql = statements(corpus, "pkg_order_lock.cancel")
    write = next(s for s in sql if s.original_sql.startswith("UPDATE products"))
    assert any(d.code == "RMW_SPLIT" for d in write.diagnostics)


def test_nothing_is_split_without_a_record(undecided):
    """決めた人がいない routine は書き換えない。ロックが落ちたままの読み書きは進めない。"""
    routine, sql = statements(undecided, "pkg_order_lock.cancel")
    assert not [d for d in routine.declarations if d.name.startswith("v_rmw_")]
    write = next(s for s in sql if s.original_sql.startswith("UPDATE products"))
    assert write.target_status == "ERROR", "決めていないのに通っている"


def test_a_routine_that_was_never_locked_is_untouched(corpus):
    """`restock` の FORALL も `SET stock_qty = stock_qty + ...` だが、記録が無いので触らない。"""
    routine, _ = statements(corpus, "pkg_bulk_load.restock")
    assert not [d for d in routine.declarations if d.name.startswith("v_rmw_")]


def test_the_counter_routine_is_recorded_and_converts(corpus):
    """D 型（採番）は方式が既に決まっている（計画 §9: NOCACHE → counters 表 + 再試行）。
    `next_payment_id` はまさにその形——読む・+1 する・書く——なので、記録は既存の決定に従う。"""
    _, sql = statements(corpus, "pkg_stock_reserve.next_payment_id")
    assert [s.target_status for s in sql] == ["WARN", "OK"]
    # `+1` はアプリで計算して bind で渡す。ScalarDB へ渡る SQL に式は残らない
    update = sql[1]
    assert update.target_sql == \
        ["UPDATE counters SET next_value = :expr2 WHERE counter_name = 'PAYMENT_ID'"]
    assert [b.expression for b in update.binds if b.expression] == ["v_next + 1"]


def test_a_handler_for_an_error_that_cannot_happen_is_not_emitted(corpus):
    """`PRAGMA EXCEPTION_INIT(e_locked, -54)` は「行ロックが取れない」で、**移行先では起こらない**
    （ScalarDB は待たないのが既定で、衝突は commit で分かる）。

    `catch` を出すと `MigratedException` を広く捕まえ、**関係のない業務例外まで「ロックされている」に
    付け替える**。Oracle では他の例外は素通りしていたので、出さないほうが元に近い（#9 §B）。
    """
    from plsql.gen_java.service import generate_module

    module = next(m for m in corpus.program.modules if m.name == "pkg_stock_reserve")
    java = generate_module(module, "g.app", "g.infra", "g.domain").file.render()
    body = java[java.index("public void reserveNowait"):]
    body = body[:body.index("\n    public", 1)] if "\n    public" in body[1:] else body
    assert "catch (MigratedException e)" not in body
    assert "try {" not in body, "catch が無い try は Java にならない"
    assert "-20031" not in body, "起こらない誤りの付け替えが残っている"
    assert "この handler は出さない" in body


def test_the_exception_is_bound_to_the_oracle_error_it_caught(corpus):
    """番号で判断する。名前で判断すると、同じ名前の別の例外に当たる。"""
    routine = next(r for _, r in corpus.routines() if r.id == "pkg_stock_reserve.reserve_nowait")
    declared = next(d for d in routine.declarations if d.declaration_kind == "exception")
    assert (declared.name, declared.initial) == ("e_locked", "-54")
