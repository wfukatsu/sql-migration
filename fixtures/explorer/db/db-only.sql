-- DB にはあるが、原文としては渡されていないもの。snapshot からしか分からない。
CREATE OR REPLACE VIEW v_open_orders AS
SELECT o.order_id, o.customer_id, o.order_date, o.status
  FROM orders o
 WHERE o.status <> 'CANCELLED';

CREATE OR REPLACE VIEW v_order_lines AS
SELECT v.order_id, v.customer_id, i.line_no, i.product_id, i.quantity
  FROM v_open_orders v
  JOIN order_items i ON i.order_id = v.order_id;

CREATE OR REPLACE TRIGGER trg_items_stock_guard
BEFORE INSERT ON order_items
FOR EACH ROW
DECLARE
    v_stock products.stock_quantity%TYPE;
BEGIN
    SELECT stock_quantity INTO v_stock FROM products WHERE product_id = :NEW.product_id;
    IF v_stock < 0 THEN
        RAISE_APPLICATION_ERROR(-20020, '在庫が負です');
    END IF;
END trg_items_stock_guard;
/
