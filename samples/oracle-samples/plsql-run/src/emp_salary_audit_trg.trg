-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE TRIGGER emp_salary_audit_trg
  AFTER UPDATE OF salary OR DELETE ON employees
  FOR EACH ROW
  WHEN (NEW.salary IS NULL OR OLD.salary <> NEW.salary)   -- WHEN 句ではコロン不要
DECLARE
  v_action emp_audit.action%TYPE;
BEGIN
  IF DELETING THEN v_action := 'DELETE'; ELSE v_action := 'UPDATE'; END IF;   -- 原文は VALUES の中の CASE WHEN DELETING（ORA-00984）
  INSERT INTO emp_audit (employee_id, action, old_salary, new_salary)
  VALUES (:OLD.employee_id, v_action, :OLD.salary, :NEW.salary);
END;
/
