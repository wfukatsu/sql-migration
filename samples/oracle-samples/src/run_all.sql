-- 全サンプルを順に実行する（SQL*Plus / SQLcl）
--   sql user/password@//host:1521/FREEPDB1 @run_all.sql
SPOOL run_all.log
@@00_setup.sql
@@01_sql_ddl.sql
@@02_sql_query.sql
@@03_sql_dml.sql
@@04_plsql_basics.sql
@@05_plsql_units.sql
@@06_plsql_advanced.sql
SPOOL OFF
-- 片付ける場合:  @99_cleanup.sql
