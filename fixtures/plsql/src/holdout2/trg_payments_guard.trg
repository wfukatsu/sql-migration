-- holdout2 / カテゴリ: trigger-dblink
CREATE OR REPLACE TRIGGER trg_payments_guard
BEFORE INSERT ON payments
FOR EACH ROW
DECLARE
  v_status orders.status%TYPE;
BEGIN
  SELECT status INTO v_status FROM orders WHERE order_id = :NEW.order_id;
  IF v_status = 'CANCELLED' THEN
    RAISE_APPLICATION_ERROR(-20072, 'cannot pay a cancelled order: ' || :NEW.order_id);
  END IF;
END;
/
