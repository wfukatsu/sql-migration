-- カテゴリ: simple-crud, trigger-dblink
-- trigger を「書き込む routine 経由で」確かめるための書き込み経路（直接の DML には移行先の trigger が掛からない。#12 §0）。
-- 実装と一緒に書いたので holdout ではない。
CREATE OR REPLACE PACKAGE BODY pkg_write_paths AS

  PROCEDURE place_order(p_customer_id IN NUMBER) IS
  BEGIN
    -- order_id を書いていない: trg_orders_seq が seq_order_id から採番する
    INSERT INTO orders (customer_id, status, ordered_at) VALUES (p_customer_id, 'NEW', SYSDATE);
  END place_order;

  PROCEDURE set_price(p_product_id IN NUMBER, p_price IN NUMBER) IS
  BEGIN
    -- unit_price を SET するので trg_products_audit（BEFORE UPDATE OF unit_price）が掛かる
    UPDATE products SET unit_price = p_price WHERE product_id = p_product_id;
  END set_price;

END pkg_write_paths;
/
