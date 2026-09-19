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


def test_an_unqualified_column_is_attributed_to_its_table_before_the_swap_is_decided():
    """MR !52: the swap read only qualified references while JOIN_SCOPE resolved unqualified ones too, so a
    query the swap could have settled was refused by the check that followed it."""
    r = join_run("SELECT c.tier FROM customers c JOIN orders2 o ON o.customer_id = c.customer_id "
                 "WHERE order_id = 1")
    assert "JOIN_SCOPE" not in codes(r), r.issues
    assert "JOIN_ORDER" in codes(r)
    assert r.converted[0].startswith("SELECT c.tier FROM orders2 AS o JOIN customers AS c")


def test_an_unqualified_column_of_the_from_table_still_stops_the_swap():
    """The same resolution the check uses: `tier` is the base table's, so swapping would break the query."""
    r = join_run("SELECT c.tier FROM customers c JOIN orders2 o ON o.customer_id = c.customer_id "
                 "WHERE order_id = 1 AND tier = 'GOLD'")
    assert "JOIN_ORDER" not in codes(r)
    assert r.status == "ERROR" and "JOIN_SCOPE" in codes(r)


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


def test_rownum_compared_with_a_bind_becomes_a_limit_bind():
    """件数は bind でも件数である（2026-09-19、`claim_batch` の `ROWNUM <= p_limit`）。"""
    result = run("SELECT order_no FROM orders WHERE status = 'NEW' AND ROWNUM <= :n", dialect="oracle")
    assert result.status != "ERROR", result.issues
    assert "LIMIT :n" in result.converted[0]


def test_rownum_less_than_a_bind_is_still_refused():
    """`ROWNUM < :n` は LIMIT n-1 である。bind から n-1 は作れないので、拒否のまま。"""
    result = run("SELECT order_no FROM orders WHERE ROWNUM < :n", dialect="oracle")
    assert result.status == "ERROR"


# ---------------------------------------------------------------- #27: positional binds, ROWNUM over aggregates
BIND_DDL = "CREATE TABLE customers (id NUMBER(9) PRIMARY KEY, name VARCHAR2(50), code VARCHAR2(10), email VARCHAR2(50));"


def bind_order(r):
    return next((i.message for i in r.issues if i.code == "BIND_ORDER"), None)


def test_binds_kept_in_source_order_raise_nothing():
    r = run(BIND_DDL + "SELECT id, name FROM customers WHERE id = ? AND name = ?", "oracle", with_schema=False)
    assert bind_order(r) is None


def test_rownum_bind_moved_to_limit_reports_the_new_order():
    r = run(BIND_DDL + "SELECT id, name FROM customers WHERE ROWNUM <= ? AND id = ?", "oracle", with_schema=False)
    assert r.converted[0].endswith("WHERE id = ? LIMIT ?")
    assert r.status == "WARN" and "[2, 1]" in bind_order(r)


def test_a_bind_duplicated_by_or_normalisation_is_reported():
    r = run(BIND_DDL + "SELECT id, name FROM customers WHERE id = ? AND (name = ? OR (code = ? AND email = ?))",
            "oracle", with_schema=False)
    assert r.converted[0].count("?") == 5
    assert "5 '?' for 4" in bind_order(r) and "[1, 2, 3, 4, 1]" in bind_order(r)


def test_postgres_numbered_binds_out_of_order_are_reported():
    ddl = "CREATE TABLE acct (id INT PRIMARY KEY, email TEXT, bal INT);"
    swapped = run(ddl + "SELECT id FROM acct WHERE email = $2 AND bal = $1", "postgres", with_schema=False)
    assert "[2, 1]" in bind_order(swapped)
    assert bind_order(run(ddl + "SELECT id FROM acct WHERE id = $1 AND bal = $2", "postgres", with_schema=False)) is None


