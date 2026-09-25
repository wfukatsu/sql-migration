-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE PROCEDURE raise_salary (
  p_emp_id   IN     employees.employee_id%TYPE,
  p_pct      IN     NUMBER DEFAULT 5,
  p_new_sal     OUT employees.salary%TYPE
) AS
BEGIN
  UPDATE employees
  SET    salary = ROUND(salary * (1 + p_pct / 100), 2)
  WHERE  employee_id = p_emp_id
  RETURNING salary INTO p_new_sal;          -- 更新後の値を受け取る

  IF SQL%ROWCOUNT = 0 THEN
    RAISE_APPLICATION_ERROR(-20010, '社員が存在しません: ' || p_emp_id);
  END IF;
END raise_salary;
/
