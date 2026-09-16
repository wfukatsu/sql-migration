-- holdout2 / カテゴリ: concurrency-lock, simple-crud
CREATE OR REPLACE PACKAGE BODY pkg_tier_admin AS

  PROCEDURE promote(p_customer_id IN NUMBER, p_tier IN VARCHAR2) IS
    v_current customers.tier%TYPE;
  BEGIN
    SELECT tier INTO v_current FROM customers WHERE customer_id = p_customer_id FOR UPDATE;
    IF v_current = p_tier THEN
      RETURN;
    END IF;
    UPDATE customers SET tier = p_tier WHERE customer_id = p_customer_id;
  END promote;

  PROCEDURE set_credit_limit(p_customer_id IN NUMBER, p_limit IN NUMBER) IS
  BEGIN
    UPDATE customers SET credit_limit = p_limit WHERE customer_id = p_customer_id;
  END set_credit_limit;

END pkg_tier_admin;
/
