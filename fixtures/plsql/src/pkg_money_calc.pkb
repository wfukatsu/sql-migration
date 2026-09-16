-- カテゴリ: datatype-edge
-- 期待判定: REVIEW。ScalarDB に DECIMAL がなく types.py が DOUBLE/BIGINT へ落とすため、
-- 金額はスケール済み整数 + BIGINT を既定にする。あわせて次の Oracle 固有意味を保つ必要がある。
--   * Oracle DATE は時刻成分を持つ（TRUNC の有無で結果が変わる）
--   * 空文字は NULL と同一（NVL / IS NULL の挙動）
--   * ROUND の丸めは half-up、Java の BigDecimal 既定（HALF_EVEN）と異なる
CREATE OR REPLACE PACKAGE BODY pkg_money_calc AS

  FUNCTION rounded_total(p_order_id IN NUMBER) RETURN NUMBER IS
    v_total orders.total_amount%TYPE;
  BEGIN
    SELECT total_amount INTO v_total FROM orders WHERE order_id = p_order_id;
    -- NULL は 0 として扱う。ROUND は half-up
    RETURN ROUND(NVL(v_total, 0), 2);
  END rounded_total;

  FUNCTION days_since_order(p_order_id IN NUMBER) RETURN NUMBER IS
    v_ordered_at orders.ordered_at%TYPE;
  BEGIN
    SELECT ordered_at INTO v_ordered_at FROM orders WHERE order_id = p_order_id;
    -- TRUNC を外すと時刻成分の分だけ小数になる
    RETURN TRUNC(SYSDATE) - TRUNC(v_ordered_at);
  END days_since_order;

  FUNCTION display_note(p_order_id IN NUMBER) RETURN VARCHAR2 IS
    v_note orders.note%TYPE;
  BEGIN
    SELECT note INTO v_note FROM orders WHERE order_id = p_order_id;
    -- Oracle では '' は NULL なので、この分岐は v_note IS NULL と同じ
    IF v_note = '' OR v_note IS NULL THEN
      RETURN '(none)';
    END IF;
    RETURN RTRIM(v_note) || ' / ' || TO_CHAR(SYSDATE, 'YYYY-MM-DD');
  END display_note;

  PROCEDURE apply_rate(p_order_id IN NUMBER, p_rate IN NUMBER) IS
    v_total orders.total_amount%TYPE;
  BEGIN
    SELECT total_amount INTO v_total FROM orders WHERE order_id = p_order_id;
    UPDATE orders SET total_amount = ROUND(v_total * p_rate, 2) WHERE order_id = p_order_id;
  END apply_rate;

END pkg_money_calc;
/
