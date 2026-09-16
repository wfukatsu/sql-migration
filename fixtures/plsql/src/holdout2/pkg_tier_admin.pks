-- holdout2 / カテゴリ: concurrency-lock, simple-crud
CREATE OR REPLACE PACKAGE pkg_tier_admin AS
  PROCEDURE promote(p_customer_id IN NUMBER, p_tier IN VARCHAR2);
  PROCEDURE set_credit_limit(p_customer_id IN NUMBER, p_limit IN NUMBER);
END pkg_tier_admin;
/
