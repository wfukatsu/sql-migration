-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_5_records_collections AS
  -- ユーザー定義レコード
  TYPE t_emp_rec IS RECORD (
    id    employees.employee_id%TYPE,
    name  employees.last_name%TYPE,
    sal   employees.salary%TYPE := 0
  );
  v_rec t_emp_rec;

  -- 連想配列（INDEX BY）：キーは PLS_INTEGER または VARCHAR2
  TYPE t_sal_by_name IS TABLE OF NUMBER INDEX BY VARCHAR2(30);
  v_sal t_sal_by_name;
  k     VARCHAR2(30);

  -- ネストした表
  TYPE t_names IS TABLE OF VARCHAR2(30);
  v_names t_names := t_names('Alpha', 'Bravo', 'Charlie');

  -- VARRAY（最大要素数固定）
  TYPE t_top3 IS VARRAY(3) OF NUMBER;
  v_top3 t_top3 := t_top3();
BEGIN
  v_rec.id := 1; v_rec.name := 'Test';
  DBMS_OUTPUT.PUT_LINE('record: ' || v_rec.id || ' ' || v_rec.name || ' ' || v_rec.sal);

  FOR r IN (SELECT last_name, salary FROM employees WHERE department_id = 90) LOOP
    v_sal(r.last_name) := r.salary;
  END LOOP;
  k := v_sal.FIRST;                         -- キー順に走査
  WHILE k IS NOT NULL LOOP
    DBMS_OUTPUT.PUT_LINE(k || ' => ' || v_sal(k));
    k := v_sal.NEXT(k);
  END LOOP;

  v_names.EXTEND;  v_names(v_names.LAST) := 'Delta';
  v_names.DELETE(2);                        -- 疎になる
  DBMS_OUTPUT.PUT_LINE('COUNT=' || v_names.COUNT || ' EXISTS(2)=' ||
                       CASE WHEN v_names.EXISTS(2) THEN 'Y' ELSE 'N' END);

  v_top3.EXTEND(3);
  v_top3(1) := 100; v_top3(2) := 90; v_top3(3) := 80;
  DBMS_OUTPUT.PUT_LINE('VARRAY LIMIT=' || v_top3.LIMIT);
END;
/
