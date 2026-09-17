"""P3-5: the KPI command.

The completion report's numbers come from here, so what matters is that each one is read off an artifact and
that the two which cannot be are reported as unmeasured rather than filled in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plsql import kpi

FIXTURES = Path("fixtures/plsql")
EVIDENCE = Path("difftest/work/plsql-diff.json")


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    return kpi.measure(FIXTURES / "src", FIXTURES / "src" / "schema.sql", FIXTURES / "scalardb-schema.json",
                       str(EVIDENCE) if EVIDENCE.exists() else None,
                       str(tmp_path_factory.mktemp("generated")), None, None)


def test_every_kpi_in_the_document_is_computed(measured):
    for key in ("kpi1", "kpi2", "kpi3", "kpi4", "kpi5", "kpi6", "kpi7"):
        assert key in measured and measured[key]["name"]


def test_each_number_says_where_it_came_from(measured):
    for key in ("kpi1", "kpi2", "kpi3", "kpi4", "kpi7"):
        assert measured[key]["source"], f"{key} does not say what it was read from"


def test_the_corpus_is_labelled_synthetic_on_every_run(measured):
    """Not a footnote somebody can drop: the numbers are not evidence about real customer code."""
    assert measured["corpus"]["origin"] == "synthetic"
    assert "合成" in measured["corpus"]["note"]
    assert "合成" in kpi.render(measured)


def test_parse_and_type_resolution_meet_their_targets(measured):
    assert measured["kpi1"]["value"] >= measured["kpi1"]["target"]
    assert measured["kpi2"]["value"] >= measured["kpi2"]["target"]


def test_no_auto_prohibition_is_missed(measured):
    """The hard condition: a routine the manifest marks REDESIGN must never come out AUTO."""
    assert measured["kpi3"]["autoProhibitionsMissed"] == []


def test_verdict_agreement_meets_its_target(measured):
    assert measured["kpi3"]["value"] >= measured["kpi3"]["target"]


def test_every_auto_routine_is_generated_cleanly(measured):
    assert measured["kpi4"]["notCleanlyGenerated"] == []
    assert measured["kpi4"]["value"] == 1.0


def test_kpi4_says_that_javac_is_the_other_half(measured):
    assert "gradle" in measured["kpi4"]["note"]


@pytest.mark.skipif(not EVIDENCE.exists(), reason="no comparison report; run difftest/plsql_diff.py --full")
def test_semantic_equivalence_is_reported_per_money_convention(measured):
    per_variant = measured["kpi5"]["byVariant"]
    assert per_variant, "the comparison report covered no variant"
    for name, entry in per_variant.items():
        assert entry["auto"]["rate"] == 1.0, f"{name}: AUTO did not fully agree with Oracle"


def test_without_a_comparison_report_semantic_equivalence_is_unmeasured_not_zero(tmp_path):
    """Nobody asked is not the same answer as it failed."""
    entry = kpi._kpi5(str(tmp_path / "absent.json"), None)
    assert entry["value"] is None and "比較結果が無い" in entry["detail"]


def test_an_unmeasured_fix_time_stays_null(measured):
    """The one KPI that measures the migration is the worst one to invent a number for."""
    assert measured["kpi6"]["value"] is None
    assert "unmeasured" in measured["kpi6"]["detail"]


def test_the_risk_density_is_per_thousand_lines_not_a_rate(measured):
    entry = measured["kpi7"]
    assert entry["unit"] == "per1000" and entry["target"] is None
    assert entry["value"] > 0 and "/1000行" in kpi.render(measured)


def test_render_marks_a_target_as_met_or_missed(measured):
    text = kpi.render(measured)
    assert "合格" in text
    assert "未計測" in text, "an unmeasured KPI must say so rather than show a number"


def test_the_numbers_round_trip_as_json(measured):
    json.loads(json.dumps(measured, ensure_ascii=False))
