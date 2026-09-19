"""P2-3: the safety net, proved rather than assumed.

"No AUTO prohibition is missed" measured on a corpus that does not contain the construct proves nothing. Before
these fixtures existed, ten rules had never fired even once -- including package state, `AUTHID CURRENT_USER`,
`DBMS_SQL`, GOTO and write-then-scan.

Each case here is the smallest file that exercises one prohibition, kept out of `fixtures/plsql/src/` so the KPI
denominators do not move. The last test is the one that matters most: no rule may exist without something that
makes it fire.
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.analysis import analyse as analyse_program
from plsql.frontend import parse_file
from plsql.ir import model as M
from plsql.lower import lower_file
from plsql.report import analyse as build_analysis
from plsql.rules.engine import Evidence, RuleSet, decide
from plsql.symbols import OracleSchema, build

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
CASES = FIXTURES / "rule-cases"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def schema() -> OracleSchema:
    return OracleSchema.from_ddl(SRC / "schema.sql")


@pytest.fixture(scope="module")
def ruleset() -> RuleSet:
    return RuleSet.load()


def judge(path: pathlib.Path, schema: OracleSchema, ruleset: RuleSet):
    parsed = parse_file(path)
    symbols = build(parsed, schema)
    program = M.Program(id=path.stem, kind="Program", modules=lower_file(parsed, symbols, schema))
    decisions = decide(program, analyse_program(program), ruleset, Evidence())
    fired = {m.rule.id for d in decisions.values() for m in d.matches}
    return decisions, fired


# --- every prohibition fires ------------------------------------------------------------------------------

@pytest.mark.parametrize("case,rule_id,verdict", [
    ("package_state.sql", "STATE-001", "REDESIGN"),
    ("authid_current_user.sql", "AUTHID-001", "REDESIGN"),
    ("dbms_sql.sql", "DYN-003", "REDESIGN"),
    ("external_package.sql", "EXT-001", "REDESIGN"),
    ("goto.sql", "LOWER-002", "REDESIGN"),
    ("write_then_scan.sql", "TX-004", "REDESIGN"),
    ("recursive.sql", "RECUR-001", "REVIEW"),
    # P4-3: the boundary of what the recorded Oracle evidence covers. Each of these is a date construct the
    # fixture does not answer for, so each must still reach a person.
    ("timezone_dependent.sql", "SEM-002", "REVIEW"),
    ("clock_read_twice.sql", "SEM-007", "REVIEW"),
    ("nls_date_format.sql", "SEM-008", "REVIEW"),
    ("timestamp_cast_to_date.sql", "SEM-009", "REVIEW"),
    ("systimestamp_written.sql", "SEM-010", "REVIEW"),
])
def test_the_prohibition_fires_on_its_case(schema, ruleset, case: str, rule_id: str, verdict: str):
    decisions, fired = judge(CASES / case, schema, ruleset)
    assert rule_id in fired, f"{case}: fired {sorted(fired)}"
    assert verdict in {d.rule_verdict for d in decisions.values()}


@pytest.mark.parametrize("case,rule_id,verdict", [
    ("prc_nightly_close.prc", "TX-001", "REDESIGN"),
    ("prc_audit_autonomous.prc", "TX-002", "REDESIGN"),
    ("trg_orders_audit.trg", "TRG-001", "REDESIGN"),
    ("prc_remote_sync.prc", "LINK-001", "REDESIGN"),
    ("pkg_dynamic_search.pkb", "DYN-001", "REDESIGN"),
    ("pkg_stock_reserve.pkb", "LOCK-001", "REDESIGN"),
])
def test_the_prohibitions_the_corpus_covers_fire_there(schema, ruleset, case: str, rule_id: str, verdict: str):
    decisions, fired = judge(SRC / case, schema, ruleset)
    assert rule_id in fired
    assert verdict in {d.rule_verdict for d in decisions.values()}


def test_an_unresolved_type_keeps_a_routine_out_of_auto(schema, ruleset):
    """No rule objects to this routine; the confidence does, because a factor is 0."""
    decisions, fired = judge(CASES / "unresolved_type.sql", schema, ruleset)
    decision = decisions["prc_unresolved_type"]
    assert fired == set(), "this is a confidence case, not a rule case"
    assert decision.rule_verdict == "AUTO"
    assert decision.verdict == "REVIEW"
    assert decision.confidence.type_resolution == 0.0


def test_direct_recursion_is_a_cycle(schema, ruleset):
    """Regression: self-edges were excluded from the call graph, hiding the plainest recursion there is."""
    parsed = parse_file(CASES / "recursive.sql")
    program = M.Program(id="r", kind="Program",
                        modules=lower_file(parsed, build(parsed, schema), schema))
    assert analyse_program(program).call_graph.cycles()


def test_package_state_is_seen_in_a_package_body(schema):
    """Regression: a package body declares state under `Package_obj_body`, not inside a `Declare_spec`."""
    parsed = parse_file(CASES / "package_state.sql")
    module = lower_file(parsed, build(parsed, schema), schema)[0]
    assert module.has_package_state


# --- no rule exists untested ---------------------------------------------------------------------------------

def test_every_rule_fires_somewhere(schema, ruleset):
    """A rule nothing exercises is a rule nobody has checked.

    The sweep includes the ScalarDB capability check, because the rules that need a target verdict (SQL-001,
    SQL-002, SCAN-001, SELECT-001) can only fire once it has run. Nothing is exempt.
    """
    from plsql import triggers
    from plsql.capability import annotate, check
    from scalardb_migrate.schema import SchemaRegistry

    registry = SchemaRegistry.from_schema_loader_json(str(FIXTURES / "scalardb-schema.json"))
    fired: set[str] = set()

    # 決定を渡さない解析と、渡した解析の両方を見る。どちらも実際に使う形である——記録された
    # routine は書き換わり（MERGE の分割など）、書き換えた形にだけ当たる規則がある（SEM-011）
    from plsql.limits import Limits, RowLocks

    # the notes that replace a REVIEW once somebody decided (CUR-OPT-002, BULK-OPT-003) fire only with the decisions
    for row_locks, limits in ((None, None), (RowLocks.load(FIXTURES / "limits.yaml"),
                                             Limits.load(FIXTURES / "limits.yaml"))):
        corpus = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json",
                                row_locks=row_locks, limits=limits)
        for decision in decide(corpus.program, analyse_program(corpus.program), ruleset,
                               Evidence()).values():
            fired |= {m.rule.id for m in decision.matches}

    for case in sorted(CASES.glob("*.sql")):
        parsed = parse_file(case)
        symbols = build(parsed, schema)
        program = M.Program(id=case.stem, kind="Program", modules=lower_file(parsed, symbols, schema))
        triggers.rewrite(program, schema, symbols)   # as the analysis does: TRG-002 reads what this leaves behind
        report = check(program, registry, symbols)
        annotate(program, report)
        for decision in decide(program, analyse_program(program), ruleset, Evidence()).values():
            fired |= {m.rule.id for m in decision.matches}

    never = {r.id for r in ruleset.rules} - fired
    assert never == set(), f"rules nothing exercises: {sorted(never)}"


def test_the_rule_cases_stay_out_of_the_corpus():
    """They exist to fire rules, not to be migrated; mixing them in would move the KPI denominators."""
    corpus_files = {p.name for p in SRC.rglob("*") if p.suffix in {".pks", ".pkb", ".prc", ".trg"}}
    case_files = {p.name for p in CASES.glob("*.sql")}
    assert corpus_files & case_files == set()
    assert not any(p.suffix in {".pks", ".pkb", ".prc", ".trg"} for p in CASES.iterdir())


def test_the_corpus_kpi_is_unchanged_by_the_rule_cases():
    inventory = build_analysis(SRC, SRC / "schema.sql")
    from plsql.report import inventory as build_inventory

    assert build_inventory(inventory)["kpi"]["totalFiles"] == 44
