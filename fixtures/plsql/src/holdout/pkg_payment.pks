-- holdout / カテゴリ: select-into-exception, datatype-edge
CREATE OR REPLACE PACKAGE pkg_payment AS
  FUNCTION paid_total(p_order_id IN NUMBER) RETURN NUMBER;
  PROCEDURE record_payment(p_payment_id IN NUMBER, p_order_id IN NUMBER, p_amount IN NUMBER, p_method IN VARCHAR2);
  FUNCTION last_paid_at(p_order_id IN NUMBER) RETURN TIMESTAMP WITH TIME ZONE;
END pkg_payment;
/
