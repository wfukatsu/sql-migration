-- holdout2 / カテゴリ: select-into-exception, private-call, datatype-edge
CREATE OR REPLACE PACKAGE pkg_shipment AS
  FUNCTION is_shippable(p_order_id IN NUMBER) RETURN VARCHAR2;
  PROCEDURE mark_shipped(p_order_id IN NUMBER, p_when IN TIMESTAMP);
  FUNCTION days_in_transit(p_order_id IN NUMBER) RETURN NUMBER;
END pkg_shipment;
/
