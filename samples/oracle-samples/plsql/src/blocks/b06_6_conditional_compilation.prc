-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_6_conditional_compilation AS
BEGIN
  $IF DBMS_DB_VERSION.VERSION >= 23 $THEN
    DBMS_OUTPUT.PUT_LINE('23ai 以降の実装');
  $ELSE
    DBMS_OUTPUT.PUT_LINE('23ai 未満の実装');
  $END
END;
/
