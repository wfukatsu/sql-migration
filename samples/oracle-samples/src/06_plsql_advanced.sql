--------------------------------------------------------------------------------
-- 06_plsql_advanced.sql : バルク処理 / 動的SQL / テーブルファンクション / 組込みパッケージ
--   参照: PL/SQL Language Reference
--     Ch.12 PL/SQL Optimization and Tuning (BULK COLLECT / FORALL / パイプライン)
--     Ch.8  PL/SQL Dynamic SQL
--   参照: PL/SQL Packages and Types Reference（DBMS_SQL / DBMS_ASSERT / DBMS_SCHEDULER 等）
--   前提: 00_setup.sql 実行済み
--------------------------------------------------------------------------------
SET ECHO ON
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR CONTINUE

CREATE TABLE bulk_target (
  employee_id NUMBER PRIMARY KEY,
  last_name   VARCHAR2(25),
  salary      NUMBER CONSTRAINT bulk_target_sal_ck CHECK (salary < 15000)
);

--==============================================================================
-- 1. BULK COLLECT（LIMIT 付きで大量件数でもメモリを抑える）
--==============================================================================
DECLARE
  CURSOR c IS SELECT employee_id, last_name, salary FROM employees;
  TYPE t_rows IS TABLE OF c%ROWTYPE;
  v_rows  t_rows;
  v_total PLS_INTEGER := 0;
BEGIN
  OPEN c;
  LOOP
    FETCH c BULK COLLECT INTO v_rows LIMIT 5;     -- 5 件ずつ（実運用は 100～1000 程度）
    EXIT WHEN v_rows.COUNT = 0;                   -- %NOTFOUND ではなく COUNT で判定
    v_total := v_total + v_rows.COUNT;
    DBMS_OUTPUT.PUT_LINE('batch: ' || v_rows.COUNT || ' 件');
  END LOOP;
  CLOSE c;
  DBMS_OUTPUT.PUT_LINE('合計: ' || v_total);
END;
/

--==============================================================================
-- 2. FORALL + SAVE EXCEPTIONS（一括DML と行単位エラーの回収）
--==============================================================================
DECLARE
  TYPE t_ids   IS TABLE OF employees.employee_id%TYPE;
  TYPE t_names IS TABLE OF employees.last_name%TYPE;
  TYPE t_sals  IS TABLE OF employees.salary%TYPE;
  v_ids   t_ids;
  v_names t_names;
  v_sals  t_sals;
  e_bulk_errors EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_bulk_errors, -24381);
BEGIN
  SELECT employee_id, last_name, salary
  BULK   COLLECT INTO v_ids, v_names, v_sals
  FROM   employees;

  FORALL i IN 1 .. v_ids.COUNT SAVE EXCEPTIONS
    INSERT INTO bulk_target VALUES (v_ids(i), v_names(i), v_sals(i));
  COMMIT;
EXCEPTION
  WHEN e_bulk_errors THEN
    DBMS_OUTPUT.PUT_LINE('成功: ' || SQL%ROWCOUNT || ' 件 / 失敗: ' || SQL%BULK_EXCEPTIONS.COUNT || ' 件');
    FOR j IN 1 .. SQL%BULK_EXCEPTIONS.COUNT LOOP
      DBMS_OUTPUT.PUT_LINE('  idx=' || SQL%BULK_EXCEPTIONS(j).ERROR_INDEX ||
                           ' id=' || v_ids(SQL%BULK_EXCEPTIONS(j).ERROR_INDEX) ||
                           ' ' || SQLERRM(-SQL%BULK_EXCEPTIONS(j).ERROR_CODE));
    END LOOP;
    COMMIT;   -- 成功分は確定
END;
/

-- 2-2. FORALL + RETURNING BULK COLLECT INTO、SQL%BULK_ROWCOUNT
DECLARE
  TYPE t_num IS TABLE OF NUMBER;
  v_depts   t_num := t_num(60, 80, 99);
  v_new_sal t_num;
