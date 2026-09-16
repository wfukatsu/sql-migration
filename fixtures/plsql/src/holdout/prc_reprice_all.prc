-- holdout / カテゴリ: cursor-loop, commit-in-routine
CREATE OR REPLACE PROCEDURE prc_reprice_all(p_tier IN VARCHAR2) IS
  CURSOR c_orders IS
    SELECT o.order_id FROM orders o JOIN customers c ON c.customer_id = o.customer_id
     WHERE c.tier = p_tier AND o.status = 'NEW';
  v_order_id orders.order_id%TYPE;
  v_done     NUMBER := 0;
BEGIN
  OPEN c_orders;
  LOOP
    FETCH c_orders INTO v_order_id;
    EXIT WHEN c_orders%NOTFOUND;
    pkg_order_pricing.reprice_order(v_order_id);
    v_done := v_done + 1;
    IF MOD(v_done, 50) = 0 THEN
      COMMIT;
    END IF;
  END LOOP;
  CLOSE c_orders;
  COMMIT;
END prc_reprice_all;
/
