CREATE OR REPLACE PACKAGE pkg_shipping IS
    -- 出荷を登録して、受注を出荷済みにする
    PROCEDURE mark_shipped (
        p_order_id IN orders.order_id%TYPE,
        p_carrier  IN shipments.carrier%TYPE
    );

    FUNCTION shipped_count (
        p_order_id IN orders.order_id%TYPE
    ) RETURN NUMBER;
END pkg_shipping;
/
