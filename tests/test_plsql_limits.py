"""走査行数の上限（2026-09-17 の決定）。

生成コードは cursor の行を先に全部読むので、動く行数はメモリで決まる。上限は業務ごとに違うため、既定を
config に置き、routine 単位で上書きする。ここで固定するのは、上限が**生成物まで届いていること**と、
超えたときに**理由を言って止まる**ことである。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plsql.generate import _undecided_limits, main as generate
from plsql.limits import DEFAULT_SCAN_ROWS, Limits

SRC = "fixtures/plsql/src"
CONFIG = "fixtures/plsql/limits.yaml"


def test_without_a_config_the_builtin_default_applies():
    limits = Limits.load(None)
    assert limits.for_routine("anything") == DEFAULT_SCAN_ROWS
    assert "決められていない" in limits.explain("anything")


def test_a_routine_can_be_given_its_own_limit():
    limits = Limits.load(CONFIG)
    assert limits.for_routine("pkg_order_pricing.order_total") == 200
    assert limits.for_routine("prc_nightly_close") == limits.scan_rows


def test_the_explanation_says_where_the_limit_came_from():
    """A generated file saying "10000" without saying why is a number nobody can question."""
    limits = Limits.load(CONFIG)
    assert CONFIG in limits.explain("pkg_order_pricing.order_total")
    assert "決められていない" in limits.explain("pkg_order_report.mark_reviewed")


def test_a_missing_config_is_an_error_not_a_silent_default():
    with pytest.raises(FileNotFoundError):
        Limits.load("does-not-exist.yaml")


@pytest.mark.parametrize("value", [0, -1])
def test_a_limit_that_cannot_stop_anything_is_refused(tmp_path, value):
    path = tmp_path / "limits.yaml"
    path.write_text(f"scanRows:\n  default: {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="正の整数"):
        Limits.load(path)


def test_the_limit_reaches_the_generated_code(tmp_path):
    generate([SRC, "--out-dir", str(tmp_path), "--limits", CONFIG, "--quiet"])
    source = (Path(tmp_path) / "src/main/java/com/example/migrated/infrastructure"
              / "PkgOrderPricingRepository.java").read_text(encoding="utf-8")
    assert "200" in source and "走査行数が上限" in source


def test_the_generated_check_stops_before_the_rows_are_held(tmp_path):
    """Counting after reading them all would have used the memory before it could stop."""
    generate([SRC, "--out-dir", str(tmp_path), "--limits", CONFIG, "--quiet"])
    source = (Path(tmp_path) / "src/main/java/com/example/migrated/infrastructure"
              / "PkgOrderPricingRepository.java").read_text(encoding="utf-8")
    check = source.index("rows.size() >=")
    add = source.index("rows.add(new OrderTotalLoop1Row")
    assert check < add, "the limit is checked after the row was added"


def test_the_generated_code_says_where_its_limit_came_from(tmp_path):
    generate([SRC, "--out-dir", str(tmp_path), "--limits", CONFIG, "--quiet"])
    source = (Path(tmp_path) / "src/main/java/com/example/migrated/infrastructure"
              / "PkgOrderPricingRepository.java").read_text(encoding="utf-8")
    assert "limits.yaml で pkg_order_pricing.order_total に指定された値" in source


def test_a_routine_with_no_specific_limit_says_so_in_the_code(tmp_path):
    """"既定値" is not a decision; the generated code should not read as if it were.

    corpus の config では全部決まった（#19 / 2026-09-19）ので、config を渡さずに見る。"""
    generate([SRC, "--out-dir", str(tmp_path), "--quiet"])
    source = (Path(tmp_path) / "src/main/java/com/example/migrated/infrastructure"
              / "PkgOrderPricingRepository.java").read_text(encoding="utf-8")
    assert "この routine 固有の上限は決められていない" in source


# --- #19: 「誰も決めていない」を合否に出す（--limits-strict、2026-09-18） -------------------------

UNBOUNDED = """\
CREATE OR REPLACE PROCEDURE prc_sum_lines(p_order_id IN NUMBER, p_total OUT NUMBER) IS
BEGIN
  p_total := 0;
  FOR r IN (SELECT qty FROM order_lines WHERE order_id = p_order_id) LOOP
    p_total := p_total + r.qty;
  END LOOP;
END prc_sum_lines;
/
"""


def _tree(tmp_path, limits: str | None):
    """走査を 1 本だけ持つ小さな入力。上限の設定を差し替えて生成できるようにする。"""
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    (source / "schema.sql").write_text(Path("fixtures/plsql/src/schema.sql").read_text(encoding="utf-8"),
                                       encoding="utf-8")
    (source / "prc_sum_lines.prc").write_text(UNBOUNDED, encoding="utf-8")
    config = tmp_path / "limits.yaml"
    config.write_text(limits if limits is not None else "scanRows:\n  default: 10000\n", encoding="utf-8")
    return [str(source), "--scalardb-schema", "fixtures/plsql/scalardb-schema.json",
            "--out-dir", str(tmp_path / "out"), "--limits", str(config)]


