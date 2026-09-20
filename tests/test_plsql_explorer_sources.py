"""The code on the page: the source files, what is on each line, and whether the database runs the same thing.

The database side is `fixtures/explorer/snapshot-with-source.json`, taken from a real Oracle into which exactly the
files under `fixtures/explorer/src` were loaded -- so "the same" here is a statement about USER_SOURCE as Oracle
really stores it, not about a string this test made up.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

from plsql.cli import main as analyse_cli
from plsql.explorer import appsql, model, sources
from plsql.explorer import snapshot as S

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "explorer"
SRC = FIXTURE / "src"


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    out = tmp_path_factory.mktemp("explorer-sources")
    assert analyse_cli([str(SRC), "--out-dir", str(out), "--quiet"]) == 0
    return out


def build(analysis, snapshot: str | None = "snapshot-with-source.json", src=SRC, **kwargs):
    return model.build(analysis, S.load(FIXTURE / snapshot) if snapshot else None,
                       appsql.collect([FIXTURE / "app"]), src_root=src, app_files=appsql.files([FIXTURE / "app"]),
                       **kwargs)


def routine(data, name):
    return next(r for r in data["routines"] if r["id"] == name)


def marks(data, file, line):
    return [m for m in data["files"][file]["marks"] if m["line"] == line]


@pytest.fixture(scope="module")
def data(analysis):
    return build(analysis)


# --- the code and what is on its lines -------------------------------------------------------------------------

def test_the_source_of_a_routine_is_there_by_line_with_what_each_line_does(data):
    """AE1."""
    create = routine(data, "create_order")
    lines = data["files"][create["file"]]["lines"]
    assert (create["line"], create["endLine"]) == (1, 69) and len(lines) >= 69
    assert lines[15].strip().startswith("SELECT stock_quantity")
    kinds = {m["kind"]: m for m in marks(data, "create_order.prc", 16)}
    assert kinds["sql"]["reads"] == ["products"] and kinds["sql"]["endLine"] == 22
    assert kinds["lock"]["label"] == "FOR UPDATE" and kinds["diagnostic"]["label"] == "ROW_LOCK"


def test_without_the_source_the_rest_of_the_page_is_what_it_was(analysis):
    """AE2."""
    data = model.build(analysis, S.load(FIXTURE / "snapshot.json"), appsql.collect([FIXTURE / "app"]))
    assert data["files"] == {}
    create = routine(data, "create_order")
    assert len(create["sql"]) == 4 and create["verdict"] and create["decision"]["rules"]


def test_raise_handler_and_dynamic_sql_each_mark_their_line(data):
    assert [m["label"] for m in marks(data, "create_order.prc", 12) if m["kind"] == "raise"] == ["RAISE -20001"]
    assert any(m["kind"] == "handler" and "NO_DATA_FOUND" in m["label"]
               for m in data["files"]["create_order.prc"]["marks"])
    assert [m["kind"] for m in marks(data, "purge_table.prc", 7) if m["kind"] == "dynamic"] == ["dynamic"]


def test_a_call_inside_an_expression_marks_no_line_but_is_still_a_call_of_the_routine(data):
    """`v_already := shipped_count(p_order_id)` is no statement of its own in the IR, so there is no line to put a
    mark on. The call graph knows it, and the routine's list of callees shows it."""
    assert not [m for m in data["files"]["pkg_shipping.pkb"]["marks"] if m["kind"] == "call"]
    assert routine(data, "pkg_shipping.mark_shipped")["calls"] == ["pkg_shipping.shipped_count"]


def test_what_the_analysis_added_marks_no_line_and_declares_no_variable(data):
    """cancel_order's UPDATE of ORDERS fires a trigger. The woven-in SELECT, call and variable sit on the UPDATE's
    lines in the IR, and none of them is in the file."""
    on_update = marks(data, "cancel_order.prc", 25)
    assert [m["kind"] for m in on_update if m["kind"] in ("sql", "call")] == ["sql"]
    assert [m["writes"] for m in on_update if m["kind"] == "sql"] == [["orders"]]
    assert [d["name"] for d in routine(data, "cancel_order")["declarations"]] == ["v_status"]


