-- カテゴリ: simple-crud, trigger-dblink（Issue #29 の 25。実装と一緒に書いたので holdout ではない）
CREATE OR REPLACE PACKAGE pkg_line_edit AS
  PROCEDURE add_line(p_order_id IN NUMBER, p_line_no IN NUMBER, p_product_id IN NUMBER, p_qty IN NUMBER,
                     p_unit_price IN NUMBER);
  PROCEDURE change_qty(p_order_id IN NUMBER, p_line_no IN NUMBER, p_qty IN NUMBER);
  PROCEDURE remove_line(p_order_id IN NUMBER, p_line_no IN NUMBER);
  PROCEDURE void_entry(p_entry_id IN NUMBER);
END pkg_line_edit;
/
