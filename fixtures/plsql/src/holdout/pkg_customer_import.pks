-- holdout / カテゴリ: bulk, dynamic-sql
CREATE OR REPLACE PACKAGE pkg_customer_import AS
  TYPE t_name_list IS TABLE OF VARCHAR2(100) INDEX BY PLS_INTEGER;
  TYPE t_id_list   IS TABLE OF NUMBER(19)    INDEX BY PLS_INTEGER;

  PROCEDURE import(p_ids IN t_id_list, p_names IN t_name_list);
  PROCEDURE truncate_staging(p_table_name IN VARCHAR2);
END pkg_customer_import;
/
