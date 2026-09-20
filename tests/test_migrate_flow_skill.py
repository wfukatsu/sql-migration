"""migrate-flow スキル: 現行の仕様 → 判断 → 変換後の仕様 の承認をはさみ、そろってからテストする（2026-09-20）。

確かめるのは 4 つ:

* 承認は順にしか取れず、その段階の検査が通っていなければ取れない
* 承認には人と日付が要り、未決の判断を残すなら理由が要る
* 承認のあとで中身が変わると承認は古くなり、テストの関門は閉じる
* 仕様書と変換後の文書に入る Mermaid の図は、実際に描ける
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest
import yaml

from plsql.cli import main as analyse
from plsql.generate import main as generate

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
PROJECT = ROOT / "fixtures" / "plsql-external" / "create_order"
CORPUS = ROOT / "fixtures" / "plsql"
SPEC_EXAMPLE = SKILLS / "plsql-spec" / "examples" / "create_order"
DOCS_EXAMPLE = SKILLS / "plsql-migrate" / "examples" / "create_order"


def _load(skill, name):
    spec = importlib.util.spec_from_file_location(name, SKILLS / skill / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flow = _load("migrate-flow", "flow")
spec_facts = _load("plsql-spec", "spec_facts")
migration_doc = _load("plsql-migrate", "migration_doc")


@pytest.fixture()
def work(tmp_path):
    """A migration that has done all the work and holds no approval yet."""
    out = tmp_path / "shop"
    limits = tmp_path / "limits.yaml"
    shutil.copy(PROJECT / "limits.yaml", limits)
    common = [str(PROJECT / "src"), "--scalardb-schema", str(PROJECT / "scalardb-schema.json"), "--limits", str(limits)]
    assert analyse([str(PROJECT / "src"), "--out-dir", str(out / "spec-analysis"), "--quiet"]) in (0, 1)
    assert generate([*common, "--out-dir", str(out / "generated"), "--quiet", "--no-verify-compile"]) == 0
    assert analyse([*common, "--evidence", str(DOCS_EXAMPLE / "evidence.json"), "--out-dir", str(out / "generated" / "analysis"), "--quiet"]) in (0, 1)
    assert run("init", out, "--kind", "plsql", "--src", str(PROJECT / "src"), "--limits", str(limits),
               "--evidence", str(DOCS_EXAMPLE / "evidence.json")) == 0
    return out


def run(command, out, *extra):
    return flow.main([command, "--out", str(out), *extra])


def finish(out, stage):
    source = SPEC_EXAMPLE if stage == "spec" else DOCS_EXAMPLE
    target = out / ("spec" if stage == "spec" else "docs")
    target.mkdir(exist_ok=True)
    for page in source.glob("*.md"):
        shutil.copy(page, target / page.name)


def approve(out, stage, *extra):
    return run("approve", out, stage, "--by", "移行責任者", "--date", "2026-09-20", *extra)


# --- the order -----------------------------------------------------------------------------------------------


def test_nothing_is_approved_before_its_work_is_done(work, capsys):
    assert approve(work, "spec") == 1
    assert "現行の仕様（spec/*.md）がまだ無い" in capsys.readouterr().out
    assert run("gate", work) == 1

    spec_facts.main(["facts", "--analysis", str(work / "spec-analysis"), "--out-dir", str(work / "spec")])
    assert approve(work, "spec") == 1, "a skeleton is not a specification"
    assert "未記入" in capsys.readouterr().out


def test_approvals_come_in_order(work, capsys):
    finish(work, "spec")
    finish(work, "converted")
    assert approve(work, "converted") == 1
    assert "`spec`（現行の仕様）が承認されていない" in capsys.readouterr().out
    assert approve(work, "spec") == 0
    assert approve(work, "decisions") == 0
    assert approve(work, "converted") == 0
    assert run("gate", work) == 0
    state = yaml.safe_load((work / "flow.yaml").read_text(encoding="utf-8"))
    assert state["approvals"]["spec"]["承認した人"] == "移行責任者" and state["approvals"]["spec"]["指紋"]


def test_status_names_the_next_thing_to_do(work, capsys):
    assert run("status", work) == 0
    said = capsys.readouterr()
    assert "STAGE=spec" in said.err and "現行の仕様を仕上げる" in said.out
    finish(work, "spec")
    run("status", work)
    assert "現行の仕様を利用者に見せて、承認を求める" in capsys.readouterr().out
    approve(work, "spec")
    run("status", work)
    assert "STAGE=decisions" in capsys.readouterr().err


def test_an_approval_needs_a_date_that_is_a_date(work, capsys):
    finish(work, "spec")
    assert run("approve", work, "spec", "--by", "業務担当", "--date", "きのう") == 2


# --- open judgments --------------------------------------------------------------------------------------------


def test_open_judgments_stop_the_approval_unless_the_user_said_why(work, tmp_path, capsys):
    record = tmp_path / "record.yaml"
    record.write_text(yaml.safe_dump({"items": {"CALL-5": {"状態": "未決"}, "OPS-1": {"状態": "決定", "決定": "a", "決めた人": "運用担当", "日付": "2026-09-20"}}},
                                     allow_unicode=True), encoding="utf-8")
    state = yaml.safe_load((work / "flow.yaml").read_text(encoding="utf-8"))
    state["inputs"]["record"] = str(record)
    flow.save(work, state)
    finish(work, "spec")
    approve(work, "spec")
    assert approve(work, "decisions") == 1
    assert "未決の判断が残っている: CALL-5" in capsys.readouterr().out
    assert approve(work, "decisions", "--with-open", "再試行の回数は呼び出し側の設計会議（9/25）で決める") == 0
    kept = yaml.safe_load((work / "flow.yaml").read_text(encoding="utf-8"))["approvals"]["decisions"]["未決のまま進める"]
    assert kept == {"項目": ["CALL-5"], "理由": "再試行の回数は呼び出し側の設計会議（9/25）で決める"}


def test_a_decision_nobody_made_is_not_approvable(work, tmp_path, capsys):
    record = tmp_path / "record.yaml"
    record.write_text(yaml.safe_dump({"items": {"OPS-1": {"状態": "決定", "決定": "a"}}}, allow_unicode=True), encoding="utf-8")
    state = yaml.safe_load((work / "flow.yaml").read_text(encoding="utf-8"))
    state["inputs"]["record"] = str(record)
    flow.save(work, state)
    finish(work, "spec")
    approve(work, "spec")
    assert approve(work, "decisions") == 1
    assert "決めた人か日付が無い" in capsys.readouterr().out


def test_a_routine_left_in_review_is_an_open_judgment():
    out = pathlib.Path("unused")
    state = {"inputs": {"kind": "plsql"}}
    assert flow.open_items(state, out) == []


# --- an approval is of the content ---------------------------------------------------------------------------


def test_a_change_after_the_approval_closes_the_gate(work, capsys):
    for stage in flow.STAGES:
        finish(work, stage)
    for stage in flow.STAGES:
        assert approve(work, stage) == 0
    assert run("tested", work, "--result", "pass", "--report", "plsql-diff.json") == 0

    page = work / "spec" / "create_order.md"
    page.write_text(page.read_text(encoding="utf-8").replace("数量は 1 以上", "数量は 1 以上 100 以下"), encoding="utf-8")
    assert run("gate", work) == 1
    said = capsys.readouterr()
    assert "承認が古い" in said.out and "GATE=closed" in said.err
    assert run("tested", work, "--result", "pass") == 1, "a result is not recorded against approvals that no longer hold"

    assert approve(work, "spec") == 0
    assert yaml.safe_load((work / "flow.yaml").read_text(encoding="utf-8"))["test"] is None, "the earlier test tested something else"
    assert run("gate", work) == 0


def test_the_source_is_part_of_what_was_approved(work, tmp_path):
    src = tmp_path / "src"
    shutil.copytree(PROJECT / "src", src)
    assert run("init", work, "--kind", "plsql", "--src", str(src), "--limits", str(tmp_path / "limits.yaml"),
               "--evidence", str(DOCS_EXAMPLE / "evidence.json")) == 0
    for stage in flow.STAGES:
        finish(work, stage)
        assert approve(work, stage) == 0
    assert run("gate", work) == 0
    with (src / "create_order.prc").open("a", encoding="utf-8") as f:
        f.write("-- 承認のあとで原文が変わった\n")
    assert run("gate", work) == 1
    assert flow.stage_state(flow.load(work), work, "spec")[0] == "承認が古い"


def test_the_test_result_survives_putting_it_into_the_documents(tmp_path):
    """SKILL.md Step 4 の順: evidence なしで承認 → テスト → 比較の結果を文書に入れる → `converted` を取り直す。"""
    out, limits = tmp_path / "shop", tmp_path / "limits.yaml"
    shutil.copy(PROJECT / "limits.yaml", limits)
    common = [str(PROJECT / "src"), "--scalardb-schema", str(PROJECT / "scalardb-schema.json"), "--limits", str(limits)]
    assert analyse([str(PROJECT / "src"), "--out-dir", str(out / "spec-analysis"), "--quiet"]) in (0, 1)
    assert generate([*common, "--out-dir", str(out / "generated"), "--quiet", "--no-verify-compile"]) == 0
    assert analyse([*common, "--out-dir", str(out / "generated" / "analysis"), "--quiet"]) in (0, 1)
    assert run("init", out, "--kind", "plsql", "--src", str(PROJECT / "src"), "--limits", str(limits)) == 0
    finish(out, "spec")
    finish(out, "converted")
    doc = ["--src", str(PROJECT / "src"), "--generated", str(out / "generated"), "--analysis", str(out / "generated" / "analysis"),
           "--limits", str(limits), "--out-dir", str(out / "docs")]
    assert migration_doc.main(["facts", *doc]) == 0   # the example was written with evidence; this is the page before the test
    for stage in flow.STAGES:
        assert approve(out, stage) == 0
    evidence = DOCS_EXAMPLE / "evidence.json"
    assert run("tested", out, "--result", "pass", "--report", str(evidence)) == 0
    assert flow.load(out)["inputs"]["evidence"] == str(evidence.resolve())

    assert migration_doc.main(["facts", *doc, "--evidence", str(evidence)]) == 0
    assert flow.stage_state(flow.load(out), out, "converted")[0] == "承認が古い"
    assert approve(out, "converted") == 0, "the check reads the same evidence the pages were made from"
    test = flow.load(out)["test"]
    assert test["結果"] == "pass" and test["テストのあとで取り直した承認"] == ["converted（2026-09-20）"]
    assert test["承認の指紋"]["converted"] == flow.load(out)["approvals"]["converted"]["指紋"]
    assert run("gate", out) == 0

    assert approve(out, "decisions") == 0
    assert flow.load(out)["test"] is None, "what was tested is what `decisions` approves; approving it again is a new subject"


def test_a_redesign_is_open_until_limits_answer_it(work, tmp_path):
    state = flow.load(work)
    assert flow.open_items(state, work) == [], "create_order is REDESIGN, and rowLocks.optimistic answers LOCK-001"

    decisions = work / "generated" / "analysis" / "decisions.json"
    data = json.loads(decisions.read_text(encoding="utf-8"))
    data["routines"][0]["redesign"]["open"] = ["LOCK-001"]
    decisions.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert flow.open_items(state, work) == ["REDESIGN: create_order（limits.yaml に答えの無いルール: LOCK-001）"]

    decisions.unlink()
    assert "決めたかどうかが分からない" in flow.open_items(state, work)[0]


def test_init_again_keeps_the_approvals(work):
    finish(work, "spec")
    approve(work, "spec")
    assert run("init", work, "--kind", "plsql", "--src", str(PROJECT / "src"), "--limits", str(PROJECT / "limits.yaml")) == 0
    assert "spec" in yaml.safe_load((work / "flow.yaml").read_text(encoding="utf-8"))["approvals"]


# --- SQL ---------------------------------------------------------------------------------------------------------


def test_a_sql_migration_needs_pictures_and_a_record_of_what_did_not_convert(tmp_path, capsys):
    out, sql = tmp_path / "sql", tmp_path / "in.sql"
    sql.write_text("SELECT 1 FROM dual;\n", encoding="utf-8")
    assert run("init", out, "--kind", "sql", "--src", str(sql), "--source-dialect", "oracle", "--target-dialect", "scalardb") == 0
    (out / "spec").mkdir()
    (out / "spec" / "README.md").write_text("# 現行の仕様\n\n## 文 1\n\n定数を返す。\n", encoding="utf-8")
    assert approve(out, "spec") == 1
    assert "Mermaid の図が 1 つも無い" in capsys.readouterr().out
    (out / "spec" / "README.md").write_text("# 現行の仕様\n\n```mermaid\nflowchart LR\n  a --> b\n```\n", encoding="utf-8")
    assert approve(out, "spec") == 0

    (out / "converted").mkdir()
    (out / "converted" / "in.report.json").write_text('{"results": [{"index": 1, "status": "ERROR"}]}', encoding="utf-8")
    assert approve(out, "decisions") == 1
    assert "SQL-1" in capsys.readouterr().out


def test_naming_a_record_is_not_a_record_of_what_did_not_convert(tmp_path, capsys):
    # SKILL.md は「まだ無いファイルも渡しておく」と言う。渡してあるだけで通ると、ERROR の文は誰も決めないまま承認される
    out, sql, record = tmp_path / "sql", tmp_path / "in.sql", tmp_path / "record.yaml"
    sql.write_text("SELECT 1 FROM dual;\n", encoding="utf-8")
    assert run("init", out, "--kind", "sql", "--src", str(sql), "--record", str(record)) == 0
    (out / "spec").mkdir()
    (out / "spec" / "README.md").write_text("# 現行の仕様\n\n```mermaid\nflowchart LR\n  a --> b\n```\n", encoding="utf-8")
    assert approve(out, "spec") == 0
    (out / "converted").mkdir()
    (out / "converted" / "in.report.json").write_text(
        '{"results": [{"index": 1, "status": "OK"}, {"index": 2, "status": "ERROR"}]}', encoding="utf-8")
    assert approve(out, "decisions") == 1 and "SQL-2" in capsys.readouterr().out

    record.write_text("items:\n  SQL-2:\n    状態: 決定\n    決定: アプリへ移す\n    決めた人: 開発\n    日付: 2026-09-20\n", encoding="utf-8")
    assert approve(out, "decisions") == 0

    record.write_text("items:\n  SQL-2: アプリへ移す\n", encoding="utf-8")
    assert run("status", out) == 2, "a record that is not item -> fields is an input error, not a traceback"


# --- the pictures ------------------------------------------------------------------------------------------------


def _blocks(text):
    return re.findall(r"```mermaid\n(.*?)\n```", text, re.S)


def test_every_page_has_its_pictures(tmp_path):
    out = tmp_path / "analysis"
    assert analyse([str(CORPUS / "src"), "--out-dir", str(out), "--quiet"]) in (0, 1)
    spec_facts.main(["facts", "--analysis", str(out), "--out-dir", str(tmp_path / "spec")])
    index = (tmp_path / "spec" / "README.md").read_text(encoding="utf-8")
    assert len(_blocks(index)) == 2, "who touches which table, and who calls or fires whom"
    page = (tmp_path / "spec" / "prc_nightly_close.md").read_text(encoding="utf-8")
    flowchart = next(b for b in _blocks(page) if "開始" in b)
    assert "SAVEPOINT" in flowchart and "WHEN OTHERS" in flowchart and "class " in flowchart
    for block in _blocks(index) + _blocks(page):
        for label in re.findall(r'"([^"]*)"', block):
            assert "<" not in label.replace("<br/>", "") and ">" not in label.replace("<br/>", ""), label
    cancel = (tmp_path / "spec" / "pkg_order_lock.md").read_text(encoding="utf-8")
    assert "trg_orders_audit" not in next(b for b in _blocks(cancel) if "L19" in b), "what lowering wove in is not drawn"


def test_every_diagnostic_of_the_corpus_is_classified(tmp_path):
    """A code nobody classified shows as 未分類, and the corpus must not have one: add it to CHANGES."""
    generated = tmp_path / "generated"
    common = [str(CORPUS / "src"), "--scalardb-schema", str(CORPUS / "scalardb-schema.json"), "--limits", str(CORPUS / "limits.yaml")]
    assert generate([*common, "--out-dir", str(generated), "--quiet", "--no-verify-compile"]) == 0
    assert analyse([*common, "--out-dir", str(generated / "analysis"), "--quiet"]) in (0, 1)
    project = migration_doc.load(migration_doc.argparse.Namespace(
        src=str(CORPUS / "src"), generated=str(generated), analysis=str(generated / "analysis"),
        limits=str(CORPUS / "limits.yaml"), evidence=None, record=None))
    codes = {code for r in project.routines.values() for s in r.statements for _, code, _ in s["diagnostics"]}
    assert codes - set(migration_doc.CHANGES) == set()
    cancel = project.routines["pkg_order_lock.cancel"]
    assert {c["kind"] for c in migration_doc._changes(cancel)} == {migration_doc.MEANING, migration_doc.SHAPE}
    picture = "".join(migration_doc.change_diagram(cancel))
    assert "SET stock_qty = stock_qty" in picture and "v_rmw" not in picture, "before is the source, not what lowering rewrote"


@pytest.mark.skipif(shutil.which("mmdc") is None, reason="mermaid-cli is not installed")
def test_the_pictures_render(tmp_path):
    pages = list(SPEC_EXAMPLE.glob("*.md")) + list(DOCS_EXAMPLE.glob("*.md")) + [SKILLS / "migrate-flow" / "SKILL.md",
                                                                                   SKILLS / "migrate-flow" / "references" / "sql.md"]
    out = tmp_path / "analysis"
    assert analyse([str(CORPUS / "src"), "--out-dir", str(out), "--quiet"]) in (0, 1)
    spec_facts.main(["facts", "--analysis", str(out), "--out-dir", str(tmp_path / "spec")])
    pages += [tmp_path / "spec" / name for name in ("README.md", "prc_nightly_close.md", "pkg_bulk_load.md", "pkg_dynamic_search.md")]
    blocks = [b for page in pages for b in _blocks(page.read_text(encoding="utf-8"))]
    assert len(blocks) >= 15
    (tmp_path / "all.md").write_text("\n\n".join(f"```mermaid\n{b}\n```" for b in blocks), encoding="utf-8")
    done = subprocess.run(["mmdc", "-i", "all.md", "-o", "rendered.md", "-q"], cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    assert len(list(tmp_path.glob("rendered-*.svg"))) == len(blocks)


# --- the skill's own text ------------------------------------------------------------------------------------


def test_every_command_of_the_skill_matches_the_allow_list_on_its_own():
    import fnmatch

    text = (SKILLS / "migrate-flow" / "SKILL.md").read_text(encoding="utf-8")
    allowed = re.findall(r"^  - Bash\((.+)\)$", text, re.M)
    commands = [line for fence in re.findall(r"```bash\n(.*?)```", text, re.S) for line in fence.splitlines() if line.strip()]
    assert len(commands) >= 12
    for command in commands:
        assert not re.search(r"[|;&]|\\$", re.sub(r"<[^<>]+>|\"[^\"]*\"", "", command)), command
        assert any(fnmatch.fnmatch(command, pattern) for pattern in allowed), command


def test_the_skill_asks_before_it_approves_and_before_it_tests():
    text = (SKILLS / "migrate-flow" / "SKILL.md").read_text(encoding="utf-8")
    assert "承認するのは利用者であって、あなたではない" in text
    assert text.index("flow.py gate") < text.index("flow.py tested")
    for stage in flow.STAGES:
        assert f"approve {stage} --out" in text, stage
    approval = (SKILLS / "migrate-flow" / "references" / "approval.md").read_text(encoding="utf-8")
    for part in ("何を決めるか", "推奨", "理由", "影響", "決めないと"):
        assert f"**{part}**" in approval, part
