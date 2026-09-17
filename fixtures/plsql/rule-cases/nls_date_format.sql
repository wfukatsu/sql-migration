-- P4-3: SEM-008。NLS_DATE_LANGUAGE で出力が変わる TO_CHAR 書式。Plsql.text は未対応書式で例外を上げるが、
-- 実行時に落ちるより先に人が見るべきものである。
CREATE OR REPLACE PACKAGE BODY pkg_nls_case AS
  FUNCTION day_name(p_when IN DATE) RETURN VARCHAR2 IS
  BEGIN
    RETURN TO_CHAR(p_when, 'DAY');
  END day_name;
END pkg_nls_case;
/
