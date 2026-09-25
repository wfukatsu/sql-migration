"""#12: 移行先に trigger は無いので、**書き込む側が呼ぶ**。

決定は #12 §0（網羅性は塞がずに検証で追う）で、ここは実装である。固定するのは 3 つ:

* **掛かるべきところに掛かる**（同じ表・同じ操作・`UPDATE OF` の列）
* **掛からないところには掛からない**（列が違う / 1 行に絞れない / 値そのものを変える trigger）
* **掛からなかった書き込みに掛けない**（0 行の更新では 1 度も発火しない）

3 つめは実際に壊した: `:OLD` を無条件に読んだために、更新する行が無いときの `SQL%ROWCOUNT = 0` が
別の失敗（NO_DATA_FOUND）に化けた（`mark_shipped`）。比較が捕まえたので、ここに置いてある。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from plsql.limits import RowLocks
from plsql.lower import _walk
from plsql.report import analyse as build_analysis

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def corpus():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json",
                          row_locks=RowLocks.load(FIXTURES / "limits.yaml"))


def statements(corpus, routine_id):
    routine = next(r for _, r in corpus.routines() if r.id == routine_id)
    return routine, _walk(routine.body)


def codes(statement) -> list[str]:
    return [d.code for d in statement.diagnostics]


def test_the_registry_knows_when_each_trigger_fires(corpus):
    from plsql.triggers import registry

    found = registry(corpus.program)
    orders = {t.module.name: t for t in found["orders"]}
    assert orders["trg_orders_audit"].timing == "AFTER"
    assert orders["trg_orders_audit"].columns == ["status"], "`UPDATE OF status` の列が落ちている"
    assert found["payments"][0].module.name == "trg_payments_guard"


def test_a_write_to_the_table_calls_the_trigger(corpus):
    """`UPDATE orders SET status = ...` は `trg_orders_audit` が掛かる書き込みである。"""
    _, body = statements(corpus, "prc_nightly_close")
    call = next(s for s in body if s.kind == "Call")
    assert call.resolved_to == "trg_orders_audit.body"
    assert "TRIGGER_CALL" in codes(call)


def test_the_old_values_are_read_before_the_write(corpus):
    """`:OLD` は更新の**前に**読む。後では元の値が無い（trigger-patterns A-1）。"""
    _, body = statements(corpus, "pkg_shipment.mark_shipped")
    kinds = [(s.kind, (getattr(s, "sql_kind", "") or "")) for s in body
             if s.kind in ("SqlOperation", "Call")]
    read = next(i for i, (kind, sql) in enumerate(kinds) if sql == "SELECT")
    write = next(i for i, (kind, sql) in enumerate(kinds) if sql == "UPDATE")
    call = next(i for i, (kind, _) in enumerate(kinds) if kind == "Call")
    assert read < write < call, "AFTER trigger なのに、読み・書き・呼び出しの順になっていない"


def test_the_old_read_does_not_raise_when_there_is_no_row(corpus):
    """更新する行が無ければ trigger は掛からない。**「無い」は例外ではなく答えである**。

    無条件に `SELECT INTO` として読んだために、`mark_shipped` の「注文が無い」（`SQL%ROWCOUNT = 0`
    で -20071）が NO_DATA_FOUND に化けた。比較がそれを捕まえた。
    """
    _, body = statements(corpus, "pkg_shipment.mark_shipped")
    read = next(s for s in body if s.kind == "SqlOperation" and (s.sql_kind or "") == "SELECT")
    assert read.cardinality == "AT_MOST_ONE"
    assert read.not_found_flag, "行が無かったことを本体が知る手段が無い"


def test_the_call_only_happens_when_a_row_was_written(corpus):
    """AFTER は `SQL%ROWCOUNT`、BEFORE は `:OLD` が見つかったかで決まる。"""
    _, body = statements(corpus, "pkg_shipment.mark_shipped")
    guard = next(s for s in body if s.kind == "If" and any(c.kind == "Call" for c in s.branches[0].body))
    assert guard.branches[0].condition == "SQL%ROWCOUNT > 0"


def test_a_before_trigger_runs_before_the_write(corpus):
    """`trg_payments_guard` は値を拒否する trigger である。書いた後で拒否しても遅い。"""
    _, body = statements(corpus, "pkg_payment.record_payment")
    order = [s.kind for s in body if s.kind in ("Call", "SqlOperation")]
    call = order.index("Call")
    insert = [i for i, s in enumerate([s for s in body if s.kind in ("Call", "SqlOperation")])
              if getattr(s, "sql_kind", "") == "INSERT"][0]
    assert call < insert


def test_a_write_that_does_not_touch_the_listed_column_is_untouched(corpus):
    """`UPDATE OF status` は status を SET していない更新には掛からない。掛けると**記録が増える**。"""
    _, body = statements(corpus, "pkg_order_report.mark_reviewed")
    assert not [s for s in body if s.kind == "Call"], "note だけの更新に監査 trigger が掛かっている"


def test_a_write_to_another_table_is_untouched(corpus):
    """`UPDATE products SET stock_qty` に `trg_products_audit`（`UPDATE OF unit_price`）は掛からない。"""
    _, body = statements(corpus, "pkg_stock_reserve.reserve")
    assert not [s for s in body if s.kind == "Call"]


def test_a_trigger_that_rewrites_the_row_is_refused(corpus):
    """採番 trigger（`:NEW.order_id := seq.NEXTVAL`）は呼び出しでは置き換えられない。

    値を書き換える trigger は「書き込まれる行そのもの」を変える。移行先では呼び出し側が値を
    持っているので、これは採番 Service への**再設計**である（trigger-patterns C）。
    """
    from plsql.triggers import registry

    trigger = next(t for t in registry(corpus.program)["orders"]
                   if t.module.name == "trg_orders_seq")
    assert trigger.assigns_correlation()


def test_the_trigger_body_is_callable(corpus):
    """呼べない本体は、掛からない trigger と同じである。"""
    routine = next(r for _, r in corpus.routines() if r.id == "trg_orders_audit.body")
    assert routine.visibility == "public"


def test_the_arguments_are_named_so_the_two_sides_cannot_drift(corpus):
    """引数は名前で持つ。並びを決めるのは呼ばれる側の signature で、呼ぶ側がそれを決め打つと、
    片方が変わったときに静かにずれる。"""
    _, body = statements(corpus, "prc_nightly_close")
    call = next(s for s in body if s.kind == "Call")
    assert sorted(a.split("=>")[0].strip() for a in call.arguments) == \
        ["NEW.order_id", "NEW.status", "OLD.status"]
    assert "'CLOSED'" in " ".join(call.arguments), "書き込む値が :NEW として渡っていない"


def test_the_generated_service_receives_the_trigger_it_calls(corpus):
    """誰が呼んでいるかが constructor に出る。**掛かるのはこの経路だけ**という事実がそこに見える。"""
    from plsql.gen_java.service import generate_module as generate

    # 境界を割っていない module を使う（#24 の分割は `--limits` の決定が要る）
    module = next(m for m in corpus.program.modules if m.name == "pkg_shipment")
    java = generate(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    assert "private final TrgOrdersAuditService trgOrdersAudit;" in java
    assert "trgOrdersAudit.body(" in java
    assert "public PkgShipmentService(PkgShipmentRepository repository, "
    assert "TrgOrdersAuditService trgOrdersAudit)" in java


def test_the_write_path_inherits_the_triggers_verdict(corpus):
    """生成できることと、移してよいことは別である。trigger が REDESIGN なら、それを呼ぶ経路も
    その判断を引き継ぐ——網羅性（#12 §0）はその経路の設計だからである。"""
    from plsql.analysis import analyse as analyse_program
    from plsql.rules.engine import Evidence, RuleSet, decide

    decisions = decide(corpus.program, analyse_program(corpus.program), RuleSet.load(), Evidence())
    decision = decisions["pkg_shipment.mark_shipped"]
    assert decision.rule_verdict == "REDESIGN"
    assert any("trg_orders_audit" in reason for reason in decision.reasons)


