-- NoSQL suitability case (Oracle dialect), docs/cassandra-verification-plan.md §4.2.
-- The same orders are stored twice: orders_rdb is keyed the RDBMS way (surrogate key order_id + secondary indexes),
-- orders_by_customer the NoSQL way (partition key customer_id, clustering key order_date, order_id). The same questions
-- are asked against both, on Oracle (baseline) and on ScalarDB with a PostgreSQL or a Cassandra backend.
-- Data: difftest/bench.py nosql_dataset() (40 orders per customer, status S0..S4, amount 1..10000, region R0..R9).
-- Annotations: see difftest/cases/bench.sql. @iterate literals are chosen so they never occur elsewhere in the statement.
CREATE TABLE customers (customer_id NUMBER(9) PRIMARY KEY, name VARCHAR2(20), region VARCHAR2(4));
CREATE INDEX idx_cust_region ON customers (region);
CREATE TABLE orders_rdb (order_id NUMBER(9) PRIMARY KEY, customer_id NUMBER(9), order_date DATE, status VARCHAR2(4), amount NUMBER(9), memo VARCHAR2(20));
CREATE INDEX idx_rdb_customer ON orders_rdb (customer_id);
CREATE INDEX idx_rdb_status ON orders_rdb (status);
CREATE TABLE orders_by_customer (customer_id NUMBER(9), order_date DATE, order_id NUMBER(9), status VARCHAR2(4), amount NUMBER(9), memo VARCHAR2(20), PRIMARY KEY (customer_id, order_date, order_id));
CREATE INDEX idx_obc_status ON orders_by_customer (status);

-- @bench: N1a point read by primary key (orders_rdb)
-- @iterate: 4242
SELECT customer_id, order_date, status, amount FROM orders_rdb WHERE order_id = 4242;

-- @bench: N1b point read by primary key (customers)
-- @iterate: 101
SELECT name, region FROM customers WHERE customer_id = 101;

-- @bench: N2 one customer's latest 10 orders since a date (NoSQL design: partition + clustering order)
-- @iterate: 101
SELECT order_id, order_date, amount FROM orders_by_customer WHERE customer_id = 101 AND order_date >= DATE '2024-01-01' ORDER BY order_date DESC, order_id DESC FETCH FIRST 10 ROWS ONLY;

-- @bench: N3 same question on the RDB design (index on customer_id + ORDER BY a non-key column)
-- @iterate: 101
SELECT order_id, order_date, amount FROM orders_rdb WHERE customer_id = 101 AND order_date >= DATE '2024-01-01' ORDER BY order_date DESC, order_id DESC FETCH FIRST 10 ROWS ONLY;

-- @bench: N4a one customer's order count and total (NoSQL design: one partition)
-- @iterate: 101
SELECT COUNT(*) AS n, SUM(amount) AS total FROM orders_by_customer WHERE customer_id = 101;

-- @bench: N4b one customer's order count and total (RDB design: secondary index)
-- @iterate: 101
SELECT COUNT(*) AS n, SUM(amount) AS total FROM orders_rdb WHERE customer_id = 101;

-- @bench: N5a secondary-index equality, 1/5 of the table (orders_rdb.status)
-- @compare: count
SELECT order_id, amount FROM orders_rdb WHERE status = 'S3';

-- @bench: N5b secondary-index equality, 1/5 of the table (orders_by_customer.status)
-- @compare: count
SELECT order_id, amount FROM orders_by_customer WHERE status = 'S3';

-- @bench: N5c secondary-index equality, 1/10 of a small table (customers.region)
SELECT customer_id, name FROM customers WHERE region = 'R3';

-- @bench: N6a non-indexed filter, about 1 % (orders_rdb.amount)
SELECT order_id, amount FROM orders_rdb WHERE amount > 9900;

-- @bench: N6b non-indexed filter, about 1 % (orders_by_customer.amount)
SELECT order_id, amount FROM orders_by_customer WHERE amount > 9900;

-- @bench: N7a top 10 by a non-key column across all partitions (orders_rdb)
SELECT order_id, amount FROM orders_rdb ORDER BY amount DESC, order_id FETCH FIRST 10 ROWS ONLY;

-- @bench: N7b top 10 by a non-key column across all partitions (orders_by_customer)
SELECT order_id, amount FROM orders_by_customer ORDER BY amount DESC, order_id FETCH FIRST 10 ROWS ONLY;

