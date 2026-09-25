### 付録 A: SQL 変換（文ごと）

#### `00_setup.sql` — 50 文 / OK 5 / WARN 37 / PLANNED 0 / ERROR 8（変換率 84.0%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 |  | `CREATE TABLE jobs ( job_id VARCHAR2(10) CONSTRAINT jobs_pk PRIMARY KE…` | WARN | TYPE |  |
| 2 |  | `CREATE TABLE departments ( department_id NUMBER(4) CONSTRAINT dept_pk…` | OK |  |  |
| 3 |  | `CREATE TABLE employees ( employee_id NUMBER(6) CONSTRAINT emp_pk PRIM…` | WARN | CHECK, FK, TYPE, UNIQUE |  |
| 4 |  | `CREATE INDEX emp_dept_ix ON employees (department_id)` | OK |  |  |
| 5 |  | `CREATE INDEX emp_mgr_ix ON employees (manager_id)` | OK |  |  |
| 6 |  | `CREATE SEQUENCE emp_seq START WITH 300 INCREMENT BY 1 NOCACHE` | ERROR | DDL |  |
| 7 |  | `CREATE SEQUENCE order_seq START WITH 1 INCREMENT BY 1` | ERROR | DDL |  |
| 8 |  | `CREATE TABLE orders ( order_id NUMBER DEFAULT order_seq.NEXTVAL CONST…` | WARN | CHECK, DEFAULT, FK, TYPE |  |
| 9 |  | `CREATE TABLE order_items ( order_id NUMBER CONSTRAINT oi_order_fk REF…` | WARN | FK, TYPE |  |
| 10 |  | `INSERT INTO jobs VALUES ('AD_PRES', 'President', 20000, 40000)` | WARN | INSERT_COLS |  |
| 11 |  | `INSERT INTO jobs VALUES ('AD_VP', 'Vice President', 15000, 30000)` | WARN | INSERT_COLS |  |
| 12 |  | `INSERT INTO jobs VALUES ('IT_PROG', 'Programmer', 4000, 10000)` | WARN | INSERT_COLS |  |
| 13 |  | `INSERT INTO jobs VALUES ('SA_MAN', 'Sales Manager', 10000, 20000)` | WARN | INSERT_COLS |  |
| 14 |  | `INSERT INTO jobs VALUES ('SA_REP', 'Sales Representative', 6000, 1200…` | WARN | INSERT_COLS |  |
| 15 |  | `INSERT INTO jobs VALUES ('ST_CLERK','Stock Clerk', 2000, 5000)` | WARN | INSERT_COLS |  |
| 16 |  | `INSERT INTO departments VALUES (10, 'Administration', NULL, 'Tokyo')` | WARN | INSERT_COLS |  |
| 17 |  | `INSERT INTO departments VALUES (60, 'IT', NULL, 'Osaka')` | WARN | INSERT_COLS |  |
| 18 |  | `INSERT INTO departments VALUES (80, 'Sales', NULL, 'Tokyo')` | WARN | INSERT_COLS |  |
| 19 |  | `INSERT INTO departments VALUES (50, 'Shipping', NULL, 'Nagoya')` | WARN | INSERT_COLS |  |
| 20 |  | `INSERT INTO departments VALUES (90, 'Executive', NULL, 'Tokyo')` | WARN | INSERT_COLS |  |
| 21 |  | `INSERT INTO departments VALUES (99, 'Research', NULL, 'Fukuoka')` | WARN | INSERT_COLS |  |
| 22 |  | `INSERT INTO employees VALUES (100,'Steven','King', 'SKING', DATE '201…` | WARN | INSERT_COLS |  |
| 23 |  | `INSERT INTO employees VALUES (101,'Neena','Kochhar', 'NKOCHHAR',DATE …` | WARN | INSERT_COLS |  |
| 24 |  | `INSERT INTO employees VALUES (102,'Lex','De Haan', 'LDEHAAN', DATE '2…` | WARN | INSERT_COLS |  |
| 25 |  | `INSERT INTO employees VALUES (103,'Alexander','Hunold','AHUNOLD',DATE…` | WARN | INSERT_COLS |  |
| 26 |  | `INSERT INTO employees VALUES (104,'Bruce','Ernst', 'BERNST', DATE '20…` | WARN | INSERT_COLS |  |
| 27 |  | `INSERT INTO employees VALUES (107,'Diana','Lorentz', 'DLORENTZ',DATE …` | WARN | INSERT_COLS |  |
| 28 |  | `INSERT INTO employees VALUES (145,'John','Russell', 'JRUSSEL', DATE '…` | WARN | INSERT_COLS |  |
| 29 |  | `INSERT INTO employees VALUES (146,'Karen','Partners', 'KPARTNER',DATE…` | WARN | INSERT_COLS |  |
| 30 |  | `INSERT INTO employees VALUES (150,'Peter','Tucker', 'PTUCKER', DATE '…` | WARN | INSERT_COLS |  |
| 31 |  | `INSERT INTO employees VALUES (151,'David','Bernstein','DBERNSTE',DATE…` | WARN | INSERT_COLS |  |
| 32 |  | `INSERT INTO employees VALUES (155,'Oliver','Tuvault', 'OTUVAULT',DATE…` | WARN | INSERT_COLS |  |
| 33 |  | `INSERT INTO employees VALUES (120,'Matthew','Weiss', 'MWEISS', DATE '…` | WARN | INSERT_COLS |  |
| 34 |  | `INSERT INTO employees VALUES (125,'Julia','Nayer', 'JNAYER', DATE '20…` | WARN | INSERT_COLS |  |
| 35 |  | `INSERT INTO employees VALUES (178,'Kimberely','Grant','KGRANT', DATE …` | WARN | INSERT_COLS |  |
| 36 |  | `INSERT INTO employees VALUES (200,'Jennifer','Whalen','JWHALEN', DATE…` | WARN | INSERT_COLS |  |
| 37 |  | `UPDATE departments d SET manager_id = CASE d.department_id WHEN 10 TH…` | ERROR | RMW |  |
| 38 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 39 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 40 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 41 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 42 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 43 |  | `INSERT INTO order_items VALUES (1, 1, 'ScalarDB License', 1, 1000)` | WARN | INSERT_COLS |  |
| 44 |  | `INSERT INTO order_items VALUES (1, 2, 'Support', 1, 200)` | WARN | INSERT_COLS |  |
| 45 |  | `INSERT INTO order_items VALUES (2, 1, 'Training', 3, 150)` | WARN | INSERT_COLS |  |
| 46 |  | `INSERT INTO order_items VALUES (3, 1, 'ScalarDL License', 2, 1500)` | WARN | INSERT_COLS |  |
| 47 |  | `INSERT INTO order_items VALUES (4, 1, 'Consulting', 4, 200)` | WARN | INSERT_COLS |  |
| 48 |  | `INSERT INTO order_items VALUES (5, 1, 'ScalarDB License', 2, 1100)` | WARN | INSERT_COLS |  |
| 49 |  | `COMMIT` | OK |  |  |
| 50 |  | `SELECT table_name, num_rows FROM user_tables ORDER BY table_name` | OK |  |  |

