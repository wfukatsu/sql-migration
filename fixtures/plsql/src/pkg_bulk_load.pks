-- カテゴリ: bulk
CREATE OR REPLACE PACKAGE pkg_bulk_load AS
  TYPE t_id_list  IS TABLE OF NUMBER(19) INDEX BY PLS_INTEGER;
  TYPE t_qty_list IS TABLE OF NUMBER(10) INDEX BY PLS_INTEGER;

  PROCEDURE restock(p_product_ids IN t_id_list, p_deltas IN t_qty_list);
  PROCEDURE collect_open_orders(p_limit IN PLS_INTEGER, p_count OUT NUMBER);
  PROCEDURE archive_lines(p_order_id IN NUMBER);
END pkg_bulk_load;
/
