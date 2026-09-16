-- カテゴリ: trigger-dblink
-- 期待判定: REDESIGN。監査 trigger は before/after 値と失敗時挙動を保ったまま
-- interceptor / outbox へ移す。全書込経路を統制する必要がある（設計書 §6.9）。
CREATE OR REPLACE TRIGGER trg_orders_audit
AFTER UPDATE OF status ON orders
FOR EACH ROW
WHEN (OLD.status <> NEW.status)
BEGIN
  INSERT INTO audit_log (audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by)
  VALUES (seq_audit_id.NEXTVAL, 'ORDERS', TO_CHAR(:NEW.order_id), 'UPDATE',
          :OLD.status, :NEW.status, SYSTIMESTAMP, USER);
END;
/
