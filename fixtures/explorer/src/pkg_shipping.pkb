CREATE OR REPLACE PACKAGE BODY pkg_shipping IS

    FUNCTION shipped_count (
        p_order_id IN orders.order_id%TYPE
    ) RETURN NUMBER IS
        v_count NUMBER;
    BEGIN
        SELECT COUNT(*)
          INTO v_count
          FROM shipments
         WHERE order_id = p_order_id;
        RETURN v_count;
    END shipped_count;

    PROCEDURE mark_shipped (
        p_order_id IN orders.order_id%TYPE,
        p_carrier  IN shipments.carrier%TYPE
    ) IS
        v_already NUMBER;
    BEGIN
        v_already := shipped_count(p_order_id);
        IF v_already > 0 THEN
            RAISE_APPLICATION_ERROR(-20030, 'この受注は出荷済みです');
        END IF;

        INSERT INTO shipments (shipment_id, order_id, shipped_at, carrier)
        VALUES (shipment_seq.NEXTVAL, p_order_id, SYSDATE, p_carrier);

        UPDATE orders
           SET status = 'SHIPPED'
         WHERE order_id = p_order_id;
    END mark_shipped;

END pkg_shipping;
/
