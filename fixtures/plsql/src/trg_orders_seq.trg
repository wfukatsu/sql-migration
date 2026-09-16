-- カテゴリ: trigger-dblink
-- 期待判定: REDESIGN。採番 trigger は ScalarDB に順序オブジェクトがないため成立しない。
-- 採番 Service か UUID へ移し、順序性・欠番の許容を要件化する（設計書 §6.4）。
CREATE OR REPLACE TRIGGER trg_orders_seq
BEFORE INSERT ON orders
FOR EACH ROW
WHEN (NEW.order_id IS NULL)
BEGIN
  :NEW.order_id := seq_order_id.NEXTVAL;
END;
/
