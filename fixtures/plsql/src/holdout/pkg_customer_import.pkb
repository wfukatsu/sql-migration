-- holdout / カテゴリ: bulk, dynamic-sql
CREATE OR REPLACE PACKAGE BODY pkg_customer_import AS

  PROCEDURE import(p_ids IN t_id_list, p_names IN t_name_list) IS
  BEGIN
    FORALL i IN 1 .. p_ids.COUNT
      MERGE INTO customers c
      USING (SELECT p_ids(i) AS customer_id, p_names(i) AS name FROM dual) s
         ON (c.customer_id = s.customer_id)
       WHEN MATCHED THEN UPDATE SET c.name = s.name
       WHEN NOT MATCHED THEN
         INSERT (customer_id, name, tier, registered_on)
         VALUES (s.customer_id, s.name, 'BRONZE', SYSDATE);
  END import;

  PROCEDURE truncate_staging(p_table_name IN VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE 'TRUNCATE TABLE ' || DBMS_ASSERT.SIMPLE_SQL_NAME(p_table_name);
  END truncate_staging;

END pkg_customer_import;
/
