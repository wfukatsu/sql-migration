"""Rule-level tests for the ScalarDB converter PoC.  Run: .venv/bin/python -m pytest -q"""

import pytest

from scalardb_migrate.converter import convert_script

SCHEMA_DDL = {
    "mysql": "CREATE TABLE orders (customer_id INT, order_no BIGINT, status VARCHAR(20), amount DOUBLE, "
             "PRIMARY KEY (customer_id, order_no)); CREATE INDEX ix ON orders (status);",
}


def run(sql, dialect="mysql", with_schema=True):
    text = (SCHEMA_DDL.get(dialect, "") if with_schema else "") + sql
    results, reg = convert_script(text, dialect, decompose=False)
    return results[-1]


def codes(r):
    return {i.code for i in r.issues}


# ---------------------------------------------------------------- DDL
def test_create_table_composite_pk_and_types():
    r = run("CREATE TABLE t (a INT, b BIGINT, c VARCHAR(10) NOT NULL, d DECIMAL(10,2), e DATETIME, PRIMARY KEY (a, b))",
            with_schema=False)
    assert r.status == "WARN"
    out = r.converted[0]
    assert "a INT" in out and "c TEXT" in out and "d DOUBLE" in out and "e TIMESTAMP" in out
    assert "PRIMARY KEY (a, b)" in out
    assert "NOT NULL" not in out and "TYPE" in codes(r)


def test_create_table_multi_partition_key_hint():
    results, reg = convert_script("CREATE TABLE t (a INT, b INT, c INT, PRIMARY KEY (a, b, c))", "postgres",
                                  key_hints={"t": (["a", "b"], ["c"])})
    assert "PRIMARY KEY ((a, b), c)" in results[0].converted[0]
    meta = reg.get("t")
    assert meta.partition_key == ["a", "b"] and meta.clustering_key == ["c"]


def test_auto_increment_is_error():
    r = run("CREATE TABLE t (id INT AUTO_INCREMENT PRIMARY KEY, v TEXT)", with_schema=False)
    assert r.status == "ERROR" and "AUTO_INC" in codes(r)


def test_serial_is_error():
    r = run("CREATE TABLE t (id SERIAL PRIMARY KEY, v TEXT)", "postgres", with_schema=False)
    assert r.status == "ERROR"


def test_no_primary_key_is_error():
    r = run("CREATE TABLE t (id INT, v TEXT)", with_schema=False)
    assert "PK" in codes(r)


def test_inline_index_becomes_create_index():
    r = run("CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(10), INDEX ix (name))", with_schema=False)
    assert r.converted[1] == "CREATE INDEX ON t (name)"


def test_composite_index_is_error():
    r = run("CREATE INDEX ix ON t (a, b)", "postgres", with_schema=False)
    assert r.status == "ERROR" and "INDEX" in codes(r)


def test_alter_add_column():
    r = run("ALTER TABLE t ADD COLUMN x VARCHAR(10)", with_schema=False)
    assert r.converted == ["ALTER TABLE t ADD COLUMN x TEXT"]


def test_sequence_and_view_unsupported():
    assert run("CREATE SEQUENCE s", "oracle", with_schema=False).status == "ERROR"
    assert run("CREATE VIEW v AS SELECT 1 FROM dual", "oracle", with_schema=False).status == "ERROR"


# ---------------------------------------------------------------- SELECT
def test_get_by_full_pk():
    r = run("SELECT status FROM orders WHERE customer_id = 1 AND order_no = 2")
    assert r.status == "OK"
    assert any("GET" in i.message for i in r.issues)


def test_partition_scan_with_clustering_order():
    r = run("SELECT order_no FROM orders WHERE customer_id = 1 AND order_no > 10 ORDER BY order_no DESC LIMIT 5")
    assert r.status == "OK" and any("partition SCAN" in i.message for i in r.issues)


def test_cross_partition_warning():
    r = run("SELECT order_no FROM orders WHERE amount > 10")
    assert r.status == "WARN" and "CROSS_PARTITION" in codes(r)