BEGIN
  FORALL i IN 1 .. v_depts.COUNT
    UPDATE employees SET salary = salary + 10
    WHERE  department_id = v_depts(i)
    RETURNING salary BULK COLLECT INTO v_new_sal;

  FOR i IN 1 .. v_depts.COUNT LOOP
    DBMS_OUTPUT.PUT_LINE('部門 ' || v_depts(i) || ': ' || SQL%BULK_ROWCOUNT(i) || ' 件更新');
  END LOOP;
  DBMS_OUTPUT.PUT_LINE('更新後の値の件数: ' || v_new_sal.COUNT);
  ROLLBACK;
END;
/

--==============================================================================
-- 3. 動的 SQL（ネイティブ動的SQL: EXECUTE IMMEDIATE / OPEN FOR）
--==============================================================================
DECLARE
  v_cnt   NUMBER;
  v_table VARCHAR2(128) := 'EMPLOYEES';
  v_dept  NUMBER := 80;
  v_name  employees.last_name%TYPE;
  rc      SYS_REFCURSOR;
BEGIN
  -- 3-1. バインド変数（USING）で値を渡す。識別子は DBMS_ASSERT で検証して連結
  EXECUTE IMMEDIATE
    'SELECT COUNT(*) FROM ' || DBMS_ASSERT.SIMPLE_SQL_NAME(v_table) ||
    ' WHERE department_id = :d'
    INTO v_cnt USING v_dept;
  DBMS_OUTPUT.PUT_LINE('部門80の人数: ' || v_cnt);

  -- 3-2. DML + RETURNING
  EXECUTE IMMEDIATE
    'UPDATE employees SET salary = salary WHERE employee_id = :id RETURNING last_name INTO :n'
    USING 100 RETURNING INTO v_name;
  DBMS_OUTPUT.PUT_LINE('対象: ' || v_name);

  -- 3-3. DDL（静的 SQL では書けない）
  EXECUTE IMMEDIATE 'CREATE TABLE dyn_tmp (id NUMBER)';
  EXECUTE IMMEDIATE 'DROP TABLE dyn_tmp PURGE';

  -- 3-4. 動的な複数行問合せ
  OPEN rc FOR 'SELECT last_name FROM employees WHERE salary > :s ORDER BY 1' USING 15000;
  LOOP
    FETCH rc INTO v_name;
    EXIT WHEN rc%NOTFOUND;
    DBMS_OUTPUT.PUT_LINE('高給: ' || v_name);
  END LOOP;
  CLOSE rc;

  -- 3-5. 動的 PL/SQL ブロック（IN OUT バインド）
  v_cnt := 1;
  EXECUTE IMMEDIATE 'BEGIN :x := :x * 10; END;' USING IN OUT v_cnt;
  DBMS_OUTPUT.PUT_LINE('動的PL/SQL結果: ' || v_cnt);
  ROLLBACK;
END;
/

-- 3-6. DBMS_SQL：列数・列名が実行時まで分からない問合せ
DECLARE
  c      INTEGER := DBMS_SQL.OPEN_CURSOR;
  n      INTEGER;
  cols   DBMS_SQL.DESC_TAB2;
  ncols  INTEGER;
  v_val  VARCHAR2(4000);
BEGIN
  DBMS_SQL.PARSE(c, 'SELECT * FROM departments WHERE ROWNUM <= 2', DBMS_SQL.NATIVE);
  DBMS_SQL.DESCRIBE_COLUMNS2(c, ncols, cols);
  FOR i IN 1 .. ncols LOOP
    DBMS_SQL.DEFINE_COLUMN(c, i, v_val, 4000);
  END LOOP;
  n := DBMS_SQL.EXECUTE(c);
  WHILE DBMS_SQL.FETCH_ROWS(c) > 0 LOOP
    FOR i IN 1 .. ncols LOOP
      DBMS_SQL.COLUMN_VALUE(c, i, v_val);
      DBMS_OUTPUT.PUT(cols(i).col_name || '=' || v_val || '  ');
    END LOOP;
    DBMS_OUTPUT.NEW_LINE;
  END LOOP;
  DBMS_SQL.CLOSE_CURSOR(c);
