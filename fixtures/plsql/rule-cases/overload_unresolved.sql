CREATE OR REPLACE PACKAGE BODY pkg_log AS
  PROCEDURE note(p_value NUMBER) IS BEGIN NULL; END note;
  PROCEDURE note(p_value VARCHAR2) IS BEGIN NULL; END note;

  PROCEDURE run(p_id NUMBER) IS
  BEGIN
    -- two overloads take one argument: which one this means depends on the argument's type, which nothing
    -- here infers
    note(p_id);
  END run;
END pkg_log;
/