def test_a_plan_fetch_binds_by_source_position():
    """Each fetch is its own statement: a bare `?` copied into one would be renumbered from 1 there."""
    ddl = "CREATE TABLE a (id INT PRIMARY KEY, x TEXT); CREATE TABLE b (id INT PRIMARY KEY, aid INT, status TEXT);"
    results, _ = convert_script(ddl + "SELECT UPPER(a.x), b.status FROM a JOIN b ON a.id = b.aid "
                                      "WHERE b.status = ? AND a.x = ?", "oracle")
    fetch = {f["table"]: f for f in results[-1].plan["fetch"]}
    assert fetch["a"]["predicates"][0]["value"] == {"param": "2"} and fetch["a"]["scalardb_sql"].endswith("x = :2")
    assert fetch["b"]["predicates"][0]["value"] == {"param": "1"}


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM customers WHERE ROWNUM <= 5",
    "SELECT MAX(id) FROM customers WHERE ROWNUM <= ?",
])
def test_rownum_over_an_aggregate_is_not_a_limit(sql):
    """ROWNUM limits the rows read; LIMIT after COUNT(*) would count the whole table."""
    r = run(BIND_DDL + sql, "oracle", with_schema=False)
    assert r.status == "ERROR" and "ROWNUM" in codes(r)


def test_a_fractional_rownum_is_an_error_not_a_crash():
    r = run(BIND_DDL + "SELECT id FROM customers WHERE ROWNUM <= 1.5", "oracle", with_schema=False)
    assert r.status == "ERROR" and "ROWNUM" in codes(r)


# ---------------------------------------------------------------- #27-2 / #27-3: UPSERT and FETCH FIRST
MERGE = ("MERGE INTO customers t USING (SELECT 1 id, 'x' name FROM dual) s ON ({on}) "
         "WHEN MATCHED THEN UPDATE SET t.name = s.name{update_where} "
         "WHEN NOT MATCHED THEN INSERT (id, name) VALUES ({values})")


def merge(on="t.id = s.id", values="s.id, s.name", update_where="", ddl=BIND_DDL):
    return run(ddl + MERGE.format(on=on, values=values, update_where=update_where), "oracle", with_schema=False)


def test_a_keyed_merge_with_matching_branches_is_an_upsert():
    r = merge()
    assert r.status == "WARN" and r.converted[0] == "UPSERT INTO customers (id, name) VALUES (1, 'x')"


def test_a_merge_whose_branches_write_different_values_is_refused():
    """Regression: `UPDATE SET name = s.name` / `INSERT ... VALUES (s.id, 'default')` became one UPSERT writing
    'default', reported as "both branches set the same columns" -- an existing row lost its name."""
    r = merge(values="s.id, 'default'")
    assert r.status == "ERROR" and "different values to 'name'" in " ".join(i.message for i in r.issues)


@pytest.mark.parametrize("kwargs,said", [
    ({"on": "t.email = s.id"}, "not the primary key"),
    ({"on": "t.id = s.id AND t.code = 'A'"}, "not 'target.key = source.col'"),
    ({"on": "t.id > s.id"}, "not 'target.key = source.col'"),
    ({"values": "99, s.name"}, "inserts 99 into it"),
    ({"update_where": " WHERE t.code = 'A'"}, "conditional"),
])
def test_a_merge_upsert_cannot_express_is_refused(kwargs, said):
    r = merge(**kwargs)
    assert r.status == "ERROR" and said in " ".join(i.message for i in r.issues)


def test_a_conditional_when_matched_is_refused():
    ddl = "CREATE TABLE acct (id INT PRIMARY KEY, email TEXT, bal INT);"
    r = run(ddl + "MERGE INTO acct t USING (SELECT 1 AS id, 5 AS bal) s ON t.id = s.id "
                  "WHEN MATCHED AND t.bal < 5 THEN UPDATE SET bal = s.bal "
                  "WHEN NOT MATCHED THEN INSERT (id, bal) VALUES (s.id, s.bal)", "postgres", with_schema=False)
    assert r.status == "ERROR" and "conditional" in " ".join(i.message for i in r.issues)