-- @bench: N8a full-table GROUP BY (orders_rdb by status)
SELECT status, COUNT(*) AS n, SUM(amount) AS total FROM orders_rdb GROUP BY status;

-- @bench: N8b full-table COUNT(*) (orders_by_customer)
SELECT COUNT(*) AS n FROM orders_by_customer;

-- @bench: N8c DISTINCT over the whole table (orders_rdb.customer_id)
-- @compare: count
SELECT DISTINCT customer_id FROM orders_rdb ORDER BY customer_id;

-- @bench: N9a join driven by one primary key (order -> customer)
-- @iterate: 4242
SELECT o.order_id, o.amount, c.name FROM orders_rdb o INNER JOIN customers c ON o.customer_id = c.customer_id WHERE o.order_id = 4242;

-- @bench: N9b join driven by one partition (a customer's orders -> customer)
-- @iterate: 101
SELECT o.order_id, o.amount, c.name FROM orders_by_customer o INNER JOIN customers c ON o.customer_id = c.customer_id WHERE o.customer_id = 101;

-- @bench: N10a join driven by a secondary index on both sides (region -> orders)
-- @compare: count
SELECT c.name, o.order_id, o.amount FROM customers c INNER JOIN orders_rdb o ON c.customer_id = o.customer_id WHERE c.region = 'R3';

-- @bench: N10b whole-table join + aggregation (sales by region)
SELECT c.region, COUNT(*) AS n, SUM(o.amount) AS total FROM customers c INNER JOIN orders_rdb o ON c.customer_id = o.customer_id GROUP BY c.region ORDER BY c.region;

-- @bench: N11a OFFSET paging over the whole table
SELECT order_id, amount FROM orders_rdb ORDER BY order_id OFFSET 1000 ROWS FETCH NEXT 10 ROWS ONLY;

-- @bench: N11b keyset paging inside one partition (next 10 orders before a date)
-- @iterate: 101
SELECT order_id, order_date, amount FROM orders_by_customer WHERE customer_id = 101 AND order_date < DATE '2023-01-01' ORDER BY order_date DESC, order_id DESC FETCH FIRST 10 ROWS ONLY;

-- @bench: N12a IN list of 5 partition keys (orders_by_customer)
SELECT customer_id, order_id, amount FROM orders_by_customer WHERE customer_id IN (101, 102, 103, 104, 105);

-- @bench: N12b IN list of 5 indexed values (orders_rdb.customer_id)
SELECT customer_id, order_id, amount FROM orders_rdb WHERE customer_id IN (101, 102, 103, 104, 105);

-- @bench: N13a single-row INSERT (orders_by_customer)
-- @iterate: 900001
INSERT INTO orders_by_customer (customer_id, order_date, order_id, status, amount, memo) VALUES (900001, DATE '2024-06-01', 900001, 'S0', 100, 'new');

-- @bench: N13b single-row UPDATE of a non-indexed column by primary key (orders_rdb)
-- @iterate: 4242
UPDATE orders_rdb SET memo = 'upd' WHERE order_id = 4242;

-- @bench: N13c single-row upsert (Oracle MERGE / ScalarDB UPSERT, orders_rdb)
-- @iterate: 900001
MERGE INTO orders_rdb t
USING (SELECT 900001 AS order_id, 7 AS customer_id, 'S0' AS status, 100 AS amount FROM dual) s
ON (t.order_id = s.order_id)
WHEN MATCHED THEN UPDATE SET t.customer_id = s.customer_id, t.status = s.status, t.amount = s.amount
WHEN NOT MATCHED THEN INSERT (order_id, customer_id, status, amount) VALUES (s.order_id, s.customer_id, s.status, s.amount);

-- @bench: N13d single-row DELETE by primary key (orders_by_customer, the rows N13a inserted)
-- @iterate: 900001
DELETE FROM orders_by_customer WHERE customer_id = 900001 AND order_date = DATE '2024-06-01' AND order_id = 900001;

-- @bench: N14 single-row UPDATE of an indexed column by primary key (orders_rdb.status)
-- @iterate: 4242
UPDATE orders_rdb SET status = 'S9' WHERE order_id = 4242;

-- @bench: N15 UPDATE by a non-key condition, about 1 % of the table (orders_rdb)
UPDATE orders_rdb SET memo = 'bulk' WHERE amount > 9900;
