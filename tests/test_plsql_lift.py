"""P4-4: lifting an expression out of SQL so the application evaluates it.

ScalarDB SQL takes literals and bind markers and nothing else. An expression whose operands are all PL/SQL
variables does not need the database to compute it, so it is lifted into a bind and evaluated in Java.

What matters here is the boundary. Lifting too much turns a clear conversion error into Java that does not
compile, or into a read the original never made.
"""

from __future__ import annotations

import pytest
import sqlglot

from plsql.ir.model import BindVariable
from plsql.report import analyse
from plsql.sqlbridge import lift_expressions

SRC = "fixtures/plsql/src"
DDL = "fixtures/plsql/src/schema.sql"
SCALARDB = "fixtures/plsql/scalardb-schema.json"


def lift(sql: str, variables: list[str] | None = None):
    """Parse, stand in for `bind_variables` by binding the named variables, then lift."""
    tree = sqlglot.parse_one(sql, read="oracle")
    binds = []
    for name in variables or []:
        binds.append(BindVariable(name=name, direction="IN", plsql_variable=name))
        for column in list(tree.find_all(sqlglot.exp.Column)):
            if not column.table and column.name.lower() == name.lower():
                column.replace(sqlglot.exp.Placeholder(this=name))
    lift_expressions(tree, binds, "scope", None)
    return tree, binds


def lifted(binds):
    return {b.name: b.expression for b in binds if b.expression}


# ---------------------------------------------------------------- what is lifted
def test_an_arithmetic_set_expression_is_lifted():
    tree, binds = lift("UPDATE counters SET next_value = v_next + 1 WHERE counter_name = 'X'", ["v_next"])
    assert list(lifted(binds).values()) == ["v_next + 1"]
    assert ":expr" in tree.sql(dialect="oracle")


def test_a_function_call_in_values_is_lifted():
    tree, binds = lift("INSERT INTO customers (customer_id, tier) VALUES (p_id, NVL(p_tier, 'BRONZE'))",
                       ["p_id", "p_tier"])
    assert list(lifted(binds).values()) == ["NVL(p_tier, 'BRONZE')"]


def test_a_clock_read_is_lifted():
    """SYSDATE is a value the application can produce; ScalarDB refuses it outright."""
    _, binds = lift("UPDATE batch_control SET last_run_at = SYSDATE WHERE batch_name = 'X'")
    assert list(lifted(binds).values()) == ["SYSDATE"]


def test_a_nested_expression_is_lifted_whole():
    _, binds = lift("UPDATE orders SET total_amount = ROUND(v_total * p_rate, 2) WHERE order_id = p_id",
                    ["v_total", "p_rate", "p_id"])
    assert list(lifted(binds).values()) == ["ROUND(v_total * p_rate, 2)"]


def test_the_lifted_expression_reads_as_the_plsql_it_came_from():
    """Not `ROUND(:v_total * :p_rate, 2)`: that is what the bridge made of it, not what the routine wrote."""
    _, binds = lift("UPDATE orders SET total_amount = ROUND(v_total * p_rate, 2) WHERE order_id = p_id",
                    ["v_total", "p_rate", "p_id"])
    expression = next(iter(lifted(binds).values()))
    assert ":" not in expression


# ---------------------------------------------------------------- what is not
def test_an_expression_mentioning_a_column_is_not_lifted():
    """`SET stock_qty = stock_qty + :n` needs the stored value; reading it first is a different race."""
    _, binds = lift("UPDATE products SET stock_qty = stock_qty + p_delta WHERE product_id = p_id",
                    ["p_delta", "p_id"])
    assert lifted(binds) == {}


def test_an_unimplemented_function_is_not_lifted():
    """Lifting it would replace a clear conversion error with Java that does not compile."""
    _, binds = lift("UPDATE orders SET note = SYS_CONNECT_BY_PATH(p_note, '/') WHERE order_id = p_id",
                    ["p_note", "p_id"])
    assert lifted(binds) == {}


