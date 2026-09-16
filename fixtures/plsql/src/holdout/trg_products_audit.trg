-- holdout / カテゴリ: trigger-dblink
CREATE OR REPLACE TRIGGER trg_products_audit
BEFORE UPDATE OF unit_price ON products
FOR EACH ROW
BEGIN
  IF :NEW.unit_price < :OLD.unit_price * 0.5 THEN
    RAISE_APPLICATION_ERROR(-20050, 'price drop over 50% requires approval: ' || :NEW.product_id);
  END IF;
  INSERT INTO audit_log (audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by)
  VALUES (seq_audit_id.NEXTVAL, 'PRODUCTS', TO_CHAR(:NEW.product_id), 'PRICE',
          TO_CHAR(:OLD.unit_price), TO_CHAR(:NEW.unit_price), SYSTIMESTAMP, USER);
END;
/
