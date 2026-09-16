-- holdout2 / カテゴリ: select-into-exception, private-call, datatype-edge
CREATE OR REPLACE PACKAGE BODY pkg_shipment AS

  FUNCTION line_count(p_order_id IN NUMBER) RETURN NUMBER IS
    v_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO v_count FROM order_lines WHERE order_id = p_order_id;
    RETURN v_count;
  END line_count;

  FUNCTION is_shippable(p_order_id IN NUMBER) RETURN VARCHAR2 IS
    v_status orders.status%TYPE;
  BEGIN
    SELECT status INTO v_status FROM orders WHERE order_id = p_order_id;
    IF v_status <> 'CONFIRMED' THEN
      RETURN 'NO';
    END IF;
    IF line_count(p_order_id) = 0 THEN
      RETURN 'NO';
    END IF;
    RETURN 'YES';
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20070, 'order not found: ' || p_order_id);
  END is_shippable;

  PROCEDURE mark_shipped(p_order_id IN NUMBER, p_when IN TIMESTAMP) IS
  BEGIN
    UPDATE orders SET shipped_at = p_when, status = 'SHIPPED' WHERE order_id = p_order_id;
    IF SQL%ROWCOUNT = 0 THEN
      RAISE_APPLICATION_ERROR(-20071, 'order not found: ' || p_order_id);
    END IF;
  END mark_shipped;

  FUNCTION days_in_transit(p_order_id IN NUMBER) RETURN NUMBER IS
    v_shipped orders.shipped_at%TYPE;
  BEGIN
    SELECT shipped_at INTO v_shipped FROM orders WHERE order_id = p_order_id;
    IF v_shipped IS NULL THEN
      RETURN NULL;
    END IF;
    RETURN TRUNC(SYSDATE) - TRUNC(CAST(v_shipped AS DATE));
  END days_in_transit;

END pkg_shipment;
/
