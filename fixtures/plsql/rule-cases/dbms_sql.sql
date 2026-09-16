CREATE OR REPLACE PROCEDURE prc_uses_dbms_sql(p_sql IN VARCHAR2) IS
  v_cursor INTEGER;
  v_rows   INTEGER;
BEGIN
  v_cursor := DBMS_SQL.OPEN_CURSOR;
  DBMS_SQL.PARSE(v_cursor, p_sql, DBMS_SQL.NATIVE);
  v_rows := DBMS_SQL.EXECUTE(v_cursor);
  DBMS_SQL.CLOSE_CURSOR(v_cursor);
END prc_uses_dbms_sql;
/