def test_the_default_says_it_is_not_a_decision():
    """既定値は「決めていない」という意味である。生成コードのコメントだけでなく、型でもそう言う。"""
    limits = Limits.load(CONFIG)
    assert limits.decided("pkg_order_pricing.order_total")
    assert not limits.decided("pkg_order_report.mark_reviewed")


def test_deciding_not_to_use_a_limit_is_also_deciding():
    """「上限では守らない」は決定であって、書き忘れではない。ツールが区別できる必要がある。"""
    limits = Limits.load(CONFIG)
    assert limits.decided("prc_nightly_close")
    assert "TX-001" in limits.not_limited["prc_nightly_close"]
    assert "上限では守らないと決めてある" in limits.explain("prc_nightly_close")


def test_a_routine_cannot_be_in_both_lists(tmp_path):
    path = tmp_path / "limits.yaml"
    path.write_text("scanRows:\n  routines:\n    a.b: 10\n  notLimited:\n    a.b: なぜか両方\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="どちらか一方"):
        Limits.load(path)


def test_the_hook_is_what_the_rules_require_not_the_verdict():
    """最初の形（AUTO の routine を見る）は**原理的に発火しなかった**。CUR-002 が cursor FOR loop を
    すべて REVIEW に落とすので、走査を持つ AUTO routine は構造上存在しない。"""
    class _Decision:
        def __init__(self, tests):
            self._tests = tests

        def required_tests(self):
            return self._tests

    decisions = {"a.scan": _Decision(["row_limit", "performance"]),
                 "b.plain": _Decision(["equivalent_result"])}
    limits = Limits()
    assert _undecided_limits(decisions, limits) == ["a.scan"]
    assert _undecided_limits(decisions, Limits(by_routine={"a.scan": 100})) == []
    assert _undecided_limits(decisions, Limits(not_limited={"a.scan": "理由"})) == []


def test_strict_is_off_unless_asked_for(tmp_path):
    """既定で落ちるようにすると、上限を決める前に生成そのものが使えなくなる。"""
    assert generate(_tree(tmp_path, None) + ["--quiet"]) == 0


def test_strict_fails_on_a_scan_nobody_decided(tmp_path, capsys):
    """記録から合否までを通しで見る唯一のテスト。ここが無いと、検出をやめても全件緑になる。"""
    assert generate(_tree(tmp_path, None) + ["--limits-strict"]) == 1
    assert "prc_sum_lines" in capsys.readouterr().out


def test_strict_passes_once_the_limit_is_decided(tmp_path, capsys):
    decided = "scanRows:\n  default: 10000\n  routines:\n    prc_sum_lines: 200\n"
    assert generate(_tree(tmp_path, decided) + ["--limits-strict"]) == 0


def test_strict_passes_when_the_decision_was_not_to_limit(tmp_path):
    not_limited = "scanRows:\n  default: 10000\n  notLimited:\n    prc_sum_lines: 再設計で決める\n"
    assert generate(_tree(tmp_path, not_limited) + ["--limits-strict"]) == 0


def test_strict_says_why_even_when_quiet(tmp_path, capsys):
    assert generate(_tree(tmp_path, None) + ["--limits-strict", "--quiet"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "prc_sum_lines" in captured.err


def test_without_a_config_the_list_says_so(tmp_path, capsys):
    """名前の羅列は「値を書き忘れた」と読める。実際は「ファイルを渡していない」かもしれず、
    次にすることが違う。"""
    assert generate([SRC, "--out-dir", str(tmp_path), "--limits-strict"]) == 1
    assert "--limits was not given" in capsys.readouterr().out


def test_with_a_config_the_list_is_about_the_routines(tmp_path, capsys):
    """1 本だけ決めていない config。挙がるのはその routine で、「config が無い」ではない。"""
    config = tmp_path / "limits.yaml"
    config.write_text(Path(CONFIG).read_text(encoding="utf-8").replace(
        "    pkg_bulk_load.archive_lines: 200\n", ""), encoding="utf-8")
    assert generate([SRC, "--out-dir", str(tmp_path / "out"), "--limits", str(config), "--limits-strict"]) == 1
    out = capsys.readouterr().out
    assert "--limits was not given" not in out and "pkg_bulk_load.archive_lines" in out


def test_the_corpus_has_nothing_left_undecided(tmp_path):
    """2026-09-19（#19）に残りを決めた: archive_lines は 200、ほかは人が値を決めなくて済む形にした
    （問い合わせが件数を絞っている / COUNT(*) にした / 処理対象を件数つきで繰り返し読む）。"""
    assert generate([SRC, "--out-dir", str(tmp_path), "--limits", CONFIG, "--limits-strict",
                     "--quiet"]) == 0


# --- #9: 行ロックを落とす判断も routine ごとに記録する（2026-09-18） ------------------------------

def test_a_row_lock_decision_is_recorded_per_routine():
    """決めることが routine ごとに違う——呼び出し側が再試行するか、冪等か、業務例外と衝突を
    区別できるか。だから 1 つの旗では切り替えない。"""
    from plsql.limits import RowLocks

    locks = RowLocks.load(CONFIG)
    assert locks.decided("pkg_stock_reserve.reserve")
    # C 型（SKIP LOCKED）は 2026-09-19 に決まった: 担当者列は足さず楽観制御で移す。書いていない
    # routine は決まっていない——それは今も変わらない
    assert locks.decided("pkg_stock_reserve.claim_batch")
    assert "担当者列は足さない" in locks.why("pkg_stock_reserve.claim_batch")
    assert not locks.decided("pkg_not_recorded.anything")
    assert "呼び出し側" in locks.why("pkg_stock_reserve.reserve") \
        or "commit で弾かれる" in locks.why("pkg_stock_reserve.reserve")


def test_without_a_config_nothing_is_decided():
    """既定は「決めていない」。決めていない routine の書き込みは拒否したままになる。"""
    from plsql.limits import RowLocks

    assert RowLocks.load(None).optimistic == {}


def test_the_decided_routine_converts_and_says_what_the_caller_must_do():
    """ロックが落ちた読みに基づく書き込みは、決めた routine でだけ通る。**要求は残す**。"""
    import pathlib

    from plsql.limits import RowLocks
    from plsql.lower import _walk
    from plsql.report import analyse as build_analysis

    fixtures = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    corpus = build_analysis(fixtures / "src", fixtures / "src" / "schema.sql",
                            scalardb_schema=fixtures / "scalardb-schema.json",
                            row_locks=RowLocks.load(CONFIG))
    by_id = {r.id: r for _, r in corpus.routines()}

    decided = [s for s in _walk(by_id["pkg_stock_reserve.reserve"].body) if s.kind == "SqlOperation"]
    assert [s.target_status for s in decided] == ["WARN", "OK"], "決めた routine の書き込みが通っていない"
    assert any(d.code == "OPTIMISTIC" for s in decided for d in s.diagnostics), \
        "再試行が呼び出し側の責務であることが残っていない"

    # 記録を渡さない解析では、ロックが落ちても楽観制御へ移さない
    bare = build_analysis(fixtures / "src", fixtures / "src" / "schema.sql",
                          scalardb_schema=fixtures / "scalardb-schema.json")
    undecided = [s for _, r in bare.routines() if r.id == "pkg_stock_reserve.claim_batch"
                 for s in _walk(r.body) + [l.query for l in _walk(r.body) if getattr(l, "query", None)]
                 if s.kind == "SqlOperation"]
    assert not [d for s in undecided for d in s.diagnostics if d.code == "OPTIMISTIC"], \
        "決めていない routine が楽観制御へ移されている"


def test_every_row_lock_decision_is_about_something():
    """記録された routine は、**落とす行ロックか、割る読み書き**を実際に持っていなければならない。

    何も持たない routine の記録は、決定ではなく書き間違いである。実際に一度あった——
    `transactions.perIteration` に足すつもりの `prc_reprice_all` が、同じ見出しの下にある別の欄
    （`rowLocks.optimistic`）にも入ってしまい、**誰も決めていない「行ロックを落とす」決定**として
    マージされた（2026-09-19 に気づいて外した）。
    """
    import pathlib

    from plsql.limits import RowLocks
    from plsql.lower import _walk
    from plsql.report import analyse as build_analysis

    fixtures = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    locks = RowLocks.load(CONFIG)
    corpus = build_analysis(fixtures / "src", fixtures / "src" / "schema.sql",
                            scalardb_schema=fixtures / "scalardb-schema.json", row_locks=locks)
    by_id = {r.id: r for _, r in corpus.routines()}
    empty = []
    for routine_id in locks.optimistic:
        statements = _walk(by_id[routine_id].body)
        if not any(getattr(s, "locking_mode", None) or
                   any(d.code in ("RMW_SPLIT", "OPTIMISTIC", "MERGE_SPLIT") for d in s.diagnostics)
                   for s in statements):
            empty.append(routine_id)
    assert empty == [], f"行ロックも読み書きも持たない routine が記録されている: {empty}"
