-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_4_3_for_update_current_of AS
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
