"""P1-4: the Migration IR model, its schema and the round trip.

The IR is a contract that outlives a single run: it is written to disk, read by other stages, and read back by a
later build. These tests defend the parts of that contract a refactor can quietly break -- the schema matching the
dataclasses, the round trip being exact, and a document from an incompatible version being refused rather than
half-understood.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from plsql.ir import _schema_gen, model as M, serde
from plsql.source import Issue, SourceRange


def sample_program() -> M.Program:
    program = M.Program(id="run-1", kind="Program", schema_snapshot="ddl@2026-09-17")
    module = M.Module(id="pkg_order", kind="Module", name="pkg_order", module_kind="package",
                      source_range=SourceRange("pkg_order.pkb", 1, 60))
    routine = M.Routine(
        id="pkg_order.create_order", kind="Routine", name="create_order", routine_kind="procedure",
        source_range=SourceRange("pkg_order.pkb", 3, 40), confidence=0.75,
        parameters=[M.Parameter(id="pkg_order.create_order#param-1", kind="Parameter", name="p_id",
                                direction="IN", type=M.TypeRef("NUMBER(19)", "NUMBER(19)"))],
        declarations=[M.Declaration(id="pkg_order.create_order#decl-1", kind="Declaration", name="v_status",
                                    type=M.TypeRef("orders.status%TYPE", "VARCHAR2(20)", "column-type",
                                                   "ddl@2026-09-17"))])
    routine.body.append(M.SqlOperation(
        id="pkg_order.create_order#stmt-1", kind="SqlOperation", sql_kind="SELECT",
        original_sql="SELECT status FROM orders WHERE order_id = :p_id", cardinality="EXACTLY_ONE",
        binds=[M.BindVariable("p_id", "IN", "NUMBER(19)", "p_id")], into_targets=["v_status"],
        read_set=["orders"], target_status="OK", target_sql=["SELECT status FROM orders WHERE order_id = ?"]))
    routine.body.append(M.If(
        id="pkg_order.create_order#stmt-2", kind="If",
        branches=[M.Branch("v_status IS NULL", [M.Raise(id="pkg_order.create_order#stmt-3", kind="Raise",
                                                        error_code=-20001, message="no status")])]))
    routine.body.append(M.TransactionStatement(id="pkg_order.create_order#stmt-4", kind="Commit"))
    routine.transaction_effects = M.TransactionEffects(commits=1)
    routine.exception_handlers.append(M.ExceptionHandler(
        id="pkg_order.create_order#handler-1", kind="ExceptionHandler", exceptions=["NO_DATA_FOUND"],
        body=[M.Assignment(id="pkg_order.create_order#stmt-5", kind="Assignment",
                           target="p_status", expression="'UNKNOWN'")]))
    module.routines.append(routine)
    program.modules.append(module)
    return program


# --- every node carries the five required fields ----------------------------------------------------

@pytest.mark.parametrize("cls", _schema_gen.NODE_CLASSES, ids=lambda c: c.__name__)
def test_every_node_type_has_the_required_fields(cls: type):
    """設計書 §5.1: id / sourceRange / type / confidence / diagnostics をすべてのノードが持つ。"""
    names = {f.name for f in cls.__dataclass_fields__.values()}
    assert {"id", "kind", "source_range", "type", "confidence", "diagnostics"} <= names


def test_a_node_can_carry_a_diagnostic_pointing_at_its_own_line():
    node = M.Assignment(id="a#stmt-1", kind="Assignment", source_range=SourceRange("a.pkb", 7, 7))
    node.add("WARN", "X", "something")
    assert node.diagnostics[0].range.start_line == 7


# --- schema -----------------------------------------------------------------------------------------

def test_the_committed_schema_matches_the_model():
    """A field added to a dataclass without regenerating the schema would travel untyped."""
    assert _schema_gen.build() == serde.schema(), "run: python -m plsql.ir._schema_gen"


def test_the_schema_itself_is_valid():
    jsonschema.Draft7Validator.check_schema(serde.schema())


def test_a_realistic_program_validates():
    serde.validate(sample_program())


def test_an_unknown_field_is_rejected_by_the_schema():
    data = serde.to_dict(sample_program())
    data["modules"][0]["surpriseField"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, serde.schema())


def test_confidence_outside_zero_to_one_is_rejected():
    program = sample_program()
    program.modules[0].routines[0].confidence = 1.5
    with pytest.raises(jsonschema.ValidationError):
        serde.validate(program)


def test_an_unknown_severity_is_rejected():
    program = sample_program()
    program.modules[0].diagnostics.append(Issue("CRITICAL", "X", "no such severity"))
    with pytest.raises(jsonschema.ValidationError):
        serde.validate(program)


# --- round trip -------------------------------------------------------------------------------------

def test_the_round_trip_is_exact():
    program = sample_program()
    assert serde.loads(serde.dumps(program)) == program


def test_the_round_trip_keeps_oracle_meaning():
    """COMMIT and %TYPE must survive as themselves; erasing them is what the design document forbids."""
    back = serde.loads(serde.dumps(sample_program()))
    routine = back.modules[0].routines[0]
    assert [s.kind for s in routine.body] == ["SqlOperation", "If", "Commit"]
    assert routine.transaction_effects.commits == 1
    declaration = routine.declarations[0]
    assert declaration.type.oracle == "orders.status%TYPE"
    assert declaration.type.origin == "column-type"
    assert declaration.type.schema_snapshot == "ddl@2026-09-17"


def test_nested_statements_survive_the_round_trip():
    back = serde.loads(serde.dumps(sample_program()))
    branch = back.modules[0].routines[0].body[1].branches[0]
    assert branch.condition == "v_status IS NULL"
    assert branch.body[0].error_code == -20001


def test_exception_handlers_survive_the_round_trip():
    handler = serde.loads(serde.dumps(sample_program())).modules[0].routines[0].exception_handlers[0]
    assert handler.exceptions == ["NO_DATA_FOUND"]
    assert handler.body[0].target == "p_status"


# --- versioning -------------------------------------------------------------------------------------

def test_a_document_without_a_version_is_refused():
    data = serde.to_dict(sample_program())
    data.pop("schemaVersion")
    with pytest.raises(ValueError, match="schemaVersion"):
        serde.loads(json.dumps(data))


def test_a_document_from_another_major_version_is_refused():
    data = serde.to_dict(sample_program())
    data["schemaVersion"] = "2.0.0"
    with pytest.raises(ValueError, match="major versions differ"):
        serde.loads(json.dumps(data))


def test_a_newer_minor_version_is_still_readable():
    """Minor versions add fields; a reader drops what it does not know rather than refusing the document."""
    data = serde.to_dict(sample_program())
    data["schemaVersion"] = "1.9.0"
    data["modules"][0]["fieldFromTheFuture"] = "x"
    assert serde.loads(json.dumps(data)).modules[0].name == "pkg_order"


def test_an_unknown_node_kind_is_refused_loudly():
    data = serde.to_dict(sample_program())
    data["modules"][0]["routines"][0]["body"][0]["kind"] = "Teleport"
    with pytest.raises(ValueError, match="unknown IR node kind"):
        serde.loads(json.dumps(data))


# --- identifiers --------------------------------------------------------------------------------------

def test_ids_are_deterministic_and_scoped():
    first = M.IdFactory("pkg_order.create_order")
    second = M.IdFactory("pkg_order.create_order")
    assert [first.next() for _ in range(3)] == [second.next() for _ in range(3)]
    assert first.next("decl") == "pkg_order.create_order#decl-1", "counters are per prefix, not global"


def test_child_scopes_do_not_collide():
    root = M.IdFactory("pkg_order")
    a, b = root.child("create_order"), root.child("cancel_order")
    assert a.next() != b.next()
    assert a.scope == "pkg_order.create_order"


# --- small behaviours the rules will depend on ---------------------------------------------------------

def test_a_package_with_state_says_so():
    module = M.Module(id="m", kind="Module", name="pkg", module_kind="package",
                      declarations=[M.Declaration(id="d", kind="Declaration", name="g_count",
                                                  declaration_kind="variable")])
    assert module.has_package_state


def test_a_package_of_only_types_has_no_state():
    module = M.Module(id="m", kind="Module", name="pkg", module_kind="package",
                      declarations=[M.Declaration(id="d", kind="Declaration", name="t_ids",
                                                  declaration_kind="type")])
    assert not module.has_package_state


def test_transaction_effects_report_control():
    assert not M.TransactionEffects().controls_transaction
    assert M.TransactionEffects(commits=1).controls_transaction
    assert M.TransactionEffects(autonomous=True).controls_transaction


def test_an_unresolved_type_says_it_is_unresolved():
    assert not M.TypeRef("orders.status%TYPE", origin="unresolved").is_resolved()
    assert M.TypeRef("NUMBER", "NUMBER").is_resolved()
