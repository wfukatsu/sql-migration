-- src/03_sql_dml.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE dml_d_create_error_log AS
BEGIN
  DBMS_ERRLOG.CREATE_ERROR_LOG(dml_table_name => 'EMP_STAGE',
                               err_log_table_name => 'EMP_ERR_LOG');
END;
/