# --- a trigger that fills in the key from a sequence ----------------------------------------------------------
SEQ_TRIGGER = """CREATE OR REPLACE TRIGGER trg_payments_seq
BEFORE INSERT ON payments
FOR EACH ROW
{when}BEGIN
  :NEW.payment_id := seq_payment_id.NEXTVAL;
END;
/
"""


def _insert_after_rewrite(tmp_path, statement: str, when: str = "WHEN (NEW.payment_id IS NULL)\n", body: str | None = None):
    from plsql.report import analyse

    trigger = SEQ_TRIGGER.format(when=when) if body is None else body
    (tmp_path / "trg.trg").write_text(trigger, encoding="utf-8")
    (tmp_path / "p.prc").write_text(
        f"CREATE OR REPLACE PROCEDURE p(p_order_id NUMBER, p_id NUMBER) IS\nBEGIN\n  {statement}\nEND;\n/\n", encoding="utf-8")
    program = analyse(str(tmp_path), "fixtures/plsql/src/schema.sql").program
    routine = next(r for m in program.modules for r in m.routines if r.id == "p")
    insert = next(s for s in _walk(routine.body) if s.kind == "SqlOperation")
    return insert, {d.code for d in insert.diagnostics}


def test_a_sequence_trigger_becomes_the_key_of_the_insert_it_fires_on(tmp_path):
    """`:NEW.id := seq.NEXTVAL` changes the row that is written, so it cannot be a call (trigger-patterns C). What it
    does is known exactly, though: the writer takes the number. The INSERT gets the column, and the NEXTVAL is then
    migrated like any other (the counters table or hi/lo the DDL asks for -- plan §9)."""
    insert, codes = _insert_after_rewrite(
        tmp_path, "INSERT INTO payments (order_id, amount, method) VALUES (p_order_id, 1, 'CARD');")
    assert "TRIGGER_INLINED" in codes and "TRIGGER_REDESIGN" not in codes
    assert re.search(r"\(order_id, amount, method, payment_id\)\s+VALUES\s+\(p_order_id, 1, 'CARD', seq_payment_id\.NEXTVAL\)",
                     insert.original_sql, re.IGNORECASE), insert.original_sql


