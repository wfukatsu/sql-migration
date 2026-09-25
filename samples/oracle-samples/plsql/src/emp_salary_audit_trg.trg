-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE TRIGGER emp_salary_audit_trg
  AFTER UPDATE OF salary OR DELETE ON employees
  FOR EACH ROW
  WHEN (NEW.salary IS NULL OR OLD.salary <> NEW.salary)   -- WHEN 句ではコロン不要
BEGIN
  INSERT INTO emp_audit (employee_id, action, old_salary, new_salary)
  VALUES (:OLD.employee_id,
          CASE WHEN DELETING THEN 'DELETE' ELSE 'UPDATE' END,
          :OLD.salary, :NEW.salary);
END;
/