def test_a_subquery_is_not_lifted():
    _, binds = lift("UPDATE orders SET total_amount = (SELECT SUM(amount) FROM payments) "
                    "WHERE order_id = p_id", ["p_id"])
    assert lifted(binds) == {}


def test_a_plain_variable_is_left_as_the_bind_it_already_is():
    _, binds = lift("UPDATE orders SET status = p_status WHERE order_id = p_id", ["p_status", "p_id"])
    assert lifted(binds) == {}


def test_a_literal_is_not_lifted():
    _, binds = lift("UPDATE orders SET status = 'CLOSED' WHERE order_id = p_id", ["p_id"])
    assert lifted(binds) == {}


def test_the_column_free_side_of_a_predicate_is_lifted():
    """**述語はデータベースが評価する。持ち上げるのは、列を読まない側の値だけ**（2026-09-19 に変えた）。

    以前は WHERE を持ち上げの場所にしていなかった。だが `ordered_at < SYSDATE - 30` の右辺は、
    データベースが持っている値を 1 つも読まない——SET / VALUES の式と同じく、アプリで計算して
    1 つの値として渡せる。比べるのは相変わらずデータベースである。持ち上げないと ScalarDB は式を
    受け付けず、計画に回っても H2 が日時の算術で止まった（`prc_purge_audit`）。

    時計の出所は SET / VALUES と同じく呼び出し側になる（#8）。
    """
    _, binds = lift("UPDATE orders SET status = 'X' WHERE ordered_at < SYSDATE - 30")
    assert lifted(binds) == {"expr1": "SYSDATE - 30"}


def test_the_side_of_a_predicate_that_reads_a_column_is_not_lifted():
    """列を読む側は持ち上げない。値を持っているのはデータベースであってアプリではない。"""
    _, binds = lift("UPDATE orders SET status = 'X' WHERE ordered_at + 1 < SYSDATE")
    assert "ordered_at + 1" not in lifted(binds).values()


# ---------------------------------------------------------------- on the corpus
@pytest.fixture(scope="module")
def corpus():
    return analyse(SRC, DDL, scalardb_schema=SCALARDB)


def test_lifting_reduced_what_scalardb_refuses(corpus):
    """P4-4's point: 21 statements ScalarDB refused, before lifting.

    Not lower, because lifting is deliberately suppressed in routines whose read carried a row lock -- there
    the refusal is the last thing standing between a dropped `FOR UPDATE` and a silent read-modify-write.
    """
    statuses = corpus.capability.counts()
    assert statuses.get("ERROR", 0) <= 16, statuses


def test_a_routine_whose_lock_was_dropped_does_not_get_its_write_rewritten(corpus):
    """`pkg_stock_reserve.reserve` keeps refusing, so the lost lock stays visible at the call site."""
    from plsql.lower import _walk

    for _, routine in corpus.routines():
        if not routine.id.startswith("pkg_stock_reserve"):
            continue
        for statement in _walk(routine.body):
            for bind in getattr(statement, "binds", None) or []:
                assert not bind.expression, f"{routine.id} had an expression lifted out of a locked read"


def test_every_lifted_expression_translates_to_java(corpus):
    """A lifted expression the translator cannot place would be a compile error instead of a refusal."""
    from plsql.gen_java.expr import translate
    from plsql.gen_java.types import java_name
    from plsql.lower import _walk

    unplaceable = []
    for _, routine in corpus.routines():
        for statement in _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]:
            binds = getattr(statement, "binds", None) or []
            scope = {b.plsql_variable or b.name: java_name(b.name) for b in binds if not b.expression}
            for bind in binds:
                if not bind.expression:
                    continue
                rendered = translate(bind.expression, scope)
                if rendered.unknown:
                    unplaceable.append((routine.id, bind.expression, sorted(rendered.unknown)))
    # the trigger correlation names (:NEW / :OLD) are refused elsewhere and are the documented exception
    assert all(any(n.startswith(":") for n in names) for _, _, names in unplaceable), unplaceable
