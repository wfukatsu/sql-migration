-- 実行計画（取得 + H2）へ分解される読み取り。target_status = PLANNED になる想定
CREATE OR REPLACE PROCEDURE prc_planned_sql IS
  v_name VARCHAR2(200);
BEGIN
  SELECT NVL(name, '(none)') INTO v_name
    FROM customers
   WHERE tier = 'GOLD'
   ORDER BY registered_on DESC
   FETCH FIRST 1 ROWS ONLY;
END prc_planned_sql;
/
