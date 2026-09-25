-- converted to scalardb
CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY,
  job_title TEXT,
  min_salary DOUBLE,
  max_salary DOUBLE
);

CREATE TABLE departments (
  department_id INT PRIMARY KEY,
  department_name TEXT,
  manager_id INT,
  location TEXT
);

CREATE TABLE employees (
  employee_id INT PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  email TEXT,
  hire_date DATE,
  job_id TEXT,
  salary DOUBLE,
  commission_pct DOUBLE,
  manager_id INT,
  department_id INT
);

CREATE INDEX ON employees (department_id);

CREATE INDEX ON employees (manager_id);

-- [NOT CONVERTED #6] CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)
-- CREATE SEQUENCE emp_seq   START WITH 300 INCREMENT BY 1 NOCACHE

-- [NOT CONVERTED #7] CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)
-- CREATE SEQUENCE order_seq START WITH 1   INCREMENT BY 1

CREATE TABLE orders (
  order_id DOUBLE PRIMARY KEY,
  order_date DATE,
  employee_id INT,
  customer TEXT,
  status TEXT,
  total DOUBLE
);

CREATE TABLE order_items (
  order_id DOUBLE,
  line_no INT,
  product TEXT,
  qty INT,
  unit_price DOUBLE,
  PRIMARY KEY (order_id, line_no)
);

/* ------------------------------------------------------------------------------ */ /* データ */ /* ------------------------------------------------------------------------------ */ INSERT INTO jobs VALUES ('AD_PRES', 'President', 20000, 40000);

INSERT INTO jobs VALUES ('AD_VP', 'Vice President', 15000, 30000);

INSERT INTO jobs VALUES ('IT_PROG', 'Programmer', 4000, 10000);

INSERT INTO jobs VALUES ('SA_MAN', 'Sales Manager', 10000, 20000);

INSERT INTO jobs VALUES ('SA_REP', 'Sales Representative', 6000, 12000);

INSERT INTO jobs VALUES ('ST_CLERK', 'Stock Clerk', 2000, 5000);

INSERT INTO departments VALUES (10, 'Administration', NULL, 'Tokyo');

INSERT INTO departments VALUES (60, 'IT', NULL, 'Osaka');

INSERT INTO departments VALUES (80, 'Sales', NULL, 'Tokyo');

INSERT INTO departments VALUES (50, 'Shipping', NULL, 'Nagoya');

INSERT INTO departments VALUES (90, 'Executive', NULL, 'Tokyo');

INSERT INTO departments VALUES (99, 'Research', NULL, 'Fukuoka');

/* 社員なし部門 */ INSERT INTO employees VALUES (100, 'Steven', 'King', 'SKING', '2013-06-17', 'AD_PRES', 24000, NULL, NULL, 90);

INSERT INTO employees VALUES (101, 'Neena', 'Kochhar', 'NKOCHHAR', '2015-09-21', 'AD_VP', 17000, NULL, 100, 90);

INSERT INTO employees VALUES (102, 'Lex', 'De Haan', 'LDEHAAN', '2011-01-13', 'AD_VP', 17000, NULL, 100, 90);

INSERT INTO employees VALUES (103, 'Alexander', 'Hunold', 'AHUNOLD', '2016-01-03', 'IT_PROG', 9000, NULL, 102, 60);

INSERT INTO employees VALUES (104, 'Bruce', 'Ernst', 'BERNST', '2017-05-21', 'IT_PROG', 6000, NULL, 103, 60);

INSERT INTO employees VALUES (107, 'Diana', 'Lorentz', 'DLORENTZ', '2019-02-07', 'IT_PROG', 4200, NULL, 103, 60);

INSERT INTO employees VALUES (145, 'John', 'Russell', 'JRUSSEL', '2014-10-01', 'SA_MAN', 14000, 0.40, 100, 80);

INSERT INTO employees VALUES (146, 'Karen', 'Partners', 'KPARTNER', '2015-01-05', 'SA_MAN', 13500, 0.30, 100, 80);

INSERT INTO employees VALUES (150, 'Peter', 'Tucker', 'PTUCKER', '2015-01-30', 'SA_REP', 10000, 0.30, 145, 80);

INSERT INTO employees VALUES (151, 'David', 'Bernstein', 'DBERNSTE', '2015-03-24', 'SA_REP', 9500, 0.25, 145, 80);

INSERT INTO employees VALUES (155, 'Oliver', 'Tuvault', 'OTUVAULT', '2017-11-23', 'SA_REP', 7000, 0.15, 146, 80);

INSERT INTO employees VALUES (120, 'Matthew', 'Weiss', 'MWEISS', '2014-07-18', 'ST_CLERK', 4800, NULL, 100, 50);

INSERT INTO employees VALUES (125, 'Julia', 'Nayer', 'JNAYER', '2015-07-16', 'ST_CLERK', 3200, NULL, 120, 50);

INSERT INTO employees VALUES (178, 'Kimberely', 'Grant', 'KGRANT', '2017-05-24', 'SA_REP', 7000, 0.15, 145, NULL);

/* 部門なし社員 */ INSERT INTO employees VALUES (200, 'Jennifer', 'Whalen', 'JWHALEN', '2013-09-17', 'AD_VP', 15000, NULL, 101, 10);

-- [NOT CONVERTED #37] SET manager_id = CASE d.department_id WHEN 10 THEN 200 WHEN 60 THEN 103 WHEN 80 THEN 145 WHEN 50 THEN 120 WHEN 90 THEN 100 END: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction
-- UPDATE departments d
--    SET manager_id = CASE d.department_id
--                       WHEN 10 THEN 200 WHEN 60 THEN 103 WHEN 80 THEN 145
--                       WHEN 50 THEN 120 WHEN 90 THEN 100 END

-- [NOT CONVERTED #38] INSERT must specify the full primary key; missing ['order_id']
-- INSERT INTO orders (order_date, employee_id, customer, status, total)
--   VALUES (DATE '2026-04-01', 150, 'Acme Corp',     'SHIPPED',  1200)

-- [NOT CONVERTED #39] INSERT must specify the full primary key; missing ['order_id']
-- INSERT INTO orders (order_date, employee_id, customer, status, total)
--   VALUES (DATE '2026-04-15', 151, 'Globex',        'NEW',       450)

-- [NOT CONVERTED #40] INSERT must specify the full primary key; missing ['order_id']
-- INSERT INTO orders (order_date, employee_id, customer, status, total)
--   VALUES (DATE '2026-05-02', 150, 'Initech',       'SHIPPED',  3000)

-- [NOT CONVERTED #41] INSERT must specify the full primary key; missing ['order_id']
-- INSERT INTO orders (order_date, employee_id, customer, status, total)
--   VALUES (DATE '2026-05-20', 155, 'Umbrella',      'CANCELLED', 800)

-- [NOT CONVERTED #42] INSERT must specify the full primary key; missing ['order_id']
-- INSERT INTO orders (order_date, employee_id, customer, status, total)
--   VALUES (DATE '2026-06-11', 145, 'Acme Corp',     'NEW',      2200)

INSERT INTO order_items VALUES (1, 1, 'ScalarDB License', 1, 1000);

INSERT INTO order_items VALUES (1, 2, 'Support', 1, 200);

INSERT INTO order_items VALUES (2, 1, 'Training', 3, 150);

INSERT INTO order_items VALUES (3, 1, 'ScalarDL License', 2, 1500);

INSERT INTO order_items VALUES (4, 1, 'Consulting', 4, 200);

INSERT INTO order_items VALUES (5, 1, 'ScalarDB License', 2, 1100);

COMMIT;

/* 統計情報収集（オプティマイザ用） */ SELECT table_name, num_rows FROM user_tables ORDER BY table_name;
