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
    if measured["kpi5"]["staleEvidence"]:
        pytest.skip("the local comparison report is stale (plsql/fingerprint.py); re-run difftest/plsql_capture.py")
    per_variant = measured["kpi5"]["byVariant"]
    assert per_variant, "the comparison report covered no variant"
    for name, entry in per_variant.items():
        assert entry["auto"]["rate"] == 1.0, f"{name}: AUTO did not fully agree with Oracle"


def test_without_a_comparison_report_semantic_equivalence_is_unmeasured_not_zero(tmp_path):
    """Nobody asked is not the same answer as it failed."""
    entry = kpi._kpi5(str(tmp_path / "absent.json"), None)
    assert entry["value"] is None and "比較結果が無い" in entry["detail"]


def test_kpi6_is_reported_as_a_decision_not_a_missed_target(measured):
    """Not measured in this PoC (2026-09-17, plan §9). A decision is not a shortfall, and says so."""
    entry = measured["kpi6"]
    assert entry["value"] is None and entry["measured"] is False
    assert entry["target"] is None, "a KPI nobody is measuring has no target to miss"
    assert "計測しない" in entry["detail"]
    assert "計測しない" in kpi.render(measured)


def test_the_run_says_what_not_measuring_kpi6_costs(measured):
    """The other six are about the tool. Nothing here says how long the migration takes."""
    assert "移行工数を測っていない" in kpi.render(measured)


def test_a_measured_fix_time_is_still_shown_if_somebody_supplies_one(tmp_path):
    """The decision was to stop asking for the number, not to refuse it."""
    from plsql import review

    routines = ["pkg_customer_crud.update_email", "pkg_customer_crud.delete_customer"]
    path = tmp_path / "fix-times.yaml"
    path.write_text("minutes:\n" + "".join(f"  {r}: 30\n" for r in routines), encoding="utf-8")
    assert review.FixTimes.load(path).for_routine(routines[0]) == 30


def test_the_risk_density_is_per_thousand_lines_not_a_rate(measured):
    entry = measured["kpi7"]
    assert entry["unit"] == "per1000" and entry["target"] is None
    assert entry["value"] > 0 and "/1000行" in kpi.render(measured)


def test_render_marks_a_target_as_met_or_missed(measured):
    assert "合格" in kpi.render(measured)


def test_a_kpi_without_a_value_never_shows_a_number(measured):
    """Either it was measured, or the line says why not. There is no third rendering."""
    text = kpi.render(measured)
    for key in ("kpi1", "kpi2", "kpi3", "kpi4", "kpi5", "kpi6", "kpi7"):
        entry = measured[key]
        if entry["value"] is not None:
            continue
        line = next(ln for ln in text.splitlines() if ln.strip().startswith(key.upper()))
        assert "計測しない" in line or "未計測" in line, line


def test_the_numbers_round_trip_as_json(measured):
    json.loads(json.dumps(measured, ensure_ascii=False))


# --- #27-33: the rate says what it covers ----------------------------------------------------------------
class _Decision:
    def __init__(self, rule_verdict):
        self.rule_verdict = rule_verdict


def test_kpi5_names_what_its_rate_leaves_out(tmp_path):
    """Regression: `100% 合格 AUTO 2/2` was printed with AUTO routines nobody compared, AUTO scenarios that could
    not run, and the verdicts taken from the report instead of from the rules as they are now."""
    report = {"scaled": {
        "scenarios": {
            "s1": {"routine": "pkg.a", "verdict": "AUTO", "differences": []},
            "s2": {"routine": "pkg.b", "verdict": "AUTO", "differences": ["returned"]},   # REVIEW by now
            "s3": {"routine": "pkg.old", "verdict": "AUTO", "differences": ["returned"]},  # stale
        },
        "not_compared": {"s4": {"routine": "pkg.c", "verdict": "AUTO", "reason": "setup does not convert"}}}}
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    decisions = {"pkg.a": _Decision("AUTO"), "pkg.b": _Decision("REVIEW"), "pkg.c": _Decision("AUTO"),
                 "pkg.d": _Decision("AUTO"), "pkg.old": _Decision("AUTO")}
    entry = kpi._kpi5(str(path), None, decisions, {"pkg.old": "比較のあとで PL/SQL のソースが変わった"})
    scaled = entry["byVariant"]["scaled"]
    assert scaled["auto"] == {"agreed": 1, "compared": 1, "rate": 1.0}, "pkg.b is judged as REVIEW, as it is now"
    assert scaled["reviewOrRedesignWithDifferences"] == 1
    assert scaled["autoScenariosNotCompared"] == ["s4"]
    assert scaled["autoRoutinesWithoutComparison"] == ["pkg.c", "pkg.d", "pkg.old"]
    assert scaled["staleScenarios"] == ["s3"]
    assert "比較の無い AUTO routine 3" in entry["detail"]["scaled"]
    assert "実行できなかった AUTO シナリオ 1" in entry["detail"]["scaled"]


# --- by where the code came from, and by how far its expectations can be trusted (#15) -------------------------

def test_the_numbers_are_also_given_by_origin_and_real_code_is_listed_while_there_is_none(measured):
    """The day real code is added its numbers must not be averaged into the synthetic ones. Until then the report
    says "none measured" -- a row left out is a thing nobody notices is missing."""
    origin = measured["breakdown"]["origin"]
    assert list(origin) == ["synthetic", "real-anonymized"]
    assert origin["synthetic"]["units"] == 29 and origin["synthetic"]["routines"] == 67
    real = origin["real-anonymized"]
    assert real["units"] == 0 and real["kpi1"]["value"] is None and real["kpi3"]["value"] is None
    assert "実案件（匿名化したもの）: 0 unit — まだ測っていない" in kpi.render(measured)


def test_the_groups_of_a_split_add_up_to_the_whole(measured):
    for split in ("origin", "evidence"):
        groups = measured["breakdown"][split].values()
        assert sum(g["routines"] for g in groups) == 67 and sum(g["units"] for g in groups) == 29
        assert sum(g["kpi3"]["agree"] for g in groups) == int(measured["kpi3"]["detail"].split("/")[0])
        assert sum(g["kpi4"]["auto"] for g in groups) == int(measured["kpi4"]["detail"].split("/")[1].split()[0])


def test_agreement_is_given_separately_for_the_holdout_nobody_has_opened(measured):
    """The rules and the expected verdicts share an author. Only the independent holdout is not self-scored, and
    its rate is its own number instead of 8 routines lost among 67."""
    evidence = measured["breakdown"]["evidence"]
    assert list(evidence) == ["independent-holdout", "referenced-holdout", "development"]
    independent = evidence["independent-holdout"]
    assert (independent["units"], independent["routines"]) == (4, 8), "fixtures/plsql/src/holdout2"
    assert independent["kpi3"]["total"] == 8
    assert len(independent["kpi3"]["mismatches"]) == 8 - independent["kpi3"]["agree"]
    assert evidence["referenced-holdout"]["units"] == 5, "fixtures/plsql/src/holdout, opened during P2-2 / P2-4"
    assert "独立した holdout" in kpi.render(measured)


def test_a_tree_with_no_manifest_is_one_group_of_unknown_origin_and_says_so(tmp_path):
    src = Path("fixtures/plsql-external/create_order/src")
    result = kpi.measure(src, src / "schema.sql", None, None, str(tmp_path / "generated"), None, None)
    assert result["breakdown"] is None
    assert "manifest.yaml が無いので分けていない" in kpi.render(result)