def test_without_the_table_definition_the_merge_key_is_an_assumption_that_is_stated():
    r = merge(ddl="")
    assert r.status == "WARN" and "assumed to be the primary key" in " ".join(i.message for i in r.issues)


ACCT = "CREATE TABLE acct (id INT PRIMARY KEY, email TEXT, bal INT);"


def test_on_conflict_on_the_primary_key_is_an_upsert():
    r = run(ACCT + "INSERT INTO acct (id, bal) VALUES (1, 2) ON CONFLICT (id) DO UPDATE SET bal = EXCLUDED.bal",
            "postgres", with_schema=False)
    assert r.status in ("OK", "WARN") and r.converted[0].startswith("UPSERT INTO acct")


@pytest.mark.parametrize("sql,said", [
    ("INSERT INTO acct (id, email, bal) VALUES (1, 'x', 2) ON CONFLICT (email) DO UPDATE SET bal = EXCLUDED.bal",
     "is not the primary key"),
    ("INSERT INTO acct (id, bal) VALUES (1, 2) ON CONFLICT (id) DO UPDATE SET bal = EXCLUDED.bal WHERE acct.bal < 5",
     "updates conditionally"),
])
def test_on_conflict_upsert_cannot_express_is_refused(sql, said):
    """Regression: both came out as a plain UPSERT with status OK."""
    r = run(ACCT + sql, "postgres", with_schema=False)
    assert r.status == "ERROR" and said in " ".join(i.message for i in r.issues)


def test_fetch_first_with_ties_is_not_a_limit():
    r = run(BIND_DDL + "SELECT id, name FROM customers ORDER BY name FETCH FIRST 3 ROWS WITH TIES", "oracle",
            with_schema=False)
    assert r.status == "ERROR" and "LIMIT" in codes(r)


def test_fetch_first_row_only_means_one_row():
    """Regression: no count was emitted as a bare `LIMIT`, with status OK."""
    r = run(BIND_DDL + "SELECT id, name FROM customers WHERE id = 1 FETCH FIRST ROW ONLY", "oracle",
            with_schema=False)
    assert r.converted[0].endswith("LIMIT 1")


# ---------------------------------------------------------------- #27-4..7: joins, types, identifiers, string semantics
JOIN_DDL = ("CREATE TABLE a (id INT PRIMARY KEY, x TEXT); CREATE TABLE b (id INT PRIMARY KEY, aid INT, x TEXT); "
            "CREATE TABLE c (id INT PRIMARY KEY, bid INT);")


def messages(r, code=None):
    return " | ".join(i.message for i in r.issues if code is None or i.code == code)


def test_a_right_join_makes_every_earlier_table_nullable():
    """Regression: only the FROM table was marked. `WHERE b.x IS NULL` went into b's fetch, the b rows with a value
    were never loaded, and their c rows came back as unmatched."""
    results, _ = convert_script(JOIN_DDL + "SELECT UPPER(c.id), b.x FROM a JOIN b ON a.id = b.aid "
                                           "RIGHT JOIN c ON c.bid = b.id WHERE b.x IS NULL", "postgres")
    fetch = {f["table"]: f for f in results[-1].plan["fetch"]}
    assert fetch["b"]["predicates"] == [] and fetch["a"]["predicates"] == []


def test_a_left_join_still_pushes_a_null_test_on_the_kept_side():
    results, _ = convert_script(JOIN_DDL + "SELECT UPPER(a.x), b.x FROM a LEFT JOIN b ON a.id = b.aid "
                                           "WHERE a.x IS NULL AND b.x IS NULL", "postgres")
    fetch = {f["table"]: f for f in results[-1].plan["fetch"]}
    assert [p["op"] for p in fetch["a"]["predicates"]] == ["IS NULL"] and fetch["b"]["predicates"] == []


