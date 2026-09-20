CREATE OR REPLACE PROCEDURE report_open_orders (
    p_customer_id IN  customers.customer_id%TYPE,
    p_lines       OUT NUMBER,
    p_today       OUT DATE
) IS
BEGIN
    -- view を読む。元の表は orders と order_items
    SELECT COUNT(*)
      INTO p_lines
      FROM v_order_lines
     WHERE customer_id = p_customer_id;

    WITH recent AS (SELECT order_id FROM orders WHERE customer_id = p_customer_id)
    SELECT COUNT(*) INTO p_lines FROM recent;

    SELECT SYSDATE INTO p_today FROM dual;
END report_open_orders;
/