def test_the_when_clause_decides_whether_a_given_key_survives(tmp_path):
    # WHEN (NEW.id IS NULL): a key that is written is kept, a NULL one is replaced
    insert, codes = _insert_after_rewrite(
        tmp_path, "INSERT INTO payments (payment_id, order_id, amount, method) VALUES (7, p_order_id, 1, 'CARD');")
    assert "VALUES (7," in insert.original_sql and "TRIGGER_INLINED" not in codes and "TRIGGER_REDESIGN" not in codes
    insert, _ = _insert_after_rewrite(
        tmp_path, "INSERT INTO payments (payment_id, order_id, amount, method) VALUES (NULL, p_order_id, 1, 'CARD');")
    assert "VALUES (seq_payment_id.NEXTVAL," in insert.original_sql
    # no WHEN: the trigger overwrites whatever was given
    insert, _ = _insert_after_rewrite(
        tmp_path, "INSERT INTO payments (payment_id, order_id, amount, method) VALUES (7, p_order_id, 1, 'CARD');", when="")
    assert "VALUES (seq_payment_id.NEXTVAL," in insert.original_sql


def test_what_cannot_be_decided_statically_is_still_a_redesign(tmp_path):
    # a variable may or may not be NULL at run time, and NVL(p_id, seq.NEXTVAL) would burn a number either way
    _, codes = _insert_after_rewrite(
        tmp_path, "INSERT INTO payments (payment_id, order_id, amount, method) VALUES (p_id, p_order_id, 1, 'CARD');")
    assert "TRIGGER_REDESIGN" in codes
    # a body that does more than take a number is not this shape
    body = SEQ_TRIGGER.format(when="").replace("  :NEW.payment_id := seq_payment_id.NEXTVAL;",
                                                "  :NEW.payment_id := seq_payment_id.NEXTVAL;\n  :NEW.method := UPPER(:NEW.method);")
    _, codes = _insert_after_rewrite(
        tmp_path, "INSERT INTO payments (order_id, amount, method) VALUES (p_order_id, 1, 'card');", body=body)
    assert "TRIGGER_REDESIGN" in codes


