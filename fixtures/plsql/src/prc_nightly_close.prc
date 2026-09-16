-- カテゴリ: commit-in-routine
-- 期待判定: REDESIGN。routine 内の COMMIT / SAVEPOINT / ROLLBACK TO は逐語変換できない（設計書 §6.7）。
-- use case 単位へ再境界化し、再試行と冪等性の方針を決める必要がある。
CREATE OR REPLACE PROCEDURE prc_nightly_close(p_batch_date IN DATE) IS
  v_processed NUMBER := 0;
BEGIN
  UPDATE batch_control SET status = 'RUNNING', last_run_at = SYSDATE WHERE batch_name = 'NIGHTLY_CLOSE';
  COMMIT;

  FOR r IN (SELECT order_id FROM orders WHERE status = 'SHIPPED' AND ordered_at < p_batch_date) LOOP
    SAVEPOINT sp_order;
    BEGIN
      UPDATE orders SET status = 'CLOSED' WHERE order_id = r.order_id;
      INSERT INTO audit_log (audit_id, table_name, key_value, action, new_value, changed_at, changed_by)
      VALUES (seq_audit_id.NEXTVAL, 'ORDERS', TO_CHAR(r.order_id), 'CLOSE', 'CLOSED', SYSTIMESTAMP, USER);
      v_processed := v_processed + 1;
      IF MOD(v_processed, 100) = 0 THEN
        COMMIT;   -- 100 件ごとに中間コミット
      END IF;
    EXCEPTION
      WHEN OTHERS THEN
        ROLLBACK TO sp_order;
        INSERT INTO audit_log (audit_id, table_name, key_value, action, new_value, changed_at, changed_by)
        VALUES (seq_audit_id.NEXTVAL, 'ORDERS', TO_CHAR(r.order_id), 'ERROR', SQLERRM, SYSTIMESTAMP, USER);
    END;
  END LOOP;

  UPDATE batch_control SET status = 'DONE' WHERE batch_name = 'NIGHTLY_CLOSE';
  COMMIT;
EXCEPTION
  WHEN OTHERS THEN
    ROLLBACK;
    UPDATE batch_control SET status = 'FAILED' WHERE batch_name = 'NIGHTLY_CLOSE';
    COMMIT;
    RAISE;
END prc_nightly_close;
/
