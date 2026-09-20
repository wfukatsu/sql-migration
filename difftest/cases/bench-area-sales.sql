-- Benchmark case file (Oracle dialect): the area / shop monthly sales analysis
-- (docs/examples/area-sales-analysis-scalardb-conversion.md). Data: difftest/bench.py --dataset bench-area-sales.
-- ScalarDB SQL cannot run it and H2 cannot run CONNECT BY, so the new implementation runs it in the application:
--   "at"appside <class>        AppSideQuery implementation (runtime-java) run on the fetched rows
--   "at"fetch <table>=<SQL>    ScalarDB SQL fetching one input table; all fetches share one transaction
CREATE TABLE organization_master (node_id NUMBER(18) PRIMARY KEY, parent_id NUMBER(18), node_name VARCHAR2(200));
CREATE TABLE sales_transactions (shop_id NUMBER(18), sales_date TIMESTAMP, order_id NUMBER(18), amount NUMBER(18),
  PRIMARY KEY (shop_id, sales_date, order_id));

-- @bench: area / shop monthly sales (CONNECT BY + WITH + LAG / moving AVG / DENSE_RANK)
-- @appside: com.scalar.migrate.examples.AreaSalesReport
-- @fetch: organization_master=SELECT node_id, parent_id, node_name FROM organization_master
-- @fetch: sales_transactions=SELECT shop_id, sales_date, amount FROM sales_transactions WHERE sales_date >= '2026-01-01 00:00:00' AND sales_date < '2027-01-01 00:00:00'
WITH area_hierarchy AS (
    SELECT node_id AS shop_or_area_id, parent_id, node_name,
           SYS_CONNECT_BY_PATH(node_name, ' > ') AS area_path, LEVEL AS hierarchy_level
    FROM organization_master
    START WITH parent_id IS NULL
    CONNECT BY PRIOR node_id = parent_id
),
monthly_sales AS (
    SELECT shop_id, TO_CHAR(sales_date, 'YYYY-MM') AS sales_month, SUM(amount) AS total_amount, COUNT(order_id) AS total_orders
    FROM sales_transactions
    WHERE sales_date >= DATE '2026-01-01' AND sales_date < DATE '2027-01-01'
    GROUP BY shop_id, TO_CHAR(sales_date, 'YYYY-MM')
)
SELECT h.area_path, h.node_name AS shop_name, s.sales_month, s.total_amount,
       ROUND((s.total_amount / LAG(s.total_amount, 1) OVER (PARTITION BY s.shop_id ORDER BY s.sales_month)) * 100, 2) AS mom_ratio_pct,
       ROUND(AVG(s.total_amount) OVER (PARTITION BY s.shop_id ORDER BY s.sales_month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 0) AS moving_avg_3m,
       DENSE_RANK() OVER (PARTITION BY h.parent_id, s.sales_month ORDER BY s.total_amount DESC) AS rank_in_area
FROM area_hierarchy h
INNER JOIN monthly_sales s ON h.shop_or_area_id = s.shop_id
WHERE h.hierarchy_level = 3
ORDER BY h.area_path, s.sales_month;
