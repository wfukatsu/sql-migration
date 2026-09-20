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

-- 原文としては渡されていない procedure。DB のオブジェクトの一覧と（取っていれば）USER_SOURCE からしか分からない。
CREATE OR REPLACE PROCEDURE archive_shipments (
    p_before IN DATE
) IS
BEGIN
    DELETE FROM shipments WHERE shipped_at < p_before;
END archive_shipments;
/

-- パーティション表。原文のどの SQL も触らない。書き込みの件数が、パーティションの行を足して二重にならないことを確かめる
CREATE TABLE order_events (
  event_id   NUMBER(12)   NOT NULL,
  order_id   NUMBER(12)   NOT NULL,
  event_date DATE         NOT NULL,
  note       VARCHAR2(200)
)
PARTITION BY RANGE (event_date) (
  PARTITION p2026h1 VALUES LESS THAN (DATE '2026-07-01'),
  PARTITION p2026h2 VALUES LESS THAN (DATE '2027-01-01')
);

COMMENT ON TABLE orders IS '受注。1 行が 1 件の受注';
COMMENT ON COLUMN orders.status IS 'RECEIVED / SHIPPED / CANCELLED';
