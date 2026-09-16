-- カテゴリ: concurrency-lock
-- 期待判定: REDESIGN。行ロックは ScalarDB のトランザクション分離／楽観制御へ置き換える設計が要る
-- （設計書 §6.4 row locking）。NOWAIT と SKIP LOCKED は個別に設計する。
CREATE OR REPLACE PACKAGE BODY pkg_stock_reserve AS

  PROCEDURE reserve(p_product_id IN NUMBER, p_qty IN NUMBER) IS
    v_stock products.stock_qty%TYPE;
  BEGIN
    SELECT stock_qty INTO v_stock FROM products WHERE product_id = p_product_id FOR UPDATE;
    IF v_stock < p_qty THEN
      RAISE_APPLICATION_ERROR(-20030, 'insufficient stock for product ' || p_product_id);
    END IF;
    UPDATE products SET stock_qty = v_stock - p_qty WHERE product_id = p_product_id;
  END reserve;

  PROCEDURE reserve_nowait(p_product_id IN NUMBER, p_qty IN NUMBER) IS
    v_stock products.stock_qty%TYPE;
    e_locked EXCEPTION;
    PRAGMA EXCEPTION_INIT(e_locked, -54);
  BEGIN
    SELECT stock_qty INTO v_stock FROM products WHERE product_id = p_product_id FOR UPDATE NOWAIT;
    UPDATE products SET stock_qty = v_stock - p_qty WHERE product_id = p_product_id;
  EXCEPTION
    WHEN e_locked THEN
      RAISE_APPLICATION_ERROR(-20031, 'product ' || p_product_id || ' is locked by another session');
  END reserve_nowait;

  FUNCTION next_payment_id RETURN NUMBER IS
    v_next counters.next_value%TYPE;
  BEGIN
    -- 採番を表の行ロックで直列化する典型形
    SELECT next_value INTO v_next FROM counters WHERE counter_name = 'PAYMENT_ID' FOR UPDATE;
    UPDATE counters SET next_value = v_next + 1 WHERE counter_name = 'PAYMENT_ID';
    RETURN v_next;
  END next_payment_id;

  PROCEDURE claim_batch(p_limit IN NUMBER) IS
    CURSOR c IS
      SELECT order_id FROM orders WHERE status = 'NEW' AND ROWNUM <= p_limit
      FOR UPDATE SKIP LOCKED;
  BEGIN
    FOR r IN c LOOP
      UPDATE orders SET status = 'CLAIMED' WHERE CURRENT OF c;
    END LOOP;
  END claim_batch;

END pkg_stock_reserve;
/
