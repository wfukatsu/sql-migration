-- カテゴリ: bulk
-- 期待判定: REVIEW/REDESIGN。BULK COLLECT は LIMIT とメモリ上限、FORALL は一括失敗/部分失敗の意味、
-- SAVE EXCEPTIONS は原子性を業務要件で決める（設計書 §6.5）。
CREATE OR REPLACE PACKAGE BODY pkg_bulk_load AS

  PROCEDURE restock(p_product_ids IN t_id_list, p_deltas IN t_qty_list) IS
  BEGIN
    FORALL i IN 1 .. p_product_ids.COUNT SAVE EXCEPTIONS
      UPDATE products SET stock_qty = stock_qty + p_deltas(i) WHERE product_id = p_product_ids(i);
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLCODE = -24381 THEN
        FOR i IN 1 .. SQL%BULK_EXCEPTIONS.COUNT LOOP
          INSERT INTO audit_log (audit_id, table_name, key_value, action, new_value, changed_at, changed_by)
          VALUES (seq_audit_id.NEXTVAL, 'PRODUCTS', TO_CHAR(SQL%BULK_EXCEPTIONS(i).ERROR_INDEX),
                  'BULKERR', SQLERRM(-SQL%BULK_EXCEPTIONS(i).ERROR_CODE), SYSTIMESTAMP, USER);
        END LOOP;
      ELSE
        RAISE;
      END IF;
  END restock;

  PROCEDURE collect_open_orders(p_limit IN PLS_INTEGER, p_count OUT NUMBER) IS
    CURSOR c IS SELECT order_id FROM orders WHERE status = 'NEW';
    v_ids t_id_list;
  BEGIN
    p_count := 0;
    OPEN c;
    LOOP
      FETCH c BULK COLLECT INTO v_ids LIMIT p_limit;
      EXIT WHEN v_ids.COUNT = 0;
      p_count := p_count + v_ids.COUNT;
    END LOOP;
    CLOSE c;
  END collect_open_orders;

  PROCEDURE archive_lines(p_order_id IN NUMBER) IS
    v_products t_id_list;
    v_qtys     t_qty_list;
  BEGIN
    SELECT product_id, qty BULK COLLECT INTO v_products, v_qtys
      FROM order_lines WHERE order_id = p_order_id;
    FORALL i IN 1 .. v_products.COUNT
      INSERT INTO inventory_tx (tx_id, product_id, delta_qty, reason, created_at)
      VALUES (seq_tx_id.NEXTVAL, v_products(i), -v_qtys(i), 'ARCHIVE', SYSDATE);
  END archive_lines;

END pkg_bulk_load;
/
