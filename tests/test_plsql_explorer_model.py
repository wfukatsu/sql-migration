"""The document the explorer page is drawn from: tables, routines and SQL, joined both ways.

What matters most is what it does *not* claim: a number nobody collected is not 0, a statement nobody can read is
not absent, and a name that is not a table is not listed as one.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from plsql.cli import main as analyse_cli
from plsql.explorer import appsql, model
from plsql.explorer import snapshot as S

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "explorer"
CORPUS = ROOT / "fixtures" / "plsql" / "src"


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    out = tmp_path_factory.mktemp("explorer-analysis")
    assert analyse_cli([str(FIXTURE / "src"), "--out-dir", str(out), "--quiet"]) == 0
    return out


@pytest.fixture(scope="module")
def app():
    return appsql.collect([FIXTURE / "app"])


def build(analysis, app, name: str | None = "snapshot.json"):
    return model.build(analysis, S.load(FIXTURE / name) if name else None, app)


def table(data, name):
    return next(t for t in data["tables"] if t["name"] == name)


def statement(data, sql_id):
    return next(s for s in data["sql"] if s["id"] == sql_id)


@pytest.fixture(scope="module")
def data(analysis, app):
    return build(analysis, app)


# --- nothing collected is not zero ------------------------------------------------------------------------------

def test_without_a_snapshot_the_source_side_is_there_and_the_database_side_says_not_collected(analysis, app):
    """AE1."""
    orders = table(build(analysis, app, None), "orders")
    assert orders["writers"] == {"routines": 3, "statements": 1}, "create_order, cancel_order, mark_shipped + the app"
    for key in ("rows", "size", "triggerCount"):
        assert orders[key]["state"] == "not_collected", key
    assert orders["foreignKeysOut"] is None and orders["foreignKeysIn"] is None
    assert orders["triggerCount"]["known"] == 1, "the trigger whose source was given is still known"


def test_a_table_nobody_analysed_is_not_a_table_with_no_rows(analysis, app):
    """AE2."""
    no_stats = build(analysis, app, "snapshot-no-stats.json")
    assert table(no_stats, "orders")["rows"] == {"state": "no_statistics"}
    assert table(no_stats, "orders")["size"]["state"] == "value"
    with_stats = build(analysis, app)
    assert table(with_stats, "orders")["rows"]["value"] == 400
    assert table(with_stats, "audit_log")["rows"] == {"state": "no_statistics"}


# --- what cannot be seen is listed --------------------------------------------------------------------------------

def test_dynamic_sql_nobody_could_enumerate_is_listed_as_not_visible(data):
    """AE3."""
    (dynamic,) = [s for s in data["sql"] if s["routine"] == "purge_table"]
    assert not dynamic["visible"] and dynamic["reason"] == model.REASON_DYNAMIC
    assert dynamic["reads"] == [] and dynamic["writes"] == [] and dynamic["line"] == 7
    assert dynamic["id"] in data["unattributable"]


def test_a_statement_that_may_touch_anything_is_reachable_from_every_table(data):
    """R7: "this table has no writers" is only true if nothing is hiding."""
    for record in data["tables"]:
        assert "purge_table#stmt-1" in model.unseen_for(data, record["name"]), record["name"]


def test_an_application_statement_nobody_can_parse_keeps_its_file_and_line(data):
    broken = statement(data, "shipping.sql:16")
    assert not broken["visible"] and broken["file"] == "shipping.sql" and broken["line"] == 16
    assert broken["id"] in data["unattributable"]


def test_a_trigger_only_the_database_knows_is_attached_and_marked_as_having_no_source(data):
    """AE4."""
    (guard,) = [a for a in table(data, "order_items")["attached"] if a["name"] == "trg_items_stock_guard"]
    assert guard["inSnapshot"] and not guard["inSource"]
    body = statement(data, guard["sql"])
    assert not body["visible"] and body["reason"] == model.REASON_TRIGGER_NO_SOURCE
    assert table(data, "products")["unseen"] == [guard["sql"]], "the catalog says its body names PRODUCTS"
    assert guard["sql"] not in data["unattributable"], "so it does not have to be suspected of touching everything"


def test_a_trigger_in_the_source_and_in_the_database_is_one_trigger(data):
    audits = [a for a in table(data, "orders")["attached"] if a["name"] == "trg_orders_audit"]
    assert len(audits) == 1 and audits[0]["inSource"] and audits[0]["inSnapshot"]
    assert audits[0]["routine"] == "trg_orders_audit.body"
    assert table(data, "orders")["triggerCount"] == {"state": "value", "value": 1}


# --- a name is a table only if it is one --------------------------------------------------------------------------

def test_a_table_only_the_application_touches_is_not_untouched(data):
    """AE7."""
    customers = table(data, "customers")
    assert customers["writers"] == {"routines": 0, "statements": 1}
    assert customers["readers"] == {"routines": 0, "statements": 0}


def test_an_into_target_a_cte_and_dual_are_not_tables(data):
    names = {t["name"] for t in data["tables"]}
    assert not names & {"v_status", "p_lines", "p_today", "recent", "dual"}
    assert statement(data, "report_open_orders#stmt-2")["reads"] == ["orders"]
    assert statement(data, "report_open_orders#stmt-3")["reads"] == []
    assert statement(data, "report_open_orders#stmt-3")["visible"], "reading nothing is not the same as unreadable"


def test_sql_the_analysis_added_is_not_sql_of_the_source(data):
    """cancel_order's UPDATE of ORDERS fires a trigger; the analysis weaves a SELECT and a call in for it."""
    ids = [s["id"] for s in data["sql"] if s["routine"] == "cancel_order"]
    assert ids == ["cancel_order#stmt-1", "cancel_order#stmt-4#query", "cancel_order#stmt-5", "cancel_order#stmt-6",
                   "cancel_order#stmt-7"]
    cancel = next(r for r in data["routines"] if r["id"] == "cancel_order")
    assert cancel["calls"] == [], "and the trigger is not something cancel_order calls"


