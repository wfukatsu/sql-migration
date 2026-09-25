-- converted to scalardb
-- [NOT CONVERTED #1] Required keyword: 'this' missing for <class 'sqlglot.expressions.constraints.DefaultColumnConstraint'>. Line 14, Col: 37.
-- --------------------------------------------------------------------------------
-- -- 01_sql_ddl.sql : DDL（データ定義言語）のサンプル
-- --   参照: SQL Language Reference - CREATE TABLE / ALTER TABLE / CREATE INDEX /
-- --         CREATE VIEW / CREATE SEQUENCE / CREATE SYNONYM
-- --   前提: 00_setup.sql 実行済み
-- --   [ORA] = Oracle 固有 / 他DBへの移行で要注意の構文
-- --------------------------------------------------------------------------------
-- 
-- -- 1. IDENTITY 列・仮想列・DEFAULT ON NULL を持つ表 (12c+)
-- CREATE TABLE sample_ddl (
--   id          NUMBER GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1)
--               CONSTRAINT sample_ddl_pk PRIMARY KEY,
--   name        VARCHAR2(50 CHAR) NOT NULL,           -- CHAR 長セマンティクス
--   price       NUMBER(10,2) DEFAULT ON NULL 0,       -- [ORA] NULL 挿入時も既定値
--   tax_rate    NUMBER(4,3)  DEFAULT 0.1,
--   price_incl  NUMBER GENERATED ALWAYS AS (ROUND(price * (1 + tax_rate), 2)) VIRTUAL, -- [ORA] 仮想列
--   created_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP,
--   note        CLOB
-- )

