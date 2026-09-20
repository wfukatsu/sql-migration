-- アプリケーションが直接流す SQL。shipments は PL/SQL からは誰も触らない。

-- 出荷を登録する
INSERT INTO shipments (shipment_id, order_id, shipped_at, carrier)
VALUES (:shipment_id, :order_id, SYSDATE, :carrier);

-- 出荷済みにする
UPDATE orders SET status = 'SHIPPED' WHERE order_id = :order_id;

-- 未出荷の一覧
SELECT v.order_id, v.customer_id
  FROM v_open_orders v
 WHERE NOT EXISTS (SELECT 1 FROM shipments s WHERE s.order_id = v.order_id);

-- 方言の拡張で、解析できない文
SELECT order_id FROM orders MATCH_RECOGNIZE oops (;