def test_a_remote_table_is_its_own_entry(tmp_path, analysis):
    script = tmp_path / "remote.sql"
    script.write_text("SELECT * FROM orders@warehouse_link;\n", encoding="utf-8")
    remote = build(analysis, appsql.collect([script]))
    assert table(remote, "orders@warehouse_link")["kind"] == "remote"
    assert table(remote, "orders@warehouse_link")["presence"] == "remote"
    assert "remote.sql:1" not in [t["sql"] for t in table(remote, "orders")["touches"]], "not the local ORDERS"


def test_a_table_the_snapshot_does_not_know_stays_on_the_list_and_says_so(tmp_path, analysis):
    script = tmp_path / "legacy.sql"
    script.write_text("DELETE FROM legacy_queue;\n", encoding="utf-8")
    legacy = table(build(analysis, appsql.collect([script])), "legacy_queue")
    assert legacy["presence"] == "missing" and legacy["writers"]["statements"] == 1


# --- views and foreign keys -----------------------------------------------------------------------------------------

def test_sql_that_reads_a_view_is_found_from_the_tables_under_it(data):
    via = [(t["sql"], t["via"]) for t in table(data, "order_items")["touches"] if t["via"]]
    assert ("report_open_orders#stmt-1", "v_order_lines") in via
    assert ("report_open_orders#stmt-1", "v_order_lines") in \
        [(t["sql"], t["via"]) for t in table(data, "orders")["touches"] if t["via"]], "through two views"
    assert table(data, "v_order_lines")["kind"] == "view"
    assert table(data, "v_order_lines")["baseTables"] == ["order_items", "orders"]
    assert {a["name"] for a in table(data, "orders")["attached"] if a["type"] == "view"} == \
        {"v_open_orders", "v_order_lines"}


def test_a_foreign_key_is_found_from_both_ends(data):
    assert {fk["refTable"] for fk in table(data, "order_items")["foreignKeysOut"]} == {"orders", "products"}
    assert {fk["table"] for fk in table(data, "orders")["foreignKeysIn"]} == {"order_items", "shipments"}


# --- routines ---------------------------------------------------------------------------------------------------------

def test_a_routine_carries_its_verdict_its_sql_and_where_it_is(data):
    create = next(r for r in data["routines"] if r["id"] == "create_order")
    assert create["verdict"] in ("AUTO", "REVIEW", "REDESIGN") and create["file"] == "create_order.prc"
    lock = statement(data, create["sql"][0])
    assert (lock["kind"], lock["reads"], lock["lock"], lock["line"]) == ("SELECT", ["products"], "FOR UPDATE", 16)
    assert any("AUTO" in note for note in data["notes"]), "why nothing is AUTO is said, not left to be wondered at"


def test_the_call_graph_reaches_the_page_both_ways(tmp_path):
    out = tmp_path / "corpus"
    assert analyse_cli([str(CORPUS), "--out-dir", str(out), "--quiet"]) == 0
    routines = {r["id"]: r for r in model.build(out)["routines"]}
    callers = routines["pkg_order_pricing.customer_tier"]["calledBy"]
    assert callers and all("pkg_order_pricing.customer_tier" in routines[c]["calls"] for c in callers)


# --- honesty about the input --------------------------------------------------------------------------------------------

