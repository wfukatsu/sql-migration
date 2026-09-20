"""docs/guide/tutorial.md walks through samples/tutorial/. The numbers it quotes are pinned here (the part that needs no
database), so that a change to the converter or the generator that moves them shows up as a failing test, not as a
tutorial that no longer matches what the reader sees."""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "tutorial"


def test_the_sql_sample_converts_as_the_tutorial_says():
    from scalardb_migrate.converter import convert_script
    results, _ = convert_script((SAMPLE / "sql" / "points.sql").read_text(encoding="utf-8"), "oracle")
    assert Counter(r.status for r in results) == {"OK": 7, "WARN": 6, "PLANNED": 4, "ERROR": 3}
    outer_join = next(r for r in results if "h.member_id(+)" in r.source_sql)
    # the cluster refuses this join (DB-SQL-10067); it has to reach a plan, not pass as a WARN
    assert outer_join.status == "PLANNED" and "JOIN_KEY" in {i.code for i in outer_join.issues}


def test_the_check_case_is_the_read_part_of_the_sample():
    sample = (SAMPLE / "sql" / "points.sql").read_text(encoding="utf-8")
    check = (SAMPLE / "sql" / "points-check.sql").read_text(encoding="utf-8")
    assert check.endswith(sample[sample.index("-- ---- 読み取り ----"):])
    assert set(json.loads((SAMPLE / "sql" / "points-check.data.json").read_text(encoding="utf-8"))) == {"members", "point_history"}


def test_the_plsql_sample_generates_with_nothing_refused(tmp_path):
    project = SAMPLE / "plsql"
    done = subprocess.run([sys.executable, "-m", "plsql.generate", str(project / "src"),
                           "--scalardb-schema", str(project / "scalardb-schema.json"), "--limits", str(project / "limits.yaml"),
                           "--out-dir", str(tmp_path), "--no-verify-compile", "--limits-strict"],
                          cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "routines: 4  AUTO 3  REVIEW 0  REDESIGN 1" in done.stdout
    assert "SQL ScalarDB refuses 0" in done.stdout


def test_without_the_lock_decision_the_writes_of_use_points_stay_refused(tmp_path):
    """The tutorial's point about decisions: nobody decided, so the generator does not rewrite past the dropped lock."""
    project = SAMPLE / "plsql"
    done = subprocess.run([sys.executable, "-m", "plsql.generate", str(project / "src"),
                           "--scalardb-schema", str(project / "scalardb-schema.json"),
                           "--out-dir", str(tmp_path), "--no-verify-compile"], cwd=ROOT, capture_output=True, text=True)
    assert "SQL ScalarDB refuses 2" in done.stdout


def test_every_scenario_names_a_routine_of_the_package():
    import yaml
    scenarios = sorted((SAMPLE / "plsql" / "scenarios").glob("*.yaml"))
    assert len(scenarios) == 13
    for path in scenarios:
        scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert scenario["name"] == path.stem and scenario["unit"] == "pkg_points"
        assert scenario["routine"] in {"get_balance", "add_points", "use_points"}
        assert (SAMPLE / "plsql" / "golden" / f"{path.stem}.json").is_file(), f"{path.stem}: no Oracle capture"
