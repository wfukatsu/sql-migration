"""P3-5: the files a reviewer works from.

What is checked here is that each file says something true and says it in a form the next reader can use: a
decision that can be traced to the rule that made it, an open item that carries its reason and its alternatives,
and a trace row that was checked against the generated source rather than derived from a naming convention.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from plsql import review
from plsql.analysis import analyse as analyse_program
from plsql.report import analyse
from plsql.rules.engine import Evidence, RuleSet, decide

SRC = "fixtures/plsql/src"
DDL = "fixtures/plsql/src/schema.sql"
SCALARDB = "fixtures/plsql/scalardb-schema.json"


@pytest.fixture(scope="module")
def analysis():
    return analyse(SRC, DDL, scalardb_schema=SCALARDB)


@pytest.fixture(scope="module")
def rules():
    return RuleSet.load()


def decisions_for(analysis, rules, evidence: Evidence | None = None):
    return decide(analysis.program, analyse_program(analysis.program), rules, evidence or Evidence())


# ---------------------------------------------------------------- decisions.json
def test_every_non_auto_verdict_says_why(analysis, rules):
    """A verdict nobody can trace to a cause is one nobody can review or change.

    The cause is a rule, or the confidence -- a routine nothing has verified cannot be AUTO even with no rule
    against it. Both are answers; having neither is not.
    """
    document = review.decisions_document(analysis.program, decisions_for(analysis, rules))
    assert document["routines"]
    for record in document["routines"]:
        if record["verdict"] == "AUTO":
            assert record["whyNotAuto"] == []
            continue
        assert record["whyNotAuto"], f"{record['routine']} is not AUTO and says nothing about why"


def test_a_rule_that_decided_is_traceable_to_the_file_it_is_written_in(analysis, rules):
    document = review.decisions_document(analysis.program, decisions_for(analysis, rules))
    named = [rule for record in document["routines"] for rule in record["rules"]]
    assert named
    for rule in named:
        assert rule["id"] and rule["source"].endswith(".yaml") and rule["message"]


def test_a_decision_carries_all_five_confidence_factors(analysis, rules):
    document = review.decisions_document(analysis.program, decisions_for(analysis, rules))
    for record in document["routines"]:
        factors = record["confidence"]
        for name in ("ruleCoverage", "symbolResolution", "typeResolution", "targetCapability", "testEvidence"):
            assert name in factors, f"{record['routine']} is missing {name}"


def test_without_evidence_nothing_reaches_auto(analysis, rules):
    """Not a quirk: a routine nobody compared against Oracle has not been verified."""
    document = review.decisions_document(analysis.program, decisions_for(analysis, rules))
    assert document["counts"].get("AUTO", 0) == 0
    assert all("testEvidence" in r["confidence"]["zeroFactors"] for r in document["routines"])


def test_evidence_from_the_comparison_lets_verified_routines_reach_auto(analysis, rules, tmp_path):
    report = {"scaled": {"scenarios": {
        "a": {"routine": "pkg_order_status.status_of", "verdict": "AUTO", "differences": []},
        "b": {"routine": "pkg_order_status.status_of", "verdict": "AUTO", "differences": []},
    }}}
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    evidence = review.evidence_from_diff(path)
    assert evidence.captures["pkg_order_status.status_of"] == (2, 2)

    document = review.decisions_document(analysis.program, decisions_for(analysis, rules, evidence))
    verdicts = {r["routine"]: r["verdict"] for r in document["routines"]}
    assert verdicts["pkg_order_status.status_of"] == "AUTO"


def test_a_routine_that_disagreed_is_not_credited(analysis, rules, tmp_path):
    report = {"scaled": {"scenarios": {
        "a": {"routine": "pkg_order_status.status_of", "verdict": "AUTO", "differences": ["returned"]},
    }}}
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert review.evidence_from_diff(path).captures["pkg_order_status.status_of"] == (0, 1)


def test_agreeing_under_one_money_convention_only_is_not_agreement(tmp_path):
    """A result that depends on a decision nobody has taken yet is not evidence."""
    report = {
        "scaled": {"scenarios": {"a": {"routine": "r", "verdict": "REVIEW", "differences": []}}},
        "double": {"scenarios": {"a": {"routine": "r", "verdict": "REVIEW", "differences": ["x"]}}},
    }
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert review.evidence_from_diff(path).captures["r"] == (1, 2)
    assert review.evidence_from_diff(path, "scaled").captures["r"] == (1, 1)


def test_a_scenario_that_could_not_run_counts_neither_way(tmp_path):
    """Blaming a routine for a fixture the target could not seed would be a lie about the routine."""
    report = {"scaled": {"scenarios": {"a": {"routine": "r", "verdict": "REVIEW", "differences": []}},
                         "not_compared": {"b": {"routine": "r", "reason": "setup does not convert"}}}}
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert review.evidence_from_diff(path).captures["r"] == (1, 1)


def test_missing_evidence_file_is_no_evidence_rather_than_an_error(tmp_path):
    assert review.evidence_from_diff(tmp_path / "absent.json").captures == {}
    assert review.evidence_from_diff(None).captures == {}


# ---------------------------------------------------------------- KPI-6
def test_an_unmeasured_fix_time_is_null_and_listed_as_unmeasured(analysis, rules):
    document = review.decisions_document(analysis.program, decisions_for(analysis, rules))
    assert all(r["humanFixMinutes"] is None for r in document["routines"])
    assert len(document["humanFixMinutes"]["unmeasured"]) == len(document["routines"])
    assert document["humanFixMinutes"]["byVerdict"] == {}


def test_a_measured_fix_time_is_recorded_and_the_median_says_how_many_it_rests_on(analysis, rules, tmp_path):
    routines = [r.id for m in analysis.program.modules for r in m.routines]
    measured = {routines[0]: 30, routines[1]: 90, routines[2]: 60}
    path = tmp_path / "fix-times.yaml"
    path.write_text("minutes:\n" + "".join(f"  {k}: {v}\n" for k, v in measured.items()), encoding="utf-8")

    document = review.decisions_document(analysis.program, decisions_for(analysis, rules),
                                         review.FixTimes.load(path))
    recorded = {r["routine"]: r["humanFixMinutes"] for r in document["routines"]
                if r["humanFixMinutes"] is not None}
    assert recorded == {k: float(v) for k, v in measured.items()}
    for summary in document["humanFixMinutes"]["byVerdict"].values():
        assert summary["measured"] >= 1 and summary["median"] is not None
    assert document["humanFixMinutes"]["source"] == str(path)


# ---------------------------------------------------------------- unresolved.md
def test_unresolved_lists_only_what_needs_a_person_worst_first(analysis, rules):
    decisions = decisions_for(analysis, rules)
    text = review.unresolved_markdown(analysis.program, decisions)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings
    kinds = [h.split(":")[0].removeprefix("## ").strip() for h in headings]
    assert "AUTO" not in kinds
    assert kinds == sorted(kinds, key=lambda k: review.VERDICT_ORDER[k])


def test_every_open_item_carries_a_reason_alternatives_and_the_tests_it_needs(analysis, rules):
    text = review.unresolved_markdown(analysis.program, decisions_for(analysis, rules))
    blocks = text.split("## ")[1:]
    assert blocks
    for block in blocks:
        assert "**根拠**" in block, block.splitlines()[0]
        assert "**代替案**" in block, block.splitlines()[0]
        assert "**受け入れに必要なテスト**" in block, block.splitlines()[0]


def test_an_open_item_points_at_the_plsql_line(analysis, rules):
    text = review.unresolved_markdown(analysis.program, decisions_for(analysis, rules))
    assert ".pkb:" in text or ".prc:" in text or ".trg:" in text


# ---------------------------------------------------------------- traceability.csv
def test_every_trace_row_points_at_a_plsql_line(analysis, rules):
    rows = list(csv.DictReader(io.StringIO(
        review.traceability_csv(analysis.program, decisions_for(analysis, rules)))))
    assert rows
    for row in rows:
        assert row["plsqlFile"] and row["plsqlStartLine"].isdigit()
        assert row["javaFile"].endswith(".java") and row["javaMember"]


def test_a_trace_row_is_checked_against_the_generated_source(analysis, rules):
    """Otherwise the file would restate the naming convention and agree with itself."""
    rows = list(csv.DictReader(io.StringIO(
        review.traceability_csv(analysis.program, decisions_for(analysis, rules), generated_root="generated"))))
    states = {row["generated"] for row in rows}
    assert states <= {"yes", "not-generated", "not-translated"}
    assert "yes" in states, "no generated member was found; the trace is not checking anything"


def test_without_the_generated_tree_no_row_claims_to_be_verified(analysis, rules):
    rows = list(csv.DictReader(io.StringIO(
        review.traceability_csv(analysis.program, decisions_for(analysis, rules)))))
    assert {row["generated"] for row in rows} == {"not-generated"}


def test_a_routine_and_each_of_its_statements_appear(analysis, rules):
    rows = review.traceability_rows(analysis.program, decisions_for(analysis, rules))
    kinds = {row[5] for row in rows}
    assert "routine" in kinds and len(kinds) > 1


# ---------------------------------------------------------------- writing them
def test_write_produces_all_three_files(analysis, rules, tmp_path):
    written = review.write(analysis.program, decisions_for(analysis, rules), tmp_path,
                           generated_root="generated")
    assert set(written) == {"decisions", "unresolved", "traceability"}
    for path in written.values():
        assert path.exists() and path.stat().st_size > 0
    json.loads(written["decisions"].read_text(encoding="utf-8"))


# ---------------------------------------------------------------- P4-1: matching and indirect evidence
def test_a_standalone_procedure_is_credited_for_its_own_scenario(analysis, tmp_path):
    """A scenario says `unit.routine`; the IR gives a standalone procedure the bare name.

    Without resolution the two never meet, and a routine whose scenario agreed on both money conventions sits
    in the "nobody verified it" list forever.
    """
    report = {"scaled": {"scenarios": {
        "a": {"routine": "prc_add_product.prc_add_product", "verdict": "AUTO", "differences": []}}}}
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    known = review.routine_ids(analysis.program)

    assert "prc_add_product" in known and "prc_add_product.prc_add_product" not in known
    assert review.evidence_from_diff(path, None, known).captures == {"prc_add_product": (1, 1)}
    assert review.unmatched_scenarios(path, known) == []


def test_resolution_never_invents_a_match(analysis, tmp_path):
    report = {"scaled": {"scenarios": {
        "a": {"routine": "pkg_nothing.no_such_routine", "verdict": "AUTO", "differences": []}}}}
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    known = review.routine_ids(analysis.program)
    assert review.unmatched_scenarios(path, known) == ["pkg_nothing.no_such_routine"]


def _graph(edges):
    class Graph:
        def callees(self, routine):
            return set(edges.get(routine, ()))
    return Graph()


def test_a_private_routine_is_credited_through_its_verified_callers(analysis):
    """It cannot be called from a scenario, but the comparison ran the whole chain against Oracle."""
    program = analysis.program
    evidence = Evidence(captures={"pkg_shipment.is_shippable": (2, 2)})
    credited = review.credit_private_callees(
        evidence, program, _graph({"pkg_shipment.is_shippable": ["pkg_shipment.line_count"]}))
    assert credited.captures["pkg_shipment.line_count"] == (2, 2)


def test_a_caller_that_disagreed_credits_its_callee_with_nothing(analysis):
    evidence = Evidence(captures={"pkg_shipment.is_shippable": (1, 2)})
    credited = review.credit_private_callees(
        evidence, analysis.program, _graph({"pkg_shipment.is_shippable": ["pkg_shipment.line_count"]}))
    assert "pkg_shipment.line_count" not in credited.captures


def test_an_unverified_caller_blocks_the_credit(analysis):
    """Every caller must be verified: one unverified path is a path nobody compared."""
    evidence = Evidence(captures={"pkg_shipment.is_shippable": (2, 2)})
    credited = review.credit_private_callees(
        evidence, analysis.program,
        _graph({"pkg_shipment.is_shippable": ["pkg_shipment.line_count"],
                "pkg_shipment.days_in_transit": ["pkg_shipment.line_count"]}))
    assert "pkg_shipment.line_count" not in credited.captures


def test_a_public_routine_is_never_given_someone_elses_evidence(analysis):
    evidence = Evidence(captures={"pkg_shipment.mark_shipped": (1, 1)})
    credited = review.credit_private_callees(
        evidence, analysis.program, _graph({"pkg_shipment.mark_shipped": ["pkg_shipment.is_shippable"]}))
    assert "pkg_shipment.is_shippable" not in credited.captures


def test_indirect_evidence_does_not_override_a_rule(analysis, rules):
    """Evidence is one factor of five. A rule that objects still blocks the routine."""
    program = analysis.program
    evidence = review.credit_private_callees(
        Evidence(captures={"pkg_shipment.is_shippable": (2, 2)}), program,
        _graph({"pkg_shipment.is_shippable": ["pkg_shipment.line_count"]}))
    decisions = decide(program, analyse_program(program), rules, evidence)
    assert decisions["pkg_shipment.line_count"].confidence.test_evidence == 1.0
    assert decisions["pkg_shipment.line_count"].verdict != "AUTO", "SEM-004 objects to the aggregate"


def test_why_not_auto_never_contradicts_the_confidence_it_reports(analysis, rules):
    """Re-deriving the cause produced "confidence 1.0 is below the threshold"; the engine knew the real one."""
    document = review.decisions_document(analysis.program, decisions_for(analysis, rules))
    for record in document["routines"]:
        if record["verdict"] == "AUTO":
            continue
        for reason in record["whyNotAuto"]:
            if "しきい値に届いていない" in reason:
                assert record["confidence"]["value"] < 0.95, record["routine"]


# ---------------------------------------------------------------- #27-33: what the evidence is evidence of
ROUTINE = "pkg_order_status.status_of"


def report_with(tmp_path, fingerprints):
    entry = {"scenarios": {"a": {"routine": ROUTINE, "verdict": "AUTO", "differences": []}}}
    if fingerprints is not None:
        entry["fingerprints"] = fingerprints
    path = tmp_path / "diff.json"
    path.write_text(json.dumps({"scaled": entry}), encoding="utf-8")
    return path


def test_evidence_measured_on_this_source_by_this_toolchain_counts(analysis, tmp_path):
    from plsql import fingerprint
    current = fingerprint.of(analysis.program, SRC)
    assert ROUTINE in current["sources"]
    evidence = review.evidence_from_diff(report_with(tmp_path, current), current=current)
    assert evidence.captures[ROUTINE] == (1, 1) and not evidence.stale


def test_a_report_nobody_can_vouch_for_makes_nothing_auto(analysis, rules, tmp_path):
    """Regression: any file of the right shape was believed, so one hand-written scenario made a routine AUTO."""
    from plsql import fingerprint
    current = fingerprint.of(analysis.program, SRC)
    evidence = review.evidence_from_diff(report_with(tmp_path, None), current=current)
    assert ROUTINE not in evidence.captures and "fingerprint が無い" in evidence.stale[ROUTINE]
    decisions = decisions_for(analysis, rules, evidence)
    assert decisions[ROUTINE].verdict == "REVIEW"
    record = next(r for r in review.decisions_document(analysis.program, decisions, stale=evidence.stale)["routines"]
                  if r["routine"] == ROUTINE)
    assert "古い" in " ".join(record["whyNotAuto"])


@pytest.mark.parametrize("change,reason", [
    (lambda f: {**f, "toolchain": "0" * 64}, "生成器か実行時ヘルパが変わった"),
    (lambda f: {**f, "sources": {**f["sources"], ROUTINE: "0" * 64}}, "ソースが変わった"),
    (lambda f: {**f, "sources": {k: v for k, v in f["sources"].items() if k != ROUTINE}}, "ソースが変わった"),
])
def test_evidence_about_another_source_or_generator_is_stale(analysis, tmp_path, change, reason):
    from plsql import fingerprint
    current = fingerprint.of(analysis.program, SRC)
    evidence = review.evidence_from_diff(report_with(tmp_path, change(current)), current=current)
    assert ROUTINE not in evidence.captures and reason in evidence.stale[ROUTINE]


def test_the_source_fingerprint_follows_the_routine_not_the_file(tmp_path):
    from plsql import fingerprint
    body = ("CREATE OR REPLACE PACKAGE BODY pkg_x AS\n"
            "  FUNCTION a RETURN NUMBER IS BEGIN RETURN 1; END a;\n"
            "  FUNCTION b RETURN NUMBER IS BEGIN RETURN {b}; END b;\n"
            "END pkg_x;\n/\n")
    hashes = []
    for value in ("2", "3"):
        root = tmp_path / value
        root.mkdir()
        (root / "pkg_x.pkb").write_text(body.format(b=value), encoding="utf-8")
        hashes.append(fingerprint.sources(analyse(str(root)).program, root))
    assert hashes[0]["pkg_x.a"] == hashes[1]["pkg_x.a"], "editing b does not make a's evidence stale"
    assert hashes[0]["pkg_x.b"] != hashes[1]["pkg_x.b"]


def test_reporting_modules_are_not_part_of_the_toolchain():
    """Or writing a report would invalidate the evidence it reports on."""
    from plsql import fingerprint
    assert {"review.py", "kpi.py", "cli.py"} <= fingerprint.REPORTING
    assert len(fingerprint.toolchain()) == 64 and fingerprint.toolchain() == fingerprint.toolchain()


# ---------------------------------------------------------------- #27-34: a refused statement is not `yes`
GENERATED = """package x;
public class PService {
    // p.prc:1
    public void run(BigDecimal pId) throws Exception {
        // p.prc:4
        v = Plsql.dec(pId);
        // p.prc:40
        // not translated: p_a ** 2
        //     unresolved: **
        throw new UnsupportedOperationException("unresolved in Assignment: **");
        // p.prc:41
        // external call: UTL_MAIL.SEND
        throw new UnsupportedOperationException("external call: UTL_MAIL.SEND");
    }
    private void runner() { run(null); }
}
"""


@pytest.mark.parametrize("anchor,state", [
    ("p.prc:4", "yes"),
    ("p.prc:40", "not-translated"),      # the comment is written before the statement is refused
    ("p.prc:41", "not-translated"),
    ("p.prc:400", "not-translated"),     # never emitted
    ("p.prc", "not-translated"),         # a prefix of an anchor is not the anchor
])
def test_an_anchor_is_read_for_what_the_generator_wrote_under_it(anchor, state):
    assert review._anchored(GENERATED, anchor) == state


def test_a_routine_row_needs_the_declaration_not_the_name():
    assert review._declares(GENERATED, "run") and review._declares(GENERATED, "runner")
    assert not review._declares(GENERATED, "ru")
    assert not review._declares(GENERATED.replace("public void run(", "public void other("), "run"), \
        "`run(null)` in another method is a call, not the routine"
