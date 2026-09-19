CREATE OR REPLACE PROCEDURE prc_insert_or_update(p_id IN NUMBER, p_name IN VARCHAR2) IS
BEGIN
  INSERT INTO products (product_id, name) VALUES (p_id, p_name);
EXCEPTION
  WHEN DUP_VAL_ON_INDEX THEN
    -- Oracle runs this when the key exists. Nothing on the target raises DUP_VAL_ON_INDEX by itself.
    UPDATE products SET name = p_name WHERE product_id = p_id;
END prc_insert_or_update;
/
