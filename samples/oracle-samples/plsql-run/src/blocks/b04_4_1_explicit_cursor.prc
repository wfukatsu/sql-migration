-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_4_1_explicit_cursor AS
  CURSOR c_emp IS
    SELECT employee_id, last_name, salary FROM employees
    WHERE  department_id = 60 ORDER BY salary DESC;
  r c_emp%ROWTYPE;
BEGIN
  OPEN c_emp;
  LOOP
    FETCH c_emp INTO r;
    EXIT WHEN c_emp%NOTFOUND;
    DBMS_OUTPUT.PUT_LINE(c_emp%ROWCOUNT || ': ' || r.last_name || ' ' || r.salary);
  END LOOP;
  CLOSE c_emp;
END;
/
