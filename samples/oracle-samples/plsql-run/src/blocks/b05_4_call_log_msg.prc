-- src/05_plsql_units.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b05_4_call_log_msg AS
BEGIN
  UPDATE employees SET salary = salary + 1 WHERE employee_id = 100;
  log_msg('社長の給与を変更しようとした');
  ROLLBACK;                                     -- UPDATE は戻るがログは残る
END;
/
