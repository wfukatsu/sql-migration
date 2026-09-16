CREATE OR REPLACE PACKAGE BODY pkg_with_state AS
  g_call_count NUMBER := 0;

  PROCEDURE touch IS
  BEGIN
    g_call_count := g_call_count + 1;
  END touch;
END pkg_with_state;
/
