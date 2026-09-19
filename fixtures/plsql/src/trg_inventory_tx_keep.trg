-- カテゴリ: trigger-dblink
-- 期待判定: REDESIGN。DELETE で発火する検証 trigger。消される行の :OLD を読む
-- （Issue #29 の 25。実装と一緒に書いたので holdout ではない）。
CREATE OR REPLACE TRIGGER trg_inventory_tx_keep
BEFORE DELETE ON inventory_tx
FOR EACH ROW
BEGIN
  IF :OLD.reason = 'AUDITED' THEN
    RAISE_APPLICATION_ERROR(-20081, 'audited inventory entry cannot be removed: ' || :OLD.entry_id);
  END IF;
END;
/
