"""P1-7: the inventory, the SARIF diagnostics and the summary.

Phase 1 converts nothing; what it owes is an honest picture. These tests check the picture is honest: the KPI
denominators are the ones the KPI document defines, the caveat about the corpus survives, and a diagnostic can be
opened on the line it belongs to.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from plsql.cli import main
from plsql.report import analyse, auto_blockers, inventory, markdown, sarif, write

SRC = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql" / "src"
SUFFIXES = {".pks", ".pkb", ".prc", ".trg"}


@pytest.fixture(scope="module")
def analysis():
    return analyse(SRC, SRC / "schema.sql")


# --- the numbers are the ones the KPI document defines ---------------------------------------------------

def test_the_parse_rate_denominator_is_every_source_file(analysis):
    """KPI-1 counts every *.pks/*.pkb/*.prc/*.trg. Parsing a spec only for its names would shrink it silently."""
    on_disk = [p for p in SRC.rglob("*") if p.suffix in SUFFIXES]
    assert inventory(analysis)["kpi"]["totalFiles"] == len(on_disk)


def test_the_kpis_meet_the_phase_1_targets(analysis):
    kpi = inventory(analysis)["kpi"]
    assert kpi["parseRate"] >= 0.90, kpi["failedFiles"]
    assert kpi["typeResolutionRate"] >= 0.95


def test_the_report_repeats_that_the_corpus_is_synthetic(analysis):
    """Without this line a reader takes 100% as evidence of real-world robustness."""
    assert inventory(analysis)["corpus"]["origin"] == "synthetic"
    assert "合成 corpus" in markdown(analysis)


def test_the_totals_match_the_lowered_program(analysis):
    data = inventory(analysis)
    assert data["totals"]["modules"] == len(analysis.program.modules)
    assert data["totals"]["routines"] == sum(len(m.routines) for m in analysis.program.modules)
    assert data["totals"]["statements"] == sum(data["statementKinds"].values())


# --- AUTO blockers ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("routine_id,expected", [
    ("prc_nightly_close", "transaction-control-in-routine"),
    ("prc_audit_autonomous", "autonomous-transaction"),
    ("prc_remote_sync", "db-link"),
    ("trg_orders_audit.body", "trigger"),
    ("pkg_dynamic_search.purge", "dynamic-sql"),
    ("pkg_stock_reserve.reserve", "row-lock"),
])
def test_the_corpus_blockers_are_found(analysis, routine_id: str, expected: str):
    found = {r.id: auto_blockers(m, r) for m, r in analysis.routines()}
    assert expected in found[routine_id], f"{routine_id}: {found[routine_id]}"


def test_row_locking_inside_a_cursor_declaration_is_found(analysis):
    """`claim_batch` locks in its cursor, not in a statement; looking only at statements would miss it."""
    found = {r.id: auto_blockers(m, r) for m, r in analysis.routines()}
    assert "row-lock" in found["pkg_stock_reserve.claim_batch"]


def test_a_plain_routine_has_no_blockers(analysis):
    found = {r.id: auto_blockers(m, r) for m, r in analysis.routines()}
    assert found["pkg_customer_crud.create_customer"] == []


def test_blocker_names_match_the_kpi_document():
    document = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "design" / "plsql-kpi.md").read_text(
        encoding="utf-8")
    for keyword in ["COMMIT", "AUTONOMOUS_TRANSACTION", "Package 変数", "EXECUTE IMMEDIATE",
                    "Trigger", "AUTHID CURRENT_USER", "DB Link", "FOR UPDATE"]:
        assert keyword in document


# --- SARIF ------------------------------------------------------------------------------------------------

def test_sarif_is_well_formed_and_positions_its_results(analysis):
    document = sarif(analysis)
    assert document["version"] == "2.1.0"
    results = document["runs"][0]["results"]
    assert results, "the corpus produces diagnostics; an empty run would mean they were lost"
    located = [r for r in results if "locations" in r]
    assert located, "a diagnostic with no position cannot be shown on a line"
    region = located[0]["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] >= 1


def test_every_sarif_rule_is_declared(analysis):
    document = sarif(analysis)
    declared = {rule["id"] for rule in document["runs"][0]["tool"]["driver"]["rules"]}
    used = {result["ruleId"] for result in document["runs"][0]["results"]}
    assert used <= declared


def test_sarif_levels_are_the_sarif_vocabulary(analysis):
    levels = {r["level"] for r in sarif(analysis)["runs"][0]["results"]}
    assert levels <= {"error", "warning", "note"}


# --- files ---------------------------------------------------------------------------------------------------

def test_write_produces_all_four_artifacts(tmp_path, analysis):
    written = write(analysis, tmp_path)
    assert set(written) == {"inventory", "diagnostics", "summary", "ir"}
    for path in written.values():
        assert path.exists() and path.stat().st_size > 0
    json.loads(written["inventory"].read_text(encoding="utf-8"))
    json.loads(written["diagnostics"].read_text(encoding="utf-8"))


def test_the_written_ir_can_be_read_back(tmp_path, analysis):
    from plsql.ir import serde

    written = write(analysis, tmp_path)
    program = serde.loads(written["ir"].read_text(encoding="utf-8"))
    serde.validate(program)
    assert len(program.modules) == len(analysis.program.modules)


# --- the command ------------------------------------------------------------------------------------------------

def test_the_cli_succeeds_on_the_corpus(tmp_path, capsys):
    assert main([str(SRC), "--out-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "parse rate" in out and "type resolution" in out


def test_the_cli_fails_when_something_does_not_parse(tmp_path, capsys):
    """The exit status is what a pipeline gates on, so a parse failure has to reach it."""
    (tmp_path / "broken.pkb").write_text("BEGIN IF THEN END;\n/\n", encoding="utf-8")
    assert main([str(tmp_path), "--quiet"]) == 1


def test_warnings_alone_do_not_fail_the_run(tmp_path):
    """Phase 1 exists to show unresolved things, not to hide the run behind them."""
    (tmp_path / "p.prc").write_text(
        "CREATE OR REPLACE PROCEDURE p IS\n  v no_such_table%ROWTYPE;\nBEGIN\n  COMMIT;\nEND;\n/\n",
        encoding="utf-8")
    assert main([str(tmp_path), "--quiet"]) == 0


def test_the_cli_finds_the_schema_next_to_the_sources(tmp_path, capsys):
    (tmp_path / "schema.sql").write_text("CREATE TABLE t (id NUMBER(9));\n", encoding="utf-8")
    (tmp_path / "p.prc").write_text(
        "CREATE OR REPLACE PROCEDURE p IS\n  v t.id%TYPE;\nBEGIN\n  NULL;\nEND;\n/\n", encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert "type resolution 100.0%" in capsys.readouterr().out


# --- the call graph is a file, not only something the analysis held in memory --------------------------------

@pytest.fixture(scope="module")
def call_graph(tmp_path_factory):
    out = tmp_path_factory.mktemp("callgraph")
    assert main([str(SRC), "--out-dir", str(out), "--quiet"]) == 0
    document = json.loads((out / "callgraph.json").read_text(encoding="utf-8"))
    return {record["routine"]: record for record in document["routines"]}, out


def test_a_function_called_inside_an_expression_is_an_edge(call_graph):
    """`v_tier := customer_tier(p_order_id)` is no Call statement in program.ir.json, so a reader of the files
    alone never saw it. The explorer reads files."""
    graph, _ = call_graph
    callers = [r for r, record in graph.items() if "pkg_order_pricing.customer_tier" in record["calls"]]
    assert callers, "nobody is recorded as calling customer_tier"


def test_every_routine_is_listed_even_when_it_calls_nothing(call_graph, analysis):
    graph, _ = call_graph
    routines = {r.id for m in analysis.program.modules for r in m.routines}
    assert set(graph) == routines
    assert any(not record["calls"] and not record["external"] for record in graph.values())


def test_a_callee_nobody_could_resolve_is_kept_by_name(call_graph, analysis):
    graph, _ = call_graph
    routines = set(graph)
    external = {name for record in graph.values() for name in record["external"]}
    assert external, "the corpus calls nothing outside itself? dbms_output at least"
    assert not external & routines


def test_the_call_graph_is_one_more_file_and_nothing_else(call_graph):
    """The files a reader already depends on keep their names, and the graph is not folded into one of them."""
    _, out = call_graph
    names = {p.name for p in out.iterdir()}
    assert {"inventory.json", "diagnostics.sarif", "summary.md", "program.ir.json", "decisions.json",
            "callgraph.json"} <= names
    assert "calls" not in json.loads((out / "inventory.json").read_text(encoding="utf-8"))
