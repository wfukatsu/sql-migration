-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE FUNCTION dept_name_of (p_dept_id IN NUMBER)
  RETURN VARCHAR2
  RESULT_CACHE
AS
  v_name departments.department_name%TYPE;
BEGIN
  SELECT department_name INTO v_name FROM departments WHERE department_id = p_dept_id;
  RETURN v_name;
EXCEPTION
  WHEN NO_DATA_FOUND THEN RETURN NULL;
END;
/