@pytest.mark.parametrize("join,column", [("RIGHT JOIN", "b.id"), ("LEFT JOIN", "a.id"), ("JOIN", "a.id")])
def test_the_merged_using_column_is_the_side_whose_rows_are_kept(join, column):
    """Regression: always the FROM table, so after a RIGHT JOIN it was NULL for every unmatched right row."""
    r = run(JOIN_DDL + f"SELECT id FROM a {join} b USING (id)", "postgres", with_schema=False)
    assert r.converted[0].startswith(f"SELECT {column} FROM a")


@pytest.mark.parametrize("dialect,declared,mapped,severity,said", [
    ("postgres", "NUMERIC", "DOUBLE", "WARN", "unconstrained NUMERIC"),      # was BIGINT, "exact, fits 64-bit"
    ("mysql", "DECIMAL", "BIGINT", "INFO", "exact"),                          # MySQL really does default to (10,0)
    ("oracle", "INTEGER", "BIGINT", "WARN", "NUMBER(38)"),                    # was a 32-bit INT, no note
    ("oracle", "SMALLINT", "BIGINT", "WARN", "NUMBER(38)"),
    ("oracle", "FLOAT", "DOUBLE", "WARN", "up to 38 digits"),                 # was a 32-bit FLOAT, no note
    ("oracle", "TIMESTAMP", "TIMESTAMP", "WARN", "default precision is 6"),   # only an explicit (6) used to warn
    ("postgres", "TIMESTAMPTZ", "TIMESTAMPTZ", "WARN", "millisecond"),
    ("mysql", "DATETIME", "TIMESTAMP", "INFO", ""),                           # MySQL's default is whole seconds
    ("oracle", "TIMESTAMP(3)", "TIMESTAMP", "INFO", ""),
    ("postgres", "TIMETZ", "TIME", "WARN", "offset is dropped"),
    ("mysql", "INT UNSIGNED", "BIGINT", "INFO", "fits"),                      # the message said it "exceeds" BIGINT
    ("mysql", "BIGINT UNSIGNED", "BIGINT", "WARN", "exceeds"),
    ("oracle", "CHAR(3)", "TEXT", "WARN", "blank-padded"),
    ("oracle", "CHAR(1)", "TEXT", "INFO", ""),
])
def test_type_mappings_that_lose_data_say_so(dialect, declared, mapped, severity, said):
    r = run(f"CREATE TABLE t (id INT PRIMARY KEY, c {declared})", dialect, with_schema=False)
    assert f"c {mapped}" in r.converted[0]
    issue = next((i for i in r.issues if i.code == "TYPE" and i.message.startswith("column c")), None)
    assert (issue.severity if issue else "INFO") == severity
    assert said in (issue.message if issue else "")


def test_an_identifier_whose_quotes_carried_meaning_is_not_reported_ok():
    """Regression: `"order"` and `"UnitPrice"` came out unquoted with status OK."""
    r = run('CREATE TABLE "Items" ("order" INT PRIMARY KEY, "UnitPrice" TEXT, "status" TEXT)', "postgres", with_schema=False)
    said = messages(r, "IDENT")
    assert r.status == "WARN"
    assert '"order" is a SQL keyword' in said and '"UnitPrice" is case-sensitive' in said and '"Items" is case-sensitive' in said
    assert '"status"' not in said, "an ORM that quotes everything is not a finding"


def test_one_table_spelled_two_ways_is_flagged():
    results, _ = convert_script("CREATE TABLE customers (id INT PRIMARY KEY); SELECT id FROM Customers WHERE id = 1",
                                "postgres", decompose=False)
    assert "also written 'customers'" in messages(results[-1], "IDENT")