def test_in_list_expanded_to_or():
    r = run("SELECT * FROM orders WHERE customer_id = 1 AND status IN ('A', 'B')")
    assert r.converted[0] == "SELECT * FROM orders WHERE customer_id = 1 AND (status = 'A' OR status = 'B')"


def test_not_in_expanded_to_and():
    r = run("SELECT * FROM orders WHERE customer_id = 1 AND status NOT IN ('A', 'B')")
    assert "status <> 'A' AND status <> 'B'" in r.converted[0]


def test_not_pushdown_and_dnf():
    r = run("SELECT * FROM orders WHERE NOT (customer_id = 1) OR (status = 'A' AND amount > 1)")
    assert r.converted[0].endswith("WHERE customer_id <> 1 OR (status = 'A' AND amount > 1)")


def test_is_not_null_and_not_like():
    r = run("SELECT * FROM orders WHERE status IS NOT NULL AND status NOT LIKE 'X%' AND customer_id = 1")
    assert "status IS NOT NULL" in r.converted[0] and "status NOT LIKE 'X%'" in r.converted[0]


def test_literal_on_left_is_flipped():
    r = run("SELECT * FROM orders WHERE 10 < amount AND customer_id = 1")
    assert "amount > 10" in r.converted[0]


def test_column_to_column_predicate_is_error():
    r = run("SELECT * FROM orders WHERE customer_id = order_no")
    assert "COL_COL" in codes(r)


def test_expression_projection_is_error():
    r = run("SELECT amount * 2 FROM orders WHERE customer_id = 1")
    assert "PROJECTION" in codes(r)


def test_aggregates_ok_and_distinct_count_error():
    ok = run("SELECT status, COUNT(*), SUM(amount) FROM orders GROUP BY status HAVING COUNT(*) > 1")
    assert ok.converted[0] == "SELECT status, COUNT(*), SUM(amount) FROM orders GROUP BY status HAVING COUNT(*) > 1"
    assert "AGG_DISTINCT" in codes(run("SELECT COUNT(DISTINCT status) FROM orders"))


@pytest.mark.parametrize("sql,code", [
    ("SELECT DISTINCT status FROM orders", "DISTINCT"),
    ("SELECT * FROM orders LIMIT 5 OFFSET 10", "OFFSET"),
    ("SELECT * FROM orders LIMIT 5, 10", "OFFSET"),
    ("WITH x AS (SELECT 1 AS a) SELECT a FROM x", "CTE"),
    ("SELECT * FROM orders WHERE customer_id IN (SELECT id FROM c)", "SUBQUERY"),
    ("SELECT * FROM orders UNION SELECT * FROM orders", "SET_OP"),
    ("SELECT * FROM orders WHERE customer_id = 1 AND UPPER(status) = 'A'", "PRED"),
])
def test_unsupported_select_features(sql, code):
    r = run(sql)
    assert r.status == "ERROR" and code in codes(r), r.issues


def test_oracle_rownum_and_fetch():
    r = run("SELECT ename FROM emp WHERE deptno = 1 AND ROWNUM <= 5", "oracle", with_schema=False)
    assert r.converted[0] == "SELECT ename FROM emp WHERE deptno = 1 LIMIT 5"
    r = run("SELECT ename FROM emp ORDER BY sal FETCH FIRST 3 ROWS ONLY", "oracle", with_schema=False)
    assert r.converted[0] == "SELECT ename FROM emp ORDER BY sal LIMIT 3"


def test_oracle_join_mark_becomes_left_join():
    r = run("SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+)", "oracle", with_schema=False)
    assert r.status == "WARN" and "ORACLE_JOIN_MARK" in codes(r)
    assert r.converted[0] == "SELECT e.ename, d.dname FROM emp AS e LEFT JOIN dept AS d ON e.deptno = d.deptno"


def test_comma_join_rewritten():
    r = run("SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno AND e.sal > 1", "oracle",
            with_schema=False)
    assert r.converted[0] == "SELECT e.ename, d.dname FROM emp AS e INNER JOIN dept AS d ON e.deptno = d.deptno WHERE e.sal > 1"


def test_join_using_rewritten_and_key_coverage_checked():
    r = run("SELECT c.name FROM customers c JOIN orders o USING (customer_id)")
    assert "ON c.customer_id = o.customer_id" in r.converted[0]
    assert "JOIN_KEY" in codes(r)  # orders PK is (customer_id, order_no): not covered


