-- src/06_plsql_advanced.sql の無名ブロック。呼び出し単位を持たせるため procedure に包んだ（本文は原文のまま）
CREATE OR REPLACE PROCEDURE b06_3_6_dbms_sql AS
  c      INTEGER := DBMS_SQL.OPEN_CURSOR;
  n      INTEGER;
  cols   DBMS_SQL.DESC_TAB2;
  ncols  INTEGER;
  v_val  VARCHAR2(4000);
BEGIN
  DBMS_SQL.PARSE(c, 'SELECT * FROM departments WHERE ROWNUM <= 2', DBMS_SQL.NATIVE);
  DBMS_SQL.DESCRIBE_COLUMNS2(c, ncols, cols);
  FOR i IN 1 .. ncols LOOP
    DBMS_SQL.DEFINE_COLUMN(c, i, v_val, 4000);
  END LOOP;
  n := DBMS_SQL.EXECUTE(c);
  WHILE DBMS_SQL.FETCH_ROWS(c) > 0 LOOP
    FOR i IN 1 .. ncols LOOP
      DBMS_SQL.COLUMN_VALUE(c, i, v_val);
      DBMS_OUTPUT.PUT(cols(i).col_name || '=' || v_val || '  ');
    END LOOP;
    DBMS_OUTPUT.NEW_LINE;
  END LOOP;
  DBMS_SQL.CLOSE_CURSOR(c);
END;
/
