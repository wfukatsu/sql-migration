--------------------------------------------------------------------------------
-- 99_cleanup.sql : サンプルで作成した全オブジェクトを削除する
--------------------------------------------------------------------------------
SET SERVEROUTPUT ON
DECLARE
  PROCEDURE drop_obj (p_sql VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
    DBMS_OUTPUT.PUT_LINE('OK   ' || p_sql);
  EXCEPTION
    WHEN OTHERS THEN DBMS_OUTPUT.PUT_LINE('SKIP ' || p_sql || ' (' || SQLCODE || ')');
  END;
BEGIN
  BEGIN
    DBMS_SCHEDULER.DROP_JOB('SAMPLE_NIGHTLY_JOB', force => TRUE);
  EXCEPTION WHEN OTHERS THEN NULL;
  END;

  drop_obj('DROP PACKAGE emp_api');
  FOR o IN (SELECT 'PROCEDURE RAISE_SALARY'  s FROM dual UNION ALL
            SELECT 'PROCEDURE NORMALIZE_NAME'  FROM dual UNION ALL
            SELECT 'PROCEDURE LOG_MSG'         FROM dual UNION ALL
            SELECT 'FUNCTION ANNUAL_COMP'      FROM dual UNION ALL
            SELECT 'FUNCTION DEPT_NAME_OF'     FROM dual UNION ALL
            SELECT 'FUNCTION EMP_GRADES'       FROM dual UNION ALL
            SELECT 'TYPE EMP_GRADE_TAB'        FROM dual UNION ALL
            SELECT 'TYPE EMP_GRADE_T'          FROM dual UNION ALL
            SELECT 'VIEW EMP_IT_V'             FROM dual UNION ALL
            SELECT 'VIEW EMP_DEPT_V'           FROM dual UNION ALL
            SELECT 'VIEW EMP_DEPT_UPD_V'       FROM dual UNION ALL
            SELECT 'SYNONYM STAFF'             FROM dual UNION ALL
            SELECT 'TABLE GTT_WORK'            FROM dual UNION ALL
            SELECT 'TABLE SALES_PART'          FROM dual UNION ALL
            SELECT 'TABLE EMP_HIGH'            FROM dual UNION ALL
            SELECT 'TABLE EMP_LOW'             FROM dual UNION ALL
            SELECT 'TABLE BULK_TARGET'         FROM dual UNION ALL
            SELECT 'TABLE EMP_AUDIT'           FROM dual UNION ALL
            SELECT 'TABLE EMP_ERR_LOG'         FROM dual UNION ALL
            SELECT 'TABLE EMP_STAGE'           FROM dual UNION ALL
            SELECT 'TABLE PRODUCTS_JSON'       FROM dual UNION ALL
            SELECT 'TABLE SAMPLE_DDL'          FROM dual UNION ALL
            SELECT 'TABLE ORDER_ITEMS'         FROM dual UNION ALL
            SELECT 'TABLE ORDERS'              FROM dual UNION ALL
            SELECT 'TABLE EMPLOYEES'           FROM dual UNION ALL
            SELECT 'TABLE DEPARTMENTS'         FROM dual UNION ALL
            SELECT 'TABLE JOBS'                FROM dual UNION ALL
            SELECT 'SEQUENCE EMP_SEQ'          FROM dual UNION ALL
            SELECT 'SEQUENCE ORDER_SEQ'        FROM dual UNION ALL
            SELECT 'SEQUENCE AUDIT_SEQ'        FROM dual)
  LOOP
    drop_obj('DROP ' || o.s ||
             CASE WHEN o.s LIKE 'TABLE%' THEN ' CASCADE CONSTRAINTS PURGE'
                  WHEN o.s LIKE 'TYPE%'  THEN ' FORCE' END);
  END LOOP;
END;
/
