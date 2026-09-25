--------------------------------------------------------------------------------
-- 00_setup.sql
--   サンプル用スキーマ（Oracle HR サンプルスキーマを簡略化したもの）を作成する。
--   対象: Oracle Database 19c 以降（23ai / 26ai でも動作）
--   実行: SQL*Plus / SQLcl で専用スキーマに接続して  @00_setup.sql
--   ※ 既存の HR スキーマとは別の、空のスキーマで実行すること。
--------------------------------------------------------------------------------
SET ECHO ON
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR CONTINUE

-- 再実行できるよう既存オブジェクトを削除（存在しなくてもエラーを無視）
BEGIN
  FOR t IN (SELECT table_name FROM user_tables
            WHERE table_name IN ('EMP_AUDIT','ORDER_ITEMS','ORDERS','EMPLOYEES',
                                 'DEPARTMENTS','JOBS','EMP_STAGE','PRODUCTS_JSON',
                                 'EMP_ERR_LOG','SAMPLE_DDL','BULK_TARGET'))
  LOOP
    EXECUTE IMMEDIATE 'DROP TABLE ' || t.table_name || ' CASCADE CONSTRAINTS PURGE';
  END LOOP;
  FOR s IN (SELECT sequence_name FROM user_sequences
            WHERE sequence_name IN ('EMP_SEQ','ORDER_SEQ','AUDIT_SEQ'))
  LOOP
    EXECUTE IMMEDIATE 'DROP SEQUENCE ' || s.sequence_name;
  END LOOP;
END;
/

--------------------------------------------------------------------------------
-- テーブル
--------------------------------------------------------------------------------
CREATE TABLE jobs (
  job_id      VARCHAR2(10)  CONSTRAINT jobs_pk PRIMARY KEY,
  job_title   VARCHAR2(35)  NOT NULL,
  min_salary  NUMBER(8,2),
  max_salary  NUMBER(8,2)
);

CREATE TABLE departments (
  department_id    NUMBER(4)     CONSTRAINT dept_pk PRIMARY KEY,
  department_name  VARCHAR2(30)  NOT NULL,
  manager_id       NUMBER(6),
  location         VARCHAR2(30)
);

CREATE TABLE employees (
  employee_id    NUMBER(6)     CONSTRAINT emp_pk PRIMARY KEY,
  first_name     VARCHAR2(20),
  last_name      VARCHAR2(25)  NOT NULL,
  email          VARCHAR2(25)  NOT NULL CONSTRAINT emp_email_uk UNIQUE,
  hire_date      DATE          NOT NULL,
  job_id         VARCHAR2(10)  NOT NULL CONSTRAINT emp_job_fk REFERENCES jobs,
  salary         NUMBER(8,2)   CONSTRAINT emp_salary_ck CHECK (salary > 0),
  commission_pct NUMBER(2,2),
  manager_id     NUMBER(6)     CONSTRAINT emp_mgr_fk  REFERENCES employees,
  department_id  NUMBER(4)     CONSTRAINT emp_dept_fk REFERENCES departments
);

CREATE INDEX emp_dept_ix ON employees (department_id);
CREATE INDEX emp_mgr_ix  ON employees (manager_id);

CREATE SEQUENCE emp_seq   START WITH 300 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE order_seq START WITH 1   INCREMENT BY 1;

CREATE TABLE orders (
  order_id     NUMBER        DEFAULT order_seq.NEXTVAL CONSTRAINT orders_pk PRIMARY KEY,
  order_date   DATE          DEFAULT SYSDATE NOT NULL,
  employee_id  NUMBER(6)     CONSTRAINT orders_emp_fk REFERENCES employees,
  customer     VARCHAR2(40)  NOT NULL,
  status       VARCHAR2(10)  DEFAULT 'NEW'
               CONSTRAINT orders_status_ck CHECK (status IN ('NEW','SHIPPED','CANCELLED')),
  total        NUMBER(10,2)
);

CREATE TABLE order_items (
  order_id    NUMBER      CONSTRAINT oi_order_fk REFERENCES orders ON DELETE CASCADE,
  line_no     NUMBER(3),
  product     VARCHAR2(40) NOT NULL,
  qty         NUMBER(5)    NOT NULL,
  unit_price  NUMBER(8,2)  NOT NULL,
  CONSTRAINT order_items_pk PRIMARY KEY (order_id, line_no)
);

--------------------------------------------------------------------------------
-- データ
--------------------------------------------------------------------------------
INSERT INTO jobs VALUES ('AD_PRES', 'President',            20000, 40000);
INSERT INTO jobs VALUES ('AD_VP',   'Vice President',       15000, 30000);
INSERT INTO jobs VALUES ('IT_PROG', 'Programmer',            4000, 10000);
INSERT INTO jobs VALUES ('SA_MAN',  'Sales Manager',        10000, 20000);
INSERT INTO jobs VALUES ('SA_REP',  'Sales Representative',  6000, 12000);
INSERT INTO jobs VALUES ('ST_CLERK','Stock Clerk',           2000,  5000);

