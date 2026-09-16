-- カテゴリ: type-rowtype
-- 期待判定: AUTO/REVIEW。%ROWTYPE は列名対応の record/DTO へ、%TYPE は DDL から解決する（設計書 §5.3）。
CREATE OR REPLACE PACKAGE BODY pkg_customer_view AS

  FUNCTION load(p_customer_id IN NUMBER) RETURN customers%ROWTYPE IS
    v_row customers%ROWTYPE;
  BEGIN
    SELECT * INTO v_row FROM customers WHERE customer_id = p_customer_id;
    RETURN v_row;
  END load;

  FUNCTION summary(p_customer_id IN NUMBER) RETURN t_customer_rec IS
    v_rec t_customer_rec;
  BEGIN
    SELECT customer_id, name, tier INTO v_rec.customer_id, v_rec.name, v_rec.tier
      FROM customers WHERE customer_id = p_customer_id;
    RETURN v_rec;
  END summary;

  PROCEDURE copy_limit(p_from IN NUMBER, p_to IN NUMBER) IS
    v_limit customers.credit_limit%TYPE;
  BEGIN
    SELECT credit_limit INTO v_limit FROM customers WHERE customer_id = p_from;
    UPDATE customers SET credit_limit = v_limit WHERE customer_id = p_to;
  END copy_limit;

END pkg_customer_view;
/
