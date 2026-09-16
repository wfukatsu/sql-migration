"""P2-2: the rule engine.

The engine must never decide anything the rule files do not say, and the confidence must never be able to soften a
rule. Those two properties are what make a verdict reviewable, so most of these tests are about them rather than
about any individual rule.

KPI-3 (docs/plsql-kpi.md) lives at the end: the rule verdicts against `manifest.yaml`, with the AUTO prohibitions
allowed no false negatives at all.
"""

from __future__ import annotations

import pathlib
from collections import Counter

import pytest
import yaml

from plsql.analysis import analyse as analyse_program
from plsql.ir import model as M
from plsql.report import analyse as build_analysis
from plsql.rules.engine import (AUTO_THRESHOLD, Confidence, Evidence, Rule, RuleSet, decide)

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def corpus():
    analysis = build_analysis(SRC, SRC / "schema.sql")
    return analysis.program, analyse_program(analysis.program)


@pytest.fixture(scope="module")
def ruleset() -> RuleSet:
    return RuleSet.load()


@pytest.fixture(scope="module")
def decisions(corpus, ruleset):
    program, analysis = corpus
    return decide(program, analysis, ruleset, Evidence())


def expected_verdicts() -> dict[str, str]:
    manifest = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
    return {r["name"].lower(): r["expected"] for u in manifest["units"] for r in u["routines"]}


def matched(decisions, routine_id: str) -> set[str]:
    return {m.rule.id for m in decisions[routine_id].matches}


# --- the rule files are the judgement ---------------------------------------------------------------------

def test_rules_load_from_yaml_with_unique_ids(ruleset: RuleSet):
    assert len(ruleset.rules) >= 15
    assert len({r.id for r in ruleset.rules}) == len(ruleset.rules)


def test_a_rule_with_an_unknown_verdict_is_rejected():
    with pytest.raises(ValueError, match="decision must be one of"):
        Rule(id="X", decision="MAYBE", message="")


def test_duplicate_rule_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate rule ids"):
        RuleSet([Rule(id="X", decision="AUTO", message=""), Rule(id="X", decision="REVIEW", message="")])


def test_every_rule_carries_a_message_and_the_redesign_ones_carry_remediation(ruleset: RuleSet):
    for rule in ruleset.rules:
        assert rule.message.strip(), rule.id
        if rule.decision == "REDESIGN":
            assert rule.remediation, f"{rule.id} says REDESIGN without saying what to do instead"


def test_a_rule_with_an_unknown_match_key_never_fires(corpus):
    program, analysis = corpus
    rules = RuleSet([Rule(id="TYPO-1", decision="REDESIGN", message="", match={"noSuchKey": True})])
    assert all(not d.matches for d in decide(program, analysis, rules, Evidence()).values())


# --- confidence cannot soften a rule -----------------------------------------------------------------------

def test_a_redesign_rule_holds_whatever_the_confidence(corpus, ruleset):
    program, analysis = corpus
    perfect = Evidence(captures={r.id: (5, 5) for m in program.modules for r in m.routines})
    decisions = decide(program, analysis, ruleset, perfect)
    assert decisions["prc_nightly_close"].verdict == "REDESIGN"
    assert decisions["prc_nightly_close"].confidence.test_evidence == 1.0


def test_a_review_rule_is_not_raised_to_auto_by_confidence(corpus, ruleset):
    program, analysis = corpus
    perfect = Evidence(captures={r.id: (5, 5) for m in program.modules for r in m.routines})
    decisions = decide(program, analysis, ruleset, perfect)
    assert decisions["pkg_money_calc.rounded_total"].verdict == "REVIEW"


def test_the_threshold_sits_where_five_nearly_perfect_factors_land():
    """docs/plsql-kpi.md picks 0.95 because 0.99^5 is 0.951: every side has to be nearly perfect."""
    assert Confidence(0.99, 0.99, 0.99, 0.99, 0.99).value == pytest.approx(0.951, abs=0.001)
    assert Confidence(0.99, 0.99, 0.99, 0.99, 0.99).value >= AUTO_THRESHOLD
    assert Confidence(0.95, 0.99, 0.99, 0.99, 0.99).value < AUTO_THRESHOLD, "one weak side is enough to stop AUTO"
    assert Confidence(1.0, 1.0, 1.0, 1.0, 1.0).value >= AUTO_THRESHOLD


def test_any_factor_at_zero_makes_the_product_zero():
    assert Confidence(1.0, 1.0, 1.0, 1.0, 0.0).value == 0
    assert Confidence(1.0, 1.0, 1.0, 1.0, 0.0).zeros() == ["testEvidence"]


def test_a_routine_with_no_capture_cannot_be_auto(corpus, ruleset):
    """P0-5 found four private routines with no capture at all. This is why they stay out of AUTO."""
    program, analysis = corpus
    decisions = decide(program, analysis, ruleset, Evidence())
    clean = decisions["pkg_customer_crud.delete_customer"]
    assert clean.rule_verdict == "AUTO", "no rule objects to this routine"
    assert clean.verdict == "REVIEW"
    assert "testEvidence" in " ".join(clean.reasons)


def test_evidence_distinguishes_no_capture_from_a_failing_one():
    evidence = Evidence(captures={"a": (0, 1)})
    assert evidence.test_evidence("a") == 0.0, "a failing capture"
    assert evidence.test_evidence("b") == 0.0, "no capture at all"
    assert evidence.test_evidence("c") == 0.0


