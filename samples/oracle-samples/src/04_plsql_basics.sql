--------------------------------------------------------------------------------
-- 04_plsql_basics.sql : PL/SQL 基本構文のサンプル
--   参照: PL/SQL Language Reference
--     Ch.3 Language Fundamentals / Ch.4 Data Types / Ch.5 Control Statements /
--     Ch.6 Collections and Records / Ch.7 Static SQL / Ch.12 Error Handling
--   前提: 00_setup.sql 実行済み
--------------------------------------------------------------------------------
SET ECHO ON
SET SERVEROUTPUT ON SIZE UNLIMITED
WHENEVER SQLERROR CONTINUE

--==============================================================================
-- 1. 無名ブロックと変数宣言（%TYPE / %ROWTYPE / 定数）
--==============================================================================
DECLARE
  c_tax      CONSTANT NUMBER := 0.10;
  v_name     employees.last_name%TYPE;        -- 列の型に追従
  v_emp      employees%ROWTYPE;               -- 行全体の型
  v_count    PLS_INTEGER := 0;
  v_flag     BOOLEAN := TRUE;                 -- PL/SQL の BOOLEAN（SQL では 23ai から）
  v_today    DATE := TRUNC(SYSDATE);
BEGIN
  SELECT last_name INTO v_name FROM employees WHERE employee_id = 100;
  SELECT *         INTO v_emp  FROM employees WHERE employee_id = 103;

  DBMS_OUTPUT.PUT_LINE('社長: ' || v_name);
  DBMS_OUTPUT.PUT_LINE(v_emp.first_name || ' の税込給与相当: ' || v_emp.salary * (1 + c_tax));
  IF v_flag THEN
    DBMS_OUTPUT.PUT_LINE('今日は ' || TO_CHAR(v_today, 'YYYY-MM-DD'));
  END IF;
END;
/

--==============================================================================
-- 2. 制御構造（IF / CASE / LOOP / WHILE / FOR / CONTINUE / EXIT / ラベル）
--==============================================================================
DECLARE
  v_sal   employees.salary%TYPE;
  v_grade VARCHAR2(10);
  i       PLS_INTEGER := 0;
BEGIN
  SELECT salary INTO v_sal FROM employees WHERE employee_id = 104;

  -- IF-ELSIF-ELSE
  IF v_sal >= 10000 THEN
    v_grade := 'A';
  ELSIF v_sal >= 5000 THEN
    v_grade := 'B';
  ELSE
    v_grade := 'C';
  END IF;

  -- CASE 文（検索 CASE）
  CASE v_grade
    WHEN 'A' THEN DBMS_OUTPUT.PUT_LINE('上位');
    WHEN 'B' THEN DBMS_OUTPUT.PUT_LINE('中位');
    ELSE          DBMS_OUTPUT.PUT_LINE('一般');
  END CASE;

  -- 基本 LOOP + EXIT WHEN
  LOOP
    i := i + 1;
    EXIT WHEN i > 3;
    DBMS_OUTPUT.PUT_LINE('LOOP ' || i);
  END LOOP;

  -- WHILE
  WHILE i > 0 LOOP
    i := i - 1;
  END LOOP;

  -- FOR（REVERSE・CONTINUE WHEN）
  FOR j IN REVERSE 1 .. 6 LOOP
    CONTINUE WHEN MOD(j, 2) = 0;
    DBMS_OUTPUT.PUT_LINE('奇数: ' || j);
  END LOOP;

  -- ラベル付きネストループ
  <<outer_loop>>
  FOR a IN 1 .. 3 LOOP
    FOR b IN 1 .. 3 LOOP
      EXIT outer_loop WHEN a * b = 4;
      DBMS_OUTPUT.PUT_LINE(a || 'x' || b || '=' || a * b);
    END LOOP;
  END LOOP outer_loop;
END;
/

