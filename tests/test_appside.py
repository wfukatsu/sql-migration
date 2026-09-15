"""Application-side analysis: every construct that moves to the application, plans H2 cannot run, date-literal
pushdown, key feeds on Cassandra, semantics to keep, cost estimates, and how the CLI and the skill report them."""

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from scalardb_migrate import cli
from scalardb_migrate.appside import estimate_cost, parse_expected_rows
from scalardb_migrate.converter import convert_script

logging.getLogger("sqlglot").setLevel(logging.ERROR)
ROOT = Path(__file__).resolve().parents[1]

AREA_SQL = """
WITH area_hierarchy AS (
    SELECT node_id AS shop_or_area_id, parent_id, node_name,
           SYS_CONNECT_BY_PATH(node_name, ' > ') AS area_path, LEVEL AS hierarchy_level
    FROM organization_master
    START WITH parent_id IS NULL
    CONNECT BY PRIOR node_id = parent_id
),
monthly_sales AS (
    SELECT shop_id, TO_CHAR(sales_date, 'YYYY-MM') AS sales_month, SUM(amount) AS total_amount
    FROM sales_transactions
    WHERE sales_date >= DATE '2026-01-01' AND sales_date < DATE '2027-01-01'
    GROUP BY shop_id, TO_CHAR(sales_date, 'YYYY-MM')
)
SELECT h.area_path, s.sales_month, s.total_amount,
       ROUND((s.total_amount / LAG(s.total_amount, 1) OVER (PARTITION BY s.shop_id ORDER BY s.sales_month)) * 100, 2) AS mom,
       DENSE_RANK() OVER (PARTITION BY h.parent_id, s.sales_month ORDER BY s.total_amount DESC) AS rank_in_area
FROM area_hierarchy h
INNER JOIN monthly_sales s ON h.shop_or_area_id = s.shop_id
WHERE h.hierarchy_level = 3
ORDER BY h.area_path, s.sales_month;
"""
AREA_DDL = """
CREATE TABLE organization_master (node_id NUMBER(18) PRIMARY KEY, parent_id NUMBER(18), node_name VARCHAR2(200));
CREATE TABLE sales_transactions (shop_id NUMBER(18), sales_date TIMESTAMP, order_id NUMBER(18), amount NUMBER(18),
  PRIMARY KEY (shop_id, sales_date, order_id));
"""
PLANNABLE = ("WITH m AS (SELECT shop_id, SUM(amount) AS t FROM sales_transactions "
             "WHERE sales_date >= DATE '2026-01-01' AND sales_date < DATE '2027-01-01' GROUP BY shop_id) "
             "SELECT shop_id, t, RANK() OVER (ORDER BY t DESC) AS r FROM m;")
ORDERS_DDL = """
CREATE TABLE customers (customer_id BIGINT PRIMARY KEY, region VARCHAR(10), name VARCHAR(20));
CREATE INDEX ix_region ON customers (region);
CREATE TABLE orders (customer_id BIGINT, order_no BIGINT, amount BIGINT, PRIMARY KEY (customer_id, order_no));
"""


def last(sql: str, **kw):
    results, _ = convert_script(sql, "oracle", **kw)
    return results[-1]


def codes(r, severity=None):
    return [i.code for i in r.issues if severity is None or i.severity == severity]


def messages(r, code):
    return [i.message for i in r.issues if i.code == code]


def test_inventory_lists_every_construct_not_only_the_first():
    r = last(AREA_SQL, decompose=False)
    assert r.status == "ERROR"
    assert {"CTE", "HIERARCHICAL", "WINDOW", "PROJECTION", "GROUP"} <= set(codes(r, "ERROR"))
    text = " ".join(i.message for i in r.issues)
    assert "area_hierarchy" in text and "CTE monthly_sales" in text and "LAG" in text and "DENSE_RANK" in text
    assert "TO_CHAR(sales_date, 'YYYY-MM')" in text  # rendered in the source dialect, not as CAST(... AS TEXT)


def test_plan_is_rejected_when_h2_cannot_run_connect_by():
    r = last(AREA_SQL)
    assert r.status == "ERROR" and r.plan is None
    assert any("CONNECT BY" in m for m in messages(r, "RESIDUAL_H2"))


@pytest.mark.parametrize("sql,construct", [
    ("SELECT deptno, SUM(sal) AS s FROM emp GROUP BY ROLLUP (deptno)", "ROLLUP"),
    ("SELECT * FROM (SELECT deptno, job, sal FROM emp) PIVOT (SUM(sal) FOR job IN ('A' AS a))", "PIVOT"),
    ("SELECT deptno, MAX(sal) KEEP (DENSE_RANK FIRST ORDER BY hiredate) AS s FROM emp GROUP BY deptno", "KEEP"),
])
def test_other_constructs_h2_lacks_are_not_planned(sql, construct):
    r = last(sql)
    assert r.status == "ERROR" and any(construct in m for m in messages(r, "RESIDUAL_H2")), r.issues


