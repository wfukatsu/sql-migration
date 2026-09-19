-- カテゴリ: simple-crud, trigger-dblink（Issue #29 の 25。実装と一緒に書いたので holdout ではない）
CREATE OR REPLACE PACKAGE BODY pkg_line_edit AS

  PROCEDURE add_line(p_order_id IN NUMBER, p_line_no IN NUMBER, p_product_id IN NUMBER, p_qty IN NUMBER,
                     p_unit_price IN NUMBER) IS
  BEGIN
    INSERT INTO order_lines (order_id, line_no, product_id, qty, unit_price)
    VALUES (p_order_id, p_line_no, p_product_id, p_qty, p_unit_price);
  END add_line;

  PROCEDURE change_qty(p_order_id IN NUMBER, p_line_no IN NUMBER, p_qty IN NUMBER) IS
  BEGIN
    UPDATE order_lines SET qty = p_qty WHERE order_id = p_order_id AND line_no = p_line_no;
  END change_qty;

  PROCEDURE remove_line(p_order_id IN NUMBER, p_line_no IN NUMBER) IS
  BEGIN
    DELETE FROM order_lines WHERE order_id = p_order_id AND line_no = p_line_no;
  END remove_line;

  PROCEDURE void_entry(p_entry_id IN NUMBER) IS
  BEGIN
    DELETE FROM inventory_tx WHERE entry_id = p_entry_id;
  END void_entry;

END pkg_line_edit;
/
