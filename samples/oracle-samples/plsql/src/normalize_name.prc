-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE PROCEDURE normalize_name (p_name IN OUT NOCOPY VARCHAR2) AS
BEGIN
  p_name := INITCAP(TRIM(p_name));
END;
/
