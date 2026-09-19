"""#12 §0「検証で追う」の照合（2026-09-19 の決定: 1a / 2b / 3 / 4）。

照合そのものが動くことは `TriggerChecksIT` が実クラスタで見ている（直接の書き込みを見つける／補う／
補完が拒否の証拠を消さない）。ここで固定するのは、**trigger の中身から正しい照合が組まれること**と、
組めないものを組めないと言うことである。
"""

from __future__ import annotations

import pathlib

import pytest

from plsql.report import analyse as build_analysis
from plsql.trigger_checks import DAILY, HOURLY, checks

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def found():
    analysis = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    return {(c.trigger, c.kind): c for c in checks(analysis.program, analysis.symbol_table().oracle_schema)}


def test_every_trigger_gets_the_checks_its_shape_calls_for(found):
    assert set(found) == {("trg_orders_audit", "A"), ("trg_products_audit", "A"),
                          ("trg_products_audit", "B"), ("trg_payments_guard", "D"), ("trg_orders_seq", "C")}


def test_the_interval_follows_how_much_damage_a_late_find_leaves(found):
    """A / C は日次、B / D は短い間隔（決定 2b）。B / D は検出が遅れた分だけ不正な値が残る。"""
    assert {k: c.interval for k, c in found.items()} == {
        ("trg_orders_audit", "A"): DAILY, ("trg_products_audit", "A"): DAILY, ("trg_orders_seq", "C"): DAILY,
        ("trg_products_audit", "B"): HOURLY, ("trg_payments_guard", "D"): HOURLY}


def test_the_audit_insert_is_read_by_what_each_column_carries(found):
    audit = found[("trg_products_audit", "A")].audit
    assert audit.table == "audit_log" and audit.key == "product_id" and audit.watched == "unit_price"
    assert audit.literals == {"table_name": "PRODUCTS", "action": "PRICE"}
    assert audit.roles["changed_by"] == "who" and audit.roles["audit_id"] == "id"
    assert audit.text, "TO_CHAR で記録している値は、比べるときも文字にする"
    assert not found[("trg_orders_audit", "A")].audit.text


def test_the_rejection_is_the_triggers_own_condition(found):
    assert found[("trg_products_audit", "B")].condition == ":NEW.unit_price < :OLD.unit_price * 0.5"


def test_a_read_only_guard_is_checked_by_calling_its_body(found):
    assert found[("trg_payments_guard", "D")].reads == ["order_id"]


def test_the_generated_backfill_marks_what_it_wrote_and_skips_rejections():
    """補った行には印を付ける（決定 4）。B が拒否する行は補わない——補うと拒否の照合から消える。"""
    from plsql.gen_java.checks import generate

    analysis = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    java = generate(checks(analysis.program, analysis.symbol_table().oracle_schema), "g.app", "g.domain",
                    {("products", "unit_price"): ("BIGINT", 2), ("products", "product_id"): ("BIGINT", 0)}
                    ).render()
    assert "'BACKFILL'" in java
    backfill = java[java.index("public int trgProductsAuditBackfill("):]
    assert "trgProductsAuditRejected()" in backfill.split("return written;")[0]
    assert 'Plsql.read(rows.getObject(2), "BIGINT", 2)' in java, "scaled の金額を戻して読んでいない"


def test_the_direct_write_restriction_names_only_b_and_d_tables(tmp_path):
    """決定 3: 被害が残るのは B / D だけなので、権限で塞ぐのもその表だけ。"""
    from plsql.generate import main as generate

    generate([str(SRC), "--scalardb-schema", str(FIXTURES / "scalardb-schema.json"),
              "--out-dir", str(tmp_path), "--quiet"])
    grants = (tmp_path / "db" / "restrict-direct-writes.sql").read_text(encoding="utf-8")
    revoked = sorted(line.split(" ON ")[1].split(" FROM")[0] for line in grants.splitlines()
                     if line.startswith("REVOKE"))
    assert revoked == ["plsqlpoc.payments", "plsqlpoc.products"]


# --- A-2 / A-3（2026-09-19）: 照合ジョブと控えの表 -------------------------------------------------

def _generated(tmp_path):
    from plsql.generate import main as generate

    generate([str(SRC), "--scalardb-schema", str(FIXTURES / "scalardb-schema.json"),
              "--limits", str(FIXTURES / "limits.yaml"), "--out-dir", str(tmp_path), "--quiet"])
    app = tmp_path / "src/main/java/com/example/migrated/application"
    return (app / "TriggerChecks.java").read_text(encoding="utf-8"), \
        (app / "TriggerCheckJob.java").read_text(encoding="utf-8"), tmp_path


def test_each_check_reads_in_one_read_only_transaction(tmp_path):
    """別々に読むと、その間の書き込みで誤検知が出る。`setReadOnly(true)` が ScalarDB の読み取り専用
    トランザクションになることは実クラスタで確かめた（書き込みは DB-CORE-10211 で拒否される）。"""
    _, job, _ = _generated(tmp_path)
    assert "connection.setReadOnly(true);" in job and "connection.setReadOnly(false);" in job
    assert 'contains("conflict")' in job, "衝突で弾かれた読み取りだけを読み直す"


def test_the_backfill_and_the_baseline_update_are_separate_transactions(tmp_path):
    """控えの更新は監査を全件読む。補完と同じトランザクションだと、書いた表の走査として拒否される。"""
    _, job, _ = _generated(tmp_path)
    daily = job[job.index("public Report daily("):job.index("public Report hourly(")]
    assert daily.index("Backfill(") < daily.index("Remember(")
    assert daily.count("write(() ->") == 2


def test_the_last_audited_value_starts_from_the_baseline(tmp_path):
    """監査行が削除されても、最後に監査した値は控えに残る。"""
    checks, _, _ = _generated(tmp_path)
    last = checks[checks.index("private Map<String, Object[]> trgProductsAuditLastAudited()"):]
    assert last.index("FROM trigger_check_baseline") < last.index("FROM audit_log")


def test_the_baseline_remembers_the_audited_value_not_the_current_one(tmp_path):
    """今の値を控えると、ずれを監査済みとして洗い流してしまう。"""
    checks, _, _ = _generated(tmp_path)
    remember = checks[checks.index("public int trgProductsAuditRemember()"):].split("return written;")[0]
    assert "LastAudited()" in remember and "Current()" not in remember


def test_the_baseline_table_ddl_is_generated(tmp_path):
    _, _, root = _generated(tmp_path)
    ddl = (root / "db" / "trigger-check-baseline.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS plsqlpoc.trigger_check_baseline" in ddl
    assert "PRIMARY KEY (trigger_name, key_value)" in ddl
