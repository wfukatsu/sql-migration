-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_3_implicit_cursor_attrs AS
BEGIN
  UPDATE employees SET salary = salary + 100 WHERE department_id = 50;
  DBMS_OUTPUT.PUT_LINE('更新件数: ' || SQL%ROWCOUNT);

  DELETE FROM employees WHERE employee_id = -1;
  IF SQL%NOTFOUND THEN
    DBMS_OUTPUT.PUT_LINE('削除対象なし');
  END IF;
  ROLLBACK;
END;
/
