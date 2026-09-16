-- カテゴリ: cursor-loop
-- 期待判定: REVIEW。明示 cursor の寿命と Tx 境界、Cursor FOR LOOP の N+1 とメモリ（設計書 §6.5）。
-- mark_reviewed は「書いた表を後で走査する」形ではないが、更新対象を cursor で回している。
CREATE OR REPLACE PACKAGE BODY pkg_order_report AS

  PROCEDURE count_by_status(p_status IN VARCHAR2, p_count OUT NUMBER) IS
    v_order_id    orders.order_id%TYPE;
    v_customer_id orders.customer_id%TYPE;
    v_total       orders.total_amount%TYPE;
  BEGIN
    p_count := 0;
    OPEN c_open_orders(p_status);
    LOOP
      FETCH c_open_orders INTO v_order_id, v_customer_id, v_total;
      EXIT WHEN c_open_orders%NOTFOUND;
      p_count := p_count + 1;
    END LOOP;
    CLOSE c_open_orders;
  EXCEPTION
    WHEN OTHERS THEN
      IF c_open_orders%ISOPEN THEN
        CLOSE c_open_orders;
      END IF;
      RAISE;
  END count_by_status;

  PROCEDURE mark_reviewed(p_status IN VARCHAR2) IS
  BEGIN
    FOR r IN (SELECT order_id FROM orders WHERE status = p_status ORDER BY ordered_at) LOOP
      UPDATE orders SET note = 'reviewed' WHERE order_id = r.order_id;
    END LOOP;
  END mark_reviewed;

  FUNCTION largest_order(p_customer_id IN NUMBER) RETURN NUMBER IS
    CURSOR c_amounts IS
      SELECT total_amount FROM orders WHERE customer_id = p_customer_id ORDER BY total_amount DESC;
    v_amount orders.total_amount%TYPE;
  BEGIN
    OPEN c_amounts;
    FETCH c_amounts INTO v_amount;
    IF c_amounts%NOTFOUND THEN
      v_amount := 0;
    END IF;
    CLOSE c_amounts;
    RETURN v_amount;
  END largest_order;

END pkg_order_report;
/
