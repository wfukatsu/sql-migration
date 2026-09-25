-- 出自: src/06_plsql_advanced.sql
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
