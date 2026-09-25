-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE PACKAGE BODY emp_api AS
  -- プライベート変数（セッション単位で状態を保持）
  g_calls PLS_INTEGER := 0;

  -- プライベートプロシージャ
  PROCEDURE validate_pct (p_pct NUMBER) IS
  BEGIN
    g_calls := g_calls + 1;
    IF p_pct NOT BETWEEN 0 AND c_max_raise_pct THEN
      RAISE e_invalid_raise;
    END IF;
  END validate_pct;

  FUNCTION hire (p_first VARCHAR2, p_last VARCHAR2, p_email VARCHAR2,
                 p_job VARCHAR2, p_salary NUMBER, p_dept NUMBER) RETURN NUMBER IS
    v_id employees.employee_id%TYPE;
  BEGIN
    INSERT INTO employees (employee_id, first_name, last_name, email, hire_date,
                           job_id, salary, department_id)
    VALUES (emp_seq.NEXTVAL, p_first, p_last, UPPER(p_email), SYSDATE,
            p_job, p_salary, p_dept)
    RETURNING employee_id INTO v_id;
    RETURN v_id;
  EXCEPTION
    WHEN DUP_VAL_ON_INDEX THEN
      RAISE_APPLICATION_ERROR(-20020, 'メールアドレス重複: ' || p_email);
  END hire;

  PROCEDURE give_raise (p_emp_id NUMBER, p_pct NUMBER) IS
  BEGIN
    validate_pct(p_pct);
    UPDATE employees SET salary = salary * (1 + p_pct / 100) WHERE employee_id = p_emp_id;
  END give_raise;

  PROCEDURE give_raise (p_dept_id NUMBER, p_pct NUMBER, p_by_dept BOOLEAN) IS
  BEGIN
    validate_pct(p_pct);
    IF p_by_dept THEN
      UPDATE employees SET salary = salary * (1 + p_pct / 100) WHERE department_id = p_dept_id;
    END IF;
  END give_raise;

  FUNCTION get_by_dept (p_dept_id NUMBER) RETURN SYS_REFCURSOR IS
    rc SYS_REFCURSOR;
  BEGIN
    OPEN rc FOR
      SELECT employee_id, last_name, salary FROM employees
      WHERE  department_id = p_dept_id ORDER BY employee_id;
    RETURN rc;                                   -- 呼び出し側（Java 等）で結果セットとして受け取る
  END get_by_dept;

  FUNCTION call_count RETURN PLS_INTEGER IS
  BEGIN
    RETURN g_calls;
  END call_count;

BEGIN
  -- 初期化部：セッションで最初にパッケージが参照されたとき 1 回だけ実行
  g_calls := 0;
END emp_api;
/
