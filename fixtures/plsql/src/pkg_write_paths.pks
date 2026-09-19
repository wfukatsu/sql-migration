-- カテゴリ: simple-crud, trigger-dblink
-- trigger を「書き込む routine 経由で」確かめるための書き込み経路（直接の DML には移行先の trigger が掛からない。#12 §0）。
-- 実装と一緒に書いたので holdout ではない。
CREATE OR REPLACE PACKAGE pkg_write_paths AS
  PROCEDURE place_order(p_customer_id IN NUMBER);
  PROCEDURE set_price(p_product_id IN NUMBER, p_price IN NUMBER);
END pkg_write_paths;
/