# --- #13: an inner join written the other way round ----------------------------------------------------

JOIN_SCHEMA = ("CREATE TABLE customers (customer_id BIGINT, tier VARCHAR(10), PRIMARY KEY (customer_id)); "
               "CREATE TABLE orders2 (order_id BIGINT, customer_id BIGINT, PRIMARY KEY (order_id)); ")


def join_run(sql):
    results, _ = convert_script(JOIN_SCHEMA + sql, "mysql", decompose=False)
    return results[-1]


def test_an_inner_join_is_swapped_so_that_where_names_the_from_table():
    """ScalarDB lets WHERE name only the FROM table. An INNER JOIN returns the same rows either way."""
    r = join_run("SELECT c.tier FROM customers c JOIN orders2 o ON o.customer_id = c.customer_id "
                 "WHERE o.order_id = 1")
    assert "JOIN_SCOPE" not in codes(r), r.issues
    assert r.converted[0] == ("SELECT c.tier FROM orders2 AS o JOIN customers AS c "
                              "ON o.customer_id = c.customer_id WHERE o.order_id = 1")
    assert "JOIN_ORDER" in codes(r), "the swap is stated, not silent"


def test_the_swap_is_not_made_when_it_would_not_settle_the_matter():
    """Both sides referenced: moving the problem from one side to the other helps nobody."""
    r = join_run("SELECT c.tier FROM customers c JOIN orders2 o ON o.customer_id = c.customer_id "
                 "WHERE o.order_id = 1 AND c.tier = 'GOLD'")
    assert r.status == "ERROR" and "JOIN_SCOPE" in codes(r)
    assert "JOIN_ORDER" not in codes(r)


def test_an_outer_join_is_not_swapped():
    """LEFT JOIN is not commutative: swapping the sides is a different query."""
    r = join_run("SELECT c.tier FROM customers c LEFT JOIN orders2 o ON o.customer_id = c.customer_id "
                 "WHERE o.order_id = 1")
    assert "JOIN_ORDER" not in codes(r)
    assert "JOIN_SCOPE" in codes(r)


def test_a_join_that_already_names_the_from_table_is_left_alone():
    r = join_run("SELECT c.tier FROM customers c JOIN orders2 o ON o.customer_id = c.customer_id "
                 "WHERE c.customer_id = 1")
    assert "JOIN_ORDER" not in codes(r)
    assert r.converted[0].startswith("SELECT c.tier FROM customers AS c")


def test_to_date_literal():
    r = run("SELECT ename FROM emp WHERE hiredate > TO_DATE('2020-01-01', 'YYYY-MM-DD')", "oracle", with_schema=False)
    assert r.converted[0].endswith("WHERE hiredate > '2020-01-01'")



@pytest.mark.parametrize("literal,value", [("DATE '2020-01-01'", "'2020-01-01'"),
                                           ("TIMESTAMP '2020-01-01 10:00:00'", "'2020-01-01 10:00:00'")])
def test_ansi_date_literal(literal, value):
    # Oracle の ANSI 日付リテラル。SQLGlot は DateStrToDate / TimeStrToTime として表す
    r = run(f"SELECT ename FROM emp WHERE hiredate > {literal}", "oracle", with_schema=False)
    assert r.status != "ERROR" and r.converted[0].endswith(f"WHERE hiredate > {value}")

def test_bind_markers():
    assert run("SELECT * FROM emp WHERE empno = :id", "oracle", with_schema=False).converted[0].endswith("= :id")
    assert run("SELECT * FROM emp WHERE empno = $1", "postgres", with_schema=False).converted[0].endswith("= ?")
    assert run("SELECT * FROM emp WHERE empno = ?", "mysql", with_schema=False).converted[0].endswith("= ?")


# ---------------------------------------------------------------- DML
def test_insert_missing_pk_is_error():
    r = run("INSERT INTO orders (customer_id, status) VALUES (1, 'A')")
    assert "PK" in codes(r)


