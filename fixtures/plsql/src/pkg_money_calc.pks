-- カテゴリ: datatype-edge
CREATE OR REPLACE PACKAGE pkg_money_calc AS
  FUNCTION rounded_total(p_order_id IN NUMBER) RETURN NUMBER;
  FUNCTION days_since_order(p_order_id IN NUMBER) RETURN NUMBER;
  FUNCTION display_note(p_order_id IN NUMBER) RETURN VARCHAR2;
  PROCEDURE apply_rate(p_order_id IN NUMBER, p_rate IN NUMBER);
END pkg_money_calc;
/