#### `01_sql_ddl.sql` — 39 文 / OK 9 / WARN 3 / PLANNED 0 / ERROR 27（変換率 30.8%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 | 1 IDENTITY 列・仮想列・DEFAULT ON NU | `CREATE TABLE sample_ddl ( id NUMBER GENERATED ALWAYS AS IDENTITY (STA…` | ERROR | PARSE |  |
| 2 |  | `COMMENT ON TABLE sample_ddl IS 'DDLサンプル用テーブル'` | ERROR | STATEMENT |  |
| 3 |  | `COMMENT ON COLUMN sample_ddl.price IS '税抜価格'` | ERROR | STATEMENT |  |
| 4 |  | `INSERT INTO sample_ddl (name, price) VALUES ('Widget', 1000)` | ERROR | PK |  |
| 5 |  | `INSERT INTO sample_ddl (name, price) VALUES ('Gadget', NULL)` | ERROR | PK |  |
| 6 |  | `SELECT id, name, price, price_incl FROM sample_ddl` | WARN | CROSS_PARTITION |  |
| 7 | 2 ALTER TABLE：列の追加・変更・名前変更・削除 | `ALTER TABLE sample_ddl ADD (category VARCHAR2(20) DEFAULT 'GENERAL' N…` | ERROR | ALTER |  |
| 8 |  | `ALTER TABLE sample_ddl MODIFY (name VARCHAR2(100 CHAR))` | ERROR | UNPARSED |  |
| 9 |  | `ALTER TABLE sample_ddl RENAME COLUMN note TO description` | OK |  |  |
| 10 |  | `ALTER TABLE sample_ddl SET UNUSED (description)` | ERROR | UNPARSED |  |
| 11 |  | `ALTER TABLE sample_ddl DROP UNUSED COLUMNS` | ERROR | ALTER |  |
| 12 | 3 制約の追加・無効化・有効化 | `ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_name_uk UNIQUE (name)` | ERROR | ALTER |  |
| 13 |  | `ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_price_ck CHECK (pric…` | ERROR | ALTER |  |
| 14 |  | `ALTER TABLE sample_ddl DISABLE CONSTRAINT sample_ddl_price_ck` | ERROR | UNPARSED |  |
| 15 |  | `ALTER TABLE sample_ddl ENABLE NOVALIDATE CONSTRAINT sample_ddl_price_…` | ERROR | UNPARSED |  |
| 16 | 4 CTAS（CREATE TABLE AS SELECT） | `CREATE TABLE emp_stage AS SELECT employee_id, last_name, salary, depa…` | ERROR | DDL |  |
| 17 | 5 インデックス | `CREATE INDEX emp_name_ix ON employees (last_name, first_name)` | ERROR | INDEX |  |
| 18 |  | `CREATE INDEX emp_upper_email_ix ON employees (UPPER(email))` | ERROR | INDEX |  |
| 19 |  | `CREATE BITMAP INDEX orders_status_bix ON orders (status)` | ERROR | UNPARSED |  |
| 20 |  | `ALTER INDEX emp_name_ix INVISIBLE` | ERROR | UNPARSED |  |
| 21 |  | `ALTER INDEX emp_name_ix VISIBLE` | ERROR | UNPARSED |  |
| 22 | 6 ビュー（WITH CHECK OPTION / READ | `CREATE OR REPLACE VIEW emp_it_v AS SELECT employee_id, last_name, sal…` | ERROR | DDL |  |
| 23 |  | `CREATE OR REPLACE VIEW emp_dept_v AS SELECT e.employee_id, e.last_nam…` | ERROR | DDL |  |
| 24 | 7 シーケンス [ORA] | `CREATE SEQUENCE audit_seq START WITH 1 INCREMENT BY 1 CACHE 20 NOCYCLE` | ERROR | DDL |  |
| 25 |  | `SELECT audit_seq.NEXTVAL, audit_seq.CURRVAL FROM dual` | OK |  |  |
| 26 |  | `ALTER SEQUENCE audit_seq INCREMENT BY 10` | ERROR | UNPARSED |  |
| 27 | 8 シノニム [ORA] | `CREATE OR REPLACE SYNONYM staff FOR employees` | ERROR | UNPARSED |  |
| 28 |  | `SELECT COUNT(*) FROM staff` | OK |  |  |
| 29 | 9 一時表 [ORA] | `CREATE GLOBAL TEMPORARY TABLE gtt_work ( id NUMBER, val VARCHAR2(100)…` | ERROR | TEMP |  |
| 30 | 10 パーティション表 [ORA]（EE の Partitio | `CREATE TABLE sales_part ( sale_id NUMBER, sale_date DATE, amount NUMB…` | ERROR | UNPARSED |  |
| 31 |  | `INSERT INTO sales_part VALUES (1, DATE '2025-12-31', 100)` | WARN | INSERT_COLS |  |
| 32 |  | `INSERT INTO sales_part VALUES (2, DATE '2026-03-15', 200)` | WARN | INSERT_COLS |  |
| 33 |  | `COMMIT` | OK |  |  |
| 34 |  | `SELECT partition_name, high_value FROM user_tab_partitions WHERE tabl…` | OK |  |  |
| 35 | 11 TRUNCATE / DROP / FLASHBACK  | `TRUNCATE TABLE emp_stage` | OK |  |  |
| 36 |  | `DROP TABLE gtt_work` | OK |  |  |
| 37 |  | `DROP TABLE sales_part` | OK |  |  |
| 38 |  | `FLASHBACK TABLE sales_part TO BEFORE DROP` | ERROR | PARSE |  |
| 39 |  | `DROP TABLE sales_part PURGE` | OK |  |  |

