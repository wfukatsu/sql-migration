"""The explorer page as a file: what is in it, what is not, and that SQL written by other people stays text.

How it behaves in a browser -- sorting, the search, walking from a table to a routine and back, 300 tables -- was
checked by hand in one (docs/guide/explorer.md says how to repeat it). What a test can hold still is the file.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from plsql.cli import main as analyse_cli
from plsql.explorer import appsql, model, page
from plsql.explorer import snapshot as S

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "explorer"
# values that exist only in the rows of the fixture's tables (fixtures/explorer/db/data.sql)
REAL_VALUES = ("GROUND", "99.5", "2026-07-19", "Customer 50")


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    out = tmp_path_factory.mktemp("explorer-page")
    assert analyse_cli([str(FIXTURE / "src"), "--out-dir", str(out), "--quiet"]) == 0
    return out


def html_for(analysis, snapshot_name: str | None, **kwargs) -> tuple[str, dict]:
    snapshot = S.load(FIXTURE / snapshot_name) if snapshot_name else None
    data = model.build(analysis, snapshot, appsql.collect([FIXTURE / "app"]), **kwargs)
    return page.render(data), data


def test_a_page_made_without_the_values_option_holds_no_value_from_the_data(analysis):
    """AE5."""
    html, data = html_for(analysis, "snapshot.json")
    for value in REAL_VALUES:
        assert value not in html, value
    orders = next(t for t in data["tables"] if t["name"] == "orders")
    status = next(c for c in orders["columnStatistics"] if c["column"] == "status")
    assert status["numDistinct"] == 3 and status["skewed"] and status["frequent"] is None
    assert "lowValue" not in status


def test_a_page_made_with_values_says_so_in_the_data_every_screen_draws_its_banner_from(analysis):
    """AE6. The banner is drawn once, above whichever screen is showing, from this flag."""
    html, data = html_for(analysis, "snapshot-with-values.json")
    assert data["meta"]["snapshot"]["containsDataValues"] is True
    assert "RECEIVED" in html and 'id: "values-banner"' in html
    assert page.extract(html)["meta"]["snapshot"]["containsDataValues"] is True


def test_every_page_says_it_holds_source_and_definitions_values_or_not(analysis):
    for name in ("snapshot.json", "snapshot-with-values.json", None):
        html, _ = html_for(analysis, name)
        assert 'id: "content-notice"' in html and "リテラル" in html
    assert "列統計の値: 取っていない" in html_for(analysis, "snapshot.json")[0]


def test_the_page_asks_the_network_for_nothing(analysis):
    html, _ = html_for(analysis, "snapshot-with-values.json")
    template = page.TEMPLATE.read_text(encoding="utf-8")
    assert not re.findall(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", template)
    assert not re.search(r"\b(fetch|XMLHttpRequest|import\s*\(|@import|url\()", template)
    assert "<link" not in template and html.count("<script") == 2, "the data and the page's own code, nothing loaded"


def test_sql_that_looks_like_markup_cannot_end_the_data_or_become_markup(analysis, tmp_path):
    script = tmp_path / "evil.sql"
    script.write_text("SELECT '</script><img src=x onerror=alert(1)>' AS x, '<!--' AS y FROM orders;\n", encoding="utf-8")
    data = model.build(analysis, None, appsql.collect([script]))
    html = page.render(data)
    body = html[html.index('<script id="data"'):]
    assert "<img" not in body and "</script><img" not in html
    assert html.count("</script>") == 2
    back = next(s for s in page.extract(html)["sql"] if s["origin"] == "app")
    assert "</script><img src=x onerror=alert(1)>" in back["text"], "the text survives; it is only never markup"


def test_the_page_never_parses_data_as_markup():
    template = page.TEMPLATE.read_text(encoding="utf-8")
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(", template)


def test_the_embedded_document_is_the_document(analysis):
    html, data = html_for(analysis, "snapshot.json")
    assert page.extract(html) == json.loads(json.dumps(data))


def test_a_routine_links_to_its_specification_only_when_there_is_one(analysis, tmp_path):
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "create_order.md").write_text("# create_order\n", encoding="utf-8")
    _, data = html_for(analysis, None, spec_dir=spec, out_dir=tmp_path / "out")
    links = {r["id"]: r["spec"] for r in data["routines"]}
    assert links["create_order"] == "../spec/create_order.md"
    assert links["cancel_order"] is None


def test_what_nobody_collected_has_a_word_on_the_page_and_the_word_is_not_zero():
    template = page.TEMPLATE.read_text(encoding="utf-8")
    for word in ("未取得", "統計なし", "snapshot に無い", "該当なし", "見えていない"):
        assert word in template, word


# --- the code on the page ------------------------------------------------------------------------------------------

def with_source(analysis, snapshot_name="snapshot-with-source.json", src=FIXTURE / "src"):
    data = model.build(analysis, S.load(FIXTURE / snapshot_name), appsql.collect([FIXTURE / "app"]), src_root=src,
                       app_files=appsql.files([FIXTURE / "app"]))
    return page.render(data), data


def test_the_source_files_and_everything_about_them_are_in_the_page(analysis):
    html, data = with_source(analysis)
    back = page.extract(html)
    assert back == json.loads(json.dumps(data))
    assert back["files"]["create_order.prc"]["lines"][0] == "CREATE OR REPLACE PROCEDURE create_order ("
    assert back["routines"][1]["decision"]["rules"] and back["dbObjects"]["dbOnly"]


def test_source_code_that_looks_like_markup_is_still_only_text(analysis, tmp_path):
    import shutil
    evil = tmp_path / "src"
    shutil.copytree(FIXTURE / "src", evil)
    path = evil / "purge_table.prc"
    path.write_text(path.read_text(encoding="utf-8").replace("-- 表の名前", "-- </script><img src=x onerror=alert(1)> 表の名前"),
                    encoding="utf-8")
    html, _ = with_source(analysis, src=evil)
    assert html.count("</script>") == 2 and "<img" not in html[html.index('<script id="data"'):]
    assert any("onerror=alert(1)" in line for line in page.extract(html)["files"]["purge_table.prc"]["lines"])


def test_the_page_says_which_code_it_holds_and_never_that_it_holds_none(analysis):
    """R9: a snapshot taken without --include-source still carries view definitions and trigger bodies."""
    template = page.TEMPLATE.read_text(encoding="utf-8")
    assert "USER_SOURCE のコードは取っていない（view・trigger の定義は含む）" in template
    assert "DB から取ったコード（USER_SOURCE）を含みます" in template
    assert "コメントも含めて全文含みます" in template
    assert "コードは無い" not in template and "構造だけ" not in template
    assert with_source(analysis)[1]["meta"]["snapshot"]["containsSourceCode"] is True
    assert with_source(analysis, "snapshot.json")[1]["meta"]["snapshot"]["containsSourceCode"] is False


def test_the_diagram_is_built_from_elements_and_the_same_facts_stay_in_a_table():
    template = page.TEMPLATE.read_text(encoding="utf-8")
    assert "createElementNS" in template and "createTextNode" in template
    assert "つながるテーブル（外部キー）" in template, "the table is still there for whoever cannot use the picture"


def test_every_new_word_for_not_knowing_is_on_the_page():
    template = page.TEMPLATE.read_text(encoding="utf-8")
    for word in ("原文が渡されていない", "比べていない", "wrapped", "DB のコードと違う", "解析の対象外"):
        assert word in template, word
