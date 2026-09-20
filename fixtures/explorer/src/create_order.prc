CREATE OR REPLACE PROCEDURE create_order (
    p_customer_id IN  customers.customer_id%TYPE,
    p_product_id  IN  products.product_id%TYPE,
    p_quantity    IN  NUMBER,
    p_order_id    OUT orders.order_id%TYPE
) IS
    v_stock_quantity products.stock_quantity%TYPE;
    v_unit_price     products.unit_price%TYPE;
    v_total_amount   NUMBER(18, 2);
BEGIN
    IF p_quantity <= 0 THEN
        RAISE_APPLICATION_ERROR(-20001, '数量は1以上で指定してください');
    END IF;

    -- 同時更新を防ぐため対象商品をロック
    SELECT stock_quantity,
           unit_price
      INTO v_stock_quantity,
           v_unit_price
      FROM products
     WHERE product_id = p_product_id
       FOR UPDATE;

    IF v_stock_quantity < p_quantity THEN
        RAISE_APPLICATION_ERROR(-20002, '在庫が不足しています');
    END IF;

    v_total_amount := ROUND(v_unit_price * p_quantity, 2);
    p_order_id := order_seq.NEXTVAL;

    INSERT INTO orders (
        order_id,
        customer_id,
        order_date,
        status,
        total_amount
    ) VALUES (
        p_order_id,
        p_customer_id,
        SYSDATE,
        'RECEIVED',
        v_total_amount
    );

    INSERT INTO order_items (
        order_id,
        line_no,
        product_id,
        quantity,
        unit_price
    ) VALUES (
        p_order_id,
        1,
        p_product_id,
        p_quantity,
        v_unit_price
    );

    UPDATE products
       SET stock_quantity = stock_quantity - p_quantity,
           updated_at     = SYSTIMESTAMP
     WHERE product_id = p_product_id;

EXCEPTION
    WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20003, '商品が見つかりません');
    WHEN DUP_VAL_ON_INDEX THEN
        RAISE_APPLICATION_ERROR(-20004, '受注番号が重複しました');
END create_order;
/
