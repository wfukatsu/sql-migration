"""A REDESIGN says what the source is. Whether the redesign has been *decided*, and whether the decided form
agrees with Oracle, is a second fact -- and the report used to show neither (decided 2026-09-20)."""

from __future__ import annotations

import pytest

from plsql import redesign
from plsql.analysis import analyse as analyse_program
from plsql.report import analyse
from plsql.rules.engine import Evidence, RuleSet, decide

SRC = "fixtures/plsql/src"
LIMITS = "fixtures/plsql/limits.yaml"


@pytest.fixture(scope="module")
def found():
    decided = redesign.Decided.load(LIMITS)
    analysis = analyse(SRC, f"{SRC}/schema.sql", scalardb_schema="fixtures/plsql/scalardb-schema.json",
                       **decided.for_analysis())
    program_analysis = analyse_program(analysis.program)
    evidence = Evidence(captures={"pkg_stock_reserve.reserve": (2, 2), "pkg_shipment.mark_shipped": (2, 2),
                                  "pkg_order_lock.cancel": (1, 2), "pkg_payment.record_payment": (3, 3)})
    decisions = decide(analysis.program, program_analysis, RuleSet.load(), evidence)
    return redesign.statuses(analysis.program, decisions, program_analysis.call_graph, decided, evidence)


def test_only_a_redesign_has_a_status(found):
    assert "pkg_customer_crud.update_email" not in found
    assert all(s.state in redesign.STATES for s in found.values())


def test_a_decided_redesign_names_the_decision_and_keeps_its_verdict(found):
    status = found["pkg_stock_reserve.reserve"]
    assert status.state == "verified"
    [item] = status.decisions
    assert item["rule"] == "LOCK-001" and item["decidedBy"] == "limits.yaml: rowLocks.optimistic"
    assert "commit で弾かれる" in item["why"]
    assert status.evidence == (2, 2) and status.open == []


def test_a_decision_without_evidence_or_with_a_disagreement_is_not_verified(found):
    assert found["pkg_tier_admin.promote"].state == "decided", "decided, never compared"
    assert found["pkg_order_lock.cancel"].state == "decided", "compared, and one scenario disagreed"


def test_what_nobody_decided_says_which_rule_is_open(found):
    status = found["prc_remote_sync"]
    assert status.state == "undecided" and "LINK-001" in status.open
    # the sequence trigger: the way is decided (a counters table, plan §9) but nothing generates it yet
    assert found["trg_orders_seq.body"].state == "undecided" and found["trg_orders_seq.body"].open == ["TRG-001"]


def test_a_trigger_is_decided_by_the_project_and_verified_through_the_routines_that_call_it(found):
    body = found["trg_payments_guard.body"]
    assert body.decisions[0]["decidedBy"].startswith("#12")
    assert body.state == "verified" and body.through == ["pkg_payment.record_payment"]
    caller = found["pkg_payment.record_payment"]
    assert caller.state == "verified" and caller.decisions[0]["rule"] == "calls trg_payments_guard.body"
    # one caller that was compared and disagreed (cancel, above) keeps the trigger it calls from being verified
    assert found["trg_orders_audit.body"].state == "decided"


def test_the_counts_add_up(found):
    counts = redesign.counts(found)
    assert sum(counts.values()) == len(found) and set(counts) == set(redesign.STATES)