# --- #47: a BEFORE trigger that rewrites :NEW is folded into the written values ------------------------------
FOLD_TRIGGER = """CREATE OR REPLACE TRIGGER trg_payments_norm
BEFORE INSERT OR UPDATE OF amount, method ON payments
FOR EACH ROW
BEGIN
  :NEW.method := UPPER(:NEW.method);
  IF :NEW.amount < 0 THEN
    RAISE_APPLICATION_ERROR(-20030, 'negative');
  END IF;
END;
/
"""


def _after_rewrite(tmp_path, statement: str, body: str):
    from plsql.report import analyse

    (tmp_path / "trg.trg").write_text(body, encoding="utf-8")
    (tmp_path / "p.prc").write_text(
        f"CREATE OR REPLACE PROCEDURE p(p_order_id NUMBER, p_id NUMBER, p_method VARCHAR2) IS\nBEGIN\n  {statement}\nEND;\n/\n",
        encoding="utf-8")
    analysis = analyse(str(tmp_path), "fixtures/plsql/src/schema.sql")
    routine = next(r for m in analysis.program.modules for r in m.routines if r.id == "p")
    return analysis, routine


def test_an_assignment_to_new_is_folded_into_an_insert(tmp_path):
    _, routine = _after_rewrite(
        tmp_path, "INSERT INTO payments (payment_id, order_id, amount, method) VALUES (p_id, p_order_id, 1, p_method);",
        FOLD_TRIGGER)
    insert = next(s for s in _walk(routine.body) if s.kind == "SqlOperation" and s.sql_kind == "INSERT")
    assert "UPPER(p_method)" in insert.original_sql, insert.original_sql
    assert {"TRIGGER_FOLDED", "TRIGGER_APPLIED"} <= {d.code for d in insert.diagnostics}
    assert "TRIGGER_REDESIGN" not in {d.code for d in insert.diagnostics}
    call = next(s for s in _walk(routine.body) if s.kind == "Call")
    assert "NEW.method => p_method" in call.arguments, "the body still runs its checks on the values as given"


def test_an_assignment_to_a_column_the_update_does_not_set_reads_it_first(tmp_path):
    _, routine = _after_rewrite(tmp_path, "UPDATE payments SET amount = 1 WHERE payment_id = p_id;", FOLD_TRIGGER)
    update = next(s for s in _walk(routine.body) if s.kind == "SqlOperation" and s.sql_kind == "UPDATE")
    read = next(s for s in _walk(routine.body) if s.kind == "SqlOperation" and s.sql_kind == "SELECT")
    variable = read.into_targets[read.original_sql.upper().index("METHOD") > -1 and
                                 [c.strip().lower() for c in read.original_sql[7:read.original_sql.upper().index(" FROM")].split(",")].index("method")]
    assert f"method = UPPER({variable})" in update.original_sql, update.original_sql


def test_the_body_reassigns_its_own_argument(tmp_path):
    from plsql.gen_java.service import generate_module

    analysis, _ = _after_rewrite(
        tmp_path, "INSERT INTO payments (payment_id, order_id, amount, method) VALUES (p_id, p_order_id, 1, p_method);",
        FOLD_TRIGGER)
    trigger = next(m for m in analysis.program.modules if m.module_kind == "trigger")
    java = generate_module(trigger, "g.app", "g.infra", "g.domain", program=analysis.program).file.render()
    assert "newMethod = Plsql.upper(newMethod);" in java, java
    assert "UnsupportedOperationException" not in java


@pytest.mark.parametrize("assignment", [
    "IF :NEW.amount > 0 THEN :NEW.method := UPPER(:NEW.method); END IF;",     # conditional: the writer cannot fold it
    "v := 'X'; :NEW.method := v;",                                            # reads a local the writer does not have
])
def test_an_assignment_the_writer_cannot_fold_stays_a_redesign(tmp_path, assignment):
    body = FOLD_TRIGGER.replace("BEGIN\n  :NEW.method := UPPER(:NEW.method);", f"DECLARE\n  v VARCHAR2(10);\nBEGIN\n  {assignment}")
    _, routine = _after_rewrite(
        tmp_path, "INSERT INTO payments (payment_id, order_id, amount, method) VALUES (p_id, p_order_id, 1, p_method);", body)
    insert = next(s for s in _walk(routine.body) if s.kind == "SqlOperation" and s.sql_kind == "INSERT")
    assert "TRIGGER_REDESIGN" in {d.code for d in insert.diagnostics}