#### `02_sql_query.sql` — 37 文 / OK 0 / WARN 5 / PLANNED 18 / ERROR 14（変換率 13.5%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 | A-1 DUAL 表 [ORA]（23ai 以降は FROM 句 | `SELECT SYSDATE, SYSTIMESTAMP, USER FROM dual` | PLANNED | PROJECTION | P1 |
| 2 | A-2 絞り込み・並べ替え（NULL の並び順指定） | `SELECT employee_id, last_name, salary, commission_pct FROM employees …` | WARN | CROSS_PARTITION, NULLS |  |
| 3 | A-3 CASE / DECODE [ORA] / NULL 処 | `SELECT last_name, salary, CASE WHEN salary >= 15000 THEN 'HIGH' WHEN …` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 4 | A-4 文字列・数値・日付関数 | `SELECT UPPER(last_name) AS upper_name, INITCAP(email) AS initcap_emai…` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 5 | A-5 正規表現 | `SELECT email, REGEXP_SUBSTR(email, '^[A-Z]') AS first_char, REGEXP_RE…` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 6 | B-1 内部結合（ANSI） | `SELECT e.last_name, d.department_name FROM employees e JOIN departmen…` | WARN | CROSS_PARTITION |  |
| 7 | B-2 外部結合（ANSI）と Oracle 独自の (+) 記 | `SELECT e.last_name, d.department_name FROM employees e LEFT JOIN depa…` | WARN | CROSS_PARTITION |  |
| 8 |  | `SELECT e.last_name, d.department_name FROM employees e, departments d…` | WARN | CROSS_PARTITION, ORACLE_JOIN_MARK |  |
| 9 | B-3 完全外部結合：部門なし社員・社員なし部門の両方を出す | `SELECT e.last_name, d.department_name FROM employees e FULL OUTER JOI…` | ERROR | JOIN, RESIDUAL_H2 |  |
| 10 | B-4 自己結合（上司名の取得） | `SELECT w.last_name AS employee, m.last_name AS manager FROM employees…` | WARN | CROSS_PARTITION |  |
| 11 | B-5 LATERAL / CROSS APPLY（12c+）： | `SELECT d.department_name, t.last_name, t.salary FROM departments d CR…` | ERROR | APP_SEMANTICS, JOIN, RESIDUAL_H2 |  |
| 12 | C-1 スカラー副問合せ | `SELECT last_name, salary, (SELECT ROUND(AVG(salary)) FROM employees) …` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 13 | C-2 相関副問合せ：部門平均より高い社員 | `SELECT e.last_name, e.department_id, e.salary FROM employees e WHERE …` | PLANNED | PLAN_CROSS_PARTITION, SUBQUERY | P5 |
| 14 | C-3 EXISTS / NOT EXISTS | `SELECT d.department_name FROM departments d WHERE NOT EXISTS (SELECT …` | PLANNED | NOT, PLAN_CROSS_PARTITION, SUBQUERY | P5 |
| 15 | C-4 多列 IN | `SELECT last_name, department_id, salary FROM employees WHERE (departm…` | PLANNED | PLAN_CROSS_PARTITION, SUBQUERY | P5 |
| 16 | C-5 集合演算（MINUS は Oracle 固有 [ORA] | `SELECT department_id FROM departments MINUS SELECT department_id FROM…` | PLANNED | PLAN_CROSS_PARTITION, SET_OP | P6 |
| 17 |  | `SELECT job_id FROM employees WHERE department_id = 80 INTERSECT SELEC…` | PLANNED | PLAN_CROSS_PARTITION, SET_OP | P6 |
| 18 | D-1 GROUP BY / HAVING | `SELECT department_id, COUNT(*) AS cnt, SUM(salary) AS total, ROUND(AV…` | PLANNED | PLAN_CROSS_PARTITION, PLAN_UNRESOLVED, PROJECTION | P1 |
| 19 | D-2 ROLLUP / CUBE / GROUPING SET | `SELECT CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(de…` | ERROR | APP_SEMANTICS, GROUP, PROJECTION, RESIDUAL_H2 |  |
| 20 |  | `SELECT department_id, job_id, SUM(salary) FROM employees GROUP BY GRO…` | ERROR | APP_SEMANTICS, GROUP, RESIDUAL_H2 |  |
| 21 | D-3 LISTAGG（文字列集約） | `SELECT department_id, LISTAGG(last_name, ', ') WITHIN GROUP (ORDER BY…` | PLANNED | AGG, PLAN_CROSS_PARTITION, PROJECTION | P1+P7 |
| 22 | D-4 PIVOT / UNPIVOT（11g+） | `SELECT * FROM (SELECT department_id, job_id, salary FROM employees) P…` | ERROR | APP_SEMANTICS, FROM, RESIDUAL_H2, SUBQUERY |  |
| 23 |  | `SELECT employee_id, pay_type, amount FROM (SELECT employee_id, salary…` | ERROR | FROM, PROJECTION, RESIDUAL_H2, SUBQUERY |  |
| 24 |  | `SELECT department_id, last_name, salary, ROW_NUMBER() OVER (PARTITION…` | PLANNED | PLAN_CROSS_PARTITION, PLAN_UNRESOLVED, PROJECTION, WINDOW | P1 |
| 25 | E-2 累計・移動平均（ウィンドウ句） | `SELECT order_date, total, SUM(total) OVER (ORDER BY order_date ROWS B…` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION, WINDOW | P1 |
| 26 | E-3 KEEP (DENSE_RANK FIRST/LAST) | `SELECT department_id, MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY …` | ERROR | APP_SEMANTICS, KEEP, PROJECTION, RESIDUAL_H2 |  |
| 27 | F-1 12c+ 標準構文 | `SELECT last_name, salary FROM employees ORDER BY salary DESC FETCH FI…` | PLANNED | LIMIT, PLAN_CROSS_PARTITION | P1 |
| 28 |  | `SELECT last_name, salary FROM employees ORDER BY salary DESC OFFSET 5…` | PLANNED | OFFSET, PLAN_CROSS_PARTITION | P4 |
| 29 | F-2 11g 以前の ROWNUM 方式 [ORA]（移行元コ | `SELECT * FROM (SELECT a.*, ROWNUM rnum FROM (SELECT last_name, salary…` | PLANNED | FROM, PLAN_CROSS_PARTITION, PLAN_UNRESOLVED, SUBQUERY | P5 |
| 30 | G-1 副問合せのファクタリング | `WITH dept_stats AS ( SELECT department_id, AVG(salary) AS avg_sal FRO…` | PLANNED | CTE, PLAN_CROSS_PARTITION, PROJECTION | P1+P6 |
| 31 | G-2 CONNECT BY による階層問合せ [ORA] | `SELECT LEVEL, LPAD(' ', 2 * (LEVEL - 1)) || last_name AS org_chart, S…` | ERROR | APP_SEMANTICS, HIERARCHICAL, PROJECTION, RESIDUAL_H2 |  |
| 32 | G-3 再帰 WITH（標準SQL、他DBへ移行しやすい書き方） | `WITH org (employee_id, last_name, manager_id, lvl, path) AS ( SELECT …` | ERROR | APP_SEMANTICS, CTE, PROJECTION, RESIDUAL_H2, SET_OP |  |
| 33 | G-4 CONNECT BY LEVEL による連番生成 [OR | `SELECT DATE '2026-09-01' + LEVEL - 1 AS cal_date FROM dual CONNECT BY…` | ERROR | APP_SEMANTICS, HIERARCHICAL, PROJECTION, RESIDUAL_H2 |  |
| 34 | H-1 フラッシュバック問合せ [ORA]（UNDO 保持期間内 | `SELECT employee_id, salary FROM employees AS OF TIMESTAMP (SYSTIMESTA…` | ERROR | PARSE |  |
| 35 | H-2 サンプリング [ORA] | `SELECT COUNT(*) FROM employees SAMPLE (50)` | ERROR | CLAUSE, RESIDUAL_H2 |  |
| 36 | H-3 ROWID [ORA]（重複行削除などで頻出） | `SELECT ROWID, employee_id FROM employees WHERE ROWNUM <= 3` | ERROR | ROWID |  |
| 37 | H-4 WITH 句内での PL/SQL 関数定義（12c+）[ | `WITH FUNCTION annual(p_sal NUMBER, p_comm NUMBER) RETURN NUMBER IS BE…` | ERROR | WITH_PLSQL |  |

#### `03_sql_dml.sql` — 37 文 / OK 9 / WARN 6 / PLANNED 1 / ERROR 21（変換率 40.5%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 | A-1 単一行（シーケンス使用） | `INSERT INTO employees (employee_id, first_name, last_name, email, hir…` | ERROR | SEQUENCE |  |
| 2 | A-2 INSERT ... SELECT | `INSERT INTO emp_stage (employee_id, last_name, salary, department_id)…` | ERROR | INSERT_SELECT |  |
| 3 | A-3 複数表への条件付き INSERT（INSERT ALL  | `CREATE TABLE emp_high AS SELECT employee_id, salary FROM employees WH…` | ERROR | DDL |  |
| 4 |  | `CREATE TABLE emp_low AS SELECT employee_id, salary FROM employees WHE…` | ERROR | DDL |  |
| 5 |  | `INSERT FIRST WHEN salary >= 10000 THEN INTO emp_high (employee_id, sa…` | ERROR | STATEMENT |  |
| 6 | A-4 INSERT ALL による複数行挿入（23ai 未満で | `INSERT ALL INTO jobs VALUES ('MK_MAN', 'Marketing Manager', 9000, 150…` | ERROR | STATEMENT |  |
| 7 | B-1 相関副問合せによる更新 | `UPDATE employees e SET salary = salary * 1.05 WHERE salary < (SELECT …` | ERROR | RMW |  |
| 8 | B-2 複数列を副問合せで一括更新 | `UPDATE departments d SET (location) = (SELECT 'Tokyo' FROM dual) WHER…` | ERROR | SET |  |
| 9 | B-3 結合更新（インラインビュー更新）[ORA] | `UPDATE (SELECT e.salary, j.min_salary FROM employees e JOIN jobs j ON…` | ERROR | PARSE |  |
| 10 | B-4 DELETE（ROWID による重複削除の定番パターン） | `DELETE FROM emp_stage a WHERE a.ROWID > (SELECT MIN(b.ROWID) FROM emp…` | ERROR | ROWID |  |
| 11 |  | `UPDATE emp_stage SET salary = salary + 500` | ERROR | RMW |  |
| 12 |  | `MERGE INTO employees t USING (SELECT employee_id, salary FROM emp_sta…` | ERROR | PARSE |  |
| 13 |  | `ALTER TABLE emp_stage ADD CONSTRAINT emp_stage_sal_ck CHECK (salary <…` | ERROR | UNPARSED |  |
| 14 |  | `INSERT INTO emp_stage (employee_id, last_name, salary, department_id)…` | ERROR | PARSE |  |
| 15 |  | `SELECT ora_err_number$, ora_err_tag$, employee_id, salary FROM emp_er…` | OK |  |  |
| 16 |  | `SAVEPOINT before_raise` | ERROR | STATEMENT |  |
| 17 |  | `UPDATE employees SET salary = salary * 2 WHERE department_id = 60` | ERROR | RMW |  |
| 18 |  | `ROLLBACK TO SAVEPOINT before_raise` | ERROR | SAVEPOINT |  |
| 19 |  | `COMMIT` | OK |  |  |
| 20 |  | `SELECT employee_id, salary FROM employees WHERE department_id = 80 FO…` | WARN | LOCK |  |
| 21 |  | `ROLLBACK` | OK |  |  |
| 22 |  | `SELECT order_id FROM orders WHERE status = 'NEW' FOR UPDATE SKIP LOCK…` | WARN | LOCK |  |
| 23 |  | `ROLLBACK` | OK |  |  |
| 24 |  | `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` | ERROR | STATEMENT |  |
| 25 |  | `SELECT COUNT(*) FROM employees` | WARN | CROSS_PARTITION |  |
| 26 |  | `COMMIT` | OK |  |  |
| 27 | F-1 JSON を格納する表（19c 互換: CLOB + I | `CREATE TABLE products_json ( id NUMBER PRIMARY KEY, doc CLOB CONSTRAI…` | WARN | CHECK, TYPE |  |
| 28 |  | `INSERT INTO products_json VALUES (1, '{"name":"ScalarDB","tier":"Ente…` | WARN | INSERT_COLS |  |
| 29 |  | `INSERT INTO products_json VALUES (2, '{"name":"ScalarDL","tier":"Stan…` | WARN | INSERT_COLS |  |
| 30 |  | `COMMIT` | OK |  |  |
| 31 | F-2 値の取り出し（JSON_VALUE / JSON_QUE | `SELECT JSON_VALUE(doc, '$.name') AS name, JSON_VALUE(doc, '$.price' R…` | ERROR | PARSE |  |
| 32 | F-3 JSON_TABLE：JSON を行列に展開 | `SELECT p.id, jt.name, jt.tag FROM products_json p, JSON_TABLE(p.doc, …` | ERROR | JOIN, RESIDUAL_H2 |  |
| 33 | F-4 リレーショナル → JSON 生成 | `SELECT JSON_OBJECT('dept' VALUE d.department_name, 'members' VALUE JS…` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 34 | F-5 部分更新（19c+） | `UPDATE products_json SET doc = JSON_MERGEPATCH(doc, '{"price":1200,"s…` | ERROR | PARSE |  |
| 35 |  | `COMMIT` | OK |  |  |
| 36 |  | `DROP TABLE emp_high PURGE` | OK |  |  |
| 37 |  | `DROP TABLE emp_low PURGE` | OK |  |  |

#### `05_plsql_units.sql` — 9 文 / OK 3 / WARN 2 / PLANNED 1 / ERROR 3（変換率 55.6%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 |  | `CREATE TABLE emp_audit ( audit_id NUMBER GENERATED BY DEFAULT AS IDEN…` | ERROR | AUTO_INC, TYPE |  |
| 2 | 1 プロシージャ（IN / OUT / IN OUT、既定値 | `SELECT last_name, dept_name_of(department_id) AS dept, annual_comp(sa…` | PLANNED | PLAN_CROSS_PARTITION, PLAN_UNRESOLVED, PROJECTION | P1 |
| 3 | 3 パッケージ（仕様部 / 本体、オーバーロード、プライベー | `SELECT action, message FROM emp_audit` | WARN | CROSS_PARTITION |  |
| 4 | 5 トリガー | `CREATE OR REPLACE VIEW emp_dept_upd_v AS SELECT e.employee_id, e.last…` | ERROR | DDL |  |
| 5 |  | `UPDATE employees SET salary = salary * 1.01 WHERE department_id = 60` | ERROR | RMW |  |
| 6 |  | `UPDATE emp_dept_upd_v SET department_name = 'Sales' WHERE employee_id…` | OK |  |  |
| 7 |  | `SELECT employee_id, action, old_salary, new_salary FROM emp_audit WHE…` | WARN | CROSS_PARTITION |  |
| 8 |  | `ROLLBACK` | OK |  |  |
| 9 |  | `SELECT name, type, line, position, text FROM user_errors ORDER BY nam…` | OK |  |  |

#### `06_plsql_advanced.sql` — 3 文 / OK 1 / WARN 1 / PLANNED 1 / ERROR 0（変換率 66.7%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 |  | `CREATE TABLE bulk_target ( employee_id NUMBER PRIMARY KEY, last_name …` | WARN | CHECK, TYPE |  |
| 2 | 1 BULK COLLECT（LIMIT 付きで大量件数でも | `SELECT * FROM TABLE(emp_grades(80)) ORDER BY grade, last_name` | PLANNED | UNSUPPORTED | P1 |
| 3 | 5 よく使う組込みパッケージ | `SELECT job_name, enabled, repeat_interval FROM user_scheduler_jobs` | OK |  |  |

### 付録 B: 実 DB 比較（SQL）
| # | 元の SQL | 変換 | 実行 | 結果 | 差の内容 |
|---|---|---|---|---|---|
| 10 | `SELECT SYSDATE, SYSTIMESTAMP, USER FROM dual` | PLANNED | 実行計画 P1 | FAIL | result mismatch (no row of the actual result equals expected (datetime.datetime(2026, 9, 25, 1, 3, 21), dateti |
| 11 | `SELECT employee_id, last_name, salary, commission_pct FROM …` | WARN | ScalarDB SQL | PASS |  |
| 12 | `SELECT last_name, salary, CASE WHEN salary >= 15000 THEN 'H…` | PLANNED | 実行計画 P1 | PASS |  |
| 13 | `SELECT UPPER(last_name) AS upper_name, INITCAP(email) AS in…` | PLANNED | 実行計画 P1 | FAIL | result mismatch (no row of the actual result equals expected ('DE HAAN', 'Ldehaan', 'De ', 2, '000102', 'Lex D |
| 14 | `SELECT email, REGEXP_SUBSTR(email, '^[A-Z]') AS first_char,…` | PLANNED | 実行計画 P1 | PASS |  |
| 15 | `SELECT e.last_name, d.department_name FROM employees e JOIN…` | WARN | ScalarDB SQL | PASS |  |
| 16 | `SELECT e.last_name, d.department_name FROM employees e LEFT…` | WARN | ScalarDB SQL | PASS |  |
| 17 | `SELECT e.last_name, d.department_name FROM employees e, dep…` | WARN | ScalarDB SQL | PASS |  |
| 18 | `SELECT e.last_name, d.department_name FROM employees e FULL…` | ERROR | — | NOT_CONVERTIBLE | OUTER JOIN is not supported; the H2 residual engine cannot run FULL OUTER JOIN (H2 has no FULL JOIN: UNION the |
| 19 | `SELECT w.last_name AS employee, m.last_name AS manager FROM…` | WARN | ScalarDB SQL | PASS |  |
| 20 | `SELECT d.department_name, t.last_name, t.salary FROM depart…` | ERROR | — | NOT_CONVERTIBLE | joined relation must be a base table (no subqueries); the H2 residual engine cannot run LATERAL / CROSS APPLY  |
| 21 | `SELECT last_name, salary, (SELECT ROUND(AVG(salary)) FROM e…` | PLANNED | 実行計画 P1 | PASS |  |
| 22 | `SELECT e.last_name, e.department_id, e.salary FROM employee…` | PLANNED | 実行計画 P5 | PASS |  |
| 23 | `SELECT d.department_name FROM departments d WHERE NOT EXIST…` | PLANNED | 実行計画 P5 | PASS |  |
| 24 | `SELECT last_name, department_id, salary FROM employees WHER…` | PLANNED | 実行計画 P5 | PASS |  |
| 25 | `SELECT department_id FROM departments MINUS SELECT departme…` | PLANNED | 実行計画 P6 | PASS |  |
| 26 | `SELECT job_id FROM employees WHERE department_id = 80 INTER…` | PLANNED | 実行計画 P6 | PASS |  |
| 27 | `SELECT department_id, COUNT(*) AS cnt, SUM(salary) AS total…` | PLANNED | 実行計画 P1 | PASS |  |
| 28 | `SELECT CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE …` | ERROR | — | NOT_CONVERTIBLE | main query: expressions in the select list (CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(depart |
| 29 | `SELECT department_id, job_id, SUM(salary) FROM employees GR…` | ERROR | — | NOT_CONVERTIBLE | main query: GROUP BY GROUPING SETS -- aggregate each level in the application; the H2 residual engine cannot r |
| 30 | `SELECT department_id, LISTAGG(last_name, ', ') WITHIN GROUP…` | PLANNED | 実行計画 P1+P7 | PASS |  |
| 31 | `SELECT * FROM (SELECT department_id, job_id, salary FROM em…` | ERROR | — | NOT_CONVERTIBLE | FROM must reference exactly one base table (no subqueries); main query: derived table in FROM -- evaluate it i |
| 32 | `SELECT employee_id, pay_type, amount FROM (SELECT employee_…` | ERROR | — | NOT_CONVERTIBLE | FROM must reference exactly one base table (no subqueries); main query: derived table in FROM -- evaluate it i |
| 33 | `SELECT department_id, last_name, salary, ROW_NUMBER() OVER …` | PLANNED | 実行計画 P1 | CASE_ERROR | source database rejected the statement: ORA-00923: FROM keyword not found where expected |
| 34 | `SELECT department_id, last_name, salary, ROW_NUMBER() OVER …` | PLANNED | 実行計画 P1 | FAIL | result mismatch (row 13: expected (90, 'Kochhar', 17000.0, 2, 2, 2, 58000, 0.293, 24000, 'Hunold', 4), actual  |
| 35 | `SELECT order_date, total, SUM(total) OVER (ORDER BY order_d…` | PLANNED | 実行計画 P1 | PASS |  |
| 36 | `SELECT department_id, MAX(last_name) KEEP (DENSE_RANK FIRST…` | ERROR | — | NOT_CONVERTIBLE | projection 'MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY salary DESC)' is an expression; ScalarDB SQL only s |
| 37 | `SELECT last_name, salary FROM employees ORDER BY salary DES…` | PLANNED | 実行計画 P1 | FAIL | result mismatch (row 2: expected ('Kochhar', 17000.0), actual ('De Haan', 17000)): expected [('King', 24000.0) |
| 38 | `SELECT last_name, salary FROM employees ORDER BY salary DES…` | PLANNED | 実行計画 P4 | FAIL | result mismatch (row 5: expected ('Tuvault', 7000.0), actual ('Grant', 7000)): expected [('Partners', 13500.0) |
| 39 | `SELECT * FROM (SELECT a.*, ROWNUM rnum FROM (SELECT last_na…` | PLANNED | 実行計画 P5 | PASS |  |
| 40 | `WITH dept_stats AS ( SELECT department_id, AVG(salary) AS a…` | PLANNED | 実行計画 P1+P6 | PASS |  |
| 41 | `SELECT LEVEL, LPAD(' ', 2 * (LEVEL - 1)) || last_name AS or…` | ERROR | — | NOT_CONVERTIBLE | main query: START WITH / CONNECT BY with CONNECT_BY_ISLEAF, CONNECT_BY_ROOT, LEVEL, SYS_CONNECT_BY_PATH -- wal |
| 42 | `WITH org (employee_id, last_name, manager_id, lvl, path) AS…` | ERROR | — | NOT_CONVERTIBLE | WITH org: evaluate each common table expression in the application (fetch its base tables through ScalarDB SQL |
| 43 | `SELECT DATE '2026-09-01' + LEVEL - 1 AS cal_date FROM dual …` | ERROR | — | NOT_CONVERTIBLE | main query: START WITH / CONNECT BY with LEVEL -- walk the tree in the application (appside.Hierarchy) or prec |
| 44 | `SELECT employee_id, salary FROM employees AS OF TIMESTAMP (…` | ERROR | — | CASE_ERROR | source database rejected the statement: ORA-01466: unable to read data - table definition has changed |
| 45 | `SELECT COUNT(*) FROM employees SAMPLE (50)` | ERROR | — | NOT_CONVERTIBLE | TABLESAMPLE on employees is not supported; it returns a random subset of the rows; the H2 residual engine cann |
| 46 | `SELECT ROWID, employee_id FROM employees WHERE ROWNUM <= 3` | ERROR | — | NOT_CONVERTIBLE | pseudo-column ROWID does not exist in ScalarDB; use the primary key |
| 47 | `SELECT JSON_VALUE(doc, '$.name') AS name, JSON_VALUE(doc, '…` | ERROR | — | NOT_CONVERTIBLE | Expecting ). Line 6, Col: 42. |
| 48 | `SELECT p.id, jt.name, jt.tag FROM products_json p, JSON_TAB…` | ERROR | — | NOT_CONVERTIBLE | comma join without join condition (cartesian product) is not supported; the H2 residual engine cannot run JSON |
| 49 | `SELECT JSON_OBJECT('dept' VALUE d.department_name, 'members…` | PLANNED | 実行計画 P1 | PASS |  |

### 付録 C: PL/SQL 判定（routine ごと）
| routine | 判定 | ルール | 理由（先頭） |
|---|---|---|---|
| `annual_comp` | REVIEW |  | confidence factor testEvidence is 0 |
| `b04_1_variables` | REVIEW |  | confidence factor testEvidence is 0 |
| `b04_2_control_flow` | REVIEW |  | confidence factor testEvidence is 0 |
| `b04_3_implicit_cursor_attrs` | REDESIGN | SQL-004, SQL-001, TX-001, TX-004, TRG-002 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b04_4_1_explicit_cursor` | REVIEW | SCAN-002, CUR-003, CUR-002 | CUR-003: 明示 cursor を先読みの走査に置き換えました。cursor が COMMIT をまたいでいたなら、読む時点が変わります; CUR-002 |
| `b04_4_2_cursor_for_loop` | REVIEW | CUR-002, SQL-002 | CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size  |
| `b04_4_3_for_update_current_of` | REDESIGN | CUR-002, SQL-004, LOCK-001, LOCK-002, SQL-001, TX-001, TRG-002 | LOCK-001: 行ロックです。ターゲットで同じ保証を別の方法で与える設計が要ります; LOCK-002: cursor の宣言で行ロックしています。文だけを |
| `b04_4_4_ref_cursor` | REVIEW | SCAN-002, CUR-003, CUR-002 | CUR-003: 明示 cursor を先読みの走査に置き換えました。cursor が COMMIT をまたいでいたなら、読む時点が変わります; CUR-003 |
| `b04_5_records_collections` | REVIEW | CALL-001, CUR-002 | CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません; CUR |
| `b04_6_1_predefined_exceptions` | REVIEW | SELECT-OPT-001 | confidence factor testEvidence is 0 |
| `b04_6_2_user_exceptions` | REDESIGN | TX-001, TRG-002 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b05_1_call_raise_salary` | REDESIGN | TX-001 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b05_3_call_emp_api` | REDESIGN | TX-001 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b05_4_call_log_msg` | REDESIGN | SQL-004, SQL-001, TX-001, TRG-002 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b06_1_bulk_collect_limit` | REVIEW | SCAN-002, CUR-002, BULK-003 | CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size  |
| `b06_2_2_forall_returning` | REDESIGN | SQL-001, BULK-001, TX-001 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b06_2_forall_save_exceptions` | REDESIGN | SCAN-002, CUR-002, BULK-003, TX-001 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b06_3_6_dbms_sql` | REDESIGN | DYN-003, CALL-001 | DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります; DYN-003: DB |
| `b06_3_native_dynamic_sql` | REDESIGN | DYN-001, DYN-002, DYN-OPT-002, LOWER-001, CUR-001, TX-001 | DYN-001: 表名など識別子が実行時に決まる SQL です。allowlist か専用 Repository への再設計が要ります; TX-001: rou |
| `b06_4_collection_in_sql` | REVIEW | SELECT-001, SEM-004, SQL-002, BULK-001 | SELECT-001: キーで届かない SELECT INTO で、ScalarDB がそのまま実行できる文ではありません。0 件と複数件の意味（NO_DATA |
| `b06_5_2_scheduler_job` | REDESIGN | CALL-001, EXT-001 | EXT-001: UTL_* / DBMS_SCHEDULER / AQ などの外部副作用があります |
| `b06_5_builtin_packages` | REVIEW | CALL-001 | CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません |
| `b06_6_conditional_compilation` | REVIEW |  | confidence factor testEvidence is 0 |
| `dept_name_of` | REVIEW |  | confidence factor testEvidence is 0 |
| `dml_d_create_error_log` | REVIEW | CALL-001 | CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません |
| `emp_api.call_count` | REDESIGN | STATE-001 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります |
| `emp_api.get_by_dept` | REDESIGN | SCAN-002, CUR-002, STATE-001 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります |
| `emp_api.give_raise~1` | REDESIGN | SQL-004, SQL-001, STATE-001, TRG-002 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります; TRG-002 |
| `emp_api.give_raise~2` | REDESIGN | SQL-001, STATE-001, TRG-002 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります; TRG-002 |
| `emp_api.hire` | REDESIGN | EXC-001, SQL-001, STATE-001, TRG-002 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります; TRG-002 |
| `emp_api.validate_pct` | REDESIGN | STATE-001 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります |
| `emp_biu_trg.body` | REDESIGN | TRG-001 | TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります |
| `emp_dept_cap_trg.body` | REDESIGN | TRG-001 | TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります |
| `emp_dept_upd_v_trg.body` | REDESIGN | SQL-001, TRG-001 | TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります |
| `emp_grades` | REVIEW | LOWER-001, CUR-002, SQL-002 | LOWER-001: lowering がまだ模していない構文です。意味が保てる保証がありません; CUR-002: Cursor FOR LOOP です。走査 |
| `emp_salary_audit_trg.body` | REDESIGN | SQL-001, TRG-001 | TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります |
| `log_msg` | REDESIGN | SQL-001, TX-001, TX-002 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `normalize_name` | REVIEW |  | confidence factor testEvidence is 0 |
| `raise_salary` | REDESIGN | SQL-004, SQL-001, TRG-002 | TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigg |
| `setup_drop_objects` | REDESIGN | DYN-001, DYN-002, CUR-002 | DYN-001: 表名など識別子が実行時に決まる SQL です。allowlist か専用 Repository への再設計が要ります |
| `setup_gather_stats` | REVIEW | CALL-001 | CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません |

### 付録 D: PL/SQL 実 DB 比較
| シナリオ | routine | 結果 | 差の内容 |
|---|---|---|---|
| `annual_comp_null_comm` | `annual_comp.annual_comp` | 一致 |  |
| `annual_comp_with_comm` | `annual_comp.annual_comp` | 一致 |  |
| `b04_1_variables` | `b04_1_variables.b04_1_variables` | 一致 |  |
| `b04_2_control_flow` | `b04_2_control_flow.b04_2_control_flow` | 一致 |  |
| `b04_3_implicit_cursor_attrs` | `b04_3_implicit_cursor_attrs.b04_3_implicit_cursor_attrs` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (SET salary = salary + 100: expressions referencing columns are not allowed; do SELECT -> compute  |
| `b04_4_1_explicit_cursor` | `b04_4_1_explicit_cursor.b04_4_1_explicit_cursor` | 一致 |  |
| `b04_4_2_cursor_for_loop` | `b04_4_2_cursor_for_loop.b04_4_2_cursor_for_loop` | 一致 |  |
| `b04_4_3_for_update_current_of` | `b04_4_3_for_update_current_of.b04_4_3_for_update_current_of` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in Loop: cursor FOR loop whose body writes ['employees'], which its own query reads) |
| `b04_4_4_ref_cursor` | `b04_4_4_ref_cursor.b04_4_4_ref_cursor` | 一致 |  |
| `b04_5_records_collections` | `b04_5_records_collections.b04_5_records_collections` | 一致 |  |
| `b04_6_1_predefined_exceptions` | `b04_6_1_predefined_exceptions.b04_6_1_predefined_exceptions` | 一致 |  |
| `b04_6_2_user_exceptions` | `b04_6_2_user_exceptions.b04_6_2_user_exceptions` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (Rollback is not translated) |
| `b05_1_call_raise_salary` | `b05_1_call_raise_salary.b05_1_call_raise_salary` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (RETURNING is not supported) |
| `b05_3_call_emp_api` | `b05_3_call_emp_api.b05_3_call_emp_api` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in Assignment: emp_api.hire) |
| `b05_4_call_log_msg` | `b05_4_call_log_msg.b05_4_call_log_msg` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (SET salary = salary + 1: expressions referencing columns are not allowed; do SELECT -> compute -> |
| `b06_1_bulk_collect_limit` | `b06_1_bulk_collect_limit.b06_1_bulk_collect_limit` | 一致 |  |
| `b06_2_2_forall_returning` | `b06_2_2_forall_returning.b06_2_2_forall_returning` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in Loop: forall loop) |
| `b06_2_forall_save_exceptions` | `b06_2_forall_save_exceptions.b06_2_forall_save_exceptions` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (Commit is not translated); table bulk_target row count: expected=11 actual=0; table bulk_target:  |
| `b06_3_6_dbms_sql` | `b06_3_6_dbms_sql.b06_3_6_dbms_sql` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in declaration c: DBMS_SQL.OPEN_CURSOR) |
| `b06_3_native_dynamic_sql` | `b06_3_native_dynamic_sql.b06_3_native_dynamic_sql` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in DynamicSql: EXECUTE IMMEDIATE whose statement is not a knowable set) |
| `b06_4_collection_in_sql` | `b06_4_collection_in_sql.b06_4_collection_in_sql` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in SqlOperation: execution plan result) |
| `b06_5_builtin_packages` | `b06_5_builtin_packages.b06_5_builtin_packages` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in Call: named arguments of a routine that is not in the program) |
| `b06_6_conditional_compilation` | `b06_6_conditional_compilation.b06_6_conditional_compilation` | 一致 |  |
| `dept_name_of_missing` | `dept_name_of.dept_name_of` | 一致 |  |
| `dept_name_of_ok` | `dept_name_of.dept_name_of` | 一致 |  |
| `emp_api_give_raise_invalid` | `emp_api.give_raise~1` | 一致 |  |
| `emp_api_give_raise_ok` | `emp_api.give_raise~1` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (SET salary = salary * (1 + :p_pct / 100): expressions referencing columns are not allowed; do SEL |
| `log_msg_ok` | `log_msg.log_msg` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (INSERT must specify the full primary key; missing ['audit_id']); table emp_audit row count: expec |
| `normalize_name_ok` | `normalize_name.normalize_name` | 一致 |  |
| `raise_salary_missing` | `raise_salary.raise_salary` | 相違 | exception code: expected=-20010 actual=java.lang.UnsupportedOperationException (RETURNING is not supported) |
| `raise_salary_ok` | `raise_salary.raise_salary` | 相違 | out p_new_sal: missing (expected only) = 6600; exception: expected=none actual=java.lang.UnsupportedOperationException (RETURNING is not supported); table emp_audit row c |
