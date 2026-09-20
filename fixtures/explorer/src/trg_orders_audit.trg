CREATE OR REPLACE TRIGGER trg_orders_audit
AFTER UPDATE ON orders
FOR EACH ROW
BEGIN
    INSERT INTO audit_log (log_id, table_name, detail, logged_at)
    VALUES (audit_seq.NEXTVAL, 'ORDERS', :OLD.status || ' -> ' || :NEW.status, SYSDATE);
END trg_orders_audit;
/
