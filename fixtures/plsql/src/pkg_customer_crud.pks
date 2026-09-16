-- カテゴリ: simple-crud
CREATE OR REPLACE PACKAGE pkg_customer_crud AS
  PROCEDURE create_customer(p_customer_id IN NUMBER, p_name IN VARCHAR2, p_email IN VARCHAR2, p_tier IN VARCHAR2);
  PROCEDURE update_email(p_customer_id IN NUMBER, p_email IN VARCHAR2);
  PROCEDURE delete_customer(p_customer_id IN NUMBER);
  FUNCTION customer_name(p_customer_id IN NUMBER) RETURN VARCHAR2;
END pkg_customer_crud;
/
