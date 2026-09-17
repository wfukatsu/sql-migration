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


def test_a_trigger_correlation_name_is_still_not_lifted(corpus):
    """`:NEW.status` is the trigger's row and the target has no trigger; where it comes from is #12.

    It used to be held back by `USER` in the same statement. Lifting it now would produce Java that does not
    compile -- which is the one outcome `lift_expressions` exists to avoid.
    """
    insert = next(s for s in statements(routine(corpus, "trg_orders_audit.body"))
                  if s.kind == "SqlOperation")
    assert not [b for b in insert.binds if b.expression and ":NEW" in b.expression.upper()]
    assert not [b for b in insert.binds if b.expression and ":OLD" in b.expression.upper()]
    assert [d.code for d in insert.diagnostics if d.severity == "ERROR"] == ["EXPR"]


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


def test_the_context_is_passed_down_from_the_service(generated):
    service, _ = generated
    assert "repository.prcAuditAutonomousStmt1(audit," in service


def test_a_routine_that_needs_nothing_from_the_caller_keeps_its_signature(corpus):
    """A dependency nobody uses would make every caller supply a value the routine never reads."""
    module = next(m for m in corpus.program.modules if m.name == "pkg_order_pricing")
    java = generate_service(module, "g.app", "g.infra", "g.domain").file.render()
    assert "AuditContext" not in java
    assert not any(routine_audit(r) for r in module.routines)
