-- src/00_setup.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE setup_drop_objects AS
BEGIN
  FOR t IN (SELECT table_name FROM user_tables
            WHERE table_name IN ('EMP_AUDIT','ORDER_ITEMS','ORDERS','EMPLOYEES',
                                 'DEPARTMENTS','JOBS','EMP_STAGE','PRODUCTS_JSON',
                                 'EMP_ERR_LOG','SAMPLE_DDL','BULK_TARGET'))
  LOOP
    EXECUTE IMMEDIATE 'DROP TABLE ' || t.table_name || ' CASCADE CONSTRAINTS PURGE';
  END LOOP;
  FOR s IN (SELECT sequence_name FROM user_sequences
            WHERE sequence_name IN ('EMP_SEQ','ORDER_SEQ','AUDIT_SEQ'))
  LOOP
    EXECUTE IMMEDIATE 'DROP SEQUENCE ' || s.sequence_name;
  END LOOP;
END;
/
