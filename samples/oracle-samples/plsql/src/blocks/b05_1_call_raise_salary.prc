-- src/05_plsql_units.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b05_1_call_raise_salary AS
  v_new employees.salary%TYPE;
BEGIN
  raise_salary(104, 10, v_new);
  DBMS_OUTPUT.PUT_LINE('位置指定: ' || v_new);
  raise_salary(p_emp_id => 107, p_new_sal => v_new);     -- p_pct は既定値 5
  DBMS_OUTPUT.PUT_LINE('名前指定: ' || v_new);
  ROLLBACK;
END;
/