def test_sql_nobody_has_checked_against_scalardb_scores_zero(corpus, ruleset):
    """Not analysed is not half a capability: AUTO must not ride on an unasked question."""
    program, analysis = corpus
    perfect = Evidence(captures={r.id: (3, 3) for m in program.modules for r in m.routines})
    decision = decide(program, analysis, ruleset, perfect)["pkg_customer_crud.delete_customer"]
    assert decision.rule_verdict == "AUTO"
    assert decision.verdict == "REVIEW"
    assert "targetCapability" in " ".join(decision.reasons)


def test_full_evidence_and_a_checked_statement_gives_auto(corpus, ruleset):
    program, analysis = corpus
    for module in program.modules:
        for routine in module.routines:
            for statement in routine.body:
                if statement.kind == "SqlOperation":
                    statement.target_status = "OK"
    perfect = Evidence(captures={r.id: (3, 3) for m in program.modules for r in m.routines})
    decisions = decide(program, analysis, ruleset, perfect)
    assert decisions["pkg_customer_crud.delete_customer"].verdict == "AUTO"
    for module in program.modules:  # leave the shared fixture as it was found
        for routine in module.routines:
            for statement in routine.body:
                if statement.kind == "SqlOperation":
                    statement.target_status = None


# --- the rules the corpus exercises ---------------------------------------------------------------------------

@pytest.mark.parametrize("routine_id,rule_id", [
    ("prc_nightly_close", "TX-001"),
    ("prc_nightly_close", "TX-003"),
    ("prc_audit_autonomous", "TX-002"),
    ("prc_remote_sync", "LINK-001"),
    ("trg_orders_audit.body", "TRG-001"),
    ("pkg_dynamic_search.purge", "DYN-001"),
    ("pkg_stock_reserve.reserve", "LOCK-001"),
    ("pkg_stock_reserve.claim_batch", "LOCK-002"),
    ("pkg_bulk_load.restock", "BULK-002"),
    ("pkg_money_calc.rounded_total", "SEM-001"),
    ("pkg_money_calc.days_since_order", "SEM-002"),
    ("pkg_money_calc.display_note", "SEM-003"),
    ("pkg_payment.paid_total", "SEM-004"),
    ("pkg_order_pricing.customer_tier", "SEM-005"),
    ("pkg_customer_import.import", "SEM-006"),
    ("pkg_order_report.count_by_status", "CUR-001"),
    ("pkg_order_report.mark_reviewed", "CUR-002"),
])
def test_the_expected_rule_fires(decisions, routine_id: str, rule_id: str):
    assert rule_id in matched(decisions, routine_id), sorted(matched(decisions, routine_id))


def test_a_finite_variant_dynamic_sql_is_review_not_redesign(decisions):
    """The design document separates the two dynamic-SQL cases; only identifier interpolation is a redesign."""
    assert "DYN-001" not in matched(decisions, "pkg_dynamic_search.count_orders")
    assert decisions["pkg_dynamic_search.count_orders"].rule_verdict == "REVIEW"
    assert decisions["pkg_dynamic_search.purge"].rule_verdict == "REDESIGN"


def test_a_caller_is_never_safer_than_what_it_calls(decisions):
    """`assert_open` has nothing remarkable of its own; it calls `status_of`."""
    assert decisions["pkg_order_pricing.reprice_order"].rule_verdict == "REVIEW"
    assert any("calls" in reason for reason in decisions["pkg_order_pricing.reprice_order"].reasons)


def test_a_redesign_decision_says_what_to_do_instead(decisions):
    decision = decisions["prc_nightly_close"]
    assert decision.remediation()
    assert decision.required_tests()


# --- KPI-3 ------------------------------------------------------------------------------------------------------

def test_no_auto_prohibition_is_missed(decisions):
    """The hard condition: a routine the manifest marks REDESIGN must never come out AUTO."""
    expected = expected_verdicts()
    missed = []
    for routine_id, decision in decisions.items():
        short = routine_id.split(".")[-1]
        want = expected.get(short if short != "body" else routine_id.split(".")[0])
        if want == "REDESIGN" and decision.rule_verdict == "AUTO":
            missed.append(routine_id)
    assert missed == [], missed


def test_the_verdict_agreement_meets_the_phase_2_target(decisions):
    expected = expected_verdicts()
    agree = disagree = 0
    mismatches = []
    for routine_id, decision in sorted(decisions.items()):
        short = routine_id.split(".")[-1]
        want = expected.get(short if short != "body" else routine_id.split(".")[0])
        if want is None:
            continue
        if want == decision.rule_verdict:
            agree += 1
        else:
            disagree += 1
            mismatches.append((routine_id, want, decision.rule_verdict))
    rate = agree / (agree + disagree)
    assert rate >= 0.90, f"{rate:.1%}: {mismatches}"


def test_the_one_known_gap_is_the_one_p2_4_owns(decisions):
    """`status_for_customer` is a SELECT INTO on a non-key column.

    Telling that from a key lookup needs the ScalarDB schema and the access path, which is the capability checker's
    job (P2-4). Pinning it here keeps the gap visible instead of letting it blend into the agreement rate.
    """
    assert decisions["pkg_order_status.status_for_customer"].rule_verdict == "AUTO"
    assert expected_verdicts()["status_for_customer"] == "REVIEW"


def test_the_verdict_distribution_is_not_degenerate(decisions):
    counts = Counter(d.rule_verdict for d in decisions.values())
    assert counts["AUTO"] and counts["REVIEW"] and counts["REDESIGN"]
