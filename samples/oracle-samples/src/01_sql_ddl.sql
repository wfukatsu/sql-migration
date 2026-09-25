--------------------------------------------------------------------------------
-- 01_sql_ddl.sql : DDL（データ定義言語）のサンプル
--   参照: SQL Language Reference - CREATE TABLE / ALTER TABLE / CREATE INDEX /
--         CREATE VIEW / CREATE SEQUENCE / CREATE SYNONYM
--   前提: 00_setup.sql 実行済み
--   [ORA] = Oracle 固有 / 他DBへの移行で要注意の構文
--------------------------------------------------------------------------------
SET ECHO ON
WHENEVER SQLERROR CONTINUE

-- 1. IDENTITY 列・仮想列・DEFAULT ON NULL を持つ表 (12c+)
CREATE TABLE sample_ddl (
  id          NUMBER GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1)
              CONSTRAINT sample_ddl_pk PRIMARY KEY,
  name        VARCHAR2(50 CHAR) NOT NULL,           -- CHAR 長セマンティクス
  price       NUMBER(10,2) DEFAULT ON NULL 0,       -- [ORA] NULL 挿入時も既定値
  tax_rate    NUMBER(4,3)  DEFAULT 0.1,
  price_incl  NUMBER GENERATED ALWAYS AS (ROUND(price * (1 + tax_rate), 2)) VIRTUAL, -- [ORA] 仮想列
  created_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP,
  note        CLOB
);

COMMENT ON TABLE  sample_ddl       IS 'DDLサンプル用テーブル';
COMMENT ON COLUMN sample_ddl.price IS '税抜価格';

INSERT INTO sample_ddl (name, price) VALUES ('Widget', 1000);
INSERT INTO sample_ddl (name, price) VALUES ('Gadget', NULL);   -- price は 0 になる
SELECT id, name, price, price_incl FROM sample_ddl;

-- 2. ALTER TABLE：列の追加・変更・名前変更・削除
ALTER TABLE sample_ddl ADD (category VARCHAR2(20) DEFAULT 'GENERAL' NOT NULL);
ALTER TABLE sample_ddl MODIFY (name VARCHAR2(100 CHAR));
ALTER TABLE sample_ddl RENAME COLUMN note TO description;
ALTER TABLE sample_ddl SET UNUSED (description);   -- [ORA] 論理削除（即時）
ALTER TABLE sample_ddl DROP UNUSED COLUMNS;         -- 物理削除

-- 3. 制約の追加・無効化・有効化
ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_name_uk UNIQUE (name);
ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_price_ck CHECK (price >= 0);
ALTER TABLE sample_ddl DISABLE CONSTRAINT sample_ddl_price_ck;
ALTER TABLE sample_ddl ENABLE NOVALIDATE CONSTRAINT sample_ddl_price_ck; -- [ORA] 既存行は検証しない

-- 4. CTAS（CREATE TABLE AS SELECT）
CREATE TABLE emp_stage AS
  SELECT employee_id, last_name, salary, department_id
  FROM   employees
  WHERE  1 = 0;           -- 構造のみコピー

-- 5. インデックス
CREATE INDEX emp_name_ix       ON employees (last_name, first_name);        -- 複合
CREATE INDEX emp_upper_email_ix ON employees (UPPER(email));                -- ファンクション索引
CREATE BITMAP INDEX orders_status_bix ON orders (status);                   -- [ORA] ビットマップ索引(EE)
ALTER INDEX emp_name_ix INVISIBLE;                                           -- [ORA] 不可視索引
ALTER INDEX emp_name_ix VISIBLE;

-- 6. ビュー（WITH CHECK OPTION / READ ONLY）
CREATE OR REPLACE VIEW emp_it_v AS
  SELECT employee_id, last_name, salary, department_id
  FROM   employees
  WHERE  department_id = 60
  WITH CHECK OPTION CONSTRAINT emp_it_v_ck;

CREATE OR REPLACE VIEW emp_dept_v AS
  SELECT e.employee_id, e.last_name, d.department_name, e.salary
  FROM   employees e JOIN departments d ON d.department_id = e.department_id
  WITH READ ONLY;

-- 7. シーケンス [ORA]
CREATE SEQUENCE audit_seq START WITH 1 INCREMENT BY 1 CACHE 20 NOCYCLE;
SELECT audit_seq.NEXTVAL, audit_seq.CURRVAL FROM dual;
ALTER SEQUENCE audit_seq INCREMENT BY 10;

-- 8. シノニム [ORA]
CREATE OR REPLACE SYNONYM staff FOR employees;
SELECT COUNT(*) FROM staff;

-- 9. 一時表 [ORA]
--    GLOBAL TEMPORARY：定義は永続、データはセッション/トランザクション単位
CREATE GLOBAL TEMPORARY TABLE gtt_work (
  id  NUMBER,
  val VARCHAR2(100)
) ON COMMIT PRESERVE ROWS;      -- DELETE ROWS ならコミットで消える

-- 10. パーティション表 [ORA]（EE の Partitioning オプション / Free 版でも利用可）
CREATE TABLE sales_part (
  sale_id    NUMBER,
  sale_date  DATE,
  amount     NUMBER(10,2)
)
PARTITION BY RANGE (sale_date)
INTERVAL (NUMTOYMINTERVAL(1, 'MONTH'))          -- 月単位で自動パーティション追加
( PARTITION p_before_2026 VALUES LESS THAN (DATE '2026-01-01') );

INSERT INTO sales_part VALUES (1, DATE '2025-12-31', 100);
INSERT INTO sales_part VALUES (2, DATE '2026-03-15', 200);
COMMIT;
SELECT partition_name, high_value FROM user_tab_partitions WHERE table_name = 'SALES_PART';

-- 11. TRUNCATE / DROP / FLASHBACK TABLE（ごみ箱から復元）[ORA]
TRUNCATE TABLE emp_stage;
DROP TABLE gtt_work;
DROP TABLE sales_part;                     -- ごみ箱へ
FLASHBACK TABLE sales_part TO BEFORE DROP; -- 復元
DROP TABLE sales_part PURGE;               -- 完全削除

-- 12. 23ai 以降のみ: IF [NOT] EXISTS / BOOLEAN 型 / SQL ドメイン
--     19c では ORA-00922 等になるためコメントアウトしている
-- CREATE TABLE IF NOT EXISTS flags (id NUMBER PRIMARY KEY, active BOOLEAN DEFAULT TRUE);
-- CREATE DOMAIN IF NOT EXISTS email_d AS VARCHAR2(100)
--   CONSTRAINT email_chk CHECK (REGEXP_LIKE(email_d, '^.+@.+$'));
-- DROP TABLE IF EXISTS flags;
