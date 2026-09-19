CREATE OR REPLACE PROCEDURE prc_touch_quietly(p_id IN NUMBER) IS
BEGIN
  UPDATE products SET name = name WHERE product_id = p_id;
EXCEPTION
  WHEN OTHERS THEN
    -- On the target this also swallows a transaction conflict, and the caller carries on with a dead transaction.
    NULL;
END prc_touch_quietly;
/
