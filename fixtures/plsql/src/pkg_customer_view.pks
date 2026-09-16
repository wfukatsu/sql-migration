-- カテゴリ: type-rowtype
CREATE OR REPLACE PACKAGE pkg_customer_view AS
  TYPE t_customer_rec IS RECORD (
    customer_id customers.customer_id%TYPE,
    name        customers.name%TYPE,
    tier        customers.tier%TYPE
  );

  FUNCTION load(p_customer_id IN NUMBER) RETURN customers%ROWTYPE;
  FUNCTION summary(p_customer_id IN NUMBER) RETURN t_customer_rec;
  PROCEDURE copy_limit(p_from IN NUMBER, p_to IN NUMBER);
END pkg_customer_view;
/
