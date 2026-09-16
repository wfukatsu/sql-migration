-- カテゴリ: private-call
-- 期待判定: AUTO/REVIEW。private の可視性は call graph で決める（設計書 §6.1）。
-- tier_discount / line_amount は spec に出ていないので private method になる。
CREATE OR REPLACE PACKAGE BODY pkg_order_pricing AS

  FUNCTION tier_discount(p_tier IN VARCHAR2) RETURN NUMBER IS
  BEGIN
    RETURN CASE p_tier
             WHEN 'GOLD'   THEN 0.10
             WHEN 'SILVER' THEN 0.05
             ELSE 0
           END;
  END tier_discount;

  FUNCTION line_amount(p_qty IN NUMBER, p_unit_price IN NUMBER) RETURN NUMBER IS
  BEGIN
    RETURN p_qty * p_unit_price;
  END line_amount;

  FUNCTION customer_tier(p_order_id IN NUMBER) RETURN VARCHAR2 IS
    v_tier customers.tier%TYPE;
  BEGIN
    SELECT c.tier INTO v_tier
      FROM customers c JOIN orders o ON o.customer_id = c.customer_id
     WHERE o.order_id = p_order_id;
    RETURN v_tier;
  END customer_tier;

  FUNCTION order_total(p_order_id IN NUMBER) RETURN NUMBER IS
    v_gross NUMBER(14,2) := 0;
    v_tier  customers.tier%TYPE;
  BEGIN
    FOR r IN (SELECT qty, unit_price FROM order_lines WHERE order_id = p_order_id) LOOP
      v_gross := v_gross + line_amount(r.qty, r.unit_price);
    END LOOP;
    v_tier := customer_tier(p_order_id);
    RETURN ROUND(v_gross * (1 - tier_discount(v_tier)), 2);
  END order_total;

  PROCEDURE reprice_order(p_order_id IN NUMBER) IS
  BEGIN
    UPDATE orders SET total_amount = order_total(p_order_id) WHERE order_id = p_order_id;
  END reprice_order;

END pkg_order_pricing;
/
