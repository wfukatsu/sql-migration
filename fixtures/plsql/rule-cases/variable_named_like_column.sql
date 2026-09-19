CREATE OR REPLACE PROCEDURE prc_rename(product_id IN NUMBER, p_name IN VARCHAR2) IS
BEGIN
  -- Oracle reads `product_id = product_id` as the column compared with itself: every row is renamed.
  UPDATE products SET name = p_name WHERE product_id = product_id;
END prc_rename;
/
