-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_5_builtin_packages AS
BEGIN
  -- 実行中処理の識別（V$SESSION.MODULE / ACTION に表示）
  DBMS_APPLICATION_INFO.SET_MODULE(module_name => 'SAMPLE_BATCH', action_name => 'STEP1');

  -- 一定時間待機（18c+。以前は DBMS_LOCK.SLEEP）
  DBMS_SESSION.SLEEP(1);

  -- 乱数
  DBMS_OUTPUT.PUT_LINE('乱数: ' || ROUND(DBMS_RANDOM.VALUE(1, 100)));

  -- 経過時間計測（1/100 秒単位）
  DBMS_OUTPUT.PUT_LINE('hsecs: ' || DBMS_UTILITY.GET_TIME);

  DBMS_APPLICATION_INFO.SET_MODULE(NULL, NULL);
END;
/
