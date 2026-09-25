-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE FUNCTION annual_comp (
  p_salary IN NUMBER,
  p_comm   IN NUMBER DEFAULT NULL
) RETURN NUMBER
  DETERMINISTIC               -- 同じ入力なら同じ結果（ファンクション索引に必要）
AS
BEGIN
  RETURN p_salary * 12 * (1 + NVL(p_comm, 0));
END annual_comp;
/