-- [NOT CONVERTED #2] Comment statements are not supported by ScalarDB SQL
-- COMMENT ON TABLE  sample_ddl       IS 'DDLサンプル用テーブル'

-- [NOT CONVERTED #3] Comment statements are not supported by ScalarDB SQL
-- COMMENT ON COLUMN sample_ddl.price IS '税抜価格'

-- [NOT CONVERTED #4] INSERT must specify the full primary key; missing ['id']
-- INSERT INTO sample_ddl (name, price) VALUES ('Widget', 1000)

-- [NOT CONVERTED #5] INSERT must specify the full primary key; missing ['id']
-- INSERT INTO sample_ddl (name, price) VALUES ('Gadget', NULL)

/* price は 0 になる */ SELECT id, name, price, price_incl FROM sample_ddl;

-- [NOT CONVERTED #7] ALTER TABLE action '(category VARCHAR2(20) DEFAULT 'GENERAL' NOT NULL)' is not supported (constraints, indexes, partitions, engine options ...)
-- -- 2. ALTER TABLE：列の追加・変更・名前変更・削除
-- ALTER TABLE sample_ddl ADD (category VARCHAR2(20) DEFAULT 'GENERAL' NOT NULL)

-- [NOT CONVERTED #8] statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- ALTER TABLE sample_ddl MODIFY (name VARCHAR2(100 CHAR))

ALTER TABLE sample_ddl RENAME COLUMN note TO description;

-- [NOT CONVERTED #10] statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- ALTER TABLE sample_ddl SET UNUSED (description)

-- [NOT CONVERTED #11] ALTER TABLE action 'DROP UNUSED COLUMNS' is not supported (constraints, indexes, partitions, engine options ...)
-- -- [ORA] 論理削除（即時）
-- ALTER TABLE sample_ddl DROP UNUSED COLUMNS

-- [NOT CONVERTED #12] ALTER TABLE action 'ADD CONSTRAINT sample_ddl_name_uk UNIQUE (name)' is not supported (constraints, indexes, partitions, engine options ...)
-- -- 物理削除
-- 
-- -- 3. 制約の追加・無効化・有効化
-- ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_name_uk UNIQUE (name)

-- [NOT CONVERTED #13] ALTER TABLE action 'ADD CONSTRAINT sample_ddl_price_ck CHECK (price >= 0)' is not supported (constraints, indexes, partitions, engine options ...)
-- ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_price_ck CHECK (price >= 0)

-- [NOT CONVERTED #14] statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- ALTER TABLE sample_ddl DISABLE CONSTRAINT sample_ddl_price_ck

-- [NOT CONVERTED #15] statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- ALTER TABLE sample_ddl ENABLE NOVALIDATE CONSTRAINT sample_ddl_price_ck

-- [NOT CONVERTED #16] CREATE TABLE ... AS SELECT / LIKE is not supported
-- -- [ORA] 既存行は検証しない
-- 
-- -- 4. CTAS（CREATE TABLE AS SELECT）
-- CREATE TABLE emp_stage AS
--   SELECT employee_id, last_name, salary, department_id
--   FROM   employees
--   WHERE  1 = 0

-- [NOT CONVERTED #17] ScalarDB secondary indexes are single-column; got ['last_name', 'first_name']. Consider making the leading column a partition key / clustering key instead
-- -- 構造のみコピー
-- 
-- -- 5. インデックス
-- CREATE INDEX emp_name_ix       ON employees (last_name, first_name)

-- [NOT CONVERTED #18] ScalarDB secondary indexes are single-column; got ['UPPER(email)']. Consider making the leading column a partition key / clustering key instead
-- -- 複合
-- CREATE INDEX emp_upper_email_ix ON employees (UPPER(email))

-- [NOT CONVERTED #19] statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- -- ファンクション索引
-- CREATE BITMAP INDEX orders_status_bix ON orders (status)

-- [NOT CONVERTED #20] statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- -- [ORA] ビットマップ索引(EE)
-- ALTER INDEX emp_name_ix INVISIBLE

-- [NOT CONVERTED #21] statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- -- [ORA] 不可視索引
-- ALTER INDEX emp_name_ix VISIBLE

-- [NOT CONVERTED #22] CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB)
-- -- 6. ビュー（WITH CHECK OPTION / READ ONLY）
-- CREATE OR REPLACE VIEW emp_it_v AS
--   SELECT employee_id, last_name, salary, department_id
--   FROM   employees
--   WHERE  department_id = 60
--   WITH CHECK OPTION CONSTRAINT emp_it_v_ck

-- [NOT CONVERTED #23] CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB)
-- CREATE OR REPLACE VIEW emp_dept_v AS
--   SELECT e.employee_id, e.last_name, d.department_name, e.salary
--   FROM   employees e JOIN departments d ON d.department_id = e.department_id
--   WITH READ ONLY

-- [NOT CONVERTED #24] CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)
-- -- 7. シーケンス [ORA]
-- CREATE SEQUENCE audit_seq START WITH 1 INCREMENT BY 1 CACHE 20 NOCYCLE

SELECT audit_seq.NEXTVAL, audit_seq.CURRVAL FROM dual;

-- [NOT CONVERTED #26] statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- ALTER SEQUENCE audit_seq INCREMENT BY 10

-- [NOT CONVERTED #27] statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- -- 8. シノニム [ORA]
-- CREATE OR REPLACE SYNONYM staff FOR employees

SELECT COUNT(*) FROM staff;

-- [NOT CONVERTED #29] temporary tables are not supported
-- -- 9. 一時表 [ORA]
-- --    GLOBAL TEMPORARY：定義は永続、データはセッション/トランザクション単位
-- CREATE GLOBAL TEMPORARY TABLE gtt_work (
--   id  NUMBER,
--   val VARCHAR2(100)
-- ) ON COMMIT PRESERVE ROWS

-- [NOT CONVERTED #30] statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- -- DELETE ROWS ならコミットで消える
-- 
-- -- 10. パーティション表 [ORA]（EE の Partitioning オプション / Free 版でも利用可）
-- CREATE TABLE sales_part (
--   sale_id    NUMBER,
--   sale_date  DATE,
--   amount     NUMBER(10,2)
-- )
-- PARTITION BY RANGE (sale_date)
-- INTERVAL (NUMTOYMINTERVAL(1, 'MONTH'))          -- 月単位で自動パーティション追加
-- ( PARTITION p_before_2026 VALUES LESS THAN (DATE '2026-01-01') )

INSERT INTO sales_part VALUES (1, '2025-12-31', 100);

INSERT INTO sales_part VALUES (2, '2026-03-15', 200);

COMMIT;

SELECT partition_name, high_value FROM user_tab_partitions WHERE table_name = 'SALES_PART';

TRUNCATE TABLE emp_stage;

DROP TABLE gtt_work;

DROP TABLE sales_part;

-- [NOT CONVERTED #38] Invalid expression / Unexpected token. Line 2, Col: 26.
-- -- ごみ箱へ
-- FLASHBACK TABLE sales_part TO BEFORE DROP

DROP TABLE sales_part;
