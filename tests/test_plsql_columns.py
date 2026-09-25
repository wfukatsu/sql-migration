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


def test_an_insert_without_a_column_list_pairs_its_binds_by_definition_order(tmp_path):
    """`INSERT INTO t VALUES (a, b, c)`: the Oracle DDL says which column each value lands in (#35). Without it the
    NUMBER(8,2) element of a BULK COLLECT reached a DOUBLE column as a BigDecimal (DB-SQL-10016)."""
    import json
    src = tmp_path / "src"
    src.mkdir()
    (src / "schema.sql").write_text(
        "CREATE TABLE bulk_target (employee_id NUMBER(6) PRIMARY KEY, last_name VARCHAR2(25), salary NUMBER(8,2));\n")
    (src / "load_one.prc").write_text(
        "CREATE OR REPLACE PROCEDURE load_one (p_id NUMBER, p_name VARCHAR2, p_salary NUMBER) AS\n"
        "BEGIN\n  INSERT INTO bulk_target VALUES (p_id, p_name, p_salary);\nEND;\n/\n")
    schema = tmp_path / "scalardb-schema.json"
    schema.write_text(json.dumps({"t.bulk_target": {
        "transaction": True, "partition-key": ["employee_id"],
        "columns": {"employee_id": "INT", "last_name": "TEXT", "salary": "DOUBLE"}}}))
    analysis = analyse(src, src / "schema.sql", scalardb_schema=schema)
    insert = next(s for _, routine in analysis.routines() for s in routine.body if getattr(s, "sql_kind", "") == "INSERT")
    assert [(b.plsql_variable, b.column, b.scalardb_type, b.column_oracle_type) for b in insert.binds] == [
        ("p_id", "employee_id", "INT", "NUMBER(6)"), ("p_name", "last_name", "TEXT", "VARCHAR2(25)"),
        ("p_salary", "salary", "DOUBLE", "NUMBER(8, 2)")]
