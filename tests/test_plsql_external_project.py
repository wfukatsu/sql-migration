"""A project that is not the corpus: the harnesses read its tables, sequences and deploy order off the project.

The corpus lists those by hand. `create_order` was the first routine to arrive from outside (2026-09-20), and
running it on the real databases needed the harnesses to stop assuming `fixtures/plsql`.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "fixtures" / "plsql-external" / "create_order"


@pytest.fixture
def runner():
    import difftest.plsql_run as module
    yield module
    importlib.reload(module)   # `use_project` rebinds module globals; the corpus tests must not see them


def test_tables_sequences_and_deploy_order_come_from_the_project(runner, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "schema.sql").write_text(
        "-- CREATE TABLE commented_out (id NUMBER);\n"
        "CREATE TABLE parent (id NUMBER PRIMARY KEY);\nCREATE TABLE child (id NUMBER PRIMARY KEY);\n"
        "CREATE SEQUENCE s_one START WITH 500 NOCACHE;\nCREATE SEQUENCE s_two;\n", encoding="utf-8")
    for name in ("b_body.pkb", "b_body.pks", "a_proc.prc", "z_trg.trg"):
        (src / name).write_text("", encoding="utf-8")
    runner.use_project(tmp_path)
    assert runner.TABLES == ["parent", "child"]
    assert runner.SEQUENCES == {"s_one": 500, "s_two": 1}
    assert runner.DEPLOY_ORDER == ["b_body.pks", "a_proc.prc", "b_body.pkb", "z_trg.trg"]
    assert runner.REMOTE_TABLES == {}, "the corpus's DB link is not this project's"


def test_the_external_sample_is_consistent_with_itself(runner):
    runner.use_project(PROJECT)
    scenarios = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((PROJECT / "scenarios").glob("*.yaml"))]
    assert len(scenarios) == 7
    for spec in scenarios:
        assert set(spec.get("mask") or {}) <= set(runner.TABLES)
        assert set((spec.get("pinned") or {}).get("sequences") or {}) <= set(runner.SEQUENCES)
        golden = json.loads((PROJECT / "golden" / f"{spec['name']}.json").read_text(encoding="utf-8"))
        assert golden["routine"] == "create_order" and golden["source"] == "oracle"


def test_the_external_sample_stays_out_of_the_corpus():
    """It has an inferred DDL and no expected verdict; counting it would move the corpus KPIs."""
    manifest = yaml.safe_load((ROOT / "fixtures" / "plsql" / "manifest.yaml").read_text(encoding="utf-8"))
    assert "create_order" not in {u["name"] for u in manifest["units"]}
    assert not (ROOT / "fixtures" / "plsql" / "src" / "create_order.prc").exists()


@pytest.fixture
def restore_banner():
    from plsql.gen_java import project
    yield
    project.regeneration_banner()   # `--handover` switches a module-wide banner


def test_the_sample_generates_and_needs_exactly_the_row_lock_decision(tmp_path, restore_banner):
    from plsql.generate import main as generate

    base = [str(PROJECT / "src"), "--schema", str(PROJECT / "src" / "schema.sql"),
            "--scalardb-schema", str(PROJECT / "scalardb-schema.json"), "--quiet"]
    assert generate(base + ["--out-dir", str(tmp_path / "open"), "--handover"]) == 1, "LOCK-001 is still open"
    assert generate(base + ["--out-dir", str(tmp_path / "decided"), "--limits", str(PROJECT / "limits.yaml"),
                            "--handover"]) == 0
