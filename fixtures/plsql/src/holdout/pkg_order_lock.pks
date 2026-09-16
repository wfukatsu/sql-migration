-- holdout / カテゴリ: concurrency-lock, private-call, type-rowtype
CREATE OR REPLACE PACKAGE pkg_order_lock AS
  PROCEDURE cancel(p_order_id IN NUMBER, p_reason IN VARCHAR2);
  FUNCTION snapshot(p_order_id IN NUMBER) RETURN orders%ROWTYPE;
END pkg_order_lock;
/
