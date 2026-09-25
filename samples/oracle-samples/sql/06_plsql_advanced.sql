--------------------------------------------------------------------------------
-- 06_plsql_advanced.sql : バルク処理 / 動的SQL / テーブルファンクション / 組込みパッケージ
--   参照: PL/SQL Language Reference
--     Ch.12 PL/SQL Optimization and Tuning (BULK COLLECT / FORALL / パイプライン)
--     Ch.8  PL/SQL Dynamic SQL
--   参照: PL/SQL Packages and Types Reference（DBMS_SQL / DBMS_ASSERT / DBMS_SCHEDULER 等）
--   前提: 00_setup.sql 実行済み
--------------------------------------------------------------------------------

CREATE TABLE bulk_target (
  employee_id NUMBER PRIMARY KEY,
  last_name   VARCHAR2(25),
  salary      NUMBER CONSTRAINT bulk_target_sal_ck CHECK (salary < 15000)
);

--==============================================================================
-- 1. BULK COLLECT（LIMIT 付きで大量件数でもメモリを抑える）
--==============================================================================

--==============================================================================
-- 2. FORALL + SAVE EXCEPTIONS（一括DML と行単位エラーの回収）
--==============================================================================

-- 2-2. FORALL + RETURNING BULK COLLECT INTO、SQL%BULK_ROWCOUNT

--==============================================================================
-- 3. 動的 SQL（ネイティブ動的SQL: EXECUTE IMMEDIATE / OPEN FOR）
--==============================================================================

-- 3-6. DBMS_SQL：列数・列名が実行時まで分からない問合せ

--==============================================================================
-- 4. スキーマレベルのオブジェクト型とパイプライン・テーブルファンクション
--==============================================================================


-- 表のように SELECT できる（18c+ は TABLE() 省略可）
SELECT * FROM TABLE(emp_grades(80)) ORDER BY grade, last_name;

-- コレクションを SQL で展開（MEMBER OF / TABLE 演算子）

--==============================================================================
-- 5. よく使う組込みパッケージ
--==============================================================================

-- 5-2. ジョブ登録（DBMS_SCHEDULER）※ CREATE JOB 権限が必要。登録のみで無効状態
SELECT job_name, enabled, repeat_interval FROM user_scheduler_jobs;

-- 5-3. ファイル出力（UTL_FILE）※ DBA による DIRECTORY 作成と権限付与が必要
-- CREATE DIRECTORY out_dir AS '/tmp/out';  GRANT READ, WRITE ON DIRECTORY out_dir TO <user>;
-- DECLARE
--   f UTL_FILE.FILE_TYPE;
-- BEGIN
--   f := UTL_FILE.FOPEN('OUT_DIR', 'employees.csv', 'w', 32767);
--   FOR r IN (SELECT employee_id, last_name, salary FROM employees) LOOP
--     UTL_FILE.PUT_LINE(f, r.employee_id || ',' || r.last_name || ',' || r.salary);
--   END LOOP;
--   UTL_FILE.FCLOSE(f);
-- END;
-- /

--==============================================================================
-- 6. 条件付きコンパイル（DBバージョンで分岐）
--==============================================================================
