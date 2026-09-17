-- SEM-010: SYSTIMESTAMP を列へ書く。ハーネスはこの時計を固定できない（semantics.json の
-- fixedDatePinsSystimestamp=false で測定済み）ので、書かれた値は Oracle と突き合わせられない。
-- 読んで判断に使うだけの場合と区別するため、書く文にだけ当てる。
CREATE OR REPLACE PACKAGE BODY pkg_stamp_write AS
  PROCEDURE touch(p_id IN NUMBER) IS
  BEGIN
    UPDATE batch_control SET last_run_at = SYSTIMESTAMP WHERE batch_name = 'X';
  END touch;
END pkg_stamp_write;
/
