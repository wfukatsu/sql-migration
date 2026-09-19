"""A DB link, once somebody decides where it leads (decided 2026-09-20: the remote tables come under ScalarDB as
another namespace, so the routine writes two databases in one ScalarDB transaction, as the distributed transaction did)."""

from __future__ import annotations

import pytest

from plsql import redesign
from plsql.analysis import analyse as analyse_program
from plsql.limits import DbLinks
from plsql.lower import _walk
from plsql.report import analyse
from plsql.rules.engine import Evidence, RuleSet, decide

SRC = "fixtures/plsql/src"
SCALARDB = "fixtures/plsql/scalardb-schema.json"
LIMITS = "fixtures/plsql/limits.yaml"


def _remote_sync(**decided):
    analysis = analyse(SRC, f"{SRC}/schema.sql", scalardb_schema=SCALARDB, **decided)
    routine = next(r for m in analysis.program.modules for r in m.routines if r.id == "prc_remote_sync")
    return analysis, [s for s in _walk(routine.body) if s.kind == "SqlOperation"]


def test_the_decision_names_the_namespace_and_says_why():
    links = DbLinks.load(LIMITS)
    assert links.namespace("warehouse_link") == "warehouse" and links.namespace("WAREHOUSE_LINK") == "warehouse"
    assert links.why("warehouse_link") and links.namespace("some_other_link") is None


def test_a_mapped_link_becomes_a_table_of_that_namespace():
    decided = redesign.Decided.load(LIMITS)
    _, statements = _remote_sync(**decided.for_analysis())
    remote = [s for s in statements if any(d.code == "DBLINK_MAPPED" for d in s.diagnostics)]
    assert len(remote) == 2
    assert "UPDATE warehouse.orders SET" in remote[0].original_sql and "@" not in remote[0].original_sql
    assert "INSERT INTO warehouse.shipment_queue" in remote[1].original_sql
    # ScalarDB runs them as they stand, in the namespace the link was mapped to -- and the local `orders` of the
    # first statement is still the local one
    assert [s.target_status for s in statements] == ["OK", "OK", "OK"]
    assert "warehouse.orders" in remote[0].target_sql[0] and "warehouse" not in statements[0].target_sql[0]


def test_a_link_nobody_mapped_is_left_alone():
    _, statements = _remote_sync()
    assert not any(d.code == "DBLINK_MAPPED" for s in statements for d in s.diagnostics)
    assert "@warehouse_link" in statements[1].original_sql.lower()


def test_the_verdict_stays_redesign_and_the_state_says_decided():
    decided = redesign.Decided.load(LIMITS)
    analysis, _ = _remote_sync(**decided.for_analysis())
    program_analysis = analyse_program(analysis.program)
    evidence = Evidence(captures={"prc_remote_sync": (1, 1)})
    decisions = decide(analysis.program, program_analysis, RuleSet.load(), evidence)
    assert decisions["prc_remote_sync"].rule_verdict == "REDESIGN", "a DB link is never AUTO"
    status = redesign.statuses(analysis.program, decisions, program_analysis.call_graph, decided, evidence)["prc_remote_sync"]
    assert status.open == [] and status.state == "verified"
    assert {d["rule"]: d["decidedBy"] for d in status.decisions} == {
        "LINK-001": "limits.yaml: dbLinks", "TX-001": "limits.yaml: transactions.separate"}
