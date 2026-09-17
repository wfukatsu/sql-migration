"""#18: a nested `BEGIN ... EXCEPTION ... END` stays where it was written.

Before this the handlers of a nested block were hoisted onto the routine and the block itself disappeared.
Three things went with it, and each has a test here: which loop iteration a statement in the handler belonged
to, which block a `ROLLBACK TO savepoint` unwound, and how far the handler actually reached. The generator
could only notice the third, and only when two hoisted handlers happened to catch the same Java type.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from plsql.analysis import build_cfg
from plsql.gen_java.service import generate_module
from plsql.ir import model as M, serde
from plsql.lower import _walk, lower_source, walk_scoped
from plsql.report import analyse as build_analysis
from plsql.symbols import OracleSchema

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
SCALARDB = FIXTURES / "scalardb-schema.json"


@pytest.fixture(scope="module")
def schema():
    return OracleSchema.from_ddl(SRC / "schema.sql")


@pytest.fixture(scope="module")
def nightly(schema):
    modules, _ = lower_source(SRC / "prc_nightly_close.prc", schema)
    return modules[0].routines[0]


def blocks(routine) -> list[M.Block]:
    return [s for s in _walk(routine.body) if s.kind == "Block"]


# --- the node ------------------------------------------------------------------------------------------

def test_a_nested_block_is_a_statement_inside_the_loop_that_holds_it(nightly):
    loop = next(s for s in nightly.body if s.kind == "Loop")
    assert [s.kind for s in loop.body] == ["Savepoint", "Block"]


def test_the_routine_keeps_only_its_own_handler(nightly):
    """One `EXCEPTION` is written on the routine; the other belongs to the block inside the loop."""
    assert len(nightly.exception_handlers) == 1
    assert nightly.exception_handlers[0].source_range.start_line == 34   # the routine's, at the end
    block = blocks(nightly)[0]
    assert [h.exceptions for h in block.exception_handlers] == [["OTHERS"]]
    assert block.exception_handlers[0].source_range.start_line == 22     # the one inside the loop


def test_the_savepoint_and_its_rollback_are_on_the_same_side_of_the_block(nightly):
    """`ROLLBACK TO sp_order` unwinds to a savepoint taken in the same loop iteration (#3 reads this)."""
    loop = next(s for s in nightly.body if s.kind == "Loop")
    savepoint = loop.body[0]
    rollback = blocks(nightly)[0].exception_handlers[0].body[0]
    assert savepoint.kind == "Savepoint" and rollback.kind == "Rollback"
    assert rollback in _walk(loop.body), "the rollback is inside the loop, not at routine level"


def test_a_block_with_its_own_declare_keeps_them(tmp_path, schema):
    source = tmp_path / "p.prc"
    source.write_text(textwrap.dedent("""\
        CREATE OR REPLACE PROCEDURE p IS
        BEGIN
          DECLARE
            v_local NUMBER;
          BEGIN
            v_local := 1;
          END;
        END p;
        /
    """), encoding="utf-8")
    modules, _ = lower_source(source, schema)
    block = blocks(modules[0].routines[0])[0]
    assert [d.name for d in block.declarations] == ["v_local"]
    assert [s.kind for s in block.body] == ["Assignment"]


def test_a_trigger_body_is_not_a_nested_block(schema):
    """The trigger's own `BEGIN ... END` is the routine's body. Reading it as a block made every trigger
    one statement."""
    modules, _ = lower_source(SRC / "trg_orders_audit.trg", schema)
    body = modules[0].routines[0].body
    assert [s.kind for s in body] == ["SqlOperation"]


# --- what it fixes -------------------------------------------------------------------------------------

def test_a_statement_in_the_blocks_handler_keeps_the_enclosing_loops_scope(nightly):
    """This is the `r.order_id` #10 could not reach while the handler was hoisted onto the routine."""
    scoped = {s.source_range.start_line: sorted(loops)
              for s, loops in walk_scoped(nightly.body) if s.source_range}
    assert scoped[26] == ["r"], "the INSERT in the handler is inside the cursor FOR loop"


def test_the_whole_corpus_has_no_loop_row_reference_left_as_a_column():
    corpus = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=SCALARDB)
    codes = [d.code for _, r in corpus.routines()
             for s in _walk(r.body) + [x for h in r.exception_handlers for x in _walk(h.body)]
             for d in getattr(s, "diagnostics", [])]
    assert "COL_COL" not in codes


# --- the control flow ----------------------------------------------------------------------------------

def test_a_blocks_handler_is_reachable_from_the_blocks_body_only(nightly):
    """Any statement in the block can raise, so the handler follows it -- and nothing outside the block."""
    cfg = build_cfg(nightly)
    block = blocks(nightly)[0]
    handler_first = block.exception_handlers[0].body[0].id
    sources = {a for a, b in cfg.edges if b == handler_first}
    inside = {s.id for s in _walk(block.body)} | {block.id}
    assert sources and sources <= inside, sorted(sources - inside)


def test_every_statement_in_the_corpus_stays_reachable():
    """A block the control-flow builder does not know about would make its whole body look like dead code."""
    from plsql.analysis import analyse as analyse_program

    corpus = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=SCALARDB)
    assert analyse_program(corpus.program).unreachable() == []


# --- the Java ------------------------------------------------------------------------------------------

BLOCK_SOURCE = """\
CREATE OR REPLACE PROCEDURE prc_block(p_order_id IN NUMBER) IS
  v_note orders.note%TYPE;
BEGIN
  BEGIN
    SELECT note INTO v_note FROM orders WHERE order_id = p_order_id;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      v_note := 'none';
  END;
  DECLARE
    v_copy VARCHAR2(200);
  BEGIN
    v_copy := v_note;
    UPDATE orders SET note = v_copy WHERE order_id = p_order_id;
  END;
END prc_block;
/
"""


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("blocks")
    (root / "schema.sql").write_text((SRC / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "prc_block.prc").write_text(BLOCK_SOURCE, encoding="utf-8")
    analysis = build_analysis(root, root / "schema.sql", scalardb_schema=SCALARDB)
    module = next(m for m in analysis.program.modules if m.name == "prc_block")
    return generate_module(module, "g.app", "g.infra", "g.domain")


def test_a_block_with_handlers_becomes_a_try_that_reaches_only_that_far(generated):
    java = generated.file.render()
    assert "catch (NoDataFoundException e) {" in java
    assert not generated.untranslated
    # the SELECT is inside the try; the UPDATE is after the catch, so the handler does not cover it
    catch = java.index("catch (NoDataFoundException e) {")
    assert java.index("repository.prcBlockStmt") < catch < java.rindex("repository.prcBlockStmt")


def test_a_block_without_handlers_is_still_a_block(generated):
    """Its `DECLARE` scopes the name the way PL/SQL does, so the block has to survive into the Java."""
    java = generated.file.render()
    assert "\n        {\n" in java, java
    assert "String vCopy = null;" in java
    assert "vCopy = vNote;" in java


def test_the_ir_round_trips_through_the_serialiser(nightly):
    program = M.Program(id="p", kind="Program", modules=[
        M.Module(id="m", kind="Module", name="m", routines=[nightly])])
    again = serde.loads(serde.dumps(program))
    block = blocks(again.modules[0].routines[0])[0]
    assert block.kind == "Block" and len(block.exception_handlers) == 1
