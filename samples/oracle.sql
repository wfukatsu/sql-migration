-- Oracle sample
CREATE TABLE emp (
  empno NUMBER(4) PRIMARY KEY,
  ename VARCHAR2(10) NOT NULL,
  job VARCHAR2(9),
  sal NUMBER(7,2),
  hiredate DATE,
  deptno NUMBER(2),
  note CLOB,
  photo BLOB
);
CREATE TABLE order_line (
  order_id NUMBER(10),
  line_no NUMBER(3),
  product_id NUMBER(10) NOT NULL,
  qty NUMBER(5),
  price BINARY_DOUBLE,
  created_at TIMESTAMP(6),
  CONSTRAINT pk_order_line PRIMARY KEY (order_id, line_no)
);
CREATE INDEX idx_emp_deptno ON emp (deptno);
CREATE SEQUENCE emp_seq START WITH 1;
SELECT empno, ename, sal FROM emp WHERE empno = :empno;
SELECT empno, ename FROM emp WHERE deptno = 10 AND ROWNUM <= 5;
SELECT empno, ename FROM emp WHERE sal > 1000 ORDER BY sal DESC FETCH FIRST 10 ROWS ONLY;
SELECT e.ename, NVL(e.sal, 0) AS sal FROM emp e WHERE e.deptno IN (10, 20, 30);
SELECT * FROM emp e, dept d WHERE e.deptno = d.deptno(+);
SELECT e.ename, d.dname FROM emp e JOIN dept d ON e.deptno = d.deptno WHERE e.sal BETWEEN 1000 AND 2000;
SELECT COUNT(*), deptno FROM emp GROUP BY deptno HAVING COUNT(*) > 2;
SELECT ename FROM emp WHERE hiredate > TO_DATE('2020-01-01', 'YYYY-MM-DD');
SELECT ename FROM emp WHERE empno IN (SELECT empno FROM bonus);
SELECT DISTINCT job FROM emp;
INSERT INTO emp (empno, ename, sal) VALUES (emp_seq.NEXTVAL, 'SMITH', 800);
INSERT INTO emp (empno, ename, sal) VALUES (7369, 'SMITH', 800);
UPDATE emp SET sal = sal * 1.1 WHERE deptno = 10;
UPDATE emp SET job = 'MANAGER' WHERE empno = 7369;
DELETE FROM emp WHERE empno = 7369;
MERGE INTO emp t USING (SELECT 1 AS empno, 'X' AS ename FROM dual) s ON (t.empno = s.empno)
  WHEN MATCHED THEN UPDATE SET t.ename = s.ename
  WHEN NOT MATCHED THEN INSERT (empno, ename) VALUES (s.empno, s.ename);
COMMIT;
