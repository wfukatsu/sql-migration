-- 合成データ。実在の人・会社・商品ではない。product_id と status は偏らせてある（ヒストグラムができる）。
BEGIN
  FOR i IN 1..50 LOOP
    INSERT INTO customers (customer_id, name, email) VALUES (i, 'Customer ' || i, 'c' || i || '@example.test');
  END LOOP;
  FOR i IN 1..20 LOOP
    INSERT INTO products (product_id, stock_quantity, unit_price, updated_at)
    VALUES (i, 1000, 100 + i * 12.5, TIMESTAMP '2026-01-01 00:00:00');
  END LOOP;
  FOR i IN 1..400 LOOP
    INSERT INTO orders (order_id, customer_id, order_date, status, total_amount)
    VALUES (i, MOD(i, 50) + 1, DATE '2026-01-01' + MOD(i, 200),
            CASE WHEN MOD(i, 10) = 0 THEN 'CANCELLED' WHEN MOD(i, 3) = 0 THEN 'SHIPPED' ELSE 'RECEIVED' END,
            i * 10);
    FOR j IN 1..3 LOOP
      INSERT INTO order_items (order_id, line_no, product_id, quantity, unit_price)
      VALUES (i, j, CASE WHEN MOD(i + j, 4) = 0 THEN MOD(i, 20) + 1 ELSE 7 END, j, 99.5);
    END LOOP;
  END LOOP;
  FOR i IN 1..120 LOOP
    INSERT INTO shipments (shipment_id, order_id, shipped_at, carrier)
    VALUES (i, i * 3, DATE '2026-02-01' + MOD(i, 30), CASE WHEN MOD(i, 5) = 0 THEN 'AIR' ELSE 'GROUND' END);
  END LOOP;
  COMMIT;
END;
/
SELECT 'data loaded' AS status FROM dual;
