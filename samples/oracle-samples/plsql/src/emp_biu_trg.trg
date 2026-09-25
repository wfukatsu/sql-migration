-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE TRIGGER emp_biu_trg
  BEFORE INSERT OR UPDATE OF salary, email ON employees
  FOR EACH ROW
BEGIN
  :NEW.email := UPPER(:NEW.email);
  IF UPDATING('SALARY') AND :NEW.salary < :OLD.salary * 0.5 THEN
    RAISE_APPLICATION_ERROR(-20030, '給与を半分未満に下げることはできません');
  END IF;
END;
/
