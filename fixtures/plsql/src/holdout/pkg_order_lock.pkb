-- holdout / カテゴリ: concurrency-lock, private-call, type-rowtype
CREATE OR REPLACE PACKAGE BODY pkg_order_lock AS

  FUNCTION is_cancellable(p_status IN VARCHAR2) RETURN BOOLEAN IS
  BEGIN
    RETURN p_status IN ('NEW', 'CONFIRMED');
  END is_cancellable;

  FUNCTION snapshot(p_order_id IN NUMBER) RETURN orders%ROWTYPE IS
    v_row orders%ROWTYPE;
  BEGIN
    SELECT * INTO v_row FROM orders WHERE order_id = p_order_id;
    RETURN v_row;
  END snapshot;

  PROCEDURE cancel(p_order_id IN NUMBER, p_reason IN VARCHAR2) IS
    v_row orders%ROWTYPE;
  BEGIN
    SELECT * INTO v_row FROM orders WHERE order_id = p_order_id FOR UPDATE WAIT 5;
    IF NOT is_cancellable(v_row.status) THEN
      RAISE_APPLICATION_ERROR(-20060, 'order ' || p_order_id || ' cannot be cancelled: ' || v_row.status);
    END IF;
    UPDATE orders SET status = 'CANCELLED', note = p_reason WHERE order_id = p_order_id;
    FOR r IN (SELECT line_no, product_id, qty FROM order_lines WHERE order_id = p_order_id) LOOP
      UPDATE products SET stock_qty = stock_qty + r.qty WHERE product_id = r.product_id;
    END LOOP;
  END cancel;

END pkg_order_lock;
/