def test_an_oracle_like_pattern_keeps_its_backslash():
    """Oracle has no default LIKE escape character; ScalarDB's is a backslash (its grammar reference), and
    `ESCAPE ''` turns that off."""
    ddl = "CREATE TABLE t (id NUMBER(9) PRIMARY KEY, name VARCHAR2(20));"
    assert run(ddl + r"SELECT id FROM t WHERE id = 1 AND name LIKE 'a\_b%'", "oracle", with_schema=False) \
        .converted[0].endswith(r"LIKE 'a\_b%' ESCAPE ''")
    assert run(ddl + "SELECT id FROM t WHERE id = 1 AND name LIKE :p", "oracle", with_schema=False) \
        .converted[0].endswith("LIKE :p ESCAPE ''")
    assert run(ddl + "SELECT id FROM t WHERE id = 1 AND name LIKE 'ab%'", "oracle", with_schema=False) \
        .converted[0].endswith("LIKE 'ab%'"), "no backslash: both read it the same way"
    assert run(ddl + r"SELECT id FROM t WHERE id = 1 AND name LIKE 'a!_b' ESCAPE '!'", "oracle", with_schema=False) \
        .converted[0].endswith("ESCAPE '!'")
    assert "ESCAPE" not in run("CREATE TABLE t (id INT PRIMARY KEY, name TEXT);"
                               r"SELECT id FROM t WHERE id = 1 AND name LIKE 'a\_b%'", "postgres",
                               with_schema=False).converted[0], "PostgreSQL's default escape is a backslash too"


def test_an_empty_string_in_a_converted_oracle_statement_is_a_warning():
    """Regression: the note existed, but only for statements that were *not* converted."""
    ddl = "CREATE TABLE t (id NUMBER(9) PRIMARY KEY, name VARCHAR2(20));"
    r = run(ddl + "INSERT INTO t (id, name) VALUES (1, '')", "oracle", with_schema=False)
    assert r.status == "WARN" and "'' is NULL in Oracle" in messages(r, "SEMANTICS")
    assert "SEMANTICS" not in codes(run(ddl + "INSERT INTO t (id, name) VALUES (1, 'x')", "oracle", with_schema=False))
    assert "SEMANTICS" not in codes(run("CREATE TABLE t (id INT PRIMARY KEY, name TEXT);"
                                        "INSERT INTO t (id, name) VALUES (1, '')", "postgres", with_schema=False))


def test_mysql_string_comparisons_mention_the_collation_without_changing_the_status():
    r = run("SELECT customer_id FROM orders WHERE customer_id = 1 AND order_no = 2 AND status = 'open'", "mysql")
    note = next(i for i in r.issues if i.code == "SEMANTICS")
    assert note.severity == "INFO" and "collation" in note.message


# ---------------------------------------------------------------- #27-41: one statement must not end the file
def test_a_statement_the_converter_chokes_on_is_one_error_not_the_end_of_the_file(monkeypatch):
    from scalardb_migrate import converter

    real = converter.StatementConverter.select

    def select(self, node):
        if "boom" in node.sql():
            raise AttributeError("'NoneType' object has no attribute 'sql'")
        return real(self, node)

    monkeypatch.setattr(converter.StatementConverter, "select", select)
    results, _ = convert_script("CREATE TABLE t (id INT PRIMARY KEY); SELECT boom FROM t WHERE id = 1; "
                                "SELECT id FROM t WHERE id = 2", "postgres", decompose=False)
    assert [r.status for r in results] == ["OK", "ERROR", "OK"]
    assert results[1].kind == "INTERNAL_ERROR" and "AttributeError" in messages(results[1], "INTERNAL")
    assert [r.index for r in results] == [1, 2, 3]


def test_a_script_the_tokenizer_cannot_split_is_one_error():
    results, _ = convert_script("SELECT 'unterminated FROM t;\nSELECT 1;", "oracle", decompose=False)
    assert len(results) == 1 and results[0].status == "ERROR" and "TOKENIZE" in codes(results[0])


