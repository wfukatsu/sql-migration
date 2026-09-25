-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_4_collection_in_sql AS
  v_list emp_grade_tab;
  v_cnt  NUMBER;
BEGIN
  SELECT emp_grade_t(employee_id, last_name, 'X')
  BULK   COLLECT INTO v_list
  FROM   employees WHERE department_id = 60;

  SELECT COUNT(*) INTO v_cnt FROM TABLE(v_list) WHERE last_name LIKE 'H%';
  DBMS_OUTPUT.PUT_LINE('H で始まる IT 社員: ' || v_cnt);
END;
/
