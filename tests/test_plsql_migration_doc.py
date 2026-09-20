"""plsql-migrate スキル Step 7: 変換後のコードの文書を生成物から起こし、書いた文章を生成物と突き合わせる（2026-09-20）。

確かめるのは 3 つ:

* 事実の欄は、生成物・解析・決定・実 DB の比較から出る（Java の入口、文の対応、判定ルール、受け入れた差）
* 事実の欄を作り直しても、人が書いた文章は残る
* `check` は、読んだ人が踏むものを文章が落としたとき（判定の理由、受け入れた差、決定、比較していないこと）に気づく
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import sys

import pytest

from plsql.cli import main as analyse
from plsql.generate import main as generate

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "plsql-migrate"
PROJECT = ROOT / "fixtures" / "plsql-external" / "create_order"
CORPUS = ROOT / "fixtures" / "plsql"
EXAMPLE = SKILL / "examples" / "create_order"

spec = importlib.util.spec_from_file_location("migration_doc", SKILL / "scripts" / "migration_doc.py")
doc = importlib.util.module_from_spec(spec)
sys.modules["migration_doc"] = doc   # dataclasses が module を引く
spec.loader.exec_module(doc)


def _converted(tmp_path_factory, project, evidence=None):
    out = tmp_path_factory.mktemp("generated")
    common = [str(project / "src"), "--scalardb-schema", str(project / "scalardb-schema.json"), "--limits", str(project / "limits.yaml")]
    assert generate([*common, "--out-dir", str(out), "--quiet", "--no-verify-compile"]) == 0
    extra = ["--evidence", str(evidence)] if evidence else []
    assert analyse([*common, *extra, "--out-dir", str(out / "analysis"), "--quiet"]) in (0, 1)
    return out


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    return _converted(tmp_path_factory, PROJECT, EXAMPLE / "evidence.json")


def run(command, generated, out, evidence=True, project=PROJECT):
    extra = ["--evidence", str(EXAMPLE / "evidence.json")] if evidence else []
    return doc.main([command, "--src", str(project / "src"), "--generated", str(generated), "--analysis", str(generated / "analysis"),
                     "--limits", str(project / "limits.yaml"), *extra, "--out-dir", str(out)])


# --- the facts ---------------------------------------------------------------------------------------------------


def test_the_facts_come_from_what_was_generated_decided_and_compared(converted, tmp_path):
    assert run("facts", converted, tmp_path) == 0
    index = (tmp_path / "README.md").read_text(encoding="utf-8")
    for chapter in ("## アーキテクチャ", "## 使い方", "## 制限", "## どのように移行したか"):
        assert chapter in index
    assert "`CreateOrderRepository` | `Connection connection, Sequences sequences`" in index
    assert "`order_seq`" in index, "the caller has to register the sequence"
    assert "`rowLocks.optimistic` | `create_order`" in index
    assert "`create_order_duplicate_id`" in index and "2026-09-20" in index, "an accepted difference is a limit"

    page = (tmp_path / "create_order.md").read_text(encoding="utf-8")
    assert "CreateOrderResult createOrder(Long pCustomerId" in page
    assert "`p_order_id` | OUT | `NUMBER(12)` | 戻り値の record に入る" in page
    assert "`CreateOrderRepository.createOrderStmt10read`<br>`CreateOrderRepository.createOrderStmt10`" in page, \
        "one source statement, the two methods it was split into"
    assert "`LOCK-001` | REDESIGN" in page and "`RMW_SPLIT`" in page
    assert "generation-report.json" not in index and "analysis/" not in index, "only what is delivered is listed"


def test_the_whole_corpus_gets_a_page_per_module(tmp_path_factory, tmp_path):
    generated = _converted(tmp_path_factory, CORPUS)
    assert run("facts", generated, tmp_path, evidence=False, project=CORPUS) == 0
    project = doc.load(doc.argparse.Namespace(src=None, generated=str(generated), analysis=str(generated / "analysis"),
                                              limits=str(CORPUS / "limits.yaml"), evidence=None, record=None))
    assert len(project.routines) == 67
    assert {p.name for p in tmp_path.glob("*.md")} == {"README.md"} | {f"{m['name']}.md" for m in project.modules}
    index = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "実 DB の比較（`--evidence`）は渡されていない" in index
    for kind in ("scanRows.routines", "transactions.perIteration", "dynamicTables", "dbLinks"):
        assert f"`{kind}`" in index, kind
    assert "`dbLinks` | `warehouse_link` | namespace: warehouse" in index
    entries = [r for r in project.routines.values() if r.method]
    assert len(entries) >= 40, "most routines have a Java entry the page can name"


def test_facts_again_keeps_the_prose(converted, tmp_path):
    run("facts", converted, tmp_path)
    page = tmp_path / "README.md"
    page.write_text(page.read_text(encoding="utf-8").replace("（未記入: 層の役割", "依存は一方向である。（未記入: 層の役割", 1), encoding="utf-8")
    assert run("facts", converted, tmp_path, evidence=False) == 0, "the comparison went away: the facts change"
    text = page.read_text(encoding="utf-8")
    assert "依存は一方向である。" in text and "渡されていない" in text


# --- check ---------------------------------------------------------------------------------------------------


def test_an_unwritten_document_does_not_pass(converted, tmp_path, capsys):
    run("facts", converted, tmp_path)
    assert run("check", converted, tmp_path) == 1
    assert "UNWRITTEN=7" in capsys.readouterr().err


def test_the_example_passes_and_is_up_to_date(converted, capsys):
    assert run("check", converted, EXAMPLE) == 0, capsys.readouterr().out


@pytest.mark.parametrize("page, old, new, said", [
    ("create_order.md", "`LOCK-001`", "行ロックの件", "判定の理由 `LOCK-001`"),
    ("create_order.md", "create_order_duplicate_id", "重複のシナリオ", "受け入れた差のシナリオ"),
    ("create_order.md", "rowLocks.optimistic", "楽観制御の決定", "決定 `rowLocks.optimistic`"),
    ("README.md", "`create_order_duplicate_id`", "重複のシナリオ", "Oracle と違うシナリオ"),
    ("README.md", "`CreateOrderRepository.java`", "`OrderRepository.java`", "`OrderRepository.java` は生成物に無い"),
])
def test_prose_that_drops_what_a_reader_would_trip_on_is_caught(converted, tmp_path, capsys, page, old, new, said):
    out = tmp_path / "docs"
    shutil.copytree(EXAMPLE, out)
    text = (out / page).read_text(encoding="utf-8")
    parts = re.split(r"(<!-- facts:begin \S+ -->.*?<!-- facts:end \S+ -->)", text, flags=re.S)
    assert any(old in part for part in parts[::2]), old
    (out / page).write_text("".join(part if index % 2 else part.replace(old, new) for index, part in enumerate(parts)), encoding="utf-8")
    assert run("check", converted, out) == 1
    assert said in capsys.readouterr().out


def test_without_a_comparison_the_limits_have_to_say_so(converted, tmp_path, capsys):
    out = tmp_path / "docs"
    shutil.copytree(EXAMPLE, out)
    run("facts", converted, out, evidence=False)
    page = out / "README.md"
    text = page.read_text(encoding="utf-8")
    head, rest = text.split("<!-- facts:end limits -->")
    prose, tail = rest.split("## どのように移行したか")
    page.write_text(head + "<!-- facts:end limits -->\n\n`create_order` は REDESIGN である。\n\n## どのように移行したか" + tail, encoding="utf-8")
    assert run("check", converted, out, evidence=False) == 1
    assert "実 DB で比べていないことが書かれていない" in capsys.readouterr().out


def test_a_file_in_the_source_that_is_not_text_is_not_the_source(tmp_path):
    (tmp_path / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\xff\xfe")
    (tmp_path / "p.prc").write_text("BEGIN\n  NULL;\nEND;\n", encoding="utf-8")
    assert doc._sources(tmp_path) == {"p.prc": ["BEGIN", "  NULL;", "END;"]}


def test_missing_input_is_an_input_error(tmp_path, capsys):
    assert doc.main(["facts", "--generated", str(tmp_path), "--analysis", str(tmp_path), "--out-dir", str(tmp_path / "docs")]) == 2
    assert "plsql.generate" in capsys.readouterr().err


# --- the skill's own text ------------------------------------------------------------------------------------


def test_the_commands_of_the_step_match_the_allow_list_on_their_own():
    import fnmatch

    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    allowed = re.findall(r"^  - Bash\((.+)\)$", text, re.M)
    step = text[text.index("### Step 7"):text.index("### Step 8")]
    commands = [line for fence in re.findall(r"```bash\n(.*?)```", step, re.S) for line in fence.splitlines() if line.strip()]
    assert len(commands) == 3
    for command in commands:
        assert not re.search(r"[|;&]|\\$", re.sub(r"<[\w.-]+>", "", command)), command
        assert any(fnmatch.fnmatch(command, pattern) for pattern in allowed), command
