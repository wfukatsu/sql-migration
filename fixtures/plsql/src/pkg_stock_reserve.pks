-- カテゴリ: concurrency-lock
CREATE OR REPLACE PACKAGE pkg_stock_reserve AS
  PROCEDURE reserve(p_product_id IN NUMBER, p_qty IN NUMBER);
  PROCEDURE reserve_nowait(p_product_id IN NUMBER, p_qty IN NUMBER);
  FUNCTION next_payment_id RETURN NUMBER;
  PROCEDURE claim_batch(p_limit IN NUMBER);
END pkg_stock_reserve;
/
