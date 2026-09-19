"""#24 / #14: 決めた routine を、トランザクション単位に割った部品として出す。

決定は #3（1 反復 = 1 トランザクション / エラー行は別トランザクション / `batch_control` も別）で、
ここで固定するのは**その決定が生成物まで届いていること**と、**決めていないものは割られない**ことである。

割った形でいちばん壊れやすいのは、**コメントに書いた呼び出し方と実際の signature の食い違い**である。
生成コードはループを持たないので、呼び出し側はそのコメントを読んで書く——ずれていたら、読んだ人は
コンパイルできないコードを書くことになる。だからそこを 1 本のテストで押さえる。
"""

from __future__ import annotations

import re

import pytest

from plsql.gen_java import split
from plsql.generate import main as generate
from plsql.limits import Boundaries

SRC = "fixtures/plsql/src"
CONFIG = "fixtures/plsql/limits.yaml"
GENERATED = "src/main/java/com/example/migrated/application"


def _service(tmp_path, name: str, limits: str | None = CONFIG) -> str:
    arguments = [SRC, "--scalardb-schema", "fixtures/plsql/scalardb-schema.json",
                 "--out-dir", str(tmp_path), "--quiet"]
    if limits:
        arguments += ["--limits", limits]
    generate(arguments)
    return (tmp_path / GENERATED / f"{name}.java").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nightly(tmp_path_factory):
    return _service(tmp_path_factory.mktemp("decided"), "PrcNightlyCloseService")


@pytest.fixture(scope="module")
def restock(tmp_path_factory):
    return _service(tmp_path_factory.mktemp("forall"), "PkgBulkLoadService")


def test_the_decision_is_recorded_as_data():
    """「割ってよい」はコメントではなくデータである。書き忘れと区別できる必要がある。"""
    boundaries = Boundaries.load(CONFIG)
    assert boundaries.decided("prc_nightly_close")
    assert boundaries.decided("pkg_bulk_load.restock")
    assert boundaries.decided("prc_reprice_all"), "#3 §E が名指ししている routine である"
    # TX-001 の routine は 2026-09-19 までに全部決まった。「書いていなければ決まっていない」を見る
    assert boundaries.decided("prc_purge_audit")
    assert not boundaries.decided("prc_not_recorded")
    assert "1 受注 = 1 トランザクション" in boundaries.why("prc_nightly_close")


def test_without_the_decision_nothing_is_split(tmp_path):
    """既定は「決めていない」。決めていない routine は 1 つの method のままで、`COMMIT` で止まる。

    **止まっているのが正しい。** 境界をどこに引くかは業務の設計であって、生成器が推測してよい
    ものではない。
    """
    source = _service(tmp_path, "PrcNightlyCloseService", limits=None)
    assert "public void prcNightlyClose(" in source
    assert "prcNightlyCloseOne(" not in source
    assert "Commit is not translated" in source


def test_the_parts_are_the_ones_that_were_decided(nightly):
    for part in ("Start", "Targets", "One", "Failed", "Done", "FailedBatch"):
        assert f"public {'List' if part == 'Targets' else 'void'}" in nightly
        assert f"prcNightlyClose{part}(" in nightly
    assert "public void prcNightlyClose(" not in nightly, "割ったのに元の method も出ている"


def test_the_generated_code_has_no_loop(nightly):
    """**ループは呼び出し側にある**（計画 §9 / #24 の決定）。生成コードが回すと、1 反復ごとに
    境界へ踏み込むことになる。"""
    code = [line for line in nightly.splitlines() if not line.strip().startswith("//")]
    assert not [line for line in code if re.search(r"\bfor\s*\(", line)]


def test_the_recommended_loop_is_a_comment(nightly):
    comment = [line for line in nightly.splitlines() if line.strip().startswith("//")]
    assert any("tx.run(() -> service.prcNightlyCloseOne(" in line for line in comment)
    assert any("決定ではない" in line for line in comment), "出発点だと書いていない"


