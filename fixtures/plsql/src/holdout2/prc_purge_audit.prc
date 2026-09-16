-- holdout2 / カテゴリ: commit-in-routine, cursor-loop
CREATE OR REPLACE PROCEDURE prc_purge_audit(p_keep_days IN NUMBER) IS
  CURSOR c_old IS
    SELECT audit_id FROM audit_log WHERE changed_at < SYSTIMESTAMP - p_keep_days;
  v_id     audit_log.audit_id%TYPE;
  v_purged NUMBER := 0;
BEGIN
  OPEN c_old;
  LOOP
    FETCH c_old INTO v_id;
    EXIT WHEN c_old%NOTFOUND;
    DELETE FROM audit_log WHERE audit_id = v_id;
    v_purged := v_purged + 1;
    IF MOD(v_purged, 200) = 0 THEN
      COMMIT;
    END IF;
  END LOOP;
  CLOSE c_old;
  COMMIT;
END prc_purge_audit;
/
