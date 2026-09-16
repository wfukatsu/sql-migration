CREATE OR REPLACE PROCEDURE prc_caller_rights(p_id IN NUMBER)
  AUTHID CURRENT_USER
IS
BEGIN
  DELETE FROM orders WHERE order_id = p_id;
END prc_caller_rights;
/
