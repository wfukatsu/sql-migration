"""#1 / #8: `USER` and `SYSTIMESTAMP` come from the caller.

Neither has an equivalent on the target. `USER` is the database session's user, and `SYSTIMESTAMP` is a clock
the comparison harness cannot pin on the Oracle side (`semantics.json`: `fixedDatePinsSystimestamp` is false),
so a column written from it is masked and never compared. Both become one argument the caller passes.

The tests here fix what that means: the value is a parameter rather than something the generated code reaches
for, `SYSDATE` is deliberately left alone, and the trigger correlation names are still refused -- lifting one
of those out of the SQL would replace a clear conversion error with Java that does not compile.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from plsql.gen_java.repository import generate_module as generate_repository, needs_audit as statement_audit
from plsql.gen_java.service import generate_module as generate_service, needs_audit as routine_audit
from plsql.lower import _walk
from plsql.report import analyse as build_analysis

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
SCALARDB = FIXTURES / "scalardb-schema.json"


@pytest.fixture(scope="module")
def corpus():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=SCALARDB)


def routine(corpus, routine_id: str):
    return next(r for _, r in corpus.routines() if r.id == routine_id)


def statements(routine):
    return _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]


# --- the analysis --------------------------------------------------------------------------------------

def test_user_is_lifted_out_of_the_sql_instead_of_being_refused(corpus):
    """`VALUES (..., USER)` was an `EXPR` error: ScalarDB takes literals and bind markers and nothing else."""
    insert = next(s for s in statements(routine(corpus, "prc_audit_autonomous"))
                  if s.kind == "SqlOperation")
    assert "USER" in [b.expression for b in insert.binds if b.expression]
    assert insert.target_status in ("OK", "WARN")
    assert not [d for d in insert.diagnostics if d.severity == "ERROR"]


def test_no_user_is_left_as_a_conversion_error_in_the_corpus(corpus):
    messages = [d.message for _, r in corpus.routines() for s in statements(r)
                for d in getattr(s, "diagnostics", []) if d.severity == "ERROR"]
    assert not [m for m in messages if "'USER'" in m], messages


def test_a_trigger_correlation_name_is_a_value_the_caller_supplies(corpus):
    """`:NEW.status` は trigger が発火した行で、移行先に trigger は無い。

    かつてここは「どこから来るのかは #12」として**拒んでいた**。#12 が答えを決めた——`USER` と
    同じく**呼び出し側が渡す**（#1）——ので、拒む理由が無くなった。渡される値なので bind になり、
    文は変換できる。
    """
    insert = next(s for s in statements(routine(corpus, "trg_orders_audit.body"))
                  if s.kind == "SqlOperation")
    supplied = {b.plsql_variable for b in insert.binds if b.plsql_variable}
    assert {"NEW.order_id", "NEW.status", "OLD.status"} <= supplied
    assert insert.target_status in ("OK", "WARN")
    assert not [d for d in insert.diagnostics if d.severity == "ERROR"]


def test_sysdate_is_left_alone(corpus):
    """`ALTER SYSTEM SET FIXED_DATE` pins it, so it is already comparable; moving it buys an argument."""
    update = next(s for s in statements(routine(corpus, "prc_nightly_close"))
                  if s.kind == "SqlOperation" and "batch_control" in (s.write_set or []))
    assert "SYSDATE" in [b.expression for b in update.binds if b.expression]
    assert not statement_audit(update), "SYSDATE alone does not make the caller supply a context"


# --- the generated Java --------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def generated(corpus):
    module = next(m for m in corpus.program.modules if m.name == "prc_audit_autonomous")
    return (generate_service(module, "g.app", "g.infra", "g.domain").file.render(),
            generate_repository(module, "g.infra", "g.domain").file.render())


def test_the_service_takes_the_context_as_an_argument(generated):
    service, _ = generated
    assert "AuditContext audit) throws Exception {" in service
    assert "import com.scalar.migrate.plsql.AuditContext;" in service


def test_the_repository_reads_the_values_off_that_argument(generated):
    """Not off an ambient value: what the caller passed is what a test and an audit can see."""
    _, repository = generated
    assert "prcAuditAutonomousStmt1(AuditContext audit," in repository
    assert "audit.user()" in repository and "audit.now()" in repository
    assert "Plsql.systimestamp()" not in repository


def test_the_context_is_passed_down_from_the_service(corpus):
    """`prc_audit_autonomous` では見られなくなった——#25 で「完走できない routine は採番の前で
    止める」と決めたので、その文へ到達しない。値が repository まで渡ることは、完走する routine
    （`pkg_payment.record_payment`）で確かめる。"""
    module = next(m for m in corpus.program.modules if m.name == "pkg_payment")
    # trigger を呼ぶ module なので program も渡す（#12）——呼ばれる側の signature が引数の並びを決める
    service = generate_service(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    assert "repository.recordPaymentStmt4(audit," in service


def test_a_routine_that_needs_nothing_from_the_caller_keeps_its_signature(corpus):
    """A dependency nobody uses would make every caller supply a value the routine never reads."""
    module = next(m for m in corpus.program.modules if m.name == "pkg_order_pricing")
    java = generate_service(module, "g.app", "g.infra", "g.domain").file.render()
    assert "AuditContext" not in java
    assert not any(routine_audit(r) for r in module.routines)


# --- what the signature has to match ---------------------------------------------------------------------

AD_HOC = {
    "USER in a RAISE message": ("""\
        CREATE OR REPLACE PROCEDURE prc_raise(p_id IN NUMBER) IS
        BEGIN
          IF p_id IS NULL THEN
            RAISE_APPLICATION_ERROR(-20001, 'rejected by ' || USER);
          END IF;
        END prc_raise;
        /
    """, True),
    "the string literal 'USER'": ("""\
        CREATE OR REPLACE PROCEDURE prc_literal(p_id IN NUMBER) IS
          v_kind VARCHAR2(20);
        BEGIN
          v_kind := 'USER';
          UPDATE orders SET note = v_kind WHERE order_id = p_id;
        END prc_literal;
        /
    """, False),
}


@pytest.mark.parametrize("what", sorted(AD_HOC))
def test_the_signature_provides_exactly_what_the_body_reads(tmp_path_factory, what):
    """MR !53: both halves of the same defect. The message was a place `needs_audit` did not look, so the body
    read an `audit` the signature did not declare; the literal was a place it looked too hard, so the
    signature declared an `audit` the body never read."""
    source, wants_audit = AD_HOC[what]
    root = tmp_path_factory.mktemp("audit")
    (root / "schema.sql").write_text((SRC / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
    name = source.split("PROCEDURE ")[1].split("(")[0].split()[0]
    (root / f"{name}.prc").write_text(textwrap.dedent(source), encoding="utf-8")
    analysis = build_analysis(root, root / "schema.sql", scalardb_schema=SCALARDB)
    module = next(m for m in analysis.program.modules if m.name == name)
    java = generate_service(module, "g.app", "g.infra", "g.domain").file.render()
    assert routine_audit(module.routines[0]) is wants_audit
    assert ("AuditContext audit" in java) is wants_audit, java
    assert ("audit." in java) is wants_audit, java