def test_insert_with_function_value_is_error():
    r = run("INSERT INTO orders (customer_id, order_no, status) VALUES (1, 2, NOW())")
    assert r.status == "ERROR" and "NOW" in codes(r)


def test_on_duplicate_key_to_upsert():
    r = run("INSERT INTO orders (customer_id, order_no, status) VALUES (1, 2, 'A') ON DUPLICATE KEY UPDATE status = VALUES(status)")
    assert r.converted[0] == "UPSERT INTO orders (customer_id, order_no, status) VALUES (1, 2, 'A')"
    assert r.status == "OK"


def test_on_conflict_to_upsert_and_do_nothing_error():
    r = run("INSERT INTO t (id, v) VALUES (1, 'a') ON CONFLICT (id) DO UPDATE SET v = EXCLUDED.v", "postgres", with_schema=False)
    assert r.converted[0].startswith("UPSERT INTO t (id, v)")
    r = run("INSERT INTO t (id, v) VALUES (1, 'a') ON CONFLICT (id) DO NOTHING", "postgres", with_schema=False)
    assert "DO_NOTHING" in codes(r)


def test_on_conflict_with_expression_is_error():
    r = run("INSERT INTO t (id, n) VALUES (1, 1) ON CONFLICT (id) DO UPDATE SET n = t.n + 1", "postgres", with_schema=False)
    assert r.status == "ERROR"


def test_replace_into_to_upsert():
    r = run("REPLACE INTO orders (customer_id, order_no, status) VALUES (1, 2, 'A')")
    assert r.converted[0].startswith("UPSERT INTO orders") and "REPLACE" in codes(r)


def test_merge_constant_source_to_upsert():
    sql = ("MERGE INTO emp t USING (SELECT 1 AS empno, 'X' AS ename FROM dual) s ON (t.empno = s.empno) "
           "WHEN MATCHED THEN UPDATE SET t.ename = s.ename WHEN NOT MATCHED THEN INSERT (empno, ename) VALUES (s.empno, s.ename)")
    r = run(sql, "oracle", with_schema=False)
    assert r.converted[0] == "UPSERT INTO emp (empno, ename) VALUES (1, 'X')"


def test_update_rmw_is_error_and_literal_ok():
    assert "RMW" in codes(run("UPDATE orders SET amount = amount + 1 WHERE customer_id = 1 AND order_no = 2"))
    r = run("UPDATE orders SET status = 'B', amount = NULL WHERE customer_id = 1 AND order_no = 2")
    assert r.converted[0] == "UPDATE orders SET status = 'B', amount = NULL WHERE customer_id = 1 AND order_no = 2"


def test_update_join_and_delete_using_are_errors():
    assert "UPDATE_JOIN" in codes(run("UPDATE orders o JOIN c ON o.customer_id = c.id SET o.status = 'X'"))
    assert "DELETE_JOIN" in codes(run("DELETE FROM orders USING c WHERE orders.customer_id = c.id", "postgres", with_schema=False))


def test_delete_without_where_warns():
    r = run("DELETE FROM orders")
    assert r.status == "WARN" and "NO_WHERE" in codes(r)


def test_transactions():
    assert run("START TRANSACTION").converted == ["BEGIN"]
    assert run("BEGIN", "postgres", with_schema=False).converted == ["BEGIN"]
    assert run("ROLLBACK").converted == ["ROLLBACK"]


def test_schema_loader_json_is_emitted():
    results, reg = convert_script(SCHEMA_DDL["mysql"], "mysql")
    import json
    j = json.loads(reg.to_schema_loader_json())
    assert j["orders"]["partition-key"] == ["customer_id"]
    assert j["orders"]["clustering-key"] == ["order_no ASC"]
    assert j["orders"]["secondary-index"] == ["status"]
    assert j["orders"]["columns"]["status"] == "TEXT"


def test_rollup_grouping_sets_and_rowid_are_errors():
    assert "GROUP" in codes(run("SELECT deptno, SUM(amount) FROM orders GROUP BY ROLLUP (deptno)", "oracle", with_schema=False))
    assert "GROUP" in codes(run("SELECT deptno FROM orders GROUP BY GROUPING SETS ((deptno), (status))", "oracle", with_schema=False))
    assert "ROWID" in codes(run("SELECT status FROM orders WHERE ROWID IS NOT NULL", "oracle", with_schema=False))


