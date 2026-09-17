"""P4-10 / P4-11: model advice, and rule proposals.

Both produce things that look authoritative and are not. What is tested here is mostly what they *cannot* do:
advice cannot become evidence, a proposal cannot become a rule, and nothing leaves the machine by default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from plsql import propose, remediate, review
from plsql.analysis import analyse as analyse_program
from plsql.report import analyse
from plsql.rules.engine import RULES_DIR, Evidence, RuleSet, decide

SRC = Path("fixtures/plsql/src")
DDL = "fixtures/plsql/src/schema.sql"
SCALARDB = "fixtures/plsql/scalardb-schema.json"


@pytest.fixture(scope="module")
def decisions():
    program = analyse(str(SRC), DDL, scalardb_schema=SCALARDB)
    return program, decide(program.program, analyse_program(program.program), RuleSet.load(), Evidence())


# ---------------------------------------------------------------- P4-10: nothing is sent by default
def test_preparing_requests_sends_nothing(decisions, tmp_path, capsys):
    assert remediate.main(["--src", str(SRC), "--prepare", "--out", str(tmp_path)]) == 0
    assert "nothing was sent" in capsys.readouterr().out
    assert (tmp_path / "requests.json").exists()


def test_there_is_no_default_provider():
    """Sending a customer's PL/SQL to a third party is the customer's decision, not a default."""
    with pytest.raises(RuntimeError, match="no provider is configured"):
        remediate.Unconfigured().complete(
            remediate.Request(routine="r", verdict="REVIEW", source_file="f", source_lines=(1, 2),
                              source_text="x"))


def test_the_manifest_says_what_would_leave_the_machine(decisions, tmp_path):
    program, decided = decisions
    requests = remediate.build_requests(program.program, decided, SRC)
    written = remediate.write_requests(requests, tmp_path)
    manifest = written["manifest"].read_text(encoding="utf-8")
    assert "PL/SQL ソースそのもの" in manifest
    assert "含まれないもの" in manifest
    assert "顧客の判断" in manifest


def test_auto_routines_are_not_sent_anywhere(decisions, tmp_path):
    program, decided = decisions
    evidence = review.evidence_from_diff(None)
    auto = {r for r, d in decide(program.program, analyse_program(program.program),
                                 RuleSet.load(), evidence).items() if d.verdict == "AUTO"}
    requests = remediate.build_requests(program.program, decided, SRC)
    assert not ({r.routine for r in requests} & auto)


# ---------------------------------------------------------------- P4-10: advice is never evidence
def test_advice_is_always_review():
    advice = remediate.Advice(routine="r", request_digest="d", model="m", produced_at="t", text="code")
    assert advice.verdict == remediate.ADVICE_VERDICT == "REVIEW"
    assert advice.as_dict()["verdict"] == "REVIEW"


def test_the_verdict_engine_does_not_read_advice(tmp_path):
    """Structural, not advisory: the engine takes Evidence, and advice is not a source of it."""
    import inspect

    from plsql.rules import engine

    source = inspect.getsource(engine)
    assert "remediate" not in source and "advice" not in source.lower()


def test_advice_records_where_it_came_from(tmp_path):
    advice = [remediate.Advice(routine="r", request_digest="abc", model="some-model",
                               produced_at=remediate.now(), text="...")]
    path = remediate.record(advice, tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    entry = written["advice"][0]
    assert entry["model"] == "some-model" and entry["requestDigest"] == "abc"
    assert entry["producedAt"] and "証拠ではない" in written["note"]


def test_advice_whose_request_changed_is_reported_as_stale():
    request = remediate.Request(routine="r", verdict="REVIEW", source_file="f", source_lines=(1, 2),
                                source_text="original")
    advice = [remediate.Advice(routine="r", request_digest=request.digest(), model="m",
                               produced_at="t", text="...")]
    assert remediate.stale(advice, [request]) == []
    changed = remediate.Request(routine="r", verdict="REVIEW", source_file="f", source_lines=(1, 2),
                                source_text="edited since")
    assert remediate.stale(advice, [changed]) == ["r"]


def test_the_prompt_points_at_the_recorded_patterns(decisions):
    """A model not pointed at them invents its own advice, and then the project has two answers."""
    program, decided = decisions
    requests = remediate.build_requests(program.program, decided, SRC)
    with_pattern = [r for r in requests if r.patterns]
    assert with_pattern, "no request referenced a pattern document"
    prompt = with_pattern[0].prompt()
    assert "設計テンプレート" in prompt and "推測を事実として書かないこと" in prompt


# ---------------------------------------------------------------- P4-11: a proposal is not a rule
def resolutions(tmp_path, entries: dict) -> Path:
    path = tmp_path / "resolutions.yaml"
    path.write_text(yaml.safe_dump({"resolutions": entries}, allow_unicode=True), encoding="utf-8")
    return path


def test_a_repeated_decision_becomes_a_proposal(decisions, tmp_path):
    _, decided = decisions
    path = resolutions(tmp_path, {
        "prc_nightly_close": {"outcome": "redesigned", "pattern": "tx-E"},
        "prc_purge_audit": {"outcome": "redesigned", "pattern": "tx-E"},
        "prc_reprice_all": {"outcome": "redesigned", "pattern": "tx-E"},
    })
    proposals = propose.propose(decided, propose.load_resolutions(path))
    assert len(proposals) == 1
    assert proposals[0].support == 3 and proposals[0].shape == ("TX-001", "TX-003")


def test_two_of_a_kind_is_a_coincidence(decisions, tmp_path):
    _, decided = decisions
    path = resolutions(tmp_path, {
        "prc_purge_audit": {"outcome": "redesigned", "pattern": "tx-E"},
        "prc_reprice_all": {"outcome": "redesigned", "pattern": "tx-E"},
    })
    assert propose.propose(decided, propose.load_resolutions(path)) == []


def test_the_grouping_uses_the_rules_that_decided_not_every_rule_that_fired(decisions, tmp_path):
    """Incidental matches make every routine its own shape, which proposes nothing."""
    _, decided = decisions
    path = resolutions(tmp_path, {
        "prc_nightly_close": {"outcome": "redesigned", "pattern": "tx-E"},
        "prc_purge_audit": {"outcome": "redesigned", "pattern": "tx-E"},
        "prc_reprice_all": {"outcome": "redesigned", "pattern": "tx-E"},
    })
    proposal = propose.propose(decided, propose.load_resolutions(path))[0]
    # nightly_close also picks up SEM-002 / SQL-001 / CUR-002; the proposal says so rather than hiding it
    assert proposal.also_fired - set(proposal.shape)


def test_the_proposal_file_says_it_is_not_a_rule(decisions, tmp_path):
    _, decided = decisions
    path = resolutions(tmp_path, {
        "prc_nightly_close": {"outcome": "redesigned", "pattern": "tx-E"},
        "prc_purge_audit": {"outcome": "redesigned", "pattern": "tx-E"},
        "prc_reprice_all": {"outcome": "redesigned", "pattern": "tx-E"},
    })
    text = propose.render(propose.propose(decided, propose.load_resolutions(path)),
                          propose.load_resolutions(path))
    assert "これはルールではない" in text
    assert "KPI-3 が下がらないか" in text, "the check before adopting one belongs in the file"


def test_proposals_are_not_written_where_rules_are_loaded_from(tmp_path):
    """`RuleSet.load` reads plsql/rules/*.yaml; a proposal takes effect when a person copies it there."""
    assert propose.main(["--src", str(SRC), "--out", str(tmp_path / "rule-proposals.md")]) == 0
    written = {p.resolve() for p in tmp_path.rglob("*")}
    assert not any(str(p).startswith(str(Path(RULES_DIR).resolve())) for p in written)


def test_an_unknown_outcome_is_refused(tmp_path):
    path = resolutions(tmp_path, {"r": {"outcome": "maybe"}})
    with pytest.raises(ValueError, match="outcome must be one of"):
        propose.load_resolutions(path)


def test_no_resolutions_proposes_nothing(decisions):
    _, decided = decisions
    assert propose.propose(decided, {}) == []
    assert "候補なし" in propose.render([], {})
