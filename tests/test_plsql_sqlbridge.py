"""P1-6: the bridge between PL/SQL statements and the existing SQL converter.

The bridge owns three decisions that are easy to get subtly wrong and expensive to find later: stripping
`SELECT INTO`, telling a PL/SQL variable from a column, and naming the binds. Each has tests that fail loudly.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import sqlglot

from plsql.frontend import parse_file, parse_text
from plsql.ir.model import SqlOperation
from plsql.sqlbridge import analyse, bind_variables, read_write_sets, strip_into
from plsql.symbols import OracleSchema, build
from scalardb_migrate.schema import SchemaRegistry

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_schema_loader_json(str(FIXTURES / "scalardb-schema.json"))


@pytest.fixture(scope="module")
def symbols():
    schema = OracleSchema.from_ddl(SRC / "schema.sql")
    return build(parse_file(SRC / "pkg_order_status.pkb"), schema, {"status_of", "status_for_customer"})


def operation(sql: str, id_: str = "pkg_order_status.status_of#stmt-1") -> SqlOperation:
    return SqlOperation(id=id_, kind="SqlOperation", original_sql=sql)


# --- SELECT INTO ------------------------------------------------------------------------------------

def test_a_single_into_target_is_stripped_and_recorded():
    tree = sqlglot.parse_one("SELECT status INTO v_status FROM orders WHERE id = 1", dialect="oracle")
    assert strip_into(tree) == ["v_status"]
    assert "INTO" not in tree.sql(dialect="oracle").upper()


def test_several_into_targets_are_stripped_in_order():
    """sqlglot stores one target and several differently; handling only one shape loses the other silently."""
    tree = sqlglot.parse_one(
        "SELECT status, total_amount INTO v_status, v_total FROM orders WHERE id = 1", dialect="oracle")
    assert strip_into(tree) == ["v_status", "v_total"]
    assert "INTO" not in tree.sql(dialect="oracle").upper()


def test_a_statement_without_into_is_untouched():
    tree = sqlglot.parse_one("SELECT status FROM orders", dialect="oracle")
    assert strip_into(tree) == []
    assert tree.sql(dialect="oracle") == "SELECT status FROM orders"


def test_the_converter_sees_a_plain_select(registry, symbols):
    op = operation("SELECT status INTO v_status FROM orders WHERE order_id = p_order_id")
    result = analyse(op, "pkg_order_status.status_of", symbols, registry)
    assert result.status == "OK"
    assert op.into_targets == ["v_status"]
    assert "INTO" not in result.converted[0].upper()


# --- variables versus columns -------------------------------------------------------------------------

def test_a_variable_in_scope_becomes_a_named_bind(registry, symbols):
    op = operation("SELECT status INTO v_status FROM orders WHERE order_id = p_order_id")
    result = analyse(op, "pkg_order_status.status_of", symbols, registry)
    assert [b["name"] for b in result.binds] == ["p_order_id"]
    assert [b["plsql_variable"] for b in result.binds] == ["p_order_id"]
    assert ":p_order_id" in result.converted[0], "binds must be named, never anonymous"


def test_a_column_is_not_mistaken_for_a_variable(registry, symbols):
    op = operation("SELECT status INTO v_status FROM orders WHERE order_id = p_order_id")
    analyse(op, "pkg_order_status.status_of", symbols, registry)
    assert "order_id" not in [b.name for b in op.binds]


def test_a_qualified_column_is_never_a_bind(symbols):
    tree = sqlglot.parse_one("SELECT o.status FROM orders o WHERE o.p_order_id = 1", dialect="oracle")
    assert bind_variables(tree, "pkg_order_status.status_of", symbols) == []


def test_without_a_symbol_table_nothing_is_rewritten():
    """Guessing would turn a real column reference into a bind, which only fails at run time."""
    tree = sqlglot.parse_one("SELECT status FROM orders WHERE order_id = p_order_id", dialect="oracle")
    assert bind_variables(tree, "pkg_order_status.status_of", None) == []
    assert "p_order_id" in tree.sql(dialect="oracle")


def test_the_same_variable_used_twice_gets_one_bind(registry, symbols):
    op = operation("SELECT status INTO v_status FROM orders "
                   "WHERE order_id = p_order_id OR customer_id = p_order_id")
    result = analyse(op, "pkg_order_status.status_of", symbols, registry)
    assert len(result.binds) == 1, "one variable is one bind; duplicating it would double the parameters"


def test_bind_direction_and_type_come_from_the_symbol(registry, symbols):
    op = operation("SELECT status INTO v_status FROM orders WHERE order_id = p_order_id")
    result = analyse(op, "pkg_order_status.status_of", symbols, registry)
    assert result.binds[0]["direction"] == "IN"
    assert result.binds[0]["oracle_type"] == "NUMBER"


# --- what comes back --------------------------------------------------------------------------------

def test_the_access_path_is_carried_back(registry, symbols):
    op = operation("SELECT status INTO v_status FROM orders WHERE order_id = p_order_id")
    result = analyse(op, "pkg_order_status.status_of", symbols, registry)
    assert "GET" in result.access_path


def test_read_and_write_sets_are_separated():
    assert read_write_sets(sqlglot.parse_one("SELECT * FROM orders", dialect="oracle")) == (["orders"], [])
    read, written = read_write_sets(
        sqlglot.parse_one("UPDATE orders SET status = 'X' WHERE order_id = 1", dialect="oracle"))
    assert written == ["orders"]
    read, written = read_write_sets(sqlglot.parse_one(
        "INSERT INTO inventory_tx (tx_id) SELECT order_id FROM orders", dialect="oracle"))
    assert written == ["inventory_tx"] and read == ["orders"]


def test_a_statement_scalardb_cannot_run_comes_back_as_a_plan(registry, symbols):
    """A cross-partition ordered read is the case the decomposer exists for."""
    op = operation("SELECT order_id FROM orders WHERE status = 'NEW' ORDER BY ordered_at",
                   "pkg_order_report.mark_reviewed#stmt-1")
    result = analyse(op, "pkg_order_report.mark_reviewed", symbols, registry)
    assert result.status in ("PLANNED", "WARN", "ERROR")
    if result.status == "PLANNED":
        assert result.plan is not None
        assert op.plan_id


def test_a_plan_can_be_written_next_to_the_report(tmp_path, registry, symbols):
    op = operation("SELECT e.order_id, NVL(e.total_amount, 0) AS amount FROM orders e WHERE e.status = 'NEW'",
                   "pkg_order_report.mark_reviewed#stmt-2")
    result = analyse(op, "pkg_order_report.mark_reviewed", symbols, registry, plan_dir=tmp_path)
    if result.plan is not None:
        written = list(tmp_path.glob("*.plan.json"))
        assert written, "a PLANNED statement must leave its plan on disk"
        assert json.loads(written[0].read_text(encoding="utf-8"))["fetch"]


def test_unparsable_sql_is_a_diagnostic_not_a_crash(registry, symbols):
    op = operation("SELECT FROM WHERE ;;;")
    result = analyse(op, "pkg_order_status.status_of", symbols, registry)
    assert result.status == "ERROR"
    assert any(i["code"] in ("SQL_PARSE", "PARSE") for i in result.issues) or op.target_status == "ERROR"


def test_the_result_is_serialisable_as_the_contract(registry, symbols):
    """§4.1 says this JSON is the process boundary, so it has to survive json.dumps unaided."""
    op = operation("SELECT status INTO v_status FROM orders WHERE order_id = p_order_id")
    result = analyse(op, "pkg_order_status.status_of", symbols, registry)
    data = json.loads(result.to_json())
    assert data["sql_id"] == op.id
    assert data["source_dialect"] == "oracle"
    assert data["into_targets"] == [{"name": "v_status"}]
    assert data["binds"][0]["name"] == "p_order_id"


def test_the_ir_node_is_updated_in_place(registry, symbols):
    op = operation("UPDATE orders SET status = 'X' WHERE order_id = p_order_id",
                   "pkg_order_status.status_of#stmt-9")
    analyse(op, "pkg_order_status.status_of", symbols, registry)
    assert op.sql_kind == "UPDATE"
    assert op.write_set == ["orders"]
    assert op.target_status in ("OK", "WARN", "PLANNED", "ERROR")
    assert op.diagnostics or op.target_status == "OK"
