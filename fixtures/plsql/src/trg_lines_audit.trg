-- カテゴリ: trigger-dblink
-- 期待判定: REDESIGN。3 つのイベントで発火し、本体は INSERTING / UPDATING / DELETING で分岐する
-- （Issue #29 の 25。実装と一緒に書いたので holdout ではない）。
CREATE OR REPLACE TRIGGER trg_lines_audit
AFTER INSERT OR UPDATE OF qty OR DELETE ON order_lines
FOR EACH ROW
BEGIN
  IF INSERTING THEN
    INSERT INTO audit_log (audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by)
    VALUES (seq_audit_id.NEXTVAL, 'ORDER_LINES', TO_CHAR(:NEW.line_no), 'INSERT',
            NULL, TO_CHAR(:NEW.qty), SYSTIMESTAMP, USER);
  ELSIF UPDATING THEN
    INSERT INTO audit_log (audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by)
    VALUES (seq_audit_id.NEXTVAL, 'ORDER_LINES', TO_CHAR(:NEW.line_no), 'UPDATE',
            TO_CHAR(:OLD.qty), TO_CHAR(:NEW.qty), SYSTIMESTAMP, USER);
  ELSE
    INSERT INTO audit_log (audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by)
    VALUES (seq_audit_id.NEXTVAL, 'ORDER_LINES', TO_CHAR(:OLD.line_no), 'DELETE',
            TO_CHAR(:OLD.qty), NULL, SYSTIMESTAMP, USER);
  END IF;
END;
/
