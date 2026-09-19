CREATE OR REPLACE PROCEDURE prc_purge(p_table IN VARCHAR2, p_deleted OUT NUMBER) IS
BEGIN
  EXECUTE IMMEDIATE 'DELETE FROM ' || p_table;
  -- the count belongs to the dynamic statement, which the generated rowCount does not follow
  p_deleted := SQL%ROWCOUNT;
END prc_purge;
/
