"""plsql-spec スキル: 既存の PL/SQL の仕様書の骨組みを IR から作り、書いた文章を IR と突き合わせる（2026-09-20）。

確かめるのは 3 つ:

* 事実の欄に出るのは原文にあるものだけである——lowering が足した文（trigger の織り込み、行数上限の検査）を
  現行の仕様として書かない
* 事実の欄を作り直しても、人が書いた文章は残る
* `check` は、文章が事実から離れたとき（落ちたエラーコード、範囲の外の引用、古い事実）に気づく
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import sys

import pytest

from plsql.cli import main as analyse

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "plsql-spec"
CORPUS = ROOT / "fixtures" / "plsql" / "src"
EXTERNAL = ROOT / "fixtures" / "plsql-external" / "create_order" / "src"
EXAMPLE = SKILL / "examples" / "create_order"

spec = importlib.util.spec_from_file_location("spec_facts", SKILL / "scripts" / "spec_facts.py")
facts_script = importlib.util.module_from_spec(spec)
sys.modules["spec_facts"] = facts_script   # dataclasses が module を引く
spec.loader.exec_module(facts_script)


def _analysis(tmp_path_factory, src):
    out = tmp_path_factory.mktemp("analysis")
    assert analyse([str(src), "--out-dir", str(out), "--quiet"]) in (0, 1)   # 1 = REVIEW / REDESIGN が残る。解析は済んでいる
    return out


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return _analysis(tmp_path_factory, CORPUS)


@pytest.fixture(scope="module")
def external(tmp_path_factory):
    return _analysis(tmp_path_factory, EXTERNAL)


def run(command, analysis, out):
    return facts_script.main([command, "--analysis", str(analysis), "--out-dir", str(out)])


# --- the facts are the source's, not the lowering's ------------------------------------------------------------


def test_every_routine_of_the_corpus_gets_a_section(corpus, tmp_path):
    assert run("facts", corpus, tmp_path) == 0
    modules, facts, _ = facts_script.load(corpus)
    assert len(facts) == 67
    assert {p.name for p in tmp_path.glob("*.md")} == {"README.md"} | {f"{m['name']}.md" for m in modules}
    for f in facts.values():
        assert f"<!-- facts:begin {f.id} -->" in (tmp_path / f"{f.module}.md").read_text(encoding="utf-8")


def test_only_what_the_source_says_is_a_fact(corpus):
    """The IR also carries what lowering wove in: trigger calls, the reads that feed them, row-limit raises."""
    _, facts, _ = facts_script.load(corpus)
    sources = {p.name: p.read_text(encoding="utf-8").splitlines() for p in CORPUS.rglob("*") if p.is_file()}
    for f in facts.values():
        lines = sources[pathlib.PurePath(f.file).name]
        for r in f.raises:
            if r["code"] is not None:
                assert str(r["code"]) in lines[r["line"] - 1] or str(r["code"]) in "\n".join(lines[f.start - 1:f.end]), (f.id, r)
        for op in f.sql:
            # an explicit cursor's query sits in the declarations (the package's, for a shared cursor), and the IR
            # places it at the loop that fetches it
            body = " ".join(lines).upper()
            assert op["kind"] in body, (f.id, op)
    cancel = facts["pkg_order_lock.cancel"]
    assert [op["kind"] for op in cancel.sql] == ["SELECT", "UPDATE", "SELECT", "UPDATE"], "the trigger's own read is not the routine's"
    assert not cancel.calls, "a woven trigger call is not a call the source makes"
    assert {x["trigger"] for x in cancel.fires} == {"trg_orders_audit", "trg_products_audit"}
    assert not facts["pkg_bulk_load.collect_open_orders"].raises, "the row-limit check belongs to the migration"


def test_what_a_test_has_to_pin_is_listed(external):
    _, facts, _ = facts_script.load(external)
    assert [a["name"] for a in facts["create_order"].ambient] == ["ORDER_SEQ.NEXTVAL", "SYSDATE", "SYSTIMESTAMP"]


# --- rewriting the facts leaves the prose alone ----------------------------------------------------------------


def test_facts_again_keeps_the_prose_and_reports_sections_the_source_lost(external, tmp_path, capsys):
    assert run("facts", external, tmp_path) == 0
    page = tmp_path / "create_order.md"
    written = page.read_text(encoding="utf-8").replace("（未記入: 原文を上から読み", "数量を確かめる（`create_order.prc:11`）。（未記入: 原文を上から読み", 1)
    written = re.sub(r"(<!-- facts:begin create_order -->\n).*?(\n<!-- facts:end create_order -->)", r"\1古い事実\2", written, flags=re.S)
    written += "\n## `create_order_v0`\n\n<!-- facts:begin create_order_v0 -->\n古い\n<!-- facts:end create_order_v0 -->\n\n### 動作\n\n消えた routine の文章\n"
    page.write_text(written, encoding="utf-8")
    assert run("check", external, tmp_path) == 1
    assert "事実の欄が IR と違う" in capsys.readouterr().out

    assert run("facts", external, tmp_path) == 0
    text = page.read_text(encoding="utf-8")
    assert "数量を確かめる（`create_order.prc:11`）" in text and "古い事実" not in text
    assert "消えた routine の文章" in text, "whether to drop it is the user's call"
    assert "create_order_v0" in capsys.readouterr().err


# --- check ---------------------------------------------------------------------------------------------------


def test_an_unwritten_spec_does_not_pass(external, tmp_path, capsys):
    run("facts", external, tmp_path)
    assert run("check", external, tmp_path) == 1
    assert "UNWRITTEN=6" in capsys.readouterr().err


def test_the_example_passes_and_is_up_to_date(external, capsys):
    assert run("check", external, EXAMPLE) == 0, capsys.readouterr().out


@pytest.mark.parametrize("old, new, said", [
    ("-20002", "在庫不足のエラー", "エラーコード -20002 が出てこない"),
    ("create_order.prc:16", "create_order.prc:160", "原文の範囲"),
    ("`order_items`", "明細の表", "書き込む表 `order_items`"),
])
def test_prose_that_drifts_from_the_facts_is_caught(external, tmp_path, capsys, old, new, said):
    out = tmp_path / "spec"
    shutil.copytree(EXAMPLE, out)
    page = out / "create_order.md"
    text = page.read_text(encoding="utf-8")
    head, block, prose = re.split(r"(<!-- facts:begin create_order -->.*?<!-- facts:end create_order -->)", text, flags=re.S)
    page.write_text(head + block + prose.replace(old, new), encoding="utf-8")
    assert run("check", external, out) == 1
    assert said in capsys.readouterr().out


def test_a_missing_analysis_is_an_input_error(tmp_path, capsys):
    assert run("facts", tmp_path, tmp_path / "spec") == 2
    assert "plsql.cli" in capsys.readouterr().err


# --- the skill's own text ------------------------------------------------------------------------------------


def test_every_command_in_the_skill_matches_the_allow_list_on_its_own():
    import fnmatch

    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    allowed = re.findall(r"^  - Bash\((.+)\)$", text, re.M)
    commands = [line for fence in re.findall(r"```bash\n(.*?)```", text, re.S) for line in fence.splitlines() if line.strip()]
    assert len(commands) == 4
    for command in commands:
        bare = re.sub(r"\"[^\"]*\"|'[^']*'", "", command)
        assert not re.search(r"[|;&<>]", bare.replace("<src>", "").replace("<out>", "").replace("<DDL>", "")), command
        assert any(fnmatch.fnmatch(command, pattern) for pattern in allowed), command


def test_a_question_to_the_user_comes_with_a_recommendation_a_reason_and_the_impact():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    shape = text[text.index("## 判断を求めるときの形"):text.index("## Workflow")]
    for part in ("何を決めるか", "推奨", "理由", "影響", "決めないとどうなるか"):
        assert f". {part} |" in shape, part
    assert "判断を求めるときの形" in text[text.index("### Step 1"):text.index("### Step 2")]
