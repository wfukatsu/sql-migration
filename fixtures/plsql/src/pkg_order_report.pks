-- カテゴリ: cursor-loop
CREATE OR REPLACE PACKAGE pkg_order_report AS
  CURSOR c_open_orders(p_status IN VARCHAR2) IS
    SELECT order_id, customer_id, total_amount FROM orders WHERE status = p_status ORDER BY order_id;

  PROCEDURE count_by_status(p_status IN VARCHAR2, p_count OUT NUMBER);
  PROCEDURE mark_reviewed(p_status IN VARCHAR2);
  FUNCTION largest_order(p_customer_id IN NUMBER) RETURN NUMBER;
END pkg_order_report;
/
