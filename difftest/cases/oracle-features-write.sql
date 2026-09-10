-- Oracle-specific write / DDL / procedural statements: classified by the converter only (not executed).
CREATE TABLE emp (empno NUMBER(4) PRIMARY KEY, ename VARCHAR2(10), job VARCHAR2(9), mgr NUMBER(4), sal NUMBER(7,2), comm NUMBER(7,2), deptno NUMBER(2), hiredate DATE);
CREATE TABLE dept (deptno NUMBER(2) PRIMARY KEY, dname VARCHAR2(14), loc VARCHAR2(13));
-- @feature: シーケンス CREATE SEQUENCE / NEXTVAL
CREATE SEQUENCE emp_seq START WITH 8000 INCREMENT BY 1;
-- @feature: シーケンス NEXTVAL を使う INSERT
INSERT INTO emp (empno, ename, deptno) VALUES (emp_seq.NEXTVAL, 'NEW', 10);
-- @feature: MERGE (表ソース)
MERGE INTO emp t USING dept d ON (t.deptno = d.deptno) WHEN MATCHED THEN UPDATE SET t.job = 'X' WHEN NOT MATCHED THEN INSERT (empno, ename) VALUES (1, 'Y');
-- @feature: MERGE (定数ソース)
MERGE INTO emp t USING (SELECT 7369 AS empno, 'SMITH2' AS ename FROM dual) s ON (t.empno = s.empno) WHEN MATCHED THEN UPDATE SET t.ename = s.ename WHEN NOT MATCHED THEN INSERT (empno, ename) VALUES (s.empno, s.ename);
-- @feature: INSERT ALL (複数表への挿入)
INSERT ALL INTO bonus (empno, amount) VALUES (empno, 10) INTO emp_log (empno) VALUES (empno) SELECT empno FROM emp WHERE deptno = 10;
-- @feature: UPDATE の SYSDATE / 式
UPDATE emp SET sal = sal * 1.1, hiredate = SYSDATE WHERE deptno = 10;
-- @feature: UPDATE 副問合せ
UPDATE emp e SET sal = (SELECT AVG(sal) FROM emp WHERE deptno = e.deptno) WHERE empno = 7369;
-- @feature: DELETE 副問合せ
DELETE FROM emp WHERE deptno IN (SELECT deptno FROM dept WHERE loc = 'BOSTON');
-- @feature: INSERT ... RETURNING INTO
INSERT INTO emp (empno, ename, deptno) VALUES (8001, 'Z', 10) RETURNING empno INTO :id;
-- @feature: TRUNCATE TABLE
TRUNCATE TABLE bonus;
-- @feature: ALTER TABLE MODIFY (Oracle 構文)
ALTER TABLE emp MODIFY (ename VARCHAR2(20));
-- @feature: CREATE VIEW
CREATE OR REPLACE VIEW v_emp AS SELECT ename, sal FROM emp WHERE deptno = 10;
-- @feature: CREATE TRIGGER (PL/SQL)
CREATE OR REPLACE TRIGGER trg_emp BEFORE INSERT ON emp FOR EACH ROW BEGIN :NEW.hiredate := SYSDATE; END;
-- @feature: PL/SQL 無名ブロック
BEGIN UPDATE emp SET sal = sal + 1 WHERE empno = 7369; COMMIT; END;
-- @feature: SAVEPOINT / ROLLBACK TO
SAVEPOINT sp1;
-- @feature: GRANT
GRANT SELECT ON emp TO scott;
-- @feature: COMMENT ON
COMMENT ON TABLE emp IS 'employees';