def test_using_column_is_qualified_with_from_table():
    r = run("SELECT name FROM customers c JOIN orders o USING (customer_id) WHERE customer_id = 1")
    assert "WHERE c.customer_id = 1" in r.converted[0]


def test_timestamp_literal_against_date_column_is_trimmed():
    ddl = "CREATE TABLE emp (empno INT PRIMARY KEY, hiredate DATE);"
    results, _ = convert_script(ddl + "SELECT empno FROM emp WHERE hiredate > TO_TIMESTAMP('1981-06-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')",
                                "oracle", decompose=False)
    assert results[-1].converted[0].endswith("WHERE hiredate > '1981-06-01'")
    results, _ = convert_script(ddl + "SELECT empno FROM emp WHERE hiredate < TIMESTAMP '1982-06-01 00:00:00'", "oracle", decompose=False)
    assert results[-1].converted[0].endswith("WHERE hiredate < '1982-06-01'")


# ---------------------------------------------------------------- storage-aware conversion (non-JDBC: Cassandra)
def run_on(sql, storage):
    results, _ = convert_script(SCHEMA_DDL["mysql"] + sql, "mysql", decompose=False, storage=storage)
    return results[-1]


def test_cross_partition_order_by_is_pushed_down_on_jdbc_only():
    sql = "SELECT order_no FROM orders WHERE status = 'X' ORDER BY amount"
    assert run_on(sql, "jdbc").status == "WARN"
    r = run_on(sql, "cassandra")
    assert r.status == "ERROR" and "ORDER_STORAGE" in codes(r)
    assert "ORDER_STORAGE" in codes(run_on("SELECT order_no FROM orders ORDER BY amount LIMIT 10", "cassandra"))


def test_partition_scan_in_clustering_order_is_kept_on_cassandra():
    for order in ("order_no", "order_no DESC"):
        r = run_on(f"SELECT order_no FROM orders WHERE customer_id = 1 ORDER BY {order} LIMIT 10", "cassandra")
        assert r.status == "OK", codes(r)
    r = run_on("SELECT order_no FROM orders WHERE customer_id = 1 ORDER BY amount", "cassandra")
    assert "ORDER_STORAGE" in codes(r)


def test_mixed_directions_against_the_clustering_order_fail_on_cassandra():
    ddl = "CREATE TABLE ev (dev INT, ts INT, seq INT, v INT, PRIMARY KEY (dev, ts, seq));"
    results, _ = convert_script(ddl + "SELECT v FROM ev WHERE dev = 1 ORDER BY ts DESC, seq", "mysql",
                                decompose=False, storage="cassandra")
    assert "ORDER_STORAGE" in codes(results[-1])
    results, _ = convert_script(ddl + "SELECT v FROM ev WHERE dev = 1 ORDER BY ts DESC, seq DESC", "mysql",
                                decompose=False, storage="cassandra")
    assert results[-1].status == "OK"


def test_grouped_order_by_is_sorted_by_the_sql_layer():
    r = run_on("SELECT status, COUNT(*) FROM orders WHERE customer_id = 1 GROUP BY status ORDER BY status", "cassandra")
    assert r.status == "OK", codes(r)
    r = run_on("SELECT status, COUNT(*) FROM orders GROUP BY status ORDER BY status", "jdbc")
    assert r.status == "WARN"


def test_no_cross_partition_scan_on_cassandra():
    for sql in ("SELECT status, COUNT(*) FROM orders GROUP BY status",
                "SELECT order_no FROM orders WHERE amount > 10",
                "SELECT order_no FROM orders WHERE amount = 1 OR amount = 2"):
        assert run_on(sql, "jdbc").status == "WARN"
        r = run_on(sql, "cassandra")
        assert r.status == "ERROR" and "NO_CROSS_PARTITION" in codes(r), (sql, codes(r))
    for sql in ("UPDATE orders SET amount = 0 WHERE amount > 1", "DELETE FROM orders WHERE amount > 10"):
        r = run_on(sql, "cassandra")
        assert r.status == "ERROR" and "NO_CROSS_PARTITION" in codes(r), (sql, codes(r))
    # a key or index condition plus a non-key filter is served without a cross-partition scan (verified on Cassandra)
    for sql in ("UPDATE orders SET amount = 0 WHERE customer_id = 1 AND order_no = 2",
                "UPDATE orders SET amount = 0 WHERE status = 'X' AND amount > 1",
                "SELECT order_no FROM orders WHERE customer_id = 1 AND amount > 1"):
        assert run_on(sql, "cassandra").status == "OK", sql


