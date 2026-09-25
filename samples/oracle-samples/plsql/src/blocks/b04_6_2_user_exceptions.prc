-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_6_2_user_exceptions AS
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
