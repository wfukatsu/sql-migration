"""採番方式（2026-09-17 の決定）。

ScalarDB に順序オブジェクトは無い。決めたのは用途ごとに使い分けることで、**どちらに寄せるかは移行元の
DDL が既に宣言している**——`CACHE n` は「停止時に未使用の n 個を捨ててよい」、`NOCACHE` はその逆である。
ここで固定するのは、その読み取りと、DDL を覆すときに理由を要求することである。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plsql.gen_java.expr import translate
from plsql.lower import _walk
from plsql.report import analyse
from plsql.sequences import COUNTER, HILO, Policies

DDL = "fixtures/plsql/src/schema.sql"


@pytest.fixture(scope="module")
def policies():
    return Policies.from_ddl(DDL)


# ---------------------------------------------------------------- DDL から導く
def test_cache_becomes_hilo(policies):
    """`CACHE 100` は欠番を受け入れると書いてあるのと同じで、それは hi/lo が持ち込む性質である。"""
    for name in ("seq_audit_id", "seq_tx_id"):
        assert policies[name].scheme == HILO
        assert policies[name].block == 100


def test_nocache_becomes_counter(policies):
    """`NOCACHE` は欠番を避けたいという意思表示。"""
    for name in ("seq_order_id", "seq_payment_id"):
        assert policies[name].scheme == COUNTER
        assert policies[name].block == 1


def test_the_start_value_is_kept(policies):
    """既存の番号と衝突させないため。1 から始め直すのは移行ではなく破壊である。"""
    assert policies["seq_order_id"].start == 1000
    assert policies["seq_payment_id"].start == 5000


def test_every_policy_says_why(policies):
    for policy in policies.values():
        assert policy.reason and "DDL" in policy.reason


def test_the_counters_table_needs_one_row_per_sequence(policies):
    assert policies.seed_rows() == [("seq_audit_id", 1), ("seq_order_id", 1000),
                                    ("seq_payment_id", 5000), ("seq_tx_id", 1)]


# ---------------------------------------------------------------- 上書き
def test_an_override_needs_a_reason(tmp_path):
    """DDL が言っていることを覆すのだから、理由が要る。"""
    path = tmp_path / "sequences.yaml"
    path.write_text("sequences:\n  seq_tx_id: {scheme: counter}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reason が要る"):
        Policies.from_ddl(DDL, path)


def test_an_override_with_a_reason_applies(tmp_path):
    path = tmp_path / "sequences.yaml"
    path.write_text("sequences:\n  seq_tx_id: {scheme: counter, reason: 監査要件で欠番を許さない}\n",
                    encoding="utf-8")
    policies = Policies.from_ddl(DDL, path)
    assert policies["seq_tx_id"].scheme == COUNTER
    assert "上書き" in policies["seq_tx_id"].reason and "監査要件" in policies["seq_tx_id"].reason


def test_an_unknown_scheme_is_refused(tmp_path):
    path = tmp_path / "sequences.yaml"
    path.write_text("sequences:\n  seq_tx_id: {scheme: magic, reason: x}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="scheme は"):
        Policies.from_ddl(DDL, path)


# ---------------------------------------------------------------- 生成
def test_nextval_translates_to_a_sequences_call():
    rendered = translate("seq_audit_id.NEXTVAL", {})
    assert rendered.java == 'sequences.next("seq_audit_id")'
    assert rendered.sequences == {"seq_audit_id"}
    assert not rendered.unknown


def test_every_nextval_in_the_corpus_is_lifted():
    """持ち上げられなければ、変換器が `SEQUENCE` として拒否したままになる。"""
    program = analyse("fixtures/plsql/src", DDL, scalardb_schema="fixtures/plsql/scalardb-schema.json")
    lifted = [b.expression for _, routine in program.routines()
              for s in _walk(routine.body) + [x for h in routine.exception_handlers for x in _walk(h.body)]
              for b in (getattr(s, "binds", None) or []) if b.expression and "NEXTVAL" in b.expression.upper()]
    assert len(lifted) == 10, lifted   # 7 + the three audit INSERTs of trg_lines_audit (#29-25)


def test_no_statement_is_refused_for_using_a_sequence_any_more():
    program = analyse("fixtures/plsql/src", DDL, scalardb_schema="fixtures/plsql/scalardb-schema.json")
    codes = {d.code for _, routine in program.routines()
             for s in _walk(routine.body) + [x for h in routine.exception_handlers for x in _walk(h.body)]
             for d in (getattr(s, "diagnostics", None) or []) if d.severity == "ERROR"}
    assert "SEQUENCE" not in codes


def test_currval_is_not_lifted():
    """直前の NEXTVAL が同じセッションで何を返したかに依存しており、その依存は移行後に存在しない。"""
    rendered = translate("seq_audit_id.CURRVAL", {})
    assert "sequences.next" not in rendered.java


@pytest.mark.skipif(not Path("generated/src/main/java").is_dir(), reason="需要 generated tree")
def test_only_repositories_that_number_take_the_dependency():
    """採番しないクラスに依存を足すと、移行先は使わない実装まで用意させられる。"""
    infra = Path("generated/src/main/java/com/example/migrated/infrastructure")
    takes = {p.stem for p in infra.glob("*.java") if "Sequences sequences" in p.read_text(encoding="utf-8")}
    assert "PkgBulkLoadRepository" in takes
    assert "PkgCustomerCrudRepository" not in takes


# --- #27-26: one statement, one sequence -----------------------------------------------------------------
def test_each_create_sequence_is_read_on_its_own(tmp_path):
    """The options were matched with `.*?`, which ran across `;`: a sequence with no START WITH took the next
    statement's, and swallowed the next sequence whole -- three declared, one policy, with another's start."""
    from plsql.sequences import Policies

    ddl = tmp_path / "seq.sql"
    ddl.write_text("CREATE SEQUENCE seq_a;\n"
                   "-- CREATE SEQUENCE seq_commented START WITH 9;\n"
                   "CREATE SEQUENCE app.seq_b CACHE 50 INCREMENT BY 2 START WITH 500;\n"
                   "CREATE SEQUENCE seq_c NOCACHE\n  START WITH 7;\n"
                   "CREATE SEQUENCE seq_d START WITH -5 INCREMENT BY -1 CACHE 1;\n", encoding="utf-8")
    found = {n: (p.scheme, p.block, p.start, p.increment) for n, p in Policies.from_ddl(ddl).items()}
    assert found == {"seq_a": ("hilo", 20, 1, 1),          # no clause: Oracle's default is CACHE 20
                     "seq_b": ("hilo", 50, 500, 2),         # options in any order
                     "seq_c": ("counter", 1, 7, 1),
                     "seq_d": ("counter", 1, -5, -1)}
    assert "既定" in Policies.from_ddl(ddl)["seq_a"].reason, "a default is not a stated intent, and the reason says so"
