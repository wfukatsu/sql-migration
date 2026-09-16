-- カテゴリ: simple-crud
-- 期待判定: AUTO。すべて主キーアクセスで、routine 内に COMMIT がない。
CREATE OR REPLACE PACKAGE BODY pkg_customer_crud AS

  PROCEDURE create_customer(p_customer_id IN NUMBER, p_name IN VARCHAR2, p_email IN VARCHAR2, p_tier IN VARCHAR2) IS
  BEGIN
    INSERT INTO customers (customer_id, name, email, tier, registered_on)
    VALUES (p_customer_id, p_name, p_email, NVL(p_tier, 'BRONZE'), SYSDATE);
  END create_customer;

  PROCEDURE update_email(p_customer_id IN NUMBER, p_email IN VARCHAR2) IS
  BEGIN
    UPDATE customers SET email = p_email WHERE customer_id = p_customer_id;
    IF SQL%ROWCOUNT = 0 THEN
      RAISE_APPLICATION_ERROR(-20010, 'customer not found: ' || p_customer_id);
    END IF;
  END update_email;

  PROCEDURE delete_customer(p_customer_id IN NUMBER) IS
  BEGIN
    DELETE FROM customers WHERE customer_id = p_customer_id;
  END delete_customer;

  FUNCTION customer_name(p_customer_id IN NUMBER) RETURN VARCHAR2 IS
    v_name customers.name%TYPE;
  BEGIN
    SELECT name INTO v_name FROM customers WHERE customer_id = p_customer_id;
    RETURN v_name;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RETURN NULL;
  END customer_name;

END pkg_customer_crud;
/
