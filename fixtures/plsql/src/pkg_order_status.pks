-- カテゴリ: select-into-exception
CREATE OR REPLACE PACKAGE pkg_order_status AS
  e_unknown_status EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_unknown_status, -20021);

  FUNCTION status_of(p_order_id IN NUMBER) RETURN VARCHAR2;
  PROCEDURE status_for_customer(p_customer_id IN NUMBER, p_status OUT VARCHAR2);
  PROCEDURE assert_open(p_order_id IN NUMBER);
END pkg_order_status;
/
