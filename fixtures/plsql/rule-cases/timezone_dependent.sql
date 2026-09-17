-- SEM-002: **セッションの**タイムゾーンに依存する構文。CURRENT_TIMESTAMP は SESSIONTIMEZONE に従うので、
-- 接続元によって値が変わる。実機で測ってある（semantics.json の clock 族）: DBTIMEZONE=+00:00 に対し
-- SESSIONTIMEZONE=+09:00 で、CURRENT_TIMESTAMP は SYSTIMESTAMP と一致しない。
--
-- SYSTIMESTAMP はこのルールの対象外になった（2026-09-17）。時刻を UTC に固定する決定と、
-- SYS_EXTRACT_UTC(SYSTIMESTAMP) = SYSDATE の測定で連鎖が閉じているため。書く場合は SEM-010 が当たる。
CREATE OR REPLACE PACKAGE BODY pkg_tz_case AS
  FUNCTION stamped RETURN TIMESTAMP IS
    v_at TIMESTAMP;
  BEGIN
    v_at := CURRENT_TIMESTAMP;
    RETURN v_at;
  END stamped;
END pkg_tz_case;
/
