"""P0-7: the KPI document has to stay attached to the corpus it measures.

The document fixes the numbers Phase 1-3 are judged by. If it drifts from the fixtures -- the corpus size, the
manifest's verdict vocabulary, the holdout share -- the phase gates start measuring something other than what the
document says. These checks are cheap and catch exactly that drift.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "plsql-kpi.md"
FIXTURES = ROOT / "fixtures" / "plsql"
SRC = FIXTURES / "src"
SUFFIXES = {".pks", ".pkb", ".prc", ".trg"}


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def manifest() -> dict:
    return yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))


def test_file_count_in_the_document_matches_the_corpus():
    files = [p for p in SRC.rglob("*") if p.suffix in SUFFIXES]
    assert f"現在 {len(files)}" in text(), f"KPI-1 の分母が corpus ({len(files)} ファイル) と食い違っている"


def test_routine_counts_in_the_document_match_the_manifest():
    units = manifest()["units"]
    routines = sum(len(u["routines"]) for u in units)
    holdout = sum(len(u["routines"]) for u in units if u["holdout"])
    assert f"現在 {routines} routine（うち holdout {holdout}）" in text(), \
        f"KPI-3 の分母が manifest ({routines} routine, holdout {holdout}) と食い違っている"


def test_holdout_share_in_the_document_matches_the_fixtures():
    units = manifest()["units"]
    holdout = sum(1 for u in units if u["holdout"])
    assert f"全 {len(units)} ユニット中 {holdout} ユニット" in text()


def test_every_verdict_the_manifest_uses_is_defined_in_the_document():
    used = {r["expected"] for u in manifest()["units"] for r in u["routines"]}
    body = text()
    for verdict in used:
        assert verdict in body, f"{verdict} が KPI 文書に出てこない"


def test_the_confidence_formula_lists_exactly_the_factors_the_table_defines():
    body = text()
    formula = re.search(r"confidence = (.+)", body).group(1)
    factors = [f.strip() for f in formula.split("×")]
    assert len(factors) == 5
    for factor in factors:
        assert f"`{factor}`" in body, f"{factor} の算出方法が表にない"


def test_the_auto_threshold_is_stated_as_a_number():
    assert re.search(r"confidence >= 0\.\d+", text()), "AUTO の下限しきい値が数値で書かれていない"


def test_the_document_says_the_numbers_are_synthetic_only():
    """計画 §9 の決定。これが消えると KPI が実案件耐性の証拠として読まれてしまう。"""
    body = text()
    assert "合成 corpus 上の値" in body
    assert "holdout" in body


def test_the_auto_prohibitions_cover_the_redesign_categories_the_corpus_exercises():
    """corpus が REDESIGN を期待している構文は、禁止条件として名指しされていなければならない。"""
    body = text()
    for keyword in ["COMMIT", "AUTONOMOUS_TRANSACTION", "Package 変数", "EXECUTE IMMEDIATE",
                    "Trigger", "AUTHID CURRENT_USER", "DB Link", "FOR UPDATE"]:
        assert keyword in body, f"AUTO 禁止条件に {keyword} がない"
