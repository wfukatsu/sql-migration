-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_4_4_ref_cursor AS
  rc      SYS_REFCURSOR;
  v_name  VARCHAR2(50);
  v_mode  VARCHAR2(10) := 'DEPT';
BEGIN
  IF v_mode = 'DEPT' THEN
    OPEN rc FOR SELECT department_name FROM departments;
  ELSE
    OPEN rc FOR SELECT last_name FROM employees;
  END IF;
  LOOP
    FETCH rc INTO v_name;
    EXIT WHEN rc%NOTFOUND;
    DBMS_OUTPUT.PUT_LINE(v_name);
  END LOOP;
  CLOSE rc;
END;
/
