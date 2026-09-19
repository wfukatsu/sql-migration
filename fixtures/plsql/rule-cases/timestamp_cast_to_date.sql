-- SEM-009。TIMESTAMP を DATE へ狭める CAST が、SQL の中に残って移行先 DB に評価される形。PL/SQL の式の CAST は互換
-- ランタイム（Plsql.castDate）が Oracle と同じく秒未満を切り捨てるので、2026-09-20 からは対象にしない。
CREATE OR REPLACE PACKAGE BODY pkg_cast_case AS
  FUNCTION shipped_on(p_order_id IN NUMBER) RETURN DATE IS
    v_day DATE;
  BEGIN
    SELECT CAST(shipped_at AS DATE) INTO v_day FROM orders WHERE order_id = p_order_id;
    RETURN v_day;
  END shipped_on;
END pkg_cast_case;
/