def test_a_parameter_has_the_type_as_written_and_as_resolved(data):
    first = routine(data, "create_order")["parameters"][0]
    assert (first["name"], first["written"], first["resolved"]) == \
        ("p_customer_id", "customers.customer_id%TYPE", "NUMBER(10)")
    assert len(routine(data, "create_order")["parameters"]) == 4


def test_the_application_sql_file_is_there_whole_with_a_mark_on_each_statement(data):
    script = data["files"]["app:shipping.sql"]
    assert script["origin"] == "app" and script["lines"][3].startswith("INSERT INTO shipments")
    assert [(m["line"], m["label"]) for m in script["marks"]] == \
        [(4, "INSERT"), (8, "UPDATE"), (11, "SELECT"), (16, "?"), (19, "UPDATE")]
    assert script["marks"][3]["kind"] == "dynamic", "the statement nobody can parse is marked as not visible"


def test_a_source_file_that_is_not_there_is_a_warning_and_not_a_failure(analysis, tmp_path):
    partial = tmp_path / "src"
    shutil.copytree(SRC, partial)
    (partial / "purge_table.prc").unlink()
    data = build(analysis, src=partial)
    assert any("purge_table.prc" in w for w in data["warnings"])
    assert data["files"]["purge_table.prc"] == {"origin": "plsql", "found": False, "lines": [], "marks": []}
    assert data["files"]["create_order.prc"]["found"]


# --- what the verdict rests on ------------------------------------------------------------------------------------

def test_the_verdict_comes_with_its_rules_its_reasons_and_what_to_do(data):
    decision = routine(data, "create_order")["decision"]
    assert {"LOCK-001", "EXC-001"} <= {r["id"] for r in decision["rules"]}
    assert all(r["message"] and r["decision"] for r in decision["rules"])
    assert decision["whyNotAuto"] and decision["remediation"] and "concurrent_update" in decision["requiredTests"]
    assert "testEvidence" in decision["zeroFactors"] and decision["confidence"]["ruleCoverage"] == 1.0


def test_a_diagnostic_is_on_its_line_and_on_its_routine(data):
    lock = [d for d in routine(data, "cancel_order")["diagnostics"] if d["rule"] == "ROW_LOCK"]
    assert lock and lock[0]["line"] == 6
    assert routine(data, "report_open_orders")["diagnostics"] == []