def test_date_literals_in_a_cte_are_pushed_into_the_fetch():
    r = last(AREA_DDL + PLANNABLE)
    assert r.status == "PLANNED", r.issues
    assert r.plan["fetch"][0]["scalardb_sql"].endswith(
        "WHERE sales_date >= '2026-01-01 00:00:00' AND sales_date < '2027-01-01 00:00:00'")
    assert r.plan["transaction"] == {"read_only": True}
    assert r.plan["recommended_config"]["scalar.db.scan_fetch_size"] == 1000
    assert "APP_SEMANTICS" not in codes(r)  # H2 runs the original SQL and keeps the semantics itself


def test_cassandra_full_scan_suggests_fetching_by_joined_keys():
    q = "SELECT c.name, SUM(o.amount) * 2 AS s FROM customers c JOIN orders o ON o.customer_id = c.customer_id " \
        "WHERE c.region = 'X' GROUP BY c.name"
    r = last(ORDERS_DDL + q, storage="cassandra")
    full = messages(r, "FULL_SCAN")
    assert len(full) == 1
    assert "read customers first, then orders with one partition scan per customers.customer_id value" in full[0]


def test_cassandra_key_feed_is_not_circular_and_every_table_is_reported():
    full = messages(last(AREA_DDL + AREA_SQL, storage="cassandra"), "FULL_SCAN")
    assert len(full) == 2
    assert all("cannot be fetched by key either" in m and "fetch by key instead" not in m for m in full)


def test_semantics_to_keep_are_listed_for_app_side_statements():
    notes = " ".join(messages(last(AREA_SQL, decompose=False), "APP_SEMANTICS"))
    for expected in ("LAG / LEAD", "ORA-01476", "half away from zero", "NULLs sort last for ASC", "NLS_SORT",
                     "ORA-30004", "ignore NULLs"):
        assert expected in notes


def test_design_advice_names_summary_and_hierarchy_tables():
    advice = " ".join(messages(last(AREA_SQL, decompose=False), "DESIGN"))
    assert "summary table keyed by (shop_id, sales_month)" in advice
    assert "precompute the hierarchy" in advice
    assert "table definitions unknown" in advice


def test_cost_estimates_and_guardrails():
    assert parse_expected_rows(["orders=5_000_000:50"]) == {"orders": (5_000_000, 50)}
    out = estimate_cost([("sales", "CROSS_PARTITION")], parse_expected_rows(["sales=3000000"]), "SERIALIZABLE", 10_000)
    assert {"ROW_LIMIT", "COST_DEADLINE"} <= {c for _, c, _ in out}  # 3M x 25 us x 2 = 150 s > 60 s
    snapshot = estimate_cost([("sales", "CROSS_PARTITION")], parse_expected_rows(["sales=1000000"]), "SNAPSHOT", None)
    assert any("~25.0 s" in m for _, _, m in snapshot)
    per_key = estimate_cost([("orders", "PARTITION_SCAN")], parse_expected_rows(["orders=5000000:40"]), "SNAPSHOT", None)
    assert any("~40 rows -> ~6 ms" in m for _, _, m in per_key)


def test_converted_cross_partition_select_gets_cost_and_settings():
    r = last(ORDERS_DDL + "SELECT * FROM orders WHERE amount > 10", expected_rows=parse_expected_rows(["orders=100000"]))
    assert r.status == "WARN" and {"COST", "CONFIG"} <= set(codes(r))


def test_cli_accepts_source_and_reports_application_side_work(tmp_path, capsys):
    src = tmp_path / "area.sql"
    src.write_text(AREA_SQL, encoding="utf-8")
    assert cli.main([str(src), "--source", "oracle", "--out-dir", str(tmp_path / "out")]) == 1
    md = (tmp_path / "out" / "area.report.md").read_text(encoding="utf-8")
    assert "## Application-side work" in md and "**Semantics the application must keep**" in md
    assert "not supported for expression" not in capsys.readouterr().err


def test_skill_plan_dir_report_sections_and_plan_comment(tmp_path):
    src = tmp_path / "q.sql"
    src.write_text(AREA_DDL + PLANNABLE + AREA_SQL, encoding="utf-8")
    p = subprocess.run([sys.executable, str(ROOT / "skills/sql-transpile/scripts/transpile.py"), str(src),
                        "--source", "oracle", "--target", "scalardb", "--out-dir", str(tmp_path / "out"),
                        "--plan-dir", str(tmp_path / "plans"), "--expected-rows", "sales_transactions=1000000"],
                       capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 1, p.stderr
    assert "PLANNED 1" in p.stdout and (tmp_path / "plans" / "q.3.plan.json").is_file()
    md = (tmp_path / "out" / "q.report.md").read_text(encoding="utf-8")
    assert "## アプリ側に移す処理" in md and "**結果を変えないための注意（意味の差）**" in md and "ROW_LIMIT" in md
    assert "[APP-SIDE PLAN #3]" in (tmp_path / "out" / "q.scalardb.sql").read_text(encoding="utf-8")
