CREATE OR REPLACE PROCEDURE prc_with_goto(p_id IN NUMBER) IS
  v_status VARCHAR2(20);
BEGIN
  SELECT status INTO v_status FROM orders WHERE order_id = p_id;
  IF v_status = 'CLOSED' THEN
    GOTO done;
  END IF;
  UPDATE orders SET status = 'CLOSED' WHERE order_id = p_id;
  <<done>>
  NULL;
END prc_with_goto;
/
