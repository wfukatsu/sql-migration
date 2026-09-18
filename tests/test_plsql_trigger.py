"""#12: trigger の `:NEW` / `:OLD` を Service 側へ落とす。

移行先に trigger は無い。trigger が読んでいた行は**呼び出し側が渡す**——`USER` について #1 が
出したのと同じ答えで、生成器が周囲から値を取りに行かないという一点で揃っている。

ここで固定するのは、**落としやすい 3 つ**（trigger-patterns A）である:

1. before と after の両方が渡ること
2. 発火条件（`WHEN`）が残ること——落とすと**記録される量が変わる**
3. `:NEW.x` への**代入**は翻訳しないこと——Java の引数に代入しても呼び出し側には返らない

網羅性（§0: この表へのすべての書き込みがこの method を通るか）は呼び出し側の設計であって、
生成器が保証できることではない。だから trigger は `TRG-001` で REDESIGN のままである。
"""

from __future__ import annotations

import pathlib

import pytest

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


@pytest.fixture(scope="module")
def audit_service(corpus):
    module = next(m for m in corpus.program.modules if m.name == "trg_orders_audit")
    return generate_service(module, "g.app", "g.infra", "g.domain").file.render()


# --- 行は呼び出し側から来る ----------------------------------------------------------------------

def test_both_the_before_and_the_after_row_are_parameters(audit_service):
    """`:OLD` を渡さない設計にすると、Service は更新前の値を**先に読む**必要がある。渡すなら渡す。"""
    assert "body(BigDecimal newOrderId, String newStatus, String oldStatus, AuditContext audit)" \
        in audit_service


def test_the_statement_runs_instead_of_being_refused(audit_service):
    """かつては `:NEW.order_id` が置けずに routine ごと拒否されていた。"""
    assert "repository.bodyStmt1(audit, oldStatus, newStatus, newOrderId)" in audit_service
    assert "UnsupportedOperationException" not in audit_service


def test_the_values_are_typed_from_the_table_the_trigger_is_on(corpus):
    """`orders.status` は VARCHAR2(20)。型は DDL から来る——渡す側と受ける側で食い違わないため。"""
    module = next(m for m in corpus.program.modules if m.name == "trg_orders_audit")
    insert = next(s for s in _walk(module.routines[0].body) if s.kind == "SqlOperation")
    types = {b.plsql_variable: b.oracle_type for b in insert.binds if b.plsql_variable}
    assert types["OLD.status"] == "VARCHAR2(20)" and types["NEW.order_id"] == "NUMBER(19)"


# --- 発火条件 --------------------------------------------------------------------------------------

def test_the_firing_condition_survives_into_the_ir(corpus):
    """`WHEN (OLD.status <> NEW.status)` は「変わったときだけ」である。落ちていた。"""
    module = next(m for m in corpus.program.modules if m.name == "trg_orders_audit")
    assert module.trigger_when == "OLD.status <> NEW.status"


def test_the_firing_condition_guards_the_body(audit_service):
    """無条件に記録すると量が変わる。番人は本体より前に立つ。"""
    assert "// WHEN (OLD.status <> NEW.status)" in audit_service
    assert "if (!(Plsql.ne(oldStatus, newStatus))) return;" in audit_service
    assert audit_service.index("Plsql.ne(oldStatus, newStatus)") < audit_service.index("bodyStmt1")


def test_a_trigger_without_a_when_has_no_guard(corpus):
    """条件が無いものに条件を付けない。"""
    modules, _ = lower_source(SRC / "holdout2" / "trg_payments_guard.trg",
                              OracleSchema.from_ddl(SRC / "schema.sql"))
    assert modules[0].trigger_when is None


# --- 書き換えてはいけないもの --------------------------------------------------------------------

def test_assigning_to_the_new_row_is_refused(corpus):
    """`:NEW.order_id := seq_order_id.NEXTVAL` は**これから書き込まれる行を書き換える**もので、
    Java の引数への代入では呼び出し側に返らない。名前としては解決できるので、黙って通る形だった。"""
    module = next(m for m in corpus.program.modules if m.name == "trg_orders_seq")
    java = generate_service(module, "g.app", "g.infra", "g.domain").file.render()
    assert "assignment to :NEW.order_id" in java
    assert "newOrderId =" not in java, "引数への代入は呼び出し側に返らない"


def test_the_trigger_local_is_declared(corpus):
    """trigger の `DECLARE` は落ちていた。生成コードが宣言していない変数へ代入していた。"""
    module = next(m for m in corpus.program.modules if m.name == "trg_payments_guard")
    assert [d.name for d in module.declarations] == ["v_status"]
    java = generate_service(module, "g.app", "g.infra", "g.domain").file.render()
    assert "vStatus = null;" in java


def test_a_trigger_is_still_a_redesign(corpus):
    """網羅性（§0）は呼び出し側の設計である。生成できることと、移してよいことは別である。"""
    from plsql.analysis import analyse as analyse_program
    from plsql.rules.engine import Evidence, RuleSet, decide

    decisions = decide(corpus.program, analyse_program(corpus.program), RuleSet.load(), Evidence())
    assert decisions["trg_orders_audit.body"].rule_verdict == "REDESIGN"
