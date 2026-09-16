CREATE OR REPLACE PACKAGE BODY pkg_recursive AS
  FUNCTION depth_of(p_id IN NUMBER) RETURN NUMBER IS
    v_parent NUMBER;
  BEGIN
    SELECT customer_id INTO v_parent FROM orders WHERE order_id = p_id;
    IF v_parent IS NULL THEN
      RETURN 0;
    END IF;
    RETURN 1 + depth_of(v_parent);
  END depth_of;
END pkg_recursive;
/
