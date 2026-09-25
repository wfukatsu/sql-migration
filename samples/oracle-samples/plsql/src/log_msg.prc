-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE PROCEDURE log_msg (p_msg VARCHAR2) AS
  PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
  INSERT INTO emp_audit (action, message) VALUES ('LOG', p_msg);
  COMMIT;                                       -- 自身のトランザクションのみ確定
END;
/
