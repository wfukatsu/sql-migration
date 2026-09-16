-- カテゴリ: private-call
CREATE OR REPLACE PACKAGE pkg_order_pricing AS
  FUNCTION order_total(p_order_id IN NUMBER) RETURN NUMBER;
  PROCEDURE reprice_order(p_order_id IN NUMBER);
END pkg_order_pricing;
/
