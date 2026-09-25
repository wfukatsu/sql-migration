-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE TRIGGER emp_dept_cap_trg
  FOR UPDATE OF salary ON employees
  COMPOUND TRIGGER
    TYPE t_ids IS TABLE OF employees.department_id%TYPE INDEX BY PLS_INTEGER;
    g_depts t_ids;

  AFTER EACH ROW IS
  BEGIN
    IF :NEW.department_id IS NOT NULL THEN
      g_depts(:NEW.department_id) := :NEW.department_id;
    END IF;
  END AFTER EACH ROW;

  AFTER STATEMENT IS
    v_total NUMBER;
    d       PLS_INTEGER := g_depts.FIRST;
  BEGIN
    WHILE d IS NOT NULL LOOP
      SELECT SUM(salary) INTO v_total FROM employees WHERE department_id = d;
      IF v_total > 200000 THEN
        RAISE_APPLICATION_ERROR(-20040, '部門 ' || d || ' の人件費上限超過');
      END IF;
      d := g_depts.NEXT(d);
    END LOOP;
  END AFTER STATEMENT;
END emp_dept_cap_trg;
/
