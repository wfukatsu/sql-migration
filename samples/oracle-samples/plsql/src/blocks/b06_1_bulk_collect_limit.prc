-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_1_bulk_collect_limit AS
  CURSOR c IS SELECT employee_id, last_name, salary FROM employees;
  TYPE t_rows IS TABLE OF c%ROWTYPE;
  v_rows  t_rows;
  v_total PLS_INTEGER := 0;
BEGIN
  OPEN c;
  LOOP
    FETCH c BULK COLLECT INTO v_rows LIMIT 5;     -- 5 件ずつ（実運用は 100～1000 程度）
    EXIT WHEN v_rows.COUNT = 0;                   -- %NOTFOUND ではなく COUNT で判定
    v_total := v_total + v_rows.COUNT;
    DBMS_OUTPUT.PUT_LINE('batch: ' || v_rows.COUNT || ' 件');
  END LOOP;
  CLOSE c;
  DBMS_OUTPUT.PUT_LINE('合計: ' || v_total);
END;
/
