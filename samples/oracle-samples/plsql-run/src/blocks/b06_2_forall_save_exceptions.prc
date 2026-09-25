-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_2_forall_save_exceptions AS
  TYPE t_ids   IS TABLE OF employees.employee_id%TYPE;
  TYPE t_names IS TABLE OF employees.last_name%TYPE;
  TYPE t_sals  IS TABLE OF employees.salary%TYPE;
  v_ids   t_ids;
  v_names t_names;
  v_sals  t_sals;
  e_bulk_errors EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_bulk_errors, -24381);
BEGIN
  SELECT employee_id, last_name, salary
  BULK   COLLECT INTO v_ids, v_names, v_sals
  FROM   employees;

  FORALL i IN 1 .. v_ids.COUNT SAVE EXCEPTIONS
    INSERT INTO bulk_target VALUES (v_ids(i), v_names(i), v_sals(i));
  COMMIT;
EXCEPTION
  WHEN e_bulk_errors THEN
    DBMS_OUTPUT.PUT_LINE('成功: ' || SQL%ROWCOUNT || ' 件 / 失敗: ' || SQL%BULK_EXCEPTIONS.COUNT || ' 件');
    FOR j IN 1 .. SQL%BULK_EXCEPTIONS.COUNT LOOP
      DBMS_OUTPUT.PUT_LINE('  idx=' || SQL%BULK_EXCEPTIONS(j).ERROR_INDEX ||
                           ' id=' || v_ids(SQL%BULK_EXCEPTIONS(j).ERROR_INDEX) ||
                           ' ' || SQLERRM(-SQL%BULK_EXCEPTIONS(j).ERROR_CODE));
    END LOOP;
    COMMIT;   -- 成功分は確定
END;
/