INSERT INTO departments VALUES (10, 'Administration', NULL, 'Tokyo');
INSERT INTO departments VALUES (60, 'IT',             NULL, 'Osaka');
INSERT INTO departments VALUES (80, 'Sales',          NULL, 'Tokyo');
INSERT INTO departments VALUES (50, 'Shipping',       NULL, 'Nagoya');
INSERT INTO departments VALUES (90, 'Executive',      NULL, 'Tokyo');
INSERT INTO departments VALUES (99, 'Research',       NULL, 'Fukuoka');  -- 社員なし部門

INSERT INTO employees VALUES (100,'Steven','King',    'SKING',   DATE '2013-06-17','AD_PRES',24000,NULL,NULL,90);
INSERT INTO employees VALUES (101,'Neena','Kochhar',  'NKOCHHAR',DATE '2015-09-21','AD_VP',  17000,NULL,100, 90);
INSERT INTO employees VALUES (102,'Lex','De Haan',    'LDEHAAN', DATE '2011-01-13','AD_VP',  17000,NULL,100, 90);
INSERT INTO employees VALUES (103,'Alexander','Hunold','AHUNOLD',DATE '2016-01-03','IT_PROG', 9000,NULL,102, 60);
INSERT INTO employees VALUES (104,'Bruce','Ernst',    'BERNST',  DATE '2017-05-21','IT_PROG', 6000,NULL,103, 60);
INSERT INTO employees VALUES (107,'Diana','Lorentz',  'DLORENTZ',DATE '2019-02-07','IT_PROG', 4200,NULL,103, 60);
INSERT INTO employees VALUES (145,'John','Russell',   'JRUSSEL', DATE '2014-10-01','SA_MAN', 14000, .40,100, 80);
INSERT INTO employees VALUES (146,'Karen','Partners', 'KPARTNER',DATE '2015-01-05','SA_MAN', 13500, .30,100, 80);
INSERT INTO employees VALUES (150,'Peter','Tucker',   'PTUCKER', DATE '2015-01-30','SA_REP', 10000, .30,145, 80);
INSERT INTO employees VALUES (151,'David','Bernstein','DBERNSTE',DATE '2015-03-24','SA_REP',  9500, .25,145, 80);
INSERT INTO employees VALUES (155,'Oliver','Tuvault', 'OTUVAULT',DATE '2017-11-23','SA_REP',  7000, .15,146, 80);
INSERT INTO employees VALUES (120,'Matthew','Weiss',  'MWEISS',  DATE '2014-07-18','ST_CLERK',4800,NULL,100, 50);
INSERT INTO employees VALUES (125,'Julia','Nayer',    'JNAYER',  DATE '2015-07-16','ST_CLERK',3200,NULL,120, 50);
INSERT INTO employees VALUES (178,'Kimberely','Grant','KGRANT',  DATE '2017-05-24','SA_REP',  7000, .15,145, NULL); -- 部門なし社員
INSERT INTO employees VALUES (200,'Jennifer','Whalen','JWHALEN', DATE '2013-09-17','AD_VP',  15000,NULL,101, 10);

UPDATE departments d
   SET manager_id = CASE d.department_id
                      WHEN 10 THEN 200 WHEN 60 THEN 103 WHEN 80 THEN 145
                      WHEN 50 THEN 120 WHEN 90 THEN 100 END;

INSERT INTO orders (order_date, employee_id, customer, status, total)
  VALUES (DATE '2026-04-01', 150, 'Acme Corp',     'SHIPPED',  1200);
INSERT INTO orders (order_date, employee_id, customer, status, total)
  VALUES (DATE '2026-04-15', 151, 'Globex',        'NEW',       450);
INSERT INTO orders (order_date, employee_id, customer, status, total)
  VALUES (DATE '2026-05-02', 150, 'Initech',       'SHIPPED',  3000);
INSERT INTO orders (order_date, employee_id, customer, status, total)
  VALUES (DATE '2026-05-20', 155, 'Umbrella',      'CANCELLED', 800);
INSERT INTO orders (order_date, employee_id, customer, status, total)
  VALUES (DATE '2026-06-11', 145, 'Acme Corp',     'NEW',      2200);

INSERT INTO order_items VALUES (1, 1, 'ScalarDB License', 1, 1000);
INSERT INTO order_items VALUES (1, 2, 'Support',          1,  200);
INSERT INTO order_items VALUES (2, 1, 'Training',         3,  150);
INSERT INTO order_items VALUES (3, 1, 'ScalarDL License', 2, 1500);
INSERT INTO order_items VALUES (4, 1, 'Consulting',       4,  200);
INSERT INTO order_items VALUES (5, 1, 'ScalarDB License', 2, 1100);

COMMIT;

-- 統計情報収集（オプティマイザ用）
BEGIN
  DBMS_STATS.GATHER_SCHEMA_STATS(ownname => USER);
END;
/

SELECT table_name, num_rows FROM user_tables ORDER BY table_name;
