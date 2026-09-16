-- Pipelined function: lowering がまだ模していない構文。Unsupported ノードになる想定
CREATE OR REPLACE FUNCTION fnc_pipelined RETURN SYS_REFCURSOR PIPELINED IS
BEGIN
  FOR r IN (SELECT order_id FROM orders) LOOP
    PIPE ROW(r.order_id);
  END LOOP;
  RETURN;
END fnc_pipelined;
/
