-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE TRIGGER emp_dept_upd_v_trg
  INSTEAD OF UPDATE ON emp_dept_upd_v
  FOR EACH ROW
BEGIN
  UPDATE employees
  SET    last_name     = :NEW.last_name,
         department_id = (SELECT department_id FROM departments
                          WHERE  department_name = :NEW.department_name)
  WHERE  employee_id   = :OLD.employee_id;
END;
/
