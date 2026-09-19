"""plsql-migrate スキル: 生成コードの外で決めることを、生成物から拾って記録する（2026-09-19）。

確かめるのは 3 つ:

* 文書の §0.1 の行はすべて見分け方を持ち、すべての項目がどこかの行に出てくる——見分けられない項目は、
  確認されないまま残る
* corpus を生成すると、各行が実際の生成物から「出た」と判定される（見分け方が空振りしていない）
* 記録は「決めた人のいない決定」を受け付けず、決定を上書きしない
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

from plsql.generate import main as generate

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "plsql"
DOC = ROOT / "docs" / "plsql-decisions-outside-generator.md"
SCRIPT = ROOT / "skills" / "plsql-migrate" / "scripts" / "decision_items.py"

spec = importlib.util.spec_from_file_location("decision_items", SCRIPT)
items = importlib.util.module_from_spec(spec)
sys.modules["decision_items"] = items   # dataclasses が module を引く
spec.loader.exec_module(items)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("generated")
    assert generate([str(FIXTURES / "src"), "--scalardb-schema", str(FIXTURES / "scalardb-schema.json"),
                     "--limits", str(FIXTURES / "limits.yaml"), "--out-dir", str(out), "--quiet",
                     "--no-verify-compile"]) == 0
    return out


def scan(generated, *extra):
    return items.main(["--doc", str(DOC), "scan", "--generated", str(generated),
                       "--limits", str(FIXTURES / "limits.yaml"),
                       "--scalardb-schema", str(FIXTURES / "scalardb-schema.json"), *extra])


# --- the document and the detectors agree ------------------------------------------------------------------

def test_every_row_of_the_table_has_exactly_one_detector():
    doc = items.read_doc(DOC)
    for row in doc.rows:
        items.detector_for(row)   # raises when a row has none, or more than one
    assert len(doc.rows) == len(items.DETECTORS)


def test_every_item_is_reachable_from_the_table():
    """An item no row points at is never asked."""
    doc = items.read_doc(DOC)
    reachable = {item for row in doc.rows for item in row.items}
    assert set(doc.titles) == reachable
    assert {i.split("-")[0] for i in doc.titles} == {"OPS", "CALL", "BIZ"}


def test_ranges_in_the_table_expand():
    assert items._expand("OPS-1〜OPS-5") == ["OPS-1", "OPS-2", "OPS-3", "OPS-4", "OPS-5"]
    assert items._expand("CALL-1〜CALL-3、BIZ-1、BIZ-2") == ["CALL-1", "CALL-2", "CALL-3", "BIZ-1", "BIZ-2"]


# --- the corpus fires every row ----------------------------------------------------------------------------

def test_the_report_carries_the_diagnostics_the_table_points_at(generated):
    report = json.loads((generated / "generation-report.json").read_text(encoding="utf-8"))
    diagnostics = report["diagnostics"]
    assert "MERGE_SPLIT" in diagnostics["pkg_customer_import.import"]
    assert "OPTIMISTIC" in diagnostics["pkg_stock_reserve.reserve_nowait"]
    assert "TRIGGER_CALL" in diagnostics["prc_nightly_close"]


def test_every_row_fires_on_the_corpus(generated):
    """The corpus was built to exercise every decision, so a row that finds nothing has a broken detector."""
    doc = items.read_doc(DOC)
    tree = items.Tree.load(generated, FIXTURES / "limits.yaml", FIXTURES / "scalardb-schema.json")
    silent = [row.artifact for row in doc.rows if not items.detector_for(row)(tree)]
    assert silent == []
    fired = items.detect(doc, tree)
    assert set(fired) == set(doc.titles)
    assert any("plsqlpoc.payments.paid_at" in e for e in fired["BIZ-11"])
    assert any("prcNightlyClose" in e for e in fired["CALL-1"])
    assert any("pkg_customer_import.import: MERGE_SPLIT" in e for e in fired["BIZ-5"])


# --- the record --------------------------------------------------------------------------------------------

def test_scan_writes_undecided_items_and_strict_fails_on_them(generated, tmp_path):
    record = tmp_path / "record.yaml"
    assert scan(generated, "--record", str(record), "--write", "--out", str(tmp_path / "items.md")) == 0
    written = items.read_record(record)
    assert written["OPS-1"]["状態"] == "未決" and written["OPS-1"]["出た"]
    assert scan(generated, "--record", str(record), "--strict", "--out", str(tmp_path / "items.md")) == 1


def test_a_decision_needs_who_and_when(tmp_path):
    record = tmp_path / "record.yaml"
    base = ["--doc", str(DOC), "set", "OPS-1", "--record", str(record), "--status", "決定",
            "--decision", "a. CronJob から回す"]
    assert items.main(base) == 1
    assert not record.exists()
    assert items.main(base + ["--by", "運用担当", "--date", "2026-09-20", "--where", "運用設計書"]) == 0
    assert items.read_record(record)["OPS-1"]["決めた人"] == "運用担当"


def test_a_hand_edited_decision_without_a_decider_is_a_problem():
    doc = items.read_doc(DOC)
    bad = items.problems({"BIZ-7": {"状態": "決定", "決定": "200 でよい"}}, doc)
    assert bad and "決めた人" in bad[0]
    assert items.problems({"XYZ-1": {"状態": "未決"}}, doc)


def test_merge_keeps_decisions_and_retires_items_that_no_longer_fire():
    doc = items.read_doc(DOC)
    record = {"OPS-1": {"状態": "決定", "決定": "a", "決めた人": "運用担当", "日付": "2026-09-20"},
              "BIZ-12": {"状態": "未決", "出た": ["old"]},
              "BIZ-11": {"状態": "対象外"}}
    merged = items.merge(record, {"BIZ-11": ["plsqlpoc.payments.paid_at"]}, doc)
    assert merged["OPS-1"]["状態"] == "決定" and "出た" not in merged["OPS-1"]
    assert merged["BIZ-12"]["状態"] == "対象外" and "出た" not in merged["BIZ-12"]
    assert merged["BIZ-11"] == {"状態": "未決", "出た": ["plsqlpoc.payments.paid_at"]}


def test_conflicts_and_remaining_parts_are_recorded_and_shown(tmp_path):
    """食い違いは案にしない。決定の一部が残っていることも、決定と一緒に見える必要がある。"""
    record = tmp_path / "record.yaml"
    assert items.main(["--doc", str(DOC), "set", "BIZ-1", "--record", str(record), "--status", "未決",
                       "--conflict", "仕様 3.2 は全件一括の確定（出典: 業務仕様書 3.2）"]) == 0
    assert items.main(["--doc", str(DOC), "set", "OPS-2", "--record", str(record), "--status", "決定",
                       "--decision", "日次は 02:00", "--by", "運用担当", "--date", "2026-09-19",
                       "--remaining", "hourly が 1 時間に収まるかの測定"]) == 0
    written = items.read_record(record)
    assert "案" not in written["BIZ-1"]
    text = items.render(items.read_doc(DOC), written, {"BIZ-1": ["x"], "OPS-2": ["y"]})
    assert "**食い違い**: 仕様 3.2" in text and "残り: hourly" in text


def test_the_corpus_record_keeps_the_promise():
    """corpus の記録にも「決めた人のいない決定」は無い。"""
    record = items.read_record(FIXTURES / "decisions-outside-generator.yaml")
    assert record
    assert items.problems(record, items.read_doc(DOC)) == []


# --- #27-35: the record does not change, or lie, behind the user's back -----------------------------------
DECIDE = ["--status", "決定", "--by", "運用担当", "--date", "2026-09-20"]


def setter(record, item, *extra):
    return items.main(["--doc", str(DOC), "set", item, "--record", str(record), *extra])


def test_an_item_that_could_not_be_detected_is_left_alone(generated, tmp_path, capsys):
    """Regression: a scan without --limits / --scalardb-schema read "cannot tell" as "did not fire", and open
    BIZ items silently became 対象外."""
    record = tmp_path / "record.yaml"
    assert scan(generated, "--record", str(record), "--write", "--out", str(tmp_path / "a.md")) == 0
    before = items.read_record(record)
    assert {before[i]["状態"] for i in ("BIZ-7", "BIZ-8", "BIZ-11")} == {"未決"}
    capsys.readouterr()
    assert items.main(["--doc", str(DOC), "scan", "--generated", str(generated), "--record", str(record),
                       "--write", "--out", str(tmp_path / "b.md")]) == 0
    assert items.read_record(record) == before
    noted = capsys.readouterr().err
    assert "BIZ-7 は --limits が無い" in noted and "BIZ-11 は --scalardb-schema が無い" in noted


def test_a_changed_decision_needs_its_own_decider_and_keeps_the_old_one(tmp_path):
    """Regression: the new text inherited the previous decider and date."""
    record = tmp_path / "record.yaml"
    assert setter(record, "OPS-1", *DECIDE, "--decision", "a. CronJob") == 0
    assert setter(record, "OPS-1", "--status", "決定", "--decision", "b. 別の決定") == 1
    assert items.read_record(record)["OPS-1"]["決定"] == "a. CronJob"
    assert setter(record, "OPS-1", "--status", "決定", "--decision", "b. 別の決定",
                  "--by", "移行担当", "--date", "2026-09-21") == 0
    entry = items.read_record(record)["OPS-1"]
    assert (entry["決定"], entry["決めた人"], str(entry["日付"])) == ("b. 別の決定", "移行担当", "2026-09-21")
    assert entry["履歴"] == [{"決定": "a. CronJob", "決めた人": "運用担当", "日付": "2026-09-20"}]
    # adding a remaining part to the same decision is not a new decision
    assert setter(record, "OPS-1", "--status", "決定", "--remaining", "測定は未了") == 0
    assert len(items.read_record(record)["OPS-1"]["履歴"]) == 1


def test_a_withdrawn_decision_does_not_stay_looking_decided(tmp_path):
    record = tmp_path / "record.yaml"
    assert setter(record, "OPS-2", *DECIDE, "--decision", "毎時") == 0
    assert setter(record, "OPS-2", "--status", "未決") == 0
    entry = items.read_record(record)["OPS-2"]
    assert "決定" not in entry and "決めた人" not in entry and entry["履歴"][0]["決定"] == "毎時"
    doc = items.read_doc(DOC)
    assert items.problems({"OPS-2": {"状態": "未決", "決定": "毎時"}}, doc), "hand-edited into that shape"


def test_a_date_has_to_be_a_date(tmp_path):
    record = tmp_path / "record.yaml"
    assert setter(record, "BIZ-1", "--status", "決定", "--decision", "x", "--by", "業務担当", "--date", "banana") == 1
    assert not record.exists()


def test_a_decision_whose_question_or_evidence_changed_is_flagged():
    """Regression: `出た` was overwritten on every scan, so a decision taken for routines A and B stayed 決定
    when C started firing the same item; and a renumbered doc handed the old answer to a new question."""
    doc = items.read_doc(DOC)
    title = doc.titles["OPS-1"]
    decided = {"状態": "決定", "決定": "a", "決めた人": "運用担当", "日付": "2026-09-20",
               "決定時の題": title, "決定時の根拠": ["A; B"], "出た": ["A; B"]}
    assert items.problems({"OPS-1": decided}, doc) == []
    merged = items.merge({"OPS-1": decided}, {"OPS-1": ["A; B; C"]}, doc)
    assert merged["OPS-1"]["状態"] == "決定"
    assert any("要再確認" in line and "根拠が変わった" in line for line in items.problems(merged, doc))
    renamed = {**decided, "決定時の題": "別の問い"}
    assert any("要再確認" in line and "別の問い" in line for line in items.problems({"OPS-1": renamed}, doc))
    legacy = {k: v for k, v in decided.items() if not k.startswith("決定時")}
    assert items.problems({"OPS-1": {**legacy, "出た": ["anything"]}}, doc) == [], "older records carry neither"


def test_set_records_what_the_decision_was_about(generated, tmp_path):
    record = tmp_path / "record.yaml"
    assert scan(generated, "--record", str(record), "--write", "--out", str(tmp_path / "a.md")) == 0
    assert setter(record, "OPS-1", *DECIDE, "--decision", "a", "--note", "手書きのメモ") == 0
    entry = items.read_record(record)["OPS-1"]
    assert entry["決定時の題"] == items.read_doc(DOC).titles["OPS-1"]
    assert entry["決定時の根拠"] == entry["出た"] and entry["メモ"] == "手書きのメモ"
    assert scan(generated, "--record", str(record), "--write", "--out", str(tmp_path / "b.md")) == 0


@pytest.mark.parametrize("text,said", [
    ("items:\n  FOO-1: {状態: 未決}\n", "項目 ID"),
    ("- just\n- a list\n", "最上位"),
    ("items:\n  OPS-1: 決定\n", "名前: 値"),
    ("items: [unclosed\n", "YAML として読めない"),
])
def test_a_malformed_record_is_an_input_error_not_a_traceback(tmp_path, capsys, text, said):
    record = tmp_path / "record.yaml"
    record.write_text(text, encoding="utf-8")
    assert setter(record, "OPS-1", "--status", "未決") == 2
    assert said in capsys.readouterr().err
    assert record.read_text(encoding="utf-8") == text, "and it is not overwritten"


def test_the_record_is_replaced_whole_and_the_doc_is_found_from_anywhere(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record = tmp_path / "record.yaml"
    assert items.main(["set", "OPS-1", "--record", str(record), *DECIDE, "--decision", "a"]) == 0
    assert not (tmp_path / "record.yaml.tmp").exists()
    assert "コメントは保存されない" in record.read_text(encoding="utf-8")