--==============================================================================
-- 3. 静的 SQL と暗黙カーソル属性
--==============================================================================
BEGIN
  UPDATE employees SET salary = salary + 100 WHERE department_id = 50;
  DBMS_OUTPUT.PUT_LINE('更新件数: ' || SQL%ROWCOUNT);

  DELETE FROM employees WHERE employee_id = -1;
  IF SQL%NOTFOUND THEN
    DBMS_OUTPUT.PUT_LINE('削除対象なし');
  END IF;
  ROLLBACK;
END;
/

--==============================================================================
-- 4. カーソル
--==============================================================================
-- 4-1. 明示カーソル（OPEN / FETCH / CLOSE）
DECLARE
  CURSOR c_emp IS
    SELECT employee_id, last_name, salary FROM employees
    WHERE  department_id = 60 ORDER BY salary DESC;
  r c_emp%ROWTYPE;
BEGIN
  OPEN c_emp;
  LOOP
    FETCH c_emp INTO r;
    EXIT WHEN c_emp%NOTFOUND;
    DBMS_OUTPUT.PUT_LINE(c_emp%ROWCOUNT || ': ' || r.last_name || ' ' || r.salary);
  END LOOP;
  CLOSE c_emp;
END;
/

-- 4-2. カーソル FOR ループ（推奨。OPEN/CLOSE 不要）とパラメータ付きカーソル
DECLARE
  CURSOR c_dept_emp (p_dept NUMBER, p_min NUMBER DEFAULT 0) IS
    SELECT last_name, salary FROM employees
    WHERE  department_id = p_dept AND salary >= p_min;
BEGIN
  FOR r IN c_dept_emp(80, 9000) LOOP
    DBMS_OUTPUT.PUT_LINE(r.last_name || ' : ' || r.salary);
  END LOOP;

  -- 暗黙カーソル FOR ループ（SELECT を直接書く）
  FOR d IN (SELECT department_name FROM departments ORDER BY 1) LOOP
    DBMS_OUTPUT.PUT_LINE('部門: ' || d.department_name);
  END LOOP;
END;
/

-- 4-3. FOR UPDATE と WHERE CURRENT OF
DECLARE
  CURSOR c_upd IS
    SELECT employee_id, salary FROM employees
    WHERE  job_id = 'ST_CLERK'
    FOR UPDATE OF salary;
BEGIN
  FOR r IN c_upd LOOP
    UPDATE employees SET salary = r.salary * 1.1 WHERE CURRENT OF c_upd;
  END LOOP;
  ROLLBACK;   -- サンプルなので戻す
END;
/

-- 4-4. カーソル変数（SYS_REFCURSOR）：実行時に問合せを切り替え
DECLARE
  rc      SYS_REFCURSOR;
  v_name  VARCHAR2(50);
  v_mode  VARCHAR2(10) := 'DEPT';
BEGIN
  IF v_mode = 'DEPT' THEN
    OPEN rc FOR SELECT department_name FROM departments;
  ELSE
    OPEN rc FOR SELECT last_name FROM employees;
  END IF;
  LOOP
    FETCH rc INTO v_name;
    EXIT WHEN rc%NOTFOUND;
    DBMS_OUTPUT.PUT_LINE(v_name);
  END LOOP;
  CLOSE rc;
END;
/

--==============================================================================
-- 5. レコードとコレクション
--==============================================================================
DECLARE
  -- ユーザー定義レコード
  TYPE t_emp_rec IS RECORD (
    id    employees.employee_id%TYPE,
    name  employees.last_name%TYPE,
    sal   employees.salary%TYPE := 0
  );
  v_rec t_emp_rec;

  -- 連想配列（INDEX BY）：キーは PLS_INTEGER または VARCHAR2
  TYPE t_sal_by_name IS TABLE OF NUMBER INDEX BY VARCHAR2(30);
  v_sal t_sal_by_name;
  k     VARCHAR2(30);

  -- ネストした表
  TYPE t_names IS TABLE OF VARCHAR2(30);
  v_names t_names := t_names('Alpha', 'Bravo', 'Charlie');

  -- VARRAY（最大要素数固定）
  TYPE t_top3 IS VARRAY(3) OF NUMBER;
  v_top3 t_top3 := t_top3();
