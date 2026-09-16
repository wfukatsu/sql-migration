-- カテゴリ: commit-in-routine
-- 期待判定: REDESIGN。Autonomous Transaction は親が失敗しても残るという意味を持つため、
-- audit / event / outbox として切り出す（設計書 §6.7）。
CREATE OR REPLACE PROCEDURE prc_audit_autonomous(
  p_table_name IN VARCHAR2,
  p_key_value  IN VARCHAR2,
  p_action     IN VARCHAR2,
  p_new_value  IN VARCHAR2
) IS
  PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
  INSERT INTO audit_log (audit_id, table_name, key_value, action, new_value, changed_at, changed_by)
  VALUES (seq_audit_id.NEXTVAL, p_table_name, p_key_value, p_action, p_new_value, SYSTIMESTAMP, USER);
  COMMIT;
EXCEPTION
  WHEN OTHERS THEN
    ROLLBACK;
    RAISE;
END prc_audit_autonomous;
/
