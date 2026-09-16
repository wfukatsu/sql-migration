-- カテゴリ: dynamic-sql
CREATE OR REPLACE PACKAGE pkg_dynamic_search AS
  PROCEDURE refresh_stats;
  FUNCTION count_orders(p_status IN VARCHAR2, p_sort_column IN VARCHAR2) RETURN NUMBER;
  PROCEDURE purge(p_table_name IN VARCHAR2, p_before IN DATE);
END pkg_dynamic_search;
/
