-- P4-3: SEM-002。タイムゾーンに依存する構文。記録した Oracle の答えは 1 つのタイムゾーンの下のものなので、
-- ここは証拠が届いていない。SYSDATE と TRUNC だけの routine とは別扱いになることを示す。
CREATE OR REPLACE PACKAGE BODY pkg_tz_case AS
  FUNCTION stamped RETURN TIMESTAMP IS
    v_at TIMESTAMP;
  BEGIN
    v_at := SYSTIMESTAMP;
    RETURN v_at;
  END stamped;
END pkg_tz_case;
/
