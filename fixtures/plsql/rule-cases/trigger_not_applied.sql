CREATE OR REPLACE TRIGGER trg_payments_any
AFTER INSERT OR UPDATE OR DELETE ON payments
FOR EACH ROW
BEGIN
  INSERT INTO audit_log (audit_id, table_name, action) VALUES (seq_audit_id.NEXTVAL, 'payments', 'CHANGED');
END;
/

CREATE OR REPLACE PROCEDURE prc_void_payment(p_id IN NUMBER) IS
BEGIN
  -- a DELETE on a table with a trigger: nothing calls the trigger's body here
  DELETE FROM payments WHERE payment_id = p_id;
END prc_void_payment;
/
