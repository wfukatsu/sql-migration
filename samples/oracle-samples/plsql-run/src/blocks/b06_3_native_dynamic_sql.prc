-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_3_native_dynamic_sql AS
  v_cnt   NUMBER;
  v_table VARCHAR2(128) := 'EMPLOYEES';
  v_dept  NUMBER := 80;
  v_name  employees.last_name%TYPE;
  rc      SYS_REFCURSOR;
BEGIN
  -- 3-1. バインド変数（USING）で値を渡す。識別子は DBMS_ASSERT で検証して連結
  EXECUTE IMMEDIATE
    'SELECT COUNT(*) FROM ' || DBMS_ASSERT.SIMPLE_SQL_NAME(v_table) ||
    ' WHERE department_id = :d'
    INTO v_cnt USING v_dept;
  DBMS_OUTPUT.PUT_LINE('部門80の人数: ' || v_cnt);

  -- 3-2. DML + RETURNING
  EXECUTE IMMEDIATE
    'UPDATE employees SET salary = salary WHERE employee_id = :id RETURNING last_name INTO :n'
    USING 100 RETURNING INTO v_name;
  DBMS_OUTPUT.PUT_LINE('対象: ' || v_name);

  -- 3-3. DDL（静的 SQL では書けない）
  EXECUTE IMMEDIATE 'CREATE TABLE dyn_tmp (id NUMBER)';
  EXECUTE IMMEDIATE 'DROP TABLE dyn_tmp PURGE';

  -- 3-4. 動的な複数行問合せ
  OPEN rc FOR 'SELECT last_name FROM employees WHERE salary > :s ORDER BY 1' USING 15000;
  LOOP
    FETCH rc INTO v_name;
    EXIT WHEN rc%NOTFOUND;
    DBMS_OUTPUT.PUT_LINE('高給: ' || v_name);
  END LOOP;
  CLOSE rc;

  -- 3-5. 動的 PL/SQL ブロック（IN OUT バインド）
  v_cnt := 1;
  EXECUTE IMMEDIATE 'BEGIN :x := :x * 10; END;' USING IN OUT v_cnt;
  DBMS_OUTPUT.PUT_LINE('動的PL/SQL結果: ' || v_cnt);
  ROLLBACK;
END;
/
