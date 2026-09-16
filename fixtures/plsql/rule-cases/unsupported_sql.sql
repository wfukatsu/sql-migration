-- ScalarDB SQL が実行できない読み取り。target_status = ERROR になる想定
CREATE OR REPLACE PROCEDURE prc_unsupported_sql IS
  v_path VARCHAR2(4000);
BEGIN
  SELECT SYS_CONNECT_BY_PATH(name, '/') INTO v_path
    FROM customers
   START WITH customer_id = 1
 CONNECT BY PRIOR customer_id = customer_id;
END prc_unsupported_sql;
/
