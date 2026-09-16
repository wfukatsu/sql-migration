CREATE OR REPLACE PROCEDURE prc_unresolved_type IS
  v_missing no_such_table.no_such_column%TYPE;
BEGIN
  v_missing := NULL;
END prc_unresolved_type;
/
