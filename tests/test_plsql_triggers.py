"""#12: 移行先に trigger は無いので、**書き込む側が呼ぶ**。

決定は #12 §0（網羅性は塞がずに検証で追う）で、ここは実装である。固定するのは 3 つ:

* **掛かるべきところに掛かる**（同じ表・同じ操作・`UPDATE OF` の列）
* **掛からないところには掛からない**（列が違う / 1 行に絞れない / 値そのものを変える trigger）
* **掛からなかった書き込みに掛けない**（0 行の更新では 1 度も発火しない）

3 つめは実際に壊した: `:OLD` を無条件に読んだために、更新する行が無いときの `SQL%ROWCOUNT = 0` が
別の失敗（NO_DATA_FOUND）に化けた（`mark_shipped`）。比較が捕まえたので、ここに置いてある。
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


def statements(corpus, routine_id):
    routine = next(r for _, r in corpus.routines() if r.id == routine_id)
    return routine, _walk(routine.body)


def codes(statement) -> list[str]:
    return [d.code for d in statement.diagnostics]


def test_the_registry_knows_when_each_trigger_fires(corpus):
    from plsql.triggers import registry

    found = registry(corpus.program)
    orders = {t.module.name: t for t in found["orders"]}
    assert orders["trg_orders_audit"].timing == "AFTER"
    assert orders["trg_orders_audit"].columns == ["status"], "`UPDATE OF status` の列が落ちている"
    assert found["payments"][0].module.name == "trg_payments_guard"


def test_a_write_to_the_table_calls_the_trigger(corpus):
    """`UPDATE orders SET status = ...` は `trg_orders_audit` が掛かる書き込みである。"""
    _, body = statements(corpus, "prc_nightly_close")
    call = next(s for s in body if s.kind == "Call")
    assert call.resolved_to == "trg_orders_audit.body"
    assert "TRIGGER_CALL" in codes(call)


def test_the_old_values_are_read_before_the_write(corpus):
    """`:OLD` は更新の**前に**読む。後では元の値が無い（trigger-patterns A-1）。"""
    _, body = statements(corpus, "pkg_shipment.mark_shipped")
    kinds = [(s.kind, (getattr(s, "sql_kind", "") or "")) for s in body
             if s.kind in ("SqlOperation", "Call")]
    read = next(i for i, (kind, sql) in enumerate(kinds) if sql == "SELECT")
    write = next(i for i, (kind, sql) in enumerate(kinds) if sql == "UPDATE")
    call = next(i for i, (kind, _) in enumerate(kinds) if kind == "Call")
    assert read < write < call, "AFTER trigger なのに、読み・書き・呼び出しの順になっていない"


def test_the_old_read_does_not_raise_when_there_is_no_row(corpus):
    """更新する行が無ければ trigger は掛からない。**「無い」は例外ではなく答えである**。

    無条件に `SELECT INTO` として読んだために、`mark_shipped` の「注文が無い」（`SQL%ROWCOUNT = 0`
    で -20071）が NO_DATA_FOUND に化けた。比較がそれを捕まえた。
    """
    _, body = statements(corpus, "pkg_shipment.mark_shipped")
    read = next(s for s in body if s.kind == "SqlOperation" and (s.sql_kind or "") == "SELECT")
    assert read.cardinality == "AT_MOST_ONE"
    assert read.not_found_flag, "行が無かったことを本体が知る手段が無い"


def test_the_call_only_happens_when_a_row_was_written(corpus):
    """AFTER は `SQL%ROWCOUNT`、BEFORE は `:OLD` が見つかったかで決まる。"""
    _, body = statements(corpus, "pkg_shipment.mark_shipped")
    guard = next(s for s in body if s.kind == "If" and any(c.kind == "Call" for c in s.branches[0].body))
    assert guard.branches[0].condition == "SQL%ROWCOUNT > 0"


def test_a_before_trigger_runs_before_the_write(corpus):
    """`trg_payments_guard` は値を拒否する trigger である。書いた後で拒否しても遅い。"""
    _, body = statements(corpus, "pkg_payment.record_payment")
    order = [s.kind for s in body if s.kind in ("Call", "SqlOperation")]
    call = order.index("Call")
    insert = [i for i, s in enumerate([s for s in body if s.kind in ("Call", "SqlOperation")])
              if getattr(s, "sql_kind", "") == "INSERT"][0]
    assert call < insert


def test_a_write_that_does_not_touch_the_listed_column_is_untouched(corpus):
    """`UPDATE OF status` は status を SET していない更新には掛からない。掛けると**記録が増える**。"""
    _, body = statements(corpus, "pkg_order_report.mark_reviewed")
    assert not [s for s in body if s.kind == "Call"], "note だけの更新に監査 trigger が掛かっている"


def test_a_write_to_another_table_is_untouched(corpus):
    """`UPDATE products SET stock_qty` に `trg_products_audit`（`UPDATE OF unit_price`）は掛からない。"""
    _, body = statements(corpus, "pkg_stock_reserve.reserve")
    assert not [s for s in body if s.kind == "Call"]


def test_a_trigger_that_rewrites_the_row_is_refused(corpus):
    """採番 trigger（`:NEW.order_id := seq.NEXTVAL`）は呼び出しでは置き換えられない。

    値を書き換える trigger は「書き込まれる行そのもの」を変える。移行先では呼び出し側が値を
    持っているので、これは採番 Service への**再設計**である（trigger-patterns C）。
    """
    from plsql.triggers import registry

    trigger = next(t for t in registry(corpus.program)["orders"]
                   if t.module.name == "trg_orders_seq")
    assert trigger.assigns_correlation()


def test_the_trigger_body_is_callable(corpus):
    """呼べない本体は、掛からない trigger と同じである。"""
    routine = next(r for _, r in corpus.routines() if r.id == "trg_orders_audit.body")
    assert routine.visibility == "public"


def test_the_arguments_are_named_so_the_two_sides_cannot_drift(corpus):
    """引数は名前で持つ。並びを決めるのは呼ばれる側の signature で、呼ぶ側がそれを決め打つと、
    片方が変わったときに静かにずれる。"""
    _, body = statements(corpus, "prc_nightly_close")
    call = next(s for s in body if s.kind == "Call")
    assert sorted(a.split("=>")[0].strip() for a in call.arguments) == \
        ["NEW.order_id", "NEW.status", "OLD.status"]
    assert "'CLOSED'" in " ".join(call.arguments), "書き込む値が :NEW として渡っていない"


def test_the_generated_service_receives_the_trigger_it_calls(corpus):
    """誰が呼んでいるかが constructor に出る。**掛かるのはこの経路だけ**という事実がそこに見える。"""
    from plsql.gen_java.service import generate_module as generate

    # 境界を割っていない module を使う（#24 の分割は `--limits` の決定が要る）
    module = next(m for m in corpus.program.modules if m.name == "pkg_shipment")
    java = generate(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    assert "private final TrgOrdersAuditService trgOrdersAudit;" in java
    assert "trgOrdersAudit.body(" in java
    assert "public PkgShipmentService(PkgShipmentRepository repository, "
    assert "TrgOrdersAuditService trgOrdersAudit)" in java


def test_the_write_path_inherits_the_triggers_verdict(corpus):
    """生成できることと、移してよいことは別である。trigger が REDESIGN なら、それを呼ぶ経路も
    その判断を引き継ぐ——網羅性（#12 §0）はその経路の設計だからである。"""
    from plsql.analysis import analyse as analyse_program
    from plsql.rules.engine import Evidence, RuleSet, decide

    decisions = decide(corpus.program, analyse_program(corpus.program), RuleSet.load(), Evidence())
    decision = decisions["pkg_shipment.mark_shipped"]
    assert decision.rule_verdict == "REDESIGN"
    assert any("trg_orders_audit" in reason for reason in decision.reasons)