def test_drop_and_truncate_of_several_tables_name_every_table():
    """`DROP TABLE a, b` raised AttributeError; `TRUNCATE TABLE a, b` silently truncated only `a`."""
    drop = run("DROP TABLE acct, other", "postgres", with_schema=False)
    assert drop.converted == ["DROP TABLE acct", "DROP TABLE other"]
    truncate = run("TRUNCATE TABLE acct, other", "postgres", with_schema=False)
    assert truncate.converted == ["TRUNCATE TABLE acct", "TRUNCATE TABLE other"]


def test_a_replace_with_a_comment_above_it_is_still_a_replace():
    r = run("-- refresh the row\n/* block */ REPLACE INTO orders (customer_id, order_no, status) VALUES (1, 2, 'x')", "mysql")
    assert r.converted and r.converted[0].startswith("UPSERT INTO orders") and "REPLACE" in codes(r)


# ---------------------------------------------------------------- review #27: 7a-7d
T_DDL = ("CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(10), d DATE, ts TIMESTAMP, tz TIMESTAMP WITH TIME ZONE); "
         "CREATE TABLE u (id INT PRIMARY KEY, tid INT, v VARCHAR(5)); ")


def run_t(sql, dialect="oracle", decompose=False):
    results, _ = convert_script(T_DDL + sql, dialect, decompose=decompose)
    return results[-1]


@pytest.mark.parametrize("call,literal", [
    ("TO_DATE('15/01/2024','DD/MM/YYYY')", "'2024-01-15'"),
    ("TO_DATE('20240115','YYYYMMDD')", "'2024-01-15'"),
    ("TO_DATE('15-01-2024 10:11:12','DD-MM-YYYY HH24:MI:SS')", "'2024-01-15 10:11:12'"),
])
def test_a_formatted_date_is_written_in_iso_not_as_it_stands(call, literal):
    """'15/01/2024' used to be passed through, which ScalarDB cannot read as a date."""
    r = run_t(f"SELECT id FROM t WHERE ts = {call}")
    assert r.status != "ERROR", r.issues
    assert literal.strip("'") in r.converted[0] and "15/01" not in r.converted[0]


def test_a_format_that_cannot_be_applied_is_refused():
    r = run_t("SELECT id FROM t WHERE d = TO_DATE('15-JAN-24','DD-MON-RR')")
    assert r.status == "ERROR" and "DATE_FMT" in codes(r)


def test_a_date_without_a_format_is_refused_unless_it_is_iso():
    assert "DATE_FMT" in codes(run_t("SELECT id FROM t WHERE d = TO_DATE('15/01/2024')"))
    ok = run_t("SELECT id FROM t WHERE d = TO_DATE('2024-01-15')")
    assert ok.status == "WARN" and "'2024-01-15'" in ok.converted[0]


def test_a_date_only_literal_is_padded_for_timestamptz_too():
    r = run_t("SELECT id FROM t WHERE tz = DATE '2024-01-15'")
    assert "'2024-01-15 00:00:00 Z'" in r.converted[0] and "TZ_ASSUMED_UTC" in codes(r)


def test_a_timestamptz_literal_is_written_the_one_way_scalardb_reads_it():
    """Checked against ScalarDB Cluster: 'YYYY-MM-DD HH:MM[:SS[.FFF]] Z' and nothing else. Without a zone, with a
    `T`, or with +09:00 the statement was OK here and "could not be parsed" when it ran."""
    r = run_t("SELECT id FROM t WHERE tz > TIMESTAMP '2024-01-15 09:30:00Z'")
    assert "'2024-01-15 09:30:00 Z'" in r.converted[0] and "TZ_ASSUMED_UTC" not in codes(r)
    r = run_t("SELECT id FROM t WHERE tz > TIMESTAMP '2024-01-15 09:30:00.123 +09:00'")
    assert "'2024-01-15 00:30:00.123 Z'" in r.converted[0] and r.status != "ERROR"
    r = run_t("SELECT id FROM t WHERE tz > TIMESTAMP '2024-01-01 03:30:00 -05:30'")
    assert "'2024-01-01 09:00:00 Z'" in r.converted[0]
    # no zone: the source reads it in the session's zone, which is not visible here -- said, not guessed silently
    r = run_t("SELECT id FROM t WHERE tz > TIMESTAMP '2024-01-15 09:30:00'")
    assert r.status == "WARN" and "TZ_ASSUMED_UTC" in codes(r) and "'2024-01-15 09:30:00 Z'" in r.converted[0]


