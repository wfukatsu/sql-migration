-- カテゴリ: trigger-dblink
-- 期待判定: REDESIGN。DB Link 越しの分散トランザクション。参加 DB・timeout・再試行を
-- 設計し直す必要がある（設計書 §6.7 / §2.2）。
CREATE OR REPLACE PROCEDURE prc_remote_sync(p_order_id IN NUMBER) IS
  v_status orders.status%TYPE;
BEGIN
  SELECT status INTO v_status FROM orders WHERE order_id = p_order_id;

  UPDATE orders@warehouse_link SET status = v_status WHERE order_id = p_order_id;

  INSERT INTO shipment_queue@warehouse_link (order_id, requested_at)
  VALUES (p_order_id, SYSDATE);

  COMMIT;
END prc_remote_sync;
/
