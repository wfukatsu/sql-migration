-- P4-3: SEM-009。TIMESTAMP を DATE へ狭める CAST。Oracle は秒未満を捨てるが、Java 側は両方 LocalDateTime
-- なので捨てる相当の処理が無い。記録した答えにも CAST は無い。
CREATE OR REPLACE PACKAGE BODY pkg_cast_case AS
  FUNCTION as_date(p_at IN TIMESTAMP) RETURN DATE IS
  BEGIN
    RETURN CAST(p_at AS DATE);
  END as_date;
END pkg_cast_case;
/