@pytest.mark.parametrize("service,name", [("nightly", "prcNightlyClose"), ("restock", "restock")])
def test_the_comment_calls_the_signature_that_was_generated(service, name, request):
    """コメントの呼び出しと、実際の signature の**引数の数が合う**こと。

    ここがずれると、コメントを読んで書いた呼び出し側はコンパイルできない。生成コードがループを
    持たない以上、このコメントが唯一の手掛かりである。
    """
    source = request.getfixturevalue(service)
    inside = r"((?:[^()]|\([^()]*\))*)"
    calls = dict(re.findall(rf"service\.({name}\w+)\({inside}\)", source))
    signatures = dict(re.findall(rf"public \w[\w<>]* ({name}\w+)\({inside}\)", source))
    assert calls, "呼び出し方のコメントが無い"
    for part, arguments in calls.items():
        assert part in signatures, f"{part} を呼んでいるのに、その method が無い"
        given = [a for a in arguments.split(",") if a.strip()]
        declared = [p for p in signatures[part].split(",") if p.strip()]
        assert len(given) == len(declared), \
            f"{part}: コメントは {len(given)} 個渡しているが、signature は {len(declared)} 個受け取る"


def test_the_iteration_takes_the_row_and_the_failure_takes_the_exception(nightly):
    assert re.search(r"void prcNightlyCloseOne\(PrcNightlyCloseLoop\d+Row r, AuditContext audit\)", nightly)
    assert re.search(r"void prcNightlyCloseFailed\(PrcNightlyCloseLoop\d+Row r, Exception failed, "
                     r"AuditContext audit\)", nightly)
    assert re.search(r"vErrorText = Plsql\.fit\(failed\.getMessage\(\), \d+, false\);", nightly), \
        "`SQLERRM` は、割ったあとは渡された例外から読む"


def test_the_targets_are_read_in_a_transaction_of_their_own(nightly):
    """割る前は「自分が書く表を読んでいる」として拒んでいた（P2-4）。対象を読むのが別の
    トランザクションになったので、その制限には当たらない——代わりに**時点がずれる**。"""
    assert "prcNightlyCloseTargets" in nightly
    assert "cursor FOR loop whose body writes" not in nightly
    assert "別のトランザクション" in nightly and "ずれる" in nightly


def test_the_intermediate_commit_became_the_boundary(nightly):
    """`IF MOD(v_processed, 100) = 0 THEN COMMIT` は、1 反復 = 1 トランザクションに吸収された。
    中身が `COMMIT` だけの分岐を残すと、空の `if` になる。"""
    assert "Commit is not translated" not in nightly
    assert "MOD" not in nightly


def test_a_counter_that_spanned_iterations_is_called_out(nightly):
    """割ったあと、`v_processed` は 1 回ごとに 0 から始まる。黙って残すと「数えている」ように
    見えるので、そう書く。"""
    assert "`v_processed` は反復をまたいで数えていた値である" in nightly


def test_the_forall_element_order_follows_the_original_signature(restock):
    """要素の並びは元の routine の引数順にする。本体が読んだ順にすると、SQL の書き方が変わった
    だけで引数が入れ替わる——呼び出し側は PL/SQL の signature しか見ていない。"""
    assert "void restockOne(BigDecimal pProductIdsItem, Long pDeltasItem)" in restock


def test_the_failed_element_gets_its_position_and_the_offset_is_stated(restock):
    """`SQL%BULK_EXCEPTIONS` は移行先に無い。どの要素が失敗したかを知っているのは、回している側
    である。ただし Oracle は 1 から、生成したループは 0 から数える——1 ずれることを書いておく。"""
    assert re.search(r"void restockFailed\(int i, Exception failed, AuditContext audit\)", restock)
    assert "vErrorIndex = i;" in restock
    assert "**1 から**" in restock and "**0 から**" in restock


def test_the_bulk_exception_branch_is_gone(restock):
    """-24381（一括の中に失敗があった）は、まとめて投げていたから付いていた番号である。
    要素ごとに失敗が来るなら、その分岐そのものが無くなる。"""
    assert "24381" not in restock
    assert "BULK_EXCEPTIONS" not in restock