def test_without_decisions_nothing_is_claimed_about_the_verdict(analysis, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    for name in ("program.ir.json", "callgraph.json"):
        shutil.copy(analysis / name, bare / name)
    data = model.build(bare)
    assert routine(data, "create_order")["decision"] is None and routine(data, "create_order")["diagnostics"] == []
    assert any("decisions.json" in w for w in data["warnings"])


# --- the same code, or not ------------------------------------------------------------------------------------------

def test_what_was_loaded_into_the_database_compares_as_the_same(data):
    states = {r["id"]: [(c["type"], c["state"]) for c in r["dbCode"]] for r in data["routines"]}
    assert states["create_order"] == [("PROCEDURE", "same")]
    assert states["trg_orders_audit.body"] == [("TRIGGER", "same")]
    assert states["pkg_shipping.mark_shipped"] == [("PACKAGE BODY", "same"), ("PACKAGE", "same")], \
        "a package is two objects; the specification comes from the sibling .pks"
    assert all(state == "same" for pairs in states.values() for _, state in pairs)


def test_one_changed_line_is_a_difference_and_the_diff_names_the_files_line(analysis, tmp_path):
    """AE5. Line 28 of create_order.prc sits below two blank lines: the diff must still call it 28."""
    changed = tmp_path / "src"
    shutil.copytree(SRC, changed)
    path = changed / "create_order.prc"
    lines = path.read_text(encoding="utf-8").split("\n")
    assert "ROUND(v_unit_price * p_quantity, 2)" in lines[27]
    lines[27] = lines[27].replace(", 2)", ", 0)")
    path.write_text("\n".join(lines), encoding="utf-8")
    (code,) = routine(build(analysis, src=changed), "create_order")["dbCode"]
    assert code["state"] == "different"
    assert "-    v_total_amount := ROUND(v_unit_price * p_quantity, 0);" in code["diff"]
    assert "+    v_total_amount := ROUND(v_unit_price * p_quantity, 2);" in code["diff"]
    assert "@@ -26,5 " in code["diff"], code["diff"]


@pytest.mark.parametrize("edit", [
    lambda t: t.replace("CREATE OR REPLACE PROCEDURE", "create procedure".upper().replace("CREATE ", "CREATE OR REPLACE EDITIONABLE ")),
    lambda t: t.replace("\n/\n", "\n"),
    lambda t: t.replace("BEGIN\n", "BEGIN   \n\n\n", 1),
    lambda t: "\n\n" + t,
])
def test_how_a_file_differs_from_user_source_without_differing_is_not_a_difference(analysis, tmp_path, edit):
    changed = tmp_path / "src"
    shutil.copytree(SRC, changed)
    path = changed / "purge_table.prc"
    path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
    out = tmp_path / "analysis"
    assert analyse_cli([str(changed), "--out-dir", str(out), "--quiet"]) == 0
    assert [c["state"] for c in routine(build(out, src=changed), "purge_table")["dbCode"]] == ["same"]


def test_a_routine_only_the_database_has_is_listed_with_its_code_when_the_code_was_taken(analysis):
    """AE6."""
    with_code = build(analysis)["dbObjects"]
    archive = next(o for o in with_code["dbOnly"] if o["name"] == "archive_shipments")
    assert archive["type"] == "PROCEDURE" and "DELETE FROM shipments" in archive["text"]
    assert {o["name"] for o in with_code["dbOnly"]} == {"archive_shipments", "trg_items_stock_guard"}
    without = build(analysis, "snapshot.json")["dbObjects"]
    assert next(o for o in without["dbOnly"] if o["name"] == "archive_shipments")["text"] is None
    assert without["containsSourceCode"] is False


def test_code_nobody_took_is_not_compared_and_says_why(analysis):
    for snapshot, reason in (("snapshot.json", sources.NOT_COMPARED_NO_CODE), (None, sources.NOT_COMPARED_NO_SNAPSHOT),
                             ("snapshot-v1.json", sources.NOT_COMPARED_NO_CODE)):
        (code,) = routine(build(analysis, snapshot), "create_order")["dbCode"]
        assert (code["state"], code["reason"]) == ("not_compared", reason)
    assert build(analysis, "snapshot-v1.json")["dbObjects"]["state"] == "not_collected"


def test_a_package_given_without_its_specification_leaves_the_specification_as_database_only(analysis, tmp_path):
    body_only = tmp_path / "src"
    shutil.copytree(SRC, body_only)
    (body_only / "pkg_shipping.pks").unlink()
    data = build(analysis, src=body_only)
    states = [(c["type"], c["state"]) for c in routine(data, "pkg_shipping.mark_shipped")["dbCode"]]
    assert states == [("PACKAGE BODY", "same"), ("PACKAGE", "not_compared")]
    assert ("PACKAGE", "pkg_shipping") in {(o["type"], o["name"]) for o in data["dbObjects"]["dbOnly"]}


def test_wrapped_code_is_not_compared_and_a_type_is_not_a_missing_source(analysis):
    document = S.load(FIXTURE / "snapshot-with-source.json")
    document.sections["sources"] = [
        {"type": "PROCEDURE", "name": "create_order", "wrapped": True} if row["name"] == "create_order" else row
        for row in document.sections["sources"]]
    document.sections["objects"].append({"name": "t_money", "type": "TYPE", "status": "VALID"})
    data = model.build(analysis, document, [], src_root=SRC)
    (code,) = routine(data, "create_order")["dbCode"]
    assert (code["state"], code["reason"]) == ("not_compared", sources.NOT_COMPARED_WRAPPED)
    assert [o["name"] for o in data["dbObjects"]["outsideAnalysis"]] == ["t_money"]
    assert "t_money" not in {o["name"] for o in data["dbObjects"]["dbOnly"]}


def test_without_the_source_nothing_is_called_database_only_on_a_guess(analysis):
    """Whether a specification was handed over can only be known by looking for the file."""
    data = model.build(analysis, S.load(FIXTURE / "snapshot-with-source.json"), [])
    assert {o["name"] for o in data["dbObjects"]["dbOnly"]} == {"archive_shipments", "trg_items_stock_guard"}


def test_the_same_input_gives_the_same_document(analysis):
    import json
    assert json.dumps(build(analysis), sort_keys=True) == json.dumps(build(analysis), sort_keys=True)
