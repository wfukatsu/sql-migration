-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_6_1_predefined_exceptions AS
  v_name employees.last_name%TYPE;
BEGIN
  BEGIN
    SELECT last_name INTO v_name FROM employees WHERE employee_id = -1;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN DBMS_OUTPUT.PUT_LINE('NO_DATA_FOUND を捕捉');
  END;

  BEGIN
    SELECT last_name INTO v_name FROM employees WHERE department_id = 60;
  EXCEPTION
    WHEN TOO_MANY_ROWS THEN DBMS_OUTPUT.PUT_LINE('TOO_MANY_ROWS を捕捉');
  END;

  BEGIN
    DBMS_OUTPUT.PUT_LINE(1 / 0);
  EXCEPTION
    WHEN ZERO_DIVIDE THEN DBMS_OUTPUT.PUT_LINE('ZERO_DIVIDE を捕捉');
  END;
END;
/
