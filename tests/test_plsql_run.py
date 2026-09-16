"""P0-4: the capture format and the scenarios, checked without a database.

`difftest/plsql_run.py` needs Oracle to do its job, but everything that decides whether a capture is comparable --
the encoding, the row normalisation, the statement splitting, and whether a scenario points at something that
exists -- is pure and is checked here. Running the captures themselves is P0-5.
"""

from __future__ import annotations

import datetime
import decimal
import importlib.util
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "plsql"
SCENARIOS = FIXTURES / "scenarios"


def _load_runner():
    spec = importlib.util.spec_from_file_location("plsql_run", ROOT / "difftest" / "plsql_run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["plsql_run"] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def scenarios() -> list[dict]:
    out = []
    for path in sorted(SCENARIOS.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        spec["_file"] = path.name
        out.append(spec)
    return out


def manifest() -> dict:
    return yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))


# --- encoding -------------------------------------------------------------------------------------

def test_decimal_keeps_its_scale():
    """1.50 と 1.5 は別物である。float に落とすとこの差が消える。"""
    assert runner.encode(decimal.Decimal("1.50")) == {"$dec": "1.50"}
    assert runner.encode(decimal.Decimal("1.5")) == {"$dec": "1.5"}


def test_datetime_is_encoded_before_date():
    assert runner.encode(datetime.datetime(2026, 1, 15, 9, 30)) == {"$ts": "2026-01-15T09:30:00"}
    assert runner.encode(datetime.date(2026, 1, 15)) == {"$date": "2026-01-15"}


def test_null_and_empty_string_stay_distinct_in_the_capture():
    """Oracle では '' は NULL だが、捕捉した値をそのまま区別できる形で持っておく。"""
    assert runner.encode(None) is None
    assert runner.encode("") == ""


def test_row_order_is_normalised_but_duplicates_survive():
    rows = [[2, "b"], [1, "a"], [1, "a"]]
    assert runner.canonical_rows(rows) == [[1, "a"], [1, "a"], [2, "b"]]


def test_normalisation_is_idempotent():
    rows = [[3], [1], [2]]
    once = runner.canonical_rows(rows)
    assert runner.canonical_rows(once) == once


# --- statement splitting --------------------------------------------------------------------------

def test_plsql_is_split_on_a_lone_slash():
    text = "CREATE PROCEDURE a IS BEGIN NULL; END;\n/\nCREATE PROCEDURE b IS BEGIN NULL; END;\n/\n"
    assert len(runner.split_plsql(text)) == 2


def test_plsql_split_keeps_inner_semicolons():
    body = runner.split_plsql("BEGIN\n  NULL;\n  NULL;\nEND;\n/\n")[0]
    assert body.count(";") == 3


def test_sql_split_drops_comment_only_chunks():
    text = "-- a comment\nCREATE TABLE t (id NUMBER);\n\n-- another\nCREATE TABLE u (id NUMBER);\n"
    assert len(runner.split_sql(text)) == 2


# --- scenarios ------------------------------------------------------------------------------------

def test_there_is_at_least_one_scenario():
    assert scenarios()


@pytest.mark.parametrize("spec", scenarios(), ids=lambda s: s["_file"])
def test_scenario_has_the_required_shape(spec: dict):
    assert spec["name"] == spec["_file"].removesuffix(".yaml"), "file name and scenario name must agree"
    call = spec["call"]
    assert call["kind"] in {"function", "procedure", "block"}
    if call["kind"] == "block":
        assert call["body"].strip().upper().endswith("END;"), "a block scenario must be an anonymous block"
    else:
        assert "." in call["name"] or call["name"].startswith("prc_"), f"{call['name']} is not callable as written"
    if call["kind"] == "function":
        assert call.get("returns"), "a function scenario must say what it returns"
    for oracle_type in list((call.get("out") or {}).values()) + ([call["returns"]] if call.get("returns") else []):
        assert oracle_type.upper() in {"NUMBER", "VARCHAR2", "DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE"}


@pytest.mark.parametrize("spec", scenarios(), ids=lambda s: s["_file"])
def test_scenario_points_at_a_unit_and_routine_the_manifest_knows(spec: dict):
    units = {u["name"]: u for u in manifest()["units"]}
    assert spec["unit"] in units, f"{spec['name']}: unknown unit {spec['unit']}"
    routines = {r["name"].lower() for r in units[spec["unit"]]["routines"]}
    assert spec["routine"].lower() in routines, f"{spec['name']}: {spec['unit']} has no routine {spec['routine']}"


