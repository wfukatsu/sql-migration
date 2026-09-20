"""`python -m plsql.explorer`: from an analysis directory, a snapshot and SQL files to one HTML file."""

from __future__ import annotations

import json
import pathlib

import pytest

from plsql.cli import main as analyse_cli
from plsql.explorer import page
from plsql.explorer.__main__ import main

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "explorer"
CREATE_ORDER = ROOT / "fixtures" / "plsql-external" / "create_order" / "src"


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    out = tmp_path_factory.mktemp("explorer-cli") / "analysis"
    assert analyse_cli([str(FIXTURE / "src"), "--out-dir", str(out), "--quiet"]) == 0
    return out


def test_analysis_snapshot_and_sql_files_make_one_page(analysis, tmp_path, capsys):
    out = tmp_path / "explorer.html"
    assert main([str(analysis), "--snapshot", str(FIXTURE / "snapshot.json"), "--sql", str(FIXTURE / "app"),
                 "--out", str(out)]) == 0
    data = page.extract(out.read_text(encoding="utf-8"))
    assert data["meta"]["counts"] == {"tables": 9, "routines": 7, "sql": 25}
    assert "tables=9" in capsys.readouterr().out


def test_without_a_snapshot_there_is_still_a_page_and_it_says_what_is_missing(analysis, tmp_path, capsys):
    out = tmp_path / "explorer.html"
    assert main([str(analysis), "--out", str(out)]) == 0
    assert page.extract(out.read_text(encoding="utf-8"))["meta"]["snapshot"] is None
    assert "未取得" in capsys.readouterr().out


def test_the_default_place_is_next_to_the_analysis(analysis):
    assert main([str(analysis), "--quiet"]) == 0
    assert (analysis.parent / "explorer.html").exists()


def test_a_page_with_real_values_is_announced_where_it_is_made(analysis, tmp_path, capsys):
    assert main([str(analysis), "--snapshot", str(FIXTURE / "snapshot-with-values.json"),
                 "--out", str(tmp_path / "e.html")]) == 0
    assert "REAL DATA VALUES" in capsys.readouterr().out


def test_a_snapshot_that_is_refused_makes_no_page(analysis, tmp_path, capsys):
    bad = json.loads((FIXTURE / "snapshot.json").read_text(encoding="utf-8"))
    bad["columnStatistics"][0]["lowValue"] = {"dataType": "NUMBER", "text": "1"}
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    out = tmp_path / "explorer.html"
    assert main([str(analysis), "--snapshot", str(tmp_path / "bad.json"), "--out", str(out)]) == 2
    assert not out.exists() and "containsDataValues" in capsys.readouterr().err


def test_a_directory_with_no_analysis_in_it_says_what_to_run_first(tmp_path, capsys):
    assert main([str(tmp_path), "--out", str(tmp_path / "explorer.html")]) == 2
    assert "python -m plsql.cli" in capsys.readouterr().err
    assert main([str(tmp_path), "--sql", str(tmp_path / "missing.sql")]) == 2


def test_an_analysis_made_with_limits_still_makes_a_page_and_the_exit_status_says_look(analysis, tmp_path, capsys):
    copy = tmp_path / "limits"
    copy.mkdir()
    for file in analysis.iterdir():
        (copy / file.name).write_bytes(file.read_bytes())
    decisions = json.loads((copy / "decisions.json").read_text(encoding="utf-8"))
    decisions["routines"][0]["redesign"] = {"status": "DECIDED"}
    (copy / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
    out = tmp_path / "explorer.html"
    assert main([str(copy), "--out", str(out)]) == 1
    assert out.exists() and "--limits" in capsys.readouterr().out


def test_the_same_input_makes_the_same_bytes(analysis, tmp_path):
    arguments = [str(analysis), "--snapshot", str(FIXTURE / "snapshot.json"), "--sql", str(FIXTURE / "app"), "--quiet"]
    assert main(arguments + ["--out", str(tmp_path / "a.html")]) == 0
    assert main(arguments + ["--out", str(tmp_path / "b.html")]) == 0
    assert (tmp_path / "a.html").read_bytes() == (tmp_path / "b.html").read_bytes()


def test_the_create_order_sample_makes_a_page_too(tmp_path):
    out = tmp_path / "analysis"
    assert analyse_cli([str(CREATE_ORDER), "--out-dir", str(out), "--quiet"]) == 0
    assert main([str(out), "--out", str(tmp_path / "explorer.html"), "--quiet"]) == 0
    data = page.extract((tmp_path / "explorer.html").read_text(encoding="utf-8"))
    assert {t["name"] for t in data["tables"]} == {"orders", "order_items", "products"}


def test_with_the_source_directory_the_page_holds_the_code(analysis, tmp_path, capsys):
    out = tmp_path / "explorer.html"
    assert main([str(analysis), "--src", str(FIXTURE / "src"), "--snapshot", str(FIXTURE / "snapshot-with-source.json"),
                 "--sql", str(FIXTURE / "app"), "--out", str(out)]) == 0
    data = page.extract(out.read_text(encoding="utf-8"))
    assert len(data["files"]["create_order.prc"]["lines"]) >= 69 and "app:shipping.sql" in data["files"]
    said = capsys.readouterr().out
    assert "whole (comments included)" in said and "USER_SOURCE" in said


def test_without_the_source_directory_it_says_what_will_be_missing(analysis, tmp_path, capsys):
    assert main([str(analysis), "--out", str(tmp_path / "e.html")]) == 0
    assert "no --src" in capsys.readouterr().out


def test_a_source_directory_that_is_not_one_makes_no_page(analysis, tmp_path, capsys):
    out = tmp_path / "explorer.html"
    assert main([str(analysis), "--src", str(tmp_path / "nowhere"), "--out", str(out)]) == 2
    assert not out.exists() and "--src" in capsys.readouterr().err


def test_a_version_1_snapshot_still_makes_a_page(analysis, tmp_path):
    out = tmp_path / "explorer.html"
    assert main([str(analysis), "--snapshot", str(FIXTURE / "snapshot-v1.json"), "--out", str(out), "--quiet"]) == 0
    data = page.extract(out.read_text(encoding="utf-8"))
    assert data["meta"]["snapshot"]["formatVersion"] == 1 and data["dbObjects"]["state"] == "not_collected"


def test_with_the_source_the_same_input_still_makes_the_same_bytes(analysis, tmp_path):
    arguments = [str(analysis), "--src", str(FIXTURE / "src"), "--snapshot", str(FIXTURE / "snapshot-with-source.json"),
                 "--quiet"]
    assert main(arguments + ["--out", str(tmp_path / "a.html")]) == 0
    assert main(arguments + ["--out", str(tmp_path / "b.html")]) == 0
    assert (tmp_path / "a.html").read_bytes() == (tmp_path / "b.html").read_bytes()