def test_a_routine_that_does_not_fit_the_shape_is_not_split(tmp_path):
    """決めてあっても、形が合わなければ割らない。**合わない形を割ると、元と違うことをする。**"""
    source = tmp_path / "src"
    source.mkdir()
    (source / "schema.sql").write_text(
        (pytest.importorskip("pathlib").Path(SRC) / "schema.sql").read_text(encoding="utf-8"),
        encoding="utf-8")
    (source / "prc_two_loops.prc").write_text("""\
CREATE OR REPLACE PROCEDURE prc_two_loops(p_batch_date IN DATE) IS
BEGIN
  FOR r IN (SELECT order_id FROM orders WHERE status = 'SHIPPED') LOOP
    UPDATE orders SET status = 'CLOSED' WHERE order_id = r.order_id;
  END LOOP;
  FOR r IN (SELECT order_id FROM orders WHERE status = 'NEW') LOOP
    UPDATE orders SET status = 'SEEN' WHERE order_id = r.order_id;
  END LOOP;
END prc_two_loops;
/
""", encoding="utf-8")
    config = tmp_path / "limits.yaml"
    config.write_text("transactions:\n  perIteration:\n    prc_two_loops: 反復が 2 つある\n",
                      encoding="utf-8")
    generate([str(source), "--scalardb-schema", "fixtures/plsql/scalardb-schema.json",
              "--out-dir", str(tmp_path / "out"), "--limits", str(config), "--quiet"])
    generated = (tmp_path / "out" / GENERATED / "PrcTwoLoopsService.java").read_text(encoding="utf-8")
    assert "prcTwoLoopsOne(" not in generated
    assert "当てはまらないので割っていない" in generated


def test_the_decision_does_not_change_the_verdict():
    """生成できるようになったことと、そのまま移してよいことは別である。判定は REDESIGN のまま
    にしてある（trigger を REDESIGN のままにしたのと同じ理由）。"""
    from plsql.rules.engine import Decision  # noqa: F401  (読む人へ: 判定は rules が決める)

    assert Boundaries.load(CONFIG).decided("prc_nightly_close")


# --- 自律トランザクション（#3 §G / 2026-09-19） --------------------------------------------------

@pytest.fixture(scope="module")
def autonomous(tmp_path_factory):
    return _service(tmp_path_factory.mktemp("autonomous"), "PrcAuditAutonomousService")


def test_the_autonomous_routine_is_recorded_as_separate():
    boundaries = Boundaries.load(CONFIG)
    assert "prc_audit_autonomous" in boundaries.separate
    assert boundaries.decided("prc_audit_autonomous")


def test_the_separate_routine_has_no_commit_or_rollback(autonomous):
    """境界は呼び出し側にある（計画 §9）。中身だけを出す。"""
    code = "\n".join(line for line in autonomous.splitlines() if not line.strip().startswith("//"))
    assert "Commit is not translated" not in autonomous and "Rollback" not in code
    assert "repository.prcAuditAutonomousStmt1(" in code


def test_a_handler_that_only_reraises_is_not_emitted(autonomous):
    """`WHEN OTHERS THEN ROLLBACK; RAISE;` は、別のトランザクションで呼ぶ側がまさにすること。
    出すと `RAISE` が別の例外に包み直され、**元の例外が変わる**。"""
    assert "catch (" not in autonomous
    assert 'MigratedException(0, "RAISE")' not in autonomous


def test_the_caller_is_told_to_use_its_own_boundary(autonomous):
    assert "別のトランザクションで呼ぶ" in autonomous
    assert "tx.runSeparately(" in autonomous


def test_a_call_inside_another_transaction_is_refused(tmp_path):
    """同じトランザクションの中で呼ぶと、親が rollback したときに一緒に消える。どこで別の境界を
    開くかは呼び出し側の設計なので、生成器は推測しない。"""
    split.set_boundaries(Boundaries(separate={"prc_log": "自律"}))
    try:
        source = tmp_path / "src"
        source.mkdir()
        (source / "schema.sql").write_text(
            pathlib_path(SRC, "schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
        (source / "prc_log.prc").write_text("""\
CREATE OR REPLACE PROCEDURE prc_log(p_text IN VARCHAR2) IS
  PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
  UPDATE batch_control SET status = p_text WHERE batch_name = 'LOG';
  COMMIT;
END prc_log;
/
""", encoding="utf-8")
        (source / "prc_work.prc").write_text("""\
CREATE OR REPLACE PROCEDURE prc_work IS
BEGIN
  prc_log('start');
END prc_work;
/
""", encoding="utf-8")
        from plsql.report import analyse
        from plsql.gen_java.service import generate_module

        from plsql.analysis import analyse as analyse_program

        analysis = analyse(source, source / "schema.sql")
        analyse_program(analysis.program)   # 呼び出しの解決（P2-1）。生成の前に走る段である
        module = next(m for m in analysis.program.modules if m.name == "prc_work")
        java = generate_module(module, "g.app", "g.infra", "g.domain", analysis.program).file.render()
        assert "は別トランザクションで呼ぶ routine である" in java
    finally:
        split.set_boundaries(Boundaries())


def pathlib_path(*parts):
    import pathlib

    return pathlib.Path(*parts)
