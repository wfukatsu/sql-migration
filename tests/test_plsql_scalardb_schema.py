"""P0-3: the ScalarDB schema for the corpus tables, and the access paths it produces.

The schema is a fixture that P1-3 (%TYPE resolution), P1-6 (SQL bridge) and P3-1 (loading fixtures into ScalarDB)
all read. What has to stay true is that it covers `schema.sql` and that the access paths it yields are the ones
KEY-DESIGN.md claims -- a key change that silently turns an index scan into a cross-partition scan would move the
Phase 2 verdicts and the Phase 3 performance numbers without anyone noticing.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from scalardb_migrate.converter import convert_script
from scalardb_migrate.schema import SchemaRegistry

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SCHEMA_JSON = FIXTURES / "scalardb-schema.json"
SCHEMA_SQL = FIXTURES / "src" / "schema.sql"
NAMESPACE = "plsqlpoc"

# KEY-DESIGN.md「アクセスパスの洗い出し」の表。左が corpus 由来の読み取り、右が期待する判定。
ACCESS_PATHS = [
    ("SELECT status FROM orders WHERE order_id = 1", "GET"),
    ("SELECT * FROM customers WHERE customer_id = 1", "GET"),
    ("SELECT stock_qty FROM products WHERE product_id = 1", "GET"),
    ("SELECT next_value FROM counters WHERE counter_name = 'PAYMENT_ID'", "GET"),
    ("SELECT qty, unit_price FROM order_lines WHERE order_id = 1", "partition SCAN"),
    ("SELECT status FROM orders WHERE customer_id = 1", "index SCAN"),
    ("SELECT COUNT(*) FROM orders WHERE status = 'NEW'", "index SCAN"),
    ("SELECT SUM(amount) FROM payments WHERE order_id = 1", "index SCAN"),
    ("SELECT o.order_id FROM orders o WHERE o.customer_id = 1 ORDER BY o.total_amount DESC", "cross-partition SCAN"),
    ("SELECT order_id FROM orders WHERE status = 'NEW' ORDER BY ordered_at", "cross-partition SCAN"),
]


def registry() -> SchemaRegistry:
    return SchemaRegistry.from_schema_loader_json(str(SCHEMA_JSON))


def analyse(sql: str):
    results, _ = convert_script(sql, "oracle", registry(), {}, decompose=False)
    return results[0]


def test_schema_loads_through_the_registry():
    assert {t.name for t in registry().tables()}, "the registry must not come back empty"


def test_every_table_in_the_oracle_ddl_is_present():
    ddl_tables = set(re.findall(r"CREATE TABLE (\w+)", SCHEMA_SQL.read_text(encoding="utf-8")))
    assert ddl_tables == {t.name for t in registry().tables()}


def test_every_column_in_the_oracle_ddl_is_present():
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    reg = registry()
    for table_name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S):
        meta = reg.get(table_name)
        declared = {m.group(1).lower() for line in body.splitlines()
                    if (m := re.match(r"\s{2}(\w+)\s+[A-Z]", line))}
        missing = declared - {c.lower() for c in meta.columns}
        assert missing == set(), f"{table_name}: columns missing from the ScalarDB schema: {sorted(missing)}"


def test_every_table_is_namespaced():
    assert {t.namespace for t in registry().tables()} == {NAMESPACE}


def test_money_columns_are_scaled_integers_not_double():
    """DOUBLE にすると ROUND(half-up) の差が Phase 3 の意味的同等性テストで出る（KEY-DESIGN.md）。"""
    reg = registry()
    for table, column in [("customers", "credit_limit"), ("products", "unit_price"),
                          ("orders", "total_amount"), ("payments", "amount")]:
        assert reg.get(table).columns[column] == "BIGINT", f"{table}.{column} must be a scaled integer"


def test_date_columns_that_carry_a_time_are_timestamps():
    """corpus はすべて SYSDATE で書くので時刻成分を持つ。DATE にすると TRUNC の意味が変わる。"""
    reg = registry()
    for table, column in [("orders", "ordered_at"), ("inventory_tx", "created_at"),
                          ("batch_control", "last_run_at"), ("customers", "registered_on")]:
        assert reg.get(table).columns[column] == "TIMESTAMP", f"{table}.{column} must keep the time component"


def test_secondary_indexes_are_single_column():
    for meta in registry().tables():
        for index in meta.secondary_indexes:
            assert "," not in index, f"{meta.name}: ScalarDB secondary indexes are single-column"


@pytest.mark.parametrize("sql,expected", ACCESS_PATHS, ids=[s[:48] for s, _ in ACCESS_PATHS])
def test_access_path_matches_the_key_design(sql: str, expected: str):
    messages = " | ".join(i.message for i in analyse(sql).issues)
    assert expected in messages, f"expected {expected!r}, got: {messages}"


def test_ordering_on_a_non_key_column_is_what_forces_a_cross_partition_scan():
    """KEY-DESIGN.md の主張そのもの。並び替えを外せば index SCAN に収まる。"""
    indexed = " | ".join(i.message for i in analyse("SELECT total_amount FROM orders WHERE customer_id = 1").issues)
    ordered = " | ".join(i.message for i in analyse(
        "SELECT total_amount FROM orders WHERE customer_id = 1 ORDER BY total_amount DESC").issues)
    assert "index SCAN" in indexed
    assert "cross-partition SCAN" in ordered
