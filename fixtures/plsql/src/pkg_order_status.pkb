-- カテゴリ: select-into-exception
-- 期待判定: AUTO/REVIEW。0 件 / 複数件の意味を保つ必要がある（設計書 §6.4）。
-- status_for_customer は主キーでない列で SELECT INTO しており、複数行で TOO_MANY_ROWS になる。
CREATE OR REPLACE PACKAGE BODY pkg_order_status AS

  FUNCTION status_of(p_order_id IN NUMBER) RETURN VARCHAR2 IS
    v_status orders.status%TYPE;
  BEGIN
    SELECT status INTO v_status FROM orders WHERE order_id = p_order_id;
    RETURN v_status;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20020, 'order not found: ' || p_order_id);
  END status_of;

  PROCEDURE status_for_customer(p_customer_id IN NUMBER, p_status OUT VARCHAR2) IS
  BEGIN
    SELECT status INTO p_status FROM orders WHERE customer_id = p_customer_id;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      p_status := 'NONE';
    WHEN TOO_MANY_ROWS THEN
      p_status := 'MULTIPLE';
  END status_for_customer;

  PROCEDURE assert_open(p_order_id IN NUMBER) IS
    v_status orders.status%TYPE;
  BEGIN
    v_status := status_of(p_order_id);
    IF v_status NOT IN ('NEW', 'CONFIRMED') THEN
      RAISE e_unknown_status;
    END IF;
  EXCEPTION
    WHEN e_unknown_status THEN
      RAISE_APPLICATION_ERROR(-20022, 'order ' || p_order_id || ' is not open: ' || v_status);
  END assert_open;

END pkg_order_status;
/
