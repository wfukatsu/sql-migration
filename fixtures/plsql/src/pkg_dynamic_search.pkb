-- カテゴリ: dynamic-sql
-- 期待判定: REVIEW/REDESIGN（設計書 §6.8）。
--   refresh_stats  : 定数 EXECUTE IMMEDIATE  -> 定数畳込み後に解析できる     -> REVIEW
--   count_orders   : 条件分岐で有限 variant  -> variant ごとに静的 query 化  -> REVIEW
--   purge          : 表名が実行時に決まる    -> allowlist / 専用 Repository  -> REDESIGN
CREATE OR REPLACE PACKAGE BODY pkg_dynamic_search AS

  PROCEDURE refresh_stats IS
  BEGIN
    EXECUTE IMMEDIATE 'UPDATE batch_control SET last_run_at = SYSDATE WHERE batch_name = ''STATS''';
  END refresh_stats;

  FUNCTION count_orders(p_status IN VARCHAR2, p_sort_column IN VARCHAR2) RETURN NUMBER IS
    v_sql   VARCHAR2(400);
    v_count NUMBER;
  BEGIN
    v_sql := 'SELECT COUNT(*) FROM orders WHERE status = :s';
    IF p_sort_column = 'ordered_at' THEN
      v_sql := v_sql || ' AND ordered_at IS NOT NULL';
    ELSIF p_sort_column = 'total_amount' THEN
      v_sql := v_sql || ' AND total_amount IS NOT NULL';
    END IF;
    EXECUTE IMMEDIATE v_sql INTO v_count USING p_status;
    RETURN v_count;
  END count_orders;

  PROCEDURE purge(p_table_name IN VARCHAR2, p_before IN DATE) IS
  BEGIN
    EXECUTE IMMEDIATE 'DELETE FROM ' || p_table_name || ' WHERE created_at < :d' USING p_before;
  END purge;

END pkg_dynamic_search;
/
