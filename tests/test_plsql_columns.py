"""P3-1: attributing binds and select items to ScalarDB columns.

The generated repository converts at the bind boundary, and it can only do that for a value it can attribute to
exactly one column. These tests fix both halves of that: what gets attributed, and what deliberately does not.
"""

import sqlglot

from plsql.columns import bind_columns, select_columns, selects_star
from plsql.report import analyse
from plsql.symbols import OracleSchema, SymbolTable
from plsql.sqlbridge import expand_star

DDL = "fixtures/plsql/src/schema.sql"


def parse(sql):
    return sqlglot.parse_one(sql, read="oracle")


# ---------------------------------------------------------------- binds
def test_insert_binds_pair_with_their_columns_positionally():
    binds = bind_columns(parse("INSERT INTO products (product_id, name, unit_price) VALUES (:a, :b, :c)"))
    assert binds == {"a": "product_id", "b": "name", "c": "unit_price"}


def test_update_set_and_where_binds_both_resolve():
    binds = bind_columns(parse("UPDATE orders SET total_amount = :t WHERE order_id = :o"))
    assert binds == {"t": "total_amount", "o": "order_id"}


def test_bind_inside_an_expression_is_not_attributed():
    """The value no longer belongs to one column, and guessing is how a silently wrong conversion happens."""
    assert bind_columns(parse("SELECT * FROM products WHERE unit_price * :rate > 10")) == {}


def test_in_list_and_between_binds_resolve_to_the_compared_column():
    assert bind_columns(parse("SELECT 1 FROM orders WHERE status IN (:a, :b)")) == {"a": "status", "b": "status"}
    assert bind_columns(parse("SELECT 1 FROM orders WHERE order_id BETWEEN :lo AND :hi")) \
        == {"lo": "order_id", "hi": "order_id"}


# ---------------------------------------------------------------- select items
def test_select_items_name_their_columns():
    assert select_columns(parse("SELECT credit_limit, name FROM customers")) == ["credit_limit", "name"]


def test_sum_carries_the_type_of_its_argument():
    assert select_columns(parse("SELECT SUM(amount) FROM payments")) == ["amount"]


def test_an_expression_names_no_single_column():
    assert select_columns(parse("SELECT unit_price * qty FROM order_lines")) == [None]


def test_count_names_no_column():
    assert select_columns(parse("SELECT COUNT(*) FROM orders")) == [None]


# ---------------------------------------------------------------- star expansion
def test_star_expands_to_the_ddl_column_order():
    schema = OracleSchema.from_ddl(DDL)
    tree = parse("SELECT * FROM orders WHERE order_id = 1")
    expand_star(tree, SymbolTable(oracle_schema=schema))
    assert select_columns(tree) == list(schema.columns("orders"))
    assert not selects_star(tree)


def test_star_over_a_join_is_left_alone():
    """Expanding it would mean deciding an order across tables, which the DDL does not settle."""
    tree = parse("SELECT * FROM orders o JOIN customers c ON o.customer_id = c.customer_id")
    expand_star(tree, SymbolTable(oracle_schema=OracleSchema.from_ddl(DDL)))
    assert selects_star(tree)


def test_star_without_a_ddl_is_left_alone():
    tree = parse("SELECT * FROM orders")
    expand_star(tree, SymbolTable())
    assert selects_star(tree)


# ---------------------------------------------------------------- end to end
def test_money_binds_carry_the_column_type_and_the_declared_oracle_type():
    """The scale comes from the column, not the variable: `v_total NUMBER` into a NUMBER(14,2) is still cents."""
    analysis = analyse("fixtures/plsql/src", DDL, scalardb_schema="fixtures/plsql/scalardb-schema.json")
    money = [b for _, routine in analysis.routines() for statement in routine.body
             for b in getattr(statement, "binds", []) or []
             if b.column in ("total_amount", "credit_limit", "unit_price", "amount")]
    assert money, "the corpus writes money columns; none were attributed"
    for bind in money:
        assert bind.scalardb_type, f"{bind.name} has no ScalarDB type"
        assert bind.column_oracle_type and "," in bind.column_oracle_type, \
            f"{bind.name} did not pick up the column's declared scale"