@pytest.mark.parametrize("spec", scenarios(), ids=lambda s: s["_file"])
def test_captured_and_masked_tables_exist(spec: dict):
    known = set(runner.TABLES)
    assert set(spec.get("capture_tables") or []) <= known, f"{spec['name']}: unknown table in capture_tables"
    assert set((spec.get("mask") or {})) <= known, f"{spec['name']}: unknown table in mask"


def test_a_scenario_masks_the_clock_columns_the_corpus_writes():
    """SYSTIMESTAMP は FIXED_DATE で固定できない。マスクしない限り 2 回実行で一致しない。"""
    masked = {table for s in scenarios() for table in (s.get("mask") or {})}
    assert "audit_log" in masked, "audit_log.changed_at is written from SYSTIMESTAMP and has to be masked somewhere"


def test_deploy_order_covers_every_corpus_file():
    on_disk = {str(p.relative_to(FIXTURES / "src")) for p in (FIXTURES / "src").rglob("*")
               if p.suffix in {".pks", ".pkb", ".prc", ".trg"}}
    assert set(runner.DEPLOY_ORDER) == on_disk


def test_package_specs_are_deployed_before_their_bodies():
    order = runner.DEPLOY_ORDER
    for name in order:
        if name.endswith(".pkb"):
            assert order.index(name.removesuffix(".pkb") + ".pks") < order.index(name), f"{name} before its spec"


# --- P0-5: golden capture ---------------------------------------------------------------------------

GOLDEN = FIXTURES / "golden"


def captures() -> list[pathlib.Path]:
    return sorted(GOLDEN.glob("*.json"))


def test_every_scenario_has_a_capture():
    import json  # noqa: PLC0415
    names = {s["name"] for s in scenarios()}
    on_disk = {p.stem for p in captures()}
    assert names == on_disk, f"missing: {sorted(names - on_disk)} / stale: {sorted(on_disk - names)}"
    for path in captures():
        json.loads(path.read_text(encoding="utf-8"))


def test_captures_record_what_was_pinned():
    import json  # noqa: PLC0415
    for path in captures():
        capture = json.loads(path.read_text(encoding="utf-8"))
        assert capture["pinned"]["sysdate"], f"{path.stem}: the pinned clock is not recorded"
        assert capture["source"] == "oracle"
        assert "exception" in capture and "result" in capture


def test_masked_columns_are_recorded_in_the_capture():
    """マスクは比較対象から外す操作なので、何を外したかが capture に残っていなければならない。"""
    import json  # noqa: PLC0415
    for spec in scenarios():
        if not spec.get("mask"):
            continue
        capture = json.loads((GOLDEN / f"{spec['name']}.json").read_text(encoding="utf-8"))
        assert capture["masked"], f"{spec['name']}: masked columns are missing from the capture"
        for table, columns in spec["mask"].items():
            assert set(capture["masked"].get(table, [])) == {c.lower() for c in columns}


def test_business_error_codes_are_captured_as_data():
    """例外は結果である。-20000 帯の業務エラーが実際に記録されていること。"""
    import json  # noqa: PLC0415
    codes = {json.loads(p.read_text(encoding="utf-8"))["exception"]["code"]
             for p in captures() if json.loads(p.read_text(encoding="utf-8"))["exception"]}
    assert {-20010, -20020, -20030, -20040, -20060} <= codes, f"captured codes: {sorted(codes)}"


def test_routines_without_a_capture_are_only_the_documented_ones():
    """capture が無い routine は testEvidence が 0 になり AUTO にならない。増えていないか見張る。"""
    covered = {(s["unit"], s["routine"].lower()) for s in scenarios()}
    uncovered = {f"{u['name']}.{r['name']}"
                 for u in manifest()["units"] for r in u["routines"]
                 if (u["name"], r["name"].lower()) not in covered}
    documented = {
        # private routines: observable only through their public callers
        "pkg_order_pricing.tier_discount", "pkg_order_pricing.line_amount",
        "pkg_order_pricing.customer_tier", "pkg_order_lock.is_cancellable",
        "pkg_shipment.line_count",
        # cannot be compiled here: the DB link does not exist
        "prc_remote_sync.prc_remote_sync",
        # holdout2 was added after P0-5 ran; its captures are collected when P0-5 is next run
        # (fixtures/plsql/golden/README.md). Listing them keeps the gap visible rather than growing silently.
        "pkg_shipment.is_shippable", "pkg_shipment.mark_shipped", "pkg_shipment.days_in_transit",
        "prc_purge_audit.prc_purge_audit", "trg_payments_guard.trg_payments_guard",
        "pkg_tier_admin.promote", "pkg_tier_admin.set_credit_limit",
    }
    assert uncovered == documented, f"undocumented gap: {sorted(uncovered - documented)}"
