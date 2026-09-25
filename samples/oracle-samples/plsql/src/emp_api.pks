-- 出自: src/05_plsql_units.sql
CREATE OR REPLACE PACKAGE emp_api AS
  -- 公開定数・型・例外
  c_max_raise_pct CONSTANT NUMBER := 20;
  TYPE t_emp_tab IS TABLE OF employees%ROWTYPE;
  e_invalid_raise EXCEPTION;

  -- 公開サブプログラム
  FUNCTION  hire (p_first VARCHAR2, p_last VARCHAR2, p_email VARCHAR2,
                  p_job VARCHAR2, p_salary NUMBER, p_dept NUMBER) RETURN NUMBER;
  PROCEDURE give_raise (p_emp_id NUMBER, p_pct NUMBER);
  PROCEDURE give_raise (p_dept_id NUMBER, p_pct NUMBER, p_by_dept BOOLEAN); -- オーバーロード
  FUNCTION  get_by_dept (p_dept_id NUMBER) RETURN SYS_REFCURSOR;
  FUNCTION  call_count RETURN PLS_INTEGER;
END emp_api;
/
