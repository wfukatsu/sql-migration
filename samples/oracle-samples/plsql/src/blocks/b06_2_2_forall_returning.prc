-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_2_2_forall_returning AS
  TYPE t_num IS TABLE OF NUMBER;
  v_depts   t_num := t_num(60, 80, 99);
  v_new_sal t_num;
BEGIN
  FORALL i IN 1 .. v_depts.COUNT
    UPDATE employees SET salary = salary + 10
    WHERE  department_id = v_depts(i)
    RETURNING salary BULK COLLECT INTO v_new_sal;

  FOR i IN 1 .. v_depts.COUNT LOOP
    DBMS_OUTPUT.PUT_LINE('部門 ' || v_depts(i) || ': ' || SQL%BULK_ROWCOUNT(i) || ' 件更新');
  END LOOP;
  DBMS_OUTPUT.PUT_LINE('更新後の値の件数: ' || v_new_sal.COUNT);
  ROLLBACK;
END;
/