def test_an_analysis_made_with_limits_is_warned_about(tmp_path, analysis):
    copy = tmp_path / "with-limits"
    copy.mkdir()
    for file in analysis.iterdir():
        (copy / file.name).write_bytes(file.read_bytes())
    decisions = json.loads((copy / "decisions.json").read_text(encoding="utf-8"))
    decisions["routines"][0]["redesign"] = {"status": "DECIDED"}
    (copy / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
    assert any("--limits" in w for w in model.build(copy)["warnings"])
    assert not any("--limits" in w for w in model.build(analysis)["warnings"])


def test_a_directory_that_is_not_an_analysis_says_what_to_run(tmp_path):
    with pytest.raises(model.InputError, match="python -m plsql.cli"):
        model.build(tmp_path)


def test_the_same_input_gives_the_same_document(analysis, app):
    assert json.dumps(build(analysis, app), sort_keys=True) == json.dumps(build(analysis, app), sort_keys=True)


# --- in step with the specification skill ---------------------------------------------------------------------------------

def test_the_source_sql_of_every_routine_in_the_corpus_is_what_the_specification_skill_finds(tmp_path):
    """KTD7: the test of "is this statement in the source" lives in two places. This keeps them saying the same."""
    path = ROOT / "skills" / "plsql-spec" / "scripts" / "spec_facts.py"
    spec = importlib.util.spec_from_file_location("spec_facts", path)
    spec_facts = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["spec_facts"] = spec_facts
    spec.loader.exec_module(spec_facts)

    out = tmp_path / "corpus"
    assert analyse_cli([str(CORPUS), "--out-dir", str(out), "--quiet"]) == 0
    _, facts, _ = spec_facts.load(out)
    mine: dict[str, set] = {}
    for s in model.build(out)["sql"]:
        if s["origin"] == "plsql":
            mine.setdefault(s["routine"], set()).add(s["id"].split("~")[0])
    assert model.FROM_SOURCE.pattern == spec_facts.FROM_SOURCE.pattern
    for routine, f in facts.items():
        assert len(mine.get(routine, ())) == len(f.sql) + len(f.dynamic), routine


# --- statistics and structure (version 2 of the snapshot) ------------------------------------------------------------

def test_an_index_is_there_with_its_columns_its_size_and_its_statistics(data):
    primary = next(i for i in table(data, "order_items")["indexes"] if i["name"] == "pk_e_order_items")
    assert primary["unique"] and primary["columns"] == ["order_id", "line_no"]
    assert primary["sizeBytes"] > 0 and primary["statistics"]["distinctKeys"] == 1200


def test_a_version_1_snapshot_shows_the_index_and_says_its_statistics_were_not_collected(analysis, app):
    """AE7."""
    old = build(analysis, app, "snapshot-v1.json")
    orders = table(old, "orders")
    assert orders["indexes"] and all(i["statistics"] is None for i in orders["indexes"])
    assert orders["modifications"] == {"state": "not_collected"} and orders["partitioning"] is None
    assert old["sequences"]["state"] == "not_collected" and old["dbObjects"]["state"] == "not_collected"
    assert old["meta"]["snapshot"]["formatVersion"] == 1


def test_writes_since_the_statistics_are_a_number_zero_or_not_collected(analysis, app, data):
    """AE8. Five and three rows went into two partitions of ORDER_EVENTS: eight, not sixteen."""
    assert table(data, "order_events")["modifications"]["inserts"] == 8
    assert table(data, "shipments")["modifications"]["deletes"] == 2
    assert table(data, "customers")["modifications"]["value"] == 0, "collected, and nothing was flushed for it"
    assert table(build(analysis, app, None), "customers")["modifications"] == {"state": "not_collected"}


def test_selectivity_and_the_share_of_nulls_are_computed_only_where_there_are_rows(data):
    status = next(c for c in table(data, "orders")["columnStatistics"] if c["column"] == "status")
    assert status["selectivity"] == 3 / 400 and status["nullRatio"] == 0
    assert table(data, "audit_log")["columnStatistics"] == [], "never analysed: nothing to compute from"


def test_partitioning_comments_and_the_columns_own_comments_reach_the_table(data):
    events = table(data, "order_events")
    assert (events["partitioning"]["type"], events["partitioning"]["keyColumns"]) == ("RANGE", ["event_date"])
    orders = table(data, "orders")
    assert orders["partitioning"] is False and orders["comment"].startswith("受注")
    assert next(c for c in orders["columns"] if c["name"] == "status")["comment"] == "RECEIVED / SHIPPED / CANCELLED"


def test_a_sequence_is_listed_with_who_uses_it_in_sql_or_in_an_assignment(data):
    users = {s["name"]: s["users"] for s in data["sequences"]["items"]}
    assert users["order_seq"] == ["create_order"], "p_order_id := order_seq.NEXTVAL is an assignment, not SQL"
    assert users["shipment_seq"] == ["pkg_shipping.mark_shipped"] and users["audit_seq"] == ["trg_orders_audit.body"]
