-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_4_2_cursor_for_loop AS
  CURSOR c_dept_emp (p_dept NUMBER, p_min NUMBER DEFAULT 0) IS
    SELECT last_name, salary FROM employees
    WHERE  department_id = p_dept AND salary >= p_min;
BEGIN
  FOR r IN c_dept_emp(80, 9000) LOOP
    DBMS_OUTPUT.PUT_LINE(r.last_name || ' : ' || r.salary);
  END LOOP;

  -- 暗黙カーソル FOR ループ（SELECT を直接書く）
  FOR d IN (SELECT department_name FROM departments ORDER BY 1) LOOP
    DBMS_OUTPUT.PUT_LINE('部門: ' || d.department_name);
  END LOOP;
END;
/
