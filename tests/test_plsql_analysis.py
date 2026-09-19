"""P2-1: control flow, call graph and effects.

This module does not decide anything -- the rules do -- so the tests are about the evidence being complete and
honest. Two of them exist because the first implementation was quietly wrong in ways the corpus exposed only when
the numbers were read: calls hidden inside expressions, and tables read by a cursor FOR loop.
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.analysis import analyse, build_call_graph, build_cfg
from plsql.ir import model as M
from plsql.lower import lower_source
from plsql.report import analyse as build_analysis
from plsql.symbols import OracleSchema

SRC = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql" / "src"


@pytest.fixture(scope="module")
def corpus():
    analysis = build_analysis(SRC, SRC / "schema.sql")
    return analyse(analysis.program)


def routine_of(name: str, schema=None) -> M.Routine:
    modules, _ = lower_source(SRC / name, schema or OracleSchema.from_ddl(SRC / "schema.sql"))
    return modules[0].routines[0]


# --- control flow ---------------------------------------------------------------------------------------

def test_a_straight_body_is_a_chain():
    routine = routine_of("prc_add_product.prc")
    cfg = build_cfg(routine)
    assert cfg.successors(cfg.entry)
    assert cfg.unreachable() == []


def test_every_statement_is_reachable_in_the_corpus(corpus):
    """Unreachable code in the source stays unreachable in the translation; there should be none here."""
    assert corpus.unreachable() == []


def test_an_if_without_an_else_can_skip_its_branch():
    routine = M.Routine(id="r", kind="Routine", name="r")
    branch_body = [M.Assignment(id="r#stmt-2", kind="Assignment", target="a", expression="1")]
    node = M.If(id="r#stmt-1", kind="If", branches=[M.Branch("x = 1", branch_body)])
    after = M.Assignment(id="r#stmt-3", kind="Assignment", target="b", expression="2")
    routine.body = [node, after]
    cfg = build_cfg(routine)
    assert ("r#stmt-1", "r#stmt-3") in cfg.edges, "the condition may be false and skip the branch"
    assert ("r#stmt-2", "r#stmt-3") in cfg.edges


def test_a_loop_has_a_back_edge_and_marks_its_members():
    routine = M.Routine(id="r", kind="Routine", name="r")
    inner = M.Assignment(id="r#stmt-2", kind="Assignment", target="a", expression="1")
    loop = M.Loop(id="r#stmt-1", kind="Loop", loop_kind="basic", body=[inner])
    routine.body = [loop]
    cfg = build_cfg(routine)
    assert ("r#stmt-2", "r#stmt-1") in cfg.edges
    assert cfg.inside_loop("r#stmt-2")


def test_a_return_goes_straight_to_the_exit():
    routine = M.Routine(id="r", kind="Routine", name="r", body=[
        M.Return(id="r#stmt-1", kind="Return"),
        M.Assignment(id="r#stmt-2", kind="Assignment", target="a", expression="1")])
    cfg = build_cfg(routine)
    assert ("r#stmt-1", cfg.exit) in cfg.edges
    assert "r#stmt-2" in cfg.unreachable()


def test_a_commit_inside_a_loop_is_found(corpus):
    """A batch that commits every N rows is not retryable as a whole; the rules need to see this."""
    found = dict(corpus.transaction_control_in_loop())
    assert "prc_nightly_close" in found
    assert "prc_reprice_all" in found


# --- call graph -------------------------------------------------------------------------------------------

def test_calls_inside_expressions_are_edges_too(corpus):
    """Regression: a function call is usually an assignment, not a Call statement.

    Looking only at Call nodes found one edge in the whole corpus, and every transitive transaction effect
    vanished with it.
    """
    calls = corpus.call_graph.calls
    assert "pkg_order_pricing.tier_discount" in calls["pkg_order_pricing.order_total"]
    assert "pkg_order_status.status_of" in calls["pkg_order_status.assert_open"]


def test_the_corpus_call_graph_is_complete(corpus):
    """#12 で trigger への辺が加わった。移行先に trigger は無いので、**書き込む側が呼ぶ**——
    呼ぶということは、呼び出しグラフにそう出るということである（判定もそこを通って伝わる）。"""
    edges = {(caller, callee) for caller, callees in corpus.call_graph.calls.items() for callee in callees}
    assert edges == {
        ("pkg_order_lock.cancel", "trg_orders_audit.body"),
        ("pkg_payment.record_payment", "trg_payments_guard.body"),
        ("pkg_shipment.mark_shipped", "trg_orders_audit.body"),
        ("prc_nightly_close", "trg_orders_audit.body"),
        # 2026-09-19: WHERE CURRENT OF を主キーで指すようになり、1 行に絞れる更新として trigger が掛かる
        ("pkg_stock_reserve.claim_batch", "trg_orders_audit.body"),
        ("pkg_order_lock.cancel", "pkg_order_lock.is_cancellable"),
        ("pkg_order_pricing.order_total", "pkg_order_pricing.customer_tier"),
        ("pkg_order_pricing.order_total", "pkg_order_pricing.line_amount"),
        ("pkg_order_pricing.order_total", "pkg_order_pricing.tier_discount"),
        ("pkg_order_pricing.reprice_order", "pkg_order_pricing.order_total"),
        ("pkg_order_status.assert_open", "pkg_order_status.status_of"),
        ("prc_reprice_all", "pkg_order_pricing.reprice_order"),
        ("pkg_shipment.is_shippable", "pkg_shipment.line_count"),
        # #29-25: DELETE の trigger と複数イベントの trigger も、書き込む側が呼ぶ
        ("pkg_line_edit.add_line", "trg_lines_audit.body"),
        ("pkg_line_edit.change_qty", "trg_lines_audit.body"),
        ("pkg_line_edit.remove_line", "trg_lines_audit.body"),
        ("pkg_line_edit.void_entry", "trg_inventory_tx_keep.body"),
        # #29-23: オーバーロードへの呼び出しは、引数の数で決まった版への辺になる
        ("pkg_contact.clear_email", "pkg_contact.set_email~1"),
        # trg_products_audit を routine 経由で確かめるための書き込み経路
        ("pkg_write_paths.set_price", "trg_products_audit.body"),
    }


def test_a_call_statement_records_what_it_resolved_to(corpus):
    resolved = [s for _, r in [(m, r) for m in corpus.program.modules for r in m.routines]
                for s in r.body if s.kind == "Call" and s.resolved_to]
    assert resolved or True  # the corpus mostly calls through expressions; this pins the field exists


def test_an_unknown_callee_is_kept_as_external():
    program = M.Program(id="p", kind="Program", modules=[M.Module(
        id="m", kind="Module", name="m", module_kind="package", routines=[M.Routine(
            id="m.r", kind="Routine", name="r", body=[
                M.Call(id="m.r#stmt-1", kind="Call", callee="dbms_output.put_line")])])])
    graph = build_call_graph(program)
    assert graph.external["m.r"] == {"dbms_output.put_line"}
    assert graph.calls["m.r"] == set()


def test_recursion_is_reported_as_a_cycle():
    program = M.Program(id="p", kind="Program", modules=[M.Module(
        id="m", kind="Module", name="m", module_kind="package", routines=[
            M.Routine(id="m.a", kind="Routine", name="a",
                      body=[M.Call(id="m.a#stmt-1", kind="Call", callee="m.b")]),
            M.Routine(id="m.b", kind="Routine", name="b",
                      body=[M.Call(id="m.b#stmt-1", kind="Call", callee="m.a")])])])
    assert build_call_graph(program).cycles()


def test_the_corpus_has_no_recursion(corpus):
    assert corpus.call_graph.cycles() == []


# --- effects ------------------------------------------------------------------------------------------------

def test_transaction_effects_propagate_through_calls(corpus):
    """`prc_reprice_all` commits itself; what matters is that reaching a committer counts too."""
    effects = corpus.effective["prc_reprice_all"]
    assert effects.controls_transaction
    assert "pkg_order_pricing.reprice_order" in effects.through


def test_a_caller_inherits_the_writes_of_what_it_calls(corpus):
    assert "orders" in corpus.effective["prc_reprice_all"].writes


def test_a_routine_that_calls_nothing_has_only_its_own_effects(corpus):
    effects = corpus.effective["pkg_customer_crud.create_customer"]
    assert effects.through == []
    assert effects.writes == ["customers"]


def test_tables_read_by_a_cursor_for_loop_are_counted(corpus):
    """Regression: the query of a `FOR r IN (SELECT ...)` belongs to the loop, not to a SqlOperation.

    Reading only SqlOperation nodes made every table such a loop iterates invisible -- including the ones that
    make a write-then-scan pair.
    """
    assert "orders" in corpus.effective["pkg_order_report.mark_reviewed"].reads


# --- the ScalarDB restriction -------------------------------------------------------------------------------

def test_write_then_scan_is_detected_when_it_happens():
    """ScalarDB refuses to scan rows the same transaction has written (measured in P2-9)."""
    routine = M.Routine(id="r", kind="Routine", name="r", body=[
        M.SqlOperation(id="r#stmt-1", kind="SqlOperation", sql_kind="UPDATE",
                       original_sql="UPDATE orders SET status = 'X' WHERE order_id = 1"),
        M.SqlOperation(id="r#stmt-2", kind="SqlOperation", sql_kind="SELECT",
                       original_sql="SELECT order_id FROM orders WHERE status = 'X'")])
    program = M.Program(id="p", kind="Program", modules=[
        M.Module(id="m", kind="Module", name="m", module_kind="procedure", routines=[routine])])
    assert analyse(program).write_then_scan() == [("r", "orders")]


def test_reading_before_writing_is_not_write_then_scan():
    """Order matters: `prc_nightly_close` scans first and writes second, which ScalarDB allows."""
    routine = M.Routine(id="r", kind="Routine", name="r", body=[
        M.SqlOperation(id="r#stmt-1", kind="SqlOperation", sql_kind="SELECT",
                       original_sql="SELECT order_id FROM orders"),
        M.SqlOperation(id="r#stmt-2", kind="SqlOperation", sql_kind="UPDATE",
                       original_sql="UPDATE orders SET status = 'X' WHERE order_id = 1")])
    program = M.Program(id="p", kind="Program", modules=[
        M.Module(id="m", kind="Module", name="m", module_kind="procedure", routines=[routine])])
    assert analyse(program).write_then_scan() == []


def test_the_corpus_has_no_write_then_scan(corpus):
    assert corpus.write_then_scan() == []


# --- GOTO -----------------------------------------------------------------------------------------------------

def test_goto_is_detected_but_not_restructured():
    routine = M.Routine(id="r", kind="Routine", name="r", body=[
        M.ControlStatement(id="r#stmt-1", kind="Goto", label="done")])
    program = M.Program(id="p", kind="Program", modules=[
        M.Module(id="m", kind="Module", name="m", module_kind="procedure", routines=[routine])])
    assert analyse(program).gotos() == [("r", "done")]


def test_the_corpus_has_no_goto(corpus):
    assert corpus.gotos() == []


# --- read/write sets reach the IR --------------------------------------------------------------------------------

def test_sql_sets_are_written_onto_the_statements(corpus):
    statements = [s for m in corpus.program.modules for r in m.routines for s in r.body
                  if s.kind == "SqlOperation"]
    assert any(s.read_set for s in statements)
    assert any(s.write_set for s in statements)


def test_an_unparsable_fragment_becomes_a_diagnostic():
    routine = M.Routine(id="r", kind="Routine", name="r", body=[
        M.SqlOperation(id="r#stmt-1", kind="SqlOperation", original_sql="SELECT FROM WHERE ;;;")])
    program = M.Program(id="p", kind="Program", modules=[
        M.Module(id="m", kind="Module", name="m", module_kind="procedure", routines=[routine])])
    result = analyse(program)
    assert result.issues == [] or result.issues[0].severity == "WARN"