def test_a_zone_is_dropped_for_a_column_that_has_none():
    from scalardb_migrate.types import fit_temporal_literal
    assert fit_temporal_literal("TIMESTAMP", "2024-01-15 09:30:00 +09:00") == ("2024-01-15 09:30:00", "zone_dropped")
    assert fit_temporal_literal("DATE", "2024-01-15 00:00:00Z") == ("2024-01-15", "zone_dropped")
    assert fit_temporal_literal("TEXT", "2024-01-15 09:30:00Z") == ("2024-01-15 09:30:00Z", None)
    assert fit_temporal_literal("TIMESTAMPTZ", "2024-01-15 09:30 Z") == ("2024-01-15 09:30 Z", None)


def test_the_plan_applies_the_format_and_fits_timestamptz():
    from scalardb_migrate.decomposer import Predicate, _fit_temporal, _literal_value
    import sqlglot
    e = sqlglot.parse_one("SELECT TO_DATE('15/01/2024','DD/MM/YYYY')", read="oracle").expressions[0]
    assert _literal_value(e) == "2024-01-15"
    fitted = _fit_temporal(Predicate("tz", ">=", "2024-01-15"), {"tz": "TIMESTAMPTZ"})
    assert fitted.value == "2024-01-15 00:00:00 Z"
    # a real time of day against a DATE column is not rounded: that would move the bound of the fetch
    assert _fit_temporal(Predicate("d", "<", "2024-01-15 10:00:00"), {"d": "DATE"}).value == "2024-01-15 10:00:00"


def test_select_star_keeps_its_column_order_when_the_join_is_swapped():
    r = run_t("SELECT * FROM t JOIN u ON t.id = u.tid WHERE u.id = 3")
    out = r.converted[0]
    assert out.startswith("SELECT t.id, t.name, t.d, t.ts, t.tz, u.id, u.tid, u.v FROM u JOIN t")


def test_select_star_is_not_swapped_without_the_schema():
    results, _ = convert_script("SELECT * FROM t JOIN u ON t.id = u.tid WHERE u.id = 3", "oracle", decompose=False)
    assert "JOIN_ORDER" not in codes(results[-1])
    assert not any(c.startswith("SELECT * FROM u") for c in results[-1].converted)


@pytest.mark.parametrize("dialect,sql,code,status", [
    ("postgres", "SELECT id FROM ONLY t WHERE id = 1", "ONLY", "WARN"),
    ("postgres", "SELECT id FROM t TABLESAMPLE BERNOULLI (10) WHERE id = 1", "CLAUSE", "ERROR"),
    ("mysql", "SELECT id FROM t USE INDEX (ix) WHERE id = 1", "HINT", "OK"),
    ("mysql", "SELECT SQL_CALC_FOUND_ROWS id FROM t WHERE id = 1", "MODIFIER", "WARN"),
    ("oracle", "SELECT /*+ INDEX(t ix) */ id FROM t WHERE id = 1", "HINT", "OK"),
])
def test_source_only_syntax_never_reaches_the_output(dialect, sql, code, status):
    ddl = T_DDL.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMP")
    results, _ = convert_script(ddl + sql, dialect, decompose=False)
    r = results[-1]
    assert r.status == status and code in codes(r), r.issues
    assert r.converted in ([], ["SELECT id FROM t WHERE id = 1"])


def test_updating_a_primary_key_column_is_refused():
    r = run_t("UPDATE t SET id = 5 WHERE id = 1")
    assert r.status == "ERROR" and "PK_UPDATE" in codes(r)
    assert run_t("UPDATE t SET name = 'x' WHERE id = 1").status == "OK"
