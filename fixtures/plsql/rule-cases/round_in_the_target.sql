CREATE OR REPLACE FUNCTION fn_rounded_paid(p_order_id IN NUMBER) RETURN NUMBER IS
  v_total NUMBER;
BEGIN
  -- the ROUND and the aggregate stay inside the SQL: ScalarDB SQL cannot run this as it stands, so a plan does,
  -- and what rounds (and what answers for zero rows) is the residual engine, not the Oracle-compatible runtime
  SELECT SUM(ROUND(amount, 0)) INTO v_total FROM payments WHERE order_id = p_order_id;
  RETURN v_total;
END fn_rounded_paid;
/