END;
/

--==============================================================================
-- 4. スキーマレベルのオブジェクト型とパイプライン・テーブルファンクション
--==============================================================================
CREATE OR REPLACE TYPE emp_grade_t AS OBJECT (
  employee_id NUMBER,
  last_name   VARCHAR2(25),
  grade       VARCHAR2(1)
);
/
CREATE OR REPLACE TYPE emp_grade_tab AS TABLE OF emp_grade_t;
/

CREATE OR REPLACE FUNCTION emp_grades (p_dept NUMBER DEFAULT NULL)
  RETURN emp_grade_tab PIPELINED
AS
BEGIN
  FOR r IN (SELECT employee_id, last_name, salary FROM employees
            WHERE  p_dept IS NULL OR department_id = p_dept) LOOP
    PIPE ROW (emp_grade_t(r.employee_id, r.last_name,
                          CASE WHEN r.salary >= 10000 THEN 'A'
                               WHEN r.salary >=  5000 THEN 'B' ELSE 'C' END));
  END LOOP;
  RETURN;
END;
/

-- 表のように SELECT できる（18c+ は TABLE() 省略可）
SELECT * FROM TABLE(emp_grades(80)) ORDER BY grade, last_name;

-- コレクションを SQL で展開（MEMBER OF / TABLE 演算子）
DECLARE
  v_list emp_grade_tab;
  v_cnt  NUMBER;
BEGIN
  SELECT emp_grade_t(employee_id, last_name, 'X')
  BULK   COLLECT INTO v_list
  FROM   employees WHERE department_id = 60;

  SELECT COUNT(*) INTO v_cnt FROM TABLE(v_list) WHERE last_name LIKE 'H%';
  DBMS_OUTPUT.PUT_LINE('H で始まる IT 社員: ' || v_cnt);
END;
/

--==============================================================================
-- 5. よく使う組込みパッケージ
--==============================================================================
BEGIN
  -- 実行中処理の識別（V$SESSION.MODULE / ACTION に表示）
  DBMS_APPLICATION_INFO.SET_MODULE(module_name => 'SAMPLE_BATCH', action_name => 'STEP1');

  -- 一定時間待機（18c+。以前は DBMS_LOCK.SLEEP）
  DBMS_SESSION.SLEEP(1);

  -- 乱数
  DBMS_OUTPUT.PUT_LINE('乱数: ' || ROUND(DBMS_RANDOM.VALUE(1, 100)));

  -- 経過時間計測（1/100 秒単位）
  DBMS_OUTPUT.PUT_LINE('hsecs: ' || DBMS_UTILITY.GET_TIME);

  DBMS_APPLICATION_INFO.SET_MODULE(NULL, NULL);
END;
/

-- 5-2. ジョブ登録（DBMS_SCHEDULER）※ CREATE JOB 権限が必要。登録のみで無効状態
BEGIN
  DBMS_SCHEDULER.CREATE_JOB(
    job_name        => 'SAMPLE_NIGHTLY_JOB',
    job_type        => 'PLSQL_BLOCK',
    job_action      => 'BEGIN DBMS_STATS.GATHER_SCHEMA_STATS(USER); END;',
    start_date      => SYSTIMESTAMP,
    repeat_interval => 'FREQ=DAILY; BYHOUR=2; BYMINUTE=0',
    enabled         => FALSE,
    comments        => 'サンプル: 毎日2時に統計収集');
END;
/
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
BEGIN
  $IF DBMS_DB_VERSION.VERSION >= 23 $THEN
    DBMS_OUTPUT.PUT_LINE('23ai 以降の実装');
  $ELSE
    DBMS_OUTPUT.PUT_LINE('23ai 未満の実装');
  $END
END;
/
