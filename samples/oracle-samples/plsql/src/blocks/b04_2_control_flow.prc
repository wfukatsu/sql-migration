-- src/04_plsql_basics.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b04_2_control_flow AS
  v_sal   employees.salary%TYPE;
  v_grade VARCHAR2(10);
  i       PLS_INTEGER := 0;
BEGIN
  SELECT salary INTO v_sal FROM employees WHERE employee_id = 104;

  -- IF-ELSIF-ELSE
  IF v_sal >= 10000 THEN
    v_grade := 'A';
  ELSIF v_sal >= 5000 THEN
    v_grade := 'B';
  ELSE
    v_grade := 'C';
  END IF;

  -- CASE 文（検索 CASE）
  CASE v_grade
    WHEN 'A' THEN DBMS_OUTPUT.PUT_LINE('上位');
    WHEN 'B' THEN DBMS_OUTPUT.PUT_LINE('中位');
    ELSE          DBMS_OUTPUT.PUT_LINE('一般');
  END CASE;

  -- 基本 LOOP + EXIT WHEN
  LOOP
    i := i + 1;
    EXIT WHEN i > 3;
    DBMS_OUTPUT.PUT_LINE('LOOP ' || i);
  END LOOP;

  -- WHILE
  WHILE i > 0 LOOP
    i := i - 1;
  END LOOP;

  -- FOR（REVERSE・CONTINUE WHEN）
  FOR j IN REVERSE 1 .. 6 LOOP
    CONTINUE WHEN MOD(j, 2) = 0;
    DBMS_OUTPUT.PUT_LINE('奇数: ' || j);
  END LOOP;

  -- ラベル付きネストループ
  <<outer_loop>>
  FOR a IN 1 .. 3 LOOP
    FOR b IN 1 .. 3 LOOP
      EXIT outer_loop WHEN a * b = 4;
      DBMS_OUTPUT.PUT_LINE(a || 'x' || b || '=' || a * b);
    END LOOP;
  END LOOP outer_loop;
END;
/
