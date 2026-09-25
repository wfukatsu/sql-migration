-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_5_2_scheduler_job AS
BEGIN
  DBMS_SCHEDULER.CREATE_JOB(
    job_name        => 'SAMPLE_NIGHTLY_JOB',
    job_type        => 'PLSQL_BLOCK',
    job_action      => 'BEGIN DBMS_STATS.GATHER_SCHEMA_STATS(USER); END;',
    start_date      => SYSTIMESTAMP,
    repeat_interval => 'FREQ=DAILY; BYHOUR=2; BYMINUTE=0',
    enabled         => FALSE,
    comments        => 'サンプル: 毎日2時に統計収集');
END;
/
