-- P4-3: SEM-007。1 つの routine が時計を 2 回読む。2 回の読みは違う値を返しうるが、シナリオは時計を
-- 1 つの値に固定するので、比較ではその差を区別できない。証拠が届かない範囲である。
CREATE OR REPLACE PACKAGE BODY pkg_two_clock_reads AS
  PROCEDURE stamp_twice(p_id IN NUMBER) IS
    v_first  DATE;
    v_second DATE;
  BEGIN
    v_first := SYSDATE;
    v_second := SYSDATE;
    UPDATE orders SET ordered_at = v_first, shipped_at = v_second WHERE order_id = p_id;
  END stamp_twice;
END pkg_two_clock_reads;
/
