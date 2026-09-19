CREATE OR REPLACE TRIGGER trg_payments_any
AFTER INSERT OR UPDATE OR DELETE ON payments
FOR EACH ROW
BEGIN
  INSERT INTO audit_log (audit_id, table_name, action) VALUES (seq_audit_id.NEXTVAL, 'payments', 'CHANGED');
END;
/

CREATE OR REPLACE PROCEDURE prc_void_payment(p_order_id IN NUMBER) IS
BEGIN
  -- a write that can hit several rows: Oracle fires the trigger once per row, and one call cannot stand in for
  -- that, so nothing calls the trigger's body here (a DELETE by primary key is applied since #29-25)
  DELETE FROM payments WHERE order_id = p_order_id;
END prc_void_payment;
/
