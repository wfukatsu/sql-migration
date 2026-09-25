-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_1_variables AS
  c_tax      CONSTANT NUMBER := 0.10;
  v_name     employees.last_name%TYPE;        -- 列の型に追従
  v_emp      employees%ROWTYPE;               -- 行全体の型
  v_count    PLS_INTEGER := 0;
  v_flag     BOOLEAN := TRUE;                 -- PL/SQL の BOOLEAN（SQL では 23ai から）
  v_today    DATE := TRUNC(SYSDATE);
BEGIN
  SELECT last_name INTO v_name FROM employees WHERE employee_id = 100;
  SELECT *         INTO v_emp  FROM employees WHERE employee_id = 103;

  DBMS_OUTPUT.PUT_LINE('社長: ' || v_name);
  DBMS_OUTPUT.PUT_LINE(v_emp.first_name || ' の税込給与相当: ' || v_emp.salary * (1 + c_tax));
  IF v_flag THEN
    DBMS_OUTPUT.PUT_LINE('今日は ' || TO_CHAR(v_today, 'YYYY-MM-DD'));
  END IF;
END;
/