BEGIN
  v_rec.id := 1; v_rec.name := 'Test';
  DBMS_OUTPUT.PUT_LINE('record: ' || v_rec.id || ' ' || v_rec.name || ' ' || v_rec.sal);

  FOR r IN (SELECT last_name, salary FROM employees WHERE department_id = 90) LOOP
    v_sal(r.last_name) := r.salary;
  END LOOP;
  k := v_sal.FIRST;                         -- キー順に走査
  WHILE k IS NOT NULL LOOP
    DBMS_OUTPUT.PUT_LINE(k || ' => ' || v_sal(k));
    k := v_sal.NEXT(k);
  END LOOP;

  v_names.EXTEND;  v_names(v_names.LAST) := 'Delta';
  v_names.DELETE(2);                        -- 疎になる
  DBMS_OUTPUT.PUT_LINE('COUNT=' || v_names.COUNT || ' EXISTS(2)=' ||
                       CASE WHEN v_names.EXISTS(2) THEN 'Y' ELSE 'N' END);

  v_top3.EXTEND(3);
  v_top3(1) := 100; v_top3(2) := 90; v_top3(3) := 80;
  DBMS_OUTPUT.PUT_LINE('VARRAY LIMIT=' || v_top3.LIMIT);
END;
/

--==============================================================================
-- 6. 例外処理
--==============================================================================
-- 6-1. 事前定義例外
DECLARE
  v_name employees.last_name%TYPE;
BEGIN
  BEGIN
    SELECT last_name INTO v_name FROM employees WHERE employee_id = -1;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN DBMS_OUTPUT.PUT_LINE('NO_DATA_FOUND を捕捉');
  END;

  BEGIN
    SELECT last_name INTO v_name FROM employees WHERE department_id = 60;
  EXCEPTION
    WHEN TOO_MANY_ROWS THEN DBMS_OUTPUT.PUT_LINE('TOO_MANY_ROWS を捕捉');
  END;

  BEGIN
    DBMS_OUTPUT.PUT_LINE(1 / 0);
  EXCEPTION
    WHEN ZERO_DIVIDE THEN DBMS_OUTPUT.PUT_LINE('ZERO_DIVIDE を捕捉');
  END;
END;
/

-- 6-2. ユーザー定義例外・PRAGMA EXCEPTION_INIT・RAISE_APPLICATION_ERROR
DECLARE
  e_salary_too_high EXCEPTION;
  e_fk_violation    EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_fk_violation, -2291);   -- ORA-02291 に名前を付ける
  v_sal NUMBER := 50000;
BEGIN
  BEGIN
    IF v_sal > 40000 THEN
      RAISE e_salary_too_high;
    END IF;
  EXCEPTION
    WHEN e_salary_too_high THEN
      DBMS_OUTPUT.PUT_LINE('給与上限超過');
  END;

  BEGIN
    INSERT INTO employees (employee_id, last_name, email, hire_date, job_id, department_id)
    VALUES (999, 'X', 'X999', SYSDATE, 'NO_SUCH_JOB', 10);
  EXCEPTION
    WHEN e_fk_violation THEN
      DBMS_OUTPUT.PUT_LINE('外部キー違反: ' || SQLERRM);
  END;

  -- 呼び出し元へ業務エラーを返す（-20000 ～ -20999）
  RAISE_APPLICATION_ERROR(-20001, '業務エラーのサンプル');
EXCEPTION
  WHEN OTHERS THEN
    DBMS_OUTPUT.PUT_LINE('SQLCODE=' || SQLCODE || ' / ' || SQLERRM);
    DBMS_OUTPUT.PUT_LINE(DBMS_UTILITY.FORMAT_ERROR_BACKTRACE);   -- 発生行の特定
    ROLLBACK;
    -- 本番コードでは握りつぶさずに RAISE で再送出するのが原則
END;
/
