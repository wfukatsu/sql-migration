"""P2-4: what ScalarDB can actually run, and the one decision only this layer can make.

Everything up to here judges PL/SQL on its own terms. This is where the target gets a say, so the tests are about
the answer being carried faithfully -- not re-derived, not softened -- and about the two cases that need the
access path: write-then-scan, and a `SELECT INTO` that does not reach its row by key.
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.analysis import analyse as analyse_program
from plsql.capability import annotate, check, is_key_access, scan_after_write
from plsql.frontend import parse_file
from plsql.ir import model as M
from plsql.lower import _walk, lower_file
from plsql.report import analyse as build_analysis
from plsql.rules.engine import Evidence, RuleSet, decide
from plsql.symbols import OracleSchema, build
from scalardb_migrate.schema import SchemaRegistry

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
CASES = FIXTURES / "rule-cases"


@pytest.fixture(scope="module")
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_schema_loader_json(str(FIXTURES / "scalardb-schema.json"))


@pytest.fixture(scope="module")
def checked(registry):
    analysis = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    return analysis, analyse_program(analysis.program)


def judge_case(name: str, registry: SchemaRegistry):
    schema = OracleSchema.from_ddl(SRC / "schema.sql")
    parsed = parse_file(CASES / name)
    symbols = build(parsed, schema)
    program = M.Program(id=name, kind="Program", modules=lower_file(parsed, symbols, schema))
    analysis = analyse_program(program)
    report = check(program, registry, symbols)
    annotate(program, report)
    decisions = decide(program, analysis, RuleSet.load(), Evidence())
    return program, report, decisions


# --- the answer is carried, not re-derived -----------------------------------------------------------------

def test_every_statement_gets_a_verdict(checked):
    analysis, _ = checked
    statements = [s for m in analysis.program.modules for r in m.routines
                  for s in _walk(r.body) if s.kind == "SqlOperation" and s.original_sql]
    assert statements
    assert all(s.target_status in ("OK", "WARN", "PLANNED", "ERROR") for s in statements)


def test_the_corpus_capability_is_reported_honestly(checked):
    """21 of 51 statements are things ScalarDB cannot run. That number is the point of the exercise."""
    analysis, _ = checked
    counts = analysis.capability.counts()
    assert counts["ERROR"] > 0, "a corpus this varied should not be fully runnable"
    assert counts["OK"] > 0
    assert 0.0 < analysis.capability.rate() < 1.0


def test_the_symbol_table_has_to_cover_every_file(registry):
    """Regression: handing the bridge one file's scopes turns every variable elsewhere into an unknown column.

    With a single file's table the corpus reported 28 errors; with every scope merged it reports 21, and the
    difference was entirely misread variables.
    """
    analysis = build_analysis(SRC, SRC / "schema.sql")
    merged = analysis.symbol_table()
    single = analysis.symbols[0]
    assert len(merged.scopes) > len(single.scopes)
    assert check(analysis.program, registry, merged).counts().get("ERROR", 0) <= \
        check(analysis.program, registry, single).counts().get("ERROR", 0)


# --- the access path ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("message,by_key", [
    ("SELECT: full primary key specified -> GET (single record)", True),
    ("SELECT: full partition key specified -> partition SCAN", True),
    ("SELECT: equality on secondary index -> index SCAN", False),
    ("SELECT: predicates do not cover the partition key -> cross-partition SCAN", False),
    (None, False),
])
def test_key_access_is_read_from_the_converters_own_words(message, by_key: bool):
    assert is_key_access(message) is by_key


def test_a_scan_after_a_write_is_found(registry):
    program, report, decisions = judge_case("write_then_scan.sql", registry)
    assert scan_after_write(program, report)
    assert any(d.code == "SCAN_AFTER_WRITE" for m in program.modules for r in m.routines
               for s in _walk(r.body) for d in s.diagnostics)
    assert decisions["prc_write_then_scan"].rule_verdict == "REDESIGN"
    assert "SCAN-001" in {m.rule.id for m in decisions["prc_write_then_scan"].matches}


def test_reading_by_key_after_a_write_is_allowed(registry):
    """ScalarDB refuses a scan, not a key lookup (measured in P2-9), so the rule must not fire on one."""
    schema = OracleSchema.from_ddl(SRC / "schema.sql")
    source = ("CREATE OR REPLACE PROCEDURE p(p_id IN NUMBER) IS\n  v VARCHAR2(20);\nBEGIN\n"
              "  UPDATE orders SET status = 'X' WHERE order_id = p_id;\n"
              "  SELECT status INTO v FROM orders WHERE order_id = p_id;\nEND;\n/\n")
    path = pathlib.Path(pytest.__file__).parent  # placeholder, not used
    from plsql.frontend import parse_text

    parsed = parse_text(source, "p.prc")
    symbols = build(parsed, schema)
    program = M.Program(id="p", kind="Program", modules=lower_file(parsed, symbols, schema))
    report = check(program, registry, symbols)
    annotate(program, report)
    assert scan_after_write(program, report) == []


def test_a_select_into_that_is_not_a_key_lookup_is_flagged(checked):
    """The gap P2-2 left open: telling TOO_MANY_ROWS risk from a key lookup needs the access path."""
    analysis, _ = checked
    flagged = {s.id for m in analysis.program.modules for r in m.routines for s in _walk(r.body)
               if any(d.code == "MULTI_ROW_INTO" for d in s.diagnostics)}
    assert flagged


def test_a_key_lookup_select_into_is_not_flagged(checked):
    analysis, _ = checked
    for module in analysis.program.modules:
        for routine in module.routines:
            if routine.id != "pkg_order_status.status_of":
                continue
            for statement in _walk(routine.body):
                assert not any(d.code == "MULTI_ROW_INTO" for d in statement.diagnostics)


# --- the rules this layer feeds ------------------------------------------------------------------------------

def test_unsupported_sql_reaches_the_rule(registry):
    _, report, decisions = judge_case("unsupported_sql.sql", registry)
    assert "ERROR" in report.counts()
    assert "SQL-001" in {m.rule.id for d in decisions.values() for m in d.matches}


def test_a_planned_statement_reaches_the_rule(registry):
    _, report, decisions = judge_case("planned_sql.sql", registry)
    fired = {m.rule.id for d in decisions.values() for m in d.matches}
    assert report.counts().get("PLANNED") or report.counts().get("ERROR")
    assert fired & {"SQL-001", "SQL-002"}


# --- KPI-3, with the target's answer included -------------------------------------------------------------------

def expected_verdicts() -> tuple[dict[str, str], dict[str, bool]]:
    import yaml

    manifest = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
    expected = {r["name"].lower(): r["expected"] for u in manifest["units"] for r in u["routines"]}
    holdout = {r["name"].lower(): u["holdout"] for u in manifest["units"] for r in u["routines"]}
    return expected, holdout


def agreement(decisions, only_holdout: bool | None = None) -> tuple[int, int, list]:
    expected, holdout = expected_verdicts()
    agree = disagree = 0
    mismatches = []
    for routine_id, decision in sorted(decisions.items()):
        short = routine_id.split(".")[-1]
        key = short if short != "body" else routine_id.split(".")[0]
        if key not in expected:
            continue
        if only_holdout is not None and holdout[key] is not only_holdout:
            continue
        if expected[key] == decision.rule_verdict:
            agree += 1
        else:
            disagree += 1
            mismatches.append((routine_id, expected[key], decision.rule_verdict))
    return agree, disagree, mismatches


def test_the_capability_check_closes_the_gap_p2_2_left(checked):
    analysis, program_analysis = checked
    decisions = decide(analysis.program, program_analysis, RuleSet.load(), Evidence())
    assert decisions["pkg_order_status.status_for_customer"].rule_verdict == "REVIEW"
    assert "SELECT-001" in {m.rule.id for m in decisions["pkg_order_status.status_for_customer"].matches}


def test_agreement_with_the_manifest(checked):
    analysis, program_analysis = checked
    decisions = decide(analysis.program, program_analysis, RuleSet.load(), Evidence())
    agree, disagree, mismatches = agreement(decisions)
    assert disagree == 0, mismatches
    assert agree == 65   # 56 + six routines of #29-25 (two triggers, pkg_line_edit) + three of #29-23 (pkg_contact)


def test_agreement_on_the_holdout_is_reported_separately(checked):
    """The holdout is the only part of the rate that is not self-scored (fixtures/plsql/README.md)."""
    analysis, program_analysis = checked
    decisions = decide(analysis.program, program_analysis, RuleSet.load(), Evidence())
    agree, disagree, mismatches = agreement(decisions, only_holdout=True)
    assert agree + disagree == 18
    assert disagree == 0, mismatches


def test_no_auto_prohibition_is_missed(checked):
    analysis, program_analysis = checked
    decisions = decide(analysis.program, program_analysis, RuleSet.load(), Evidence())
    expected, _ = expected_verdicts()
    missed = [rid for rid, d in decisions.items()
              if expected.get(rid.split(".")[-1]) == "REDESIGN" and d.rule_verdict == "AUTO"]
    assert missed == []


# --- the pipeline ------------------------------------------------------------------------------------------------

def test_the_report_carries_the_capability_numbers(checked):
    from plsql.report import inventory

    analysis, _ = checked
    data = inventory(analysis)["targetCapability"]
    assert data is not None
    assert 0.0 < data["runnableRate"] < 1.0
    assert set(data["statuses"]) <= {"OK", "WARN", "PLANNED", "ERROR"}


def test_the_report_leaves_the_capability_unasked_when_no_schema_is_given():
    from plsql.report import inventory

    analysis = build_analysis(SRC, SRC / "schema.sql")
    assert inventory(analysis)["targetCapability"] is None
