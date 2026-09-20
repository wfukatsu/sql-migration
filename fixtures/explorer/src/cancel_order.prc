CREATE OR REPLACE PROCEDURE cancel_order (
    p_order_id IN orders.order_id%TYPE
) IS
    v_status orders.status%TYPE;
BEGIN
    SELECT status
      INTO v_status
      FROM orders
     WHERE order_id = p_order_id
       FOR UPDATE;

    IF v_status = 'SHIPPED' THEN
        RAISE_APPLICATION_ERROR(-20010, '出荷済みの受注は取り消せません');
    END IF;

    -- 在庫を戻す
    FOR r IN (SELECT product_id, quantity FROM order_items WHERE order_id = p_order_id) LOOP
        UPDATE products
           SET stock_quantity = stock_quantity + r.quantity
         WHERE product_id = r.product_id;
    END LOOP;

    DELETE FROM order_items WHERE order_id = p_order_id;

    UPDATE orders
       SET status = 'CANCELLED'
     WHERE order_id = p_order_id;
END cancel_order;
/