def test_key_in_list_is_split_only_on_cassandra_selects():
    sql = "SELECT order_no FROM orders WHERE customer_id IN (1, 2, 3)"
    assert run_on(sql, "jdbc").status == "WARN"
    assert "OR_KEYS" in codes(run_on(sql, "cassandra"))
    assert "OR_KEYS" in codes(run_on("SELECT order_no FROM orders WHERE status IN ('A', 'B') AND amount > 1", "cassandra"))
    r = run_on("UPDATE orders SET amount = 0 WHERE customer_id IN (1, 2)", "cassandra")  # writes are not planned
    assert r.status == "ERROR" and "OR_KEYS" not in codes(r) and "NO_CROSS_PARTITION" in codes(r)


# ---------------------------------------------------------------- reserved columns (P3-1)
def test_create_table_rejects_consensus_commit_column_names():
    """Consensus Commit keeps its own columns in the user's table, so those names are already taken.

    Schema Loader reports this as DB-CORE-10101 at load time. The converter has the DDL in front of it, so the
    name is knowable before anyone waits for a deployment to fail.
    """
    r = run("CREATE TABLE t (tx_id BIGINT PRIMARY KEY, note VARCHAR(10))", with_schema=False)
    assert r.status == "ERROR" and "RESERVED_COLUMN" in codes(r)


def test_create_table_rejects_before_prefixed_non_key_column():
    r = run("CREATE TABLE t (id BIGINT PRIMARY KEY, before_x VARCHAR(10))", with_schema=False)
    assert r.status == "ERROR" and "RESERVED_COLUMN" in codes(r)


def test_create_table_allows_before_prefixed_key_column():
    """Consensus Commit only shadows the non-key columns, so a key may legitimately start with before_."""
    r = run("CREATE TABLE t (before_id BIGINT PRIMARY KEY, note VARCHAR(10))", with_schema=False)
    assert "RESERVED_COLUMN" not in codes(r)


# ---------------------------------------------------------------- temporal literals in INSERT (P3-1)
TEMPORAL_DDL = ("CREATE TABLE ev (id BIGINT PRIMARY KEY, at_ts TIMESTAMP, on_date DATE);")


def temporal(sql):
    results, _ = convert_script(TEMPORAL_DDL + sql, "oracle", decompose=False)
    return results[-1]


def test_insert_pads_a_date_only_literal_for_a_timestamp_column():
    """ScalarDB parses a TIMESTAMP strictly and rejects 'YYYY-MM-DD'; Oracle's DATE literal means midnight."""
    r = temporal("INSERT INTO ev (id, at_ts) VALUES (1, DATE '2025-04-01');")
    assert r.status in ("OK", "WARN")
    assert "'2025-04-01 00:00:00'" in r.converted[0]
    assert "DATE_LIT" in codes(r)


def test_insert_keeps_a_date_only_literal_for_a_date_column():
    r = temporal("INSERT INTO ev (id, on_date) VALUES (1, DATE '2025-04-01');")
    assert "'2025-04-01'" in r.converted[0] and "00:00:00" not in r.converted[0]


def test_insert_drops_a_midnight_time_for_a_date_column():
    r = temporal("INSERT INTO ev (id, on_date) VALUES (1, TIMESTAMP '2025-04-01 00:00:00');")
    assert "'2025-04-01'" in r.converted[0] and "00:00:00" not in r.converted[0]


def test_insert_leaves_a_timestamp_literal_alone():
    r = temporal("INSERT INTO ev (id, at_ts) VALUES (1, TIMESTAMP '2025-04-01 10:30:00');")
    assert "'2025-04-01 10:30:00'" in r.converted[0]
