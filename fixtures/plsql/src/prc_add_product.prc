-- カテゴリ: simple-crud
-- 期待判定: AUTO。単独 procedure、主キー INSERT のみ。
CREATE OR REPLACE PROCEDURE prc_add_product(
  p_product_id IN NUMBER,
  p_name       IN VARCHAR2,
  p_unit_price IN NUMBER,
  p_stock_qty  IN NUMBER DEFAULT 0
) IS
BEGIN
  INSERT INTO products (product_id, name, unit_price, stock_qty, discontinued)
  VALUES (p_product_id, p_name, p_unit_price, p_stock_qty, 'N');
END prc_add_product;
/
