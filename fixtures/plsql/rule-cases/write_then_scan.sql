CREATE OR REPLACE PROCEDURE prc_write_then_scan(p_id IN NUMBER) IS
  v_count NUMBER;
BEGIN
  UPDATE orders SET status = 'CLAIMED' WHERE order_id = p_id;
  SELECT COUNT(*) INTO v_count FROM orders WHERE status = 'CLAIMED';
END prc_write_then_scan;
/
