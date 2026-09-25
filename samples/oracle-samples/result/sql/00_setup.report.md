# SQL 変換レポート: oracle → scalardb

- 入力: `samples/oracle-samples/sql/00_setup.sql`
- 文数: 50　|　OK: 5　|　WARN: 37　|　ERROR: 8
- **変換率: 84.0%**（42 / 50 文が scalardb の SQL を出力できた）

| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |
|---|---|---|---|---|---|
| 1 | CREATE | ⚠️ WARN | `-------------------------------------------------------------------------------- -- 00_set…` | `CREATE TABLE jobs (   job_id TEXT PRIMARY KEY,   job_title TEXT,   min_salary DOUBLE,   ma…` | **INFO** TYPE: column job_id: VARCHAR2(10): length limit is not enforced by ScalarDB TEXT<br>**INFO** TYPE: column job_title: VARCHAR2(35): length limit is not enforced by ScalarDB TEXT<br>**INFO** NOT_NULL: column job_title: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**WARN** TYPE: column min_salary: NUMBER(8, 2): ScalarDB has no DECIMAL type; mapped to DOUBLE (precision loss). For money, store a scaled integer (x10^2) in BIGINT instead<br>**WARN** TYPE: column max_salary: NUMBER(8, 2): ScalarDB has no DECIMAL type; mapped to DOUBLE (precision loss). For money, store a scaled integer (x10^2) in BIGINT instead |
| 2 | CREATE | ✅ OK | `CREATE TABLE departments (   department_id    NUMBER(4)     CONSTRAINT dept_pk PRIMARY KEY…` | `CREATE TABLE departments (   department_id INT PRIMARY KEY,   department_name TEXT,   mana…` | **INFO** TYPE: column department_id: NUMBER(4) -> INT (exact, fits 32-bit)<br>**INFO** TYPE: column department_name: VARCHAR2(30): length limit is not enforced by ScalarDB TEXT<br>**INFO** NOT_NULL: column department_name: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column manager_id: NUMBER(6) -> INT (exact, fits 32-bit)<br>**INFO** TYPE: column location: VARCHAR2(30): length limit is not enforced by ScalarDB TEXT |
| 3 | CREATE | ⚠️ WARN | `CREATE TABLE employees (   employee_id    NUMBER(6)     CONSTRAINT emp_pk PRIMARY KEY,   f…` | `CREATE TABLE employees (   employee_id INT PRIMARY KEY,   first_name TEXT,   last_name TEX…` | **INFO** TYPE: column employee_id: NUMBER(6) -> INT (exact, fits 32-bit)<br>**INFO** TYPE: column first_name: VARCHAR2(20): length limit is not enforced by ScalarDB TEXT<br>**INFO** TYPE: column last_name: VARCHAR2(25): length limit is not enforced by ScalarDB TEXT<br>**INFO** NOT_NULL: column last_name: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column email: VARCHAR2(25): length limit is not enforced by ScalarDB TEXT<br>**INFO** NOT_NULL: column email: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**WARN** UNIQUE: column email: UNIQUE dropped (a secondary index does not enforce uniqueness)<br>**WARN** TYPE: column hire_date: Oracle DATE carries a time-of-day component; use TIMESTAMP if the time part is used<br>**INFO** NOT_NULL: column hire_date: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column job_id: VARCHAR2(10): length limit is not enforced by ScalarDB TEXT<br>**INFO** NOT_NULL: column job_id: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**WARN** FK: column job_id: REFERENCES dropped (no referential integrity)<br>**WARN** TYPE: column salary: NUMBER(8, 2): ScalarDB has no DECIMAL type; mapped to DOUBLE (precision loss). For money, store a scaled integer (x10^2) in BIGINT instead<br>**WARN** CHECK: column salary: CHECK dropped; enforce in the application<br>**WARN** TYPE: column commission_pct: NUMBER(2, 2): ScalarDB has no DECIMAL type; mapped to DOUBLE (precision loss). For money, store a scaled integer (x10^2) in BIGINT instead<br>**INFO** TYPE: column manager_id: NUMBER(6) -> INT (exact, fits 32-bit)<br>**WARN** FK: column manager_id: REFERENCES dropped (no referential integrity)<br>**INFO** TYPE: column department_id: NUMBER(4) -> INT (exact, fits 32-bit)<br>**WARN** FK: column department_id: REFERENCES dropped (no referential integrity) |
| 4 | CREATE | ✅ OK | `CREATE INDEX emp_dept_ix ON employees (department_id)` | `CREATE INDEX ON employees (department_id)` | **INFO** INDEX: index name 'emp_dept_ix' dropped: ScalarDB identifies indexes by table + column |
| 5 | CREATE | ✅ OK | `CREATE INDEX emp_mgr_ix  ON employees (manager_id)` | `CREATE INDEX ON employees (manager_id)` | **INFO** INDEX: index name 'emp_mgr_ix' dropped: ScalarDB identifies indexes by table + column |
| 6 | CREATE | ❌ ERROR | `CREATE SEQUENCE emp_seq   START WITH 300 INCREMENT BY 1 NOCACHE` | — | **ERROR** DDL: CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 7 | CREATE | ❌ ERROR | `CREATE SEQUENCE order_seq START WITH 1   INCREMENT BY 1` | — | **ERROR** DDL: CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 8 | CREATE | ⚠️ WARN | `CREATE TABLE orders (   order_id     NUMBER        DEFAULT order_seq.NEXTVAL CONSTRAINT or…` | `CREATE TABLE orders (   order_id DOUBLE PRIMARY KEY,   order_date DATE,   employee_id INT,…` | **WARN** TYPE: column order_id: NUMBER: unconstrained NUMBER mapped to DOUBLE; exact decimal precision is lost<br>**WARN** DEFAULT: column order_id: DEFAULT order_seq.NEXTVAL dropped; the application must supply the value<br>**WARN** TYPE: column order_date: Oracle DATE carries a time-of-day component; use TIMESTAMP if the time part is used<br>**WARN** DEFAULT: column order_date: DEFAULT SYSDATE dropped; the application must supply the value<br>**INFO** NOT_NULL: column order_date: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column employee_id: NUMBER(6) -> INT (exact, fits 32-bit)<br>**WARN** FK: column employee_id: REFERENCES dropped (no referential integrity)<br>**INFO** TYPE: column customer: VARCHAR2(40): length limit is not enforced by ScalarDB TEXT<br>**INFO** NOT_NULL: column customer: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column status: VARCHAR2(10): length limit is not enforced by ScalarDB TEXT<br>**WARN** DEFAULT: column status: DEFAULT 'NEW' dropped; the application must supply the value<br>**WARN** CHECK: column status: CHECK dropped; enforce in the application<br>**WARN** TYPE: column total: NUMBER(10, 2): ScalarDB has no DECIMAL type; mapped to DOUBLE (precision loss). For money, store a scaled integer (x10^2) in BIGINT instead |
| 9 | CREATE | ⚠️ WARN | `CREATE TABLE order_items (   order_id    NUMBER      CONSTRAINT oi_order_fk REFERENCES ord…` | `CREATE TABLE order_items (   order_id DOUBLE,   line_no INT,   product TEXT,   qty INT,   …` | **WARN** TYPE: column order_id: NUMBER: unconstrained NUMBER mapped to DOUBLE; exact decimal precision is lost<br>**WARN** FK: column order_id: REFERENCES dropped (no referential integrity)<br>**INFO** TYPE: column line_no: NUMBER(3) -> INT (exact, fits 32-bit)<br>**INFO** TYPE: column product: VARCHAR2(40): length limit is not enforced by ScalarDB TEXT<br>**INFO** NOT_NULL: column product: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column qty: NUMBER(5) -> INT (exact, fits 32-bit)<br>**INFO** NOT_NULL: column qty: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**WARN** TYPE: column unit_price: NUMBER(8, 2): ScalarDB has no DECIMAL type; mapped to DOUBLE (precision loss). For money, store a scaled integer (x10^2) in BIGINT instead<br>**INFO** NOT_NULL: column unit_price: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** KEYS: primary key ['order_id', 'line_no']: first column 'order_id' used as partition key, ['line_no'] as clustering key(s). Review with --keys if a different split is needed |
| 10 | INSERT | ⚠️ WARN | `-------------------------------------------------------------------------------- -- データ --…` | `/* ------------------------------------------------------------------------------ */ /* デー…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 11 | INSERT | ⚠️ WARN | `INSERT INTO jobs VALUES ('AD_VP',   'Vice President',       15000, 30000)` | `INSERT INTO jobs VALUES ('AD_VP', 'Vice President', 15000, 30000)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 12 | INSERT | ⚠️ WARN | `INSERT INTO jobs VALUES ('IT_PROG', 'Programmer',            4000, 10000)` | `INSERT INTO jobs VALUES ('IT_PROG', 'Programmer', 4000, 10000)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 13 | INSERT | ⚠️ WARN | `INSERT INTO jobs VALUES ('SA_MAN',  'Sales Manager',        10000, 20000)` | `INSERT INTO jobs VALUES ('SA_MAN', 'Sales Manager', 10000, 20000)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 14 | INSERT | ⚠️ WARN | `INSERT INTO jobs VALUES ('SA_REP',  'Sales Representative',  6000, 12000)` | `INSERT INTO jobs VALUES ('SA_REP', 'Sales Representative', 6000, 12000)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 15 | INSERT | ⚠️ WARN | `INSERT INTO jobs VALUES ('ST_CLERK','Stock Clerk',           2000,  5000)` | `INSERT INTO jobs VALUES ('ST_CLERK', 'Stock Clerk', 2000, 5000)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 16 | INSERT | ⚠️ WARN | `INSERT INTO departments VALUES (10, 'Administration', NULL, 'Tokyo')` | `INSERT INTO departments VALUES (10, 'Administration', NULL, 'Tokyo')` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 17 | INSERT | ⚠️ WARN | `INSERT INTO departments VALUES (60, 'IT',             NULL, 'Osaka')` | `INSERT INTO departments VALUES (60, 'IT', NULL, 'Osaka')` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 18 | INSERT | ⚠️ WARN | `INSERT INTO departments VALUES (80, 'Sales',          NULL, 'Tokyo')` | `INSERT INTO departments VALUES (80, 'Sales', NULL, 'Tokyo')` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 19 | INSERT | ⚠️ WARN | `INSERT INTO departments VALUES (50, 'Shipping',       NULL, 'Nagoya')` | `INSERT INTO departments VALUES (50, 'Shipping', NULL, 'Nagoya')` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 20 | INSERT | ⚠️ WARN | `INSERT INTO departments VALUES (90, 'Executive',      NULL, 'Tokyo')` | `INSERT INTO departments VALUES (90, 'Executive', NULL, 'Tokyo')` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 21 | INSERT | ⚠️ WARN | `INSERT INTO departments VALUES (99, 'Research',       NULL, 'Fukuoka')` | `INSERT INTO departments VALUES (99, 'Research', NULL, 'Fukuoka')` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 22 | INSERT | ⚠️ WARN | `-- 社員なし部門  INSERT INTO employees VALUES (100,'Steven','King',    'SKING',   DATE '2013-06-…` | `/* 社員なし部門 */ INSERT INTO employees VALUES (100, 'Steven', 'King', 'SKING', '2013-06-17', '…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2013-06-17', 'YYYY-MM-DD') written as the plain literal '2013-06-17' |
| 23 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (101,'Neena','Kochhar',  'NKOCHHAR',DATE '2015-09-21','AD_VP'…` | `INSERT INTO employees VALUES (101, 'Neena', 'Kochhar', 'NKOCHHAR', '2015-09-21', 'AD_VP', …` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2015-09-21', 'YYYY-MM-DD') written as the plain literal '2015-09-21' |
| 24 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (102,'Lex','De Haan',    'LDEHAAN', DATE '2011-01-13','AD_VP'…` | `INSERT INTO employees VALUES (102, 'Lex', 'De Haan', 'LDEHAAN', '2011-01-13', 'AD_VP', 170…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2011-01-13', 'YYYY-MM-DD') written as the plain literal '2011-01-13' |
| 25 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (103,'Alexander','Hunold','AHUNOLD',DATE '2016-01-03','IT_PRO…` | `INSERT INTO employees VALUES (103, 'Alexander', 'Hunold', 'AHUNOLD', '2016-01-03', 'IT_PRO…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2016-01-03', 'YYYY-MM-DD') written as the plain literal '2016-01-03' |
| 26 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (104,'Bruce','Ernst',    'BERNST',  DATE '2017-05-21','IT_PRO…` | `INSERT INTO employees VALUES (104, 'Bruce', 'Ernst', 'BERNST', '2017-05-21', 'IT_PROG', 60…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2017-05-21', 'YYYY-MM-DD') written as the plain literal '2017-05-21' |
| 27 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (107,'Diana','Lorentz',  'DLORENTZ',DATE '2019-02-07','IT_PRO…` | `INSERT INTO employees VALUES (107, 'Diana', 'Lorentz', 'DLORENTZ', '2019-02-07', 'IT_PROG'…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2019-02-07', 'YYYY-MM-DD') written as the plain literal '2019-02-07' |
| 28 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (145,'John','Russell',   'JRUSSEL', DATE '2014-10-01','SA_MAN…` | `INSERT INTO employees VALUES (145, 'John', 'Russell', 'JRUSSEL', '2014-10-01', 'SA_MAN', 1…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2014-10-01', 'YYYY-MM-DD') written as the plain literal '2014-10-01' |
| 29 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (146,'Karen','Partners', 'KPARTNER',DATE '2015-01-05','SA_MAN…` | `INSERT INTO employees VALUES (146, 'Karen', 'Partners', 'KPARTNER', '2015-01-05', 'SA_MAN'…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2015-01-05', 'YYYY-MM-DD') written as the plain literal '2015-01-05' |
| 30 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (150,'Peter','Tucker',   'PTUCKER', DATE '2015-01-30','SA_REP…` | `INSERT INTO employees VALUES (150, 'Peter', 'Tucker', 'PTUCKER', '2015-01-30', 'SA_REP', 1…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2015-01-30', 'YYYY-MM-DD') written as the plain literal '2015-01-30' |
| 31 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (151,'David','Bernstein','DBERNSTE',DATE '2015-03-24','SA_REP…` | `INSERT INTO employees VALUES (151, 'David', 'Bernstein', 'DBERNSTE', '2015-03-24', 'SA_REP…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2015-03-24', 'YYYY-MM-DD') written as the plain literal '2015-03-24' |
| 32 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (155,'Oliver','Tuvault', 'OTUVAULT',DATE '2017-11-23','SA_REP…` | `INSERT INTO employees VALUES (155, 'Oliver', 'Tuvault', 'OTUVAULT', '2017-11-23', 'SA_REP'…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2017-11-23', 'YYYY-MM-DD') written as the plain literal '2017-11-23' |
| 33 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (120,'Matthew','Weiss',  'MWEISS',  DATE '2014-07-18','ST_CLE…` | `INSERT INTO employees VALUES (120, 'Matthew', 'Weiss', 'MWEISS', '2014-07-18', 'ST_CLERK',…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2014-07-18', 'YYYY-MM-DD') written as the plain literal '2014-07-18' |
| 34 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (125,'Julia','Nayer',    'JNAYER',  DATE '2015-07-16','ST_CLE…` | `INSERT INTO employees VALUES (125, 'Julia', 'Nayer', 'JNAYER', '2015-07-16', 'ST_CLERK', 3…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2015-07-16', 'YYYY-MM-DD') written as the plain literal '2015-07-16' |
| 35 | INSERT | ⚠️ WARN | `INSERT INTO employees VALUES (178,'Kimberely','Grant','KGRANT',  DATE '2017-05-24','SA_REP…` | `INSERT INTO employees VALUES (178, 'Kimberely', 'Grant', 'KGRANT', '2017-05-24', 'SA_REP',…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2017-05-24', 'YYYY-MM-DD') written as the plain literal '2017-05-24' |
| 36 | INSERT | ⚠️ WARN | `-- 部門なし社員 INSERT INTO employees VALUES (200,'Jennifer','Whalen','JWHALEN', DATE '2013-09-1…` | `/* 部門なし社員 */ INSERT INTO employees VALUES (200, 'Jennifer', 'Whalen', 'JWHALEN', '2013-09-…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column hire_date: TO_DATE('2013-09-17', 'YYYY-MM-DD') written as the plain literal '2013-09-17' |
| 37 | UPDATE | ❌ ERROR | `UPDATE departments d    SET manager_id = CASE d.department_id                       WHEN 1…` | — | **ERROR** RMW: SET manager_id = CASE d.department_id WHEN 10 THEN 200 WHEN 60 THEN 103 WHEN 80 THEN 145 WHEN 50 THEN 120 WHEN 90 THEN 100 END: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction |
| 38 | INSERT | ❌ ERROR | `INSERT INTO orders (order_date, employee_id, customer, status, total)   VALUES (DATE '2026…` | — | **INFO** DATE_LIT: VALUES column order_date: TO_DATE('2026-04-01', 'YYYY-MM-DD') written as the plain literal '2026-04-01'<br>**ERROR** PK: INSERT must specify the full primary key; missing ['order_id'] |
| 39 | INSERT | ❌ ERROR | `INSERT INTO orders (order_date, employee_id, customer, status, total)   VALUES (DATE '2026…` | — | **INFO** DATE_LIT: VALUES column order_date: TO_DATE('2026-04-15', 'YYYY-MM-DD') written as the plain literal '2026-04-15'<br>**ERROR** PK: INSERT must specify the full primary key; missing ['order_id'] |
| 40 | INSERT | ❌ ERROR | `INSERT INTO orders (order_date, employee_id, customer, status, total)   VALUES (DATE '2026…` | — | **INFO** DATE_LIT: VALUES column order_date: TO_DATE('2026-05-02', 'YYYY-MM-DD') written as the plain literal '2026-05-02'<br>**ERROR** PK: INSERT must specify the full primary key; missing ['order_id'] |
| 41 | INSERT | ❌ ERROR | `INSERT INTO orders (order_date, employee_id, customer, status, total)   VALUES (DATE '2026…` | — | **INFO** DATE_LIT: VALUES column order_date: TO_DATE('2026-05-20', 'YYYY-MM-DD') written as the plain literal '2026-05-20'<br>**ERROR** PK: INSERT must specify the full primary key; missing ['order_id'] |
| 42 | INSERT | ❌ ERROR | `INSERT INTO orders (order_date, employee_id, customer, status, total)   VALUES (DATE '2026…` | — | **INFO** DATE_LIT: VALUES column order_date: TO_DATE('2026-06-11', 'YYYY-MM-DD') written as the plain literal '2026-06-11'<br>**ERROR** PK: INSERT must specify the full primary key; missing ['order_id'] |
| 43 | INSERT | ⚠️ WARN | `INSERT INTO order_items VALUES (1, 1, 'ScalarDB License', 1, 1000)` | `INSERT INTO order_items VALUES (1, 1, 'ScalarDB License', 1, 1000)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 44 | INSERT | ⚠️ WARN | `INSERT INTO order_items VALUES (1, 2, 'Support',          1,  200)` | `INSERT INTO order_items VALUES (1, 2, 'Support', 1, 200)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 45 | INSERT | ⚠️ WARN | `INSERT INTO order_items VALUES (2, 1, 'Training',         3,  150)` | `INSERT INTO order_items VALUES (2, 1, 'Training', 3, 150)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 46 | INSERT | ⚠️ WARN | `INSERT INTO order_items VALUES (3, 1, 'ScalarDL License', 2, 1500)` | `INSERT INTO order_items VALUES (3, 1, 'ScalarDL License', 2, 1500)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 47 | INSERT | ⚠️ WARN | `INSERT INTO order_items VALUES (4, 1, 'Consulting',       4,  200)` | `INSERT INTO order_items VALUES (4, 1, 'Consulting', 4, 200)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 48 | INSERT | ⚠️ WARN | `INSERT INTO order_items VALUES (5, 1, 'ScalarDB License', 2, 1100)` | `INSERT INTO order_items VALUES (5, 1, 'ScalarDB License', 2, 1100)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 49 | COMMIT | ✅ OK | `COMMIT` | `COMMIT` |  |
| 50 | SELECT | ✅ OK | `-- 統計情報収集（オプティマイザ用）  SELECT table_name, num_rows FROM user_tables ORDER BY table_name` | `/* 統計情報収集（オプティマイザ用） */ SELECT table_name, num_rows FROM user_tables ORDER BY table_name` | **INFO** SCHEMA: SELECT: table definition unknown, access path (GET / SCAN / cross-partition) not analysed |

## 指摘の集計

| 重要度 | コード | 件数 |
|---|---|---|
| ERROR | PK | 5 |
| ERROR | DDL | 2 |
| ERROR | RMW | 1 |
| WARN | INSERT_COLS | 33 |
| WARN | TYPE | 10 |
| WARN | FK | 5 |
| WARN | DEFAULT | 3 |
| WARN | CHECK | 2 |
| WARN | UNIQUE | 1 |
| INFO | DATE_LIT | 20 |
| INFO | TYPE | 19 |
| INFO | NOT_NULL | 11 |
| INFO | INDEX | 2 |
| INFO | KEYS | 1 |
| INFO | SCHEMA | 1 |

## アプリ側に移す処理

### #6 ❌ ERROR `CREATE SEQUENCE emp_seq   START WITH 300 INCREMENT BY 1 NOCACHE`

**アプリ側で処理する構文**

- `DDL` CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)

### #7 ❌ ERROR `CREATE SEQUENCE order_seq START WITH 1   INCREMENT BY 1`

**アプリ側で処理する構文**

- `DDL` CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)

### #37 ❌ ERROR `UPDATE departments d    SET manager_id = CASE d.department_id         …`

**アプリ側で処理する構文**

- `RMW` SET manager_id = CASE d.department_id WHEN 10 THEN 200 WHEN 60 THEN 103 WHEN 80 THEN 145 WHEN 50 THEN 120 WHEN 90 THEN 100 END: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction

### #38 ❌ ERROR `INSERT INTO orders (order_date, employee_id, customer, status, total) …`

**アプリ側で処理する構文**

- `PK` INSERT must specify the full primary key; missing ['order_id']

### #39 ❌ ERROR `INSERT INTO orders (order_date, employee_id, customer, status, total) …`

**アプリ側で処理する構文**

- `PK` INSERT must specify the full primary key; missing ['order_id']

### #40 ❌ ERROR `INSERT INTO orders (order_date, employee_id, customer, status, total) …`

**アプリ側で処理する構文**

- `PK` INSERT must specify the full primary key; missing ['order_id']

### #41 ❌ ERROR `INSERT INTO orders (order_date, employee_id, customer, status, total) …`

**アプリ側で処理する構文**

- `PK` INSERT must specify the full primary key; missing ['order_id']

### #42 ❌ ERROR `INSERT INTO orders (order_date, employee_id, customer, status, total) …`

**アプリ側で処理する構文**

- `PK` INSERT must specify the full primary key; missing ['order_id']

