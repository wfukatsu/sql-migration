-- src/05_plsql_units.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b05_3_call_emp_api AS
  v_id NUMBER;
BEGIN
  v_id := emp_api.hire('Hanako', 'Sato', 'hsato', 'SA_REP', 8000, 80);
  DBMS_OUTPUT.PUT_LINE('採用ID: ' || v_id);
  emp_api.give_raise(v_id, 10);
  emp_api.give_raise(p_dept_id => 60, p_pct => 3, p_by_dept => TRUE);
  BEGIN
    emp_api.give_raise(v_id, 50);
  EXCEPTION
    WHEN emp_api.e_invalid_raise THEN DBMS_OUTPUT.PUT_LINE('昇給率が不正');
  END;
  DBMS_OUTPUT.PUT_LINE('validate 呼び出し回数: ' || emp_api.call_count);
  ROLLBACK;
END;
/
