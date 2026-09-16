-- holdout / カテゴリ: select-into-exception, datatype-edge
-- TIMESTAMP WITH TIME ZONE はセッション TZ 依存を検出する対象（設計書 §5.3）。
CREATE OR REPLACE PACKAGE BODY pkg_payment AS

  FUNCTION paid_total(p_order_id IN NUMBER) RETURN NUMBER IS
    v_sum NUMBER(14,2);
  BEGIN
    SELECT SUM(amount) INTO v_sum FROM payments WHERE order_id = p_order_id;
    -- 集約は 0 件でも 1 行返るので NO_DATA_FOUND にならず NULL になる
    RETURN NVL(v_sum, 0);
  END paid_total;

  PROCEDURE record_payment(p_payment_id IN NUMBER, p_order_id IN NUMBER, p_amount IN NUMBER, p_method IN VARCHAR2) IS
    v_status orders.status%TYPE;
  BEGIN
    SELECT status INTO v_status FROM orders WHERE order_id = p_order_id;
    IF v_status = 'CANCELLED' THEN
      RAISE_APPLICATION_ERROR(-20040, 'cannot pay a cancelled order: ' || p_order_id);
    END IF;
    INSERT INTO payments (payment_id, order_id, amount, method, paid_at)
    VALUES (p_payment_id, p_order_id, p_amount, p_method, SYSTIMESTAMP);
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20041, 'order not found: ' || p_order_id);
  END record_payment;

  FUNCTION last_paid_at(p_order_id IN NUMBER) RETURN TIMESTAMP WITH TIME ZONE IS
    v_at payments.paid_at%TYPE;
  BEGIN
    SELECT MAX(paid_at) INTO v_at FROM payments WHERE order_id = p_order_id;
    RETURN v_at;
  END last_paid_at;

END pkg_payment;
/
