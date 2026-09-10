-- Differential-test case file (Oracle dialect). Source: Oracle Database 23ai Free. Same data as the PostgreSQL case.
CREATE TABLE emp (empno NUMBER(4) PRIMARY KEY, ename VARCHAR2(10), sal NUMBER(7,2), comm NUMBER(7,2), deptno NUMBER(2), hiredate DATE);
CREATE TABLE dept (deptno NUMBER(2) PRIMARY KEY, dname VARCHAR2(20));
CREATE TABLE bonus (empno NUMBER(4) PRIMARY KEY, amount NUMBER(5));
CREATE INDEX idx_emp_deptno ON emp (deptno);
-- convertible to ScalarDB SQL (executed through the licensed cluster)
SELECT ename, sal FROM emp WHERE empno = 2;
SELECT ename FROM emp WHERE deptno = 30 ORDER BY ename;
SELECT ename FROM emp WHERE sal > 1000 ORDER BY sal DESC FETCH FIRST 2 ROWS ONLY;
SELECT ename FROM emp WHERE deptno = 30 AND ROWNUM <= 5;
SELECT ename, hiredate FROM emp WHERE hiredate > TO_DATE('2020-06-01', 'YYYY-MM-DD') ORDER BY ename;
SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+) ORDER BY e.ename;
SELECT deptno, COUNT(*) AS n, SUM(sal) AS total FROM emp GROUP BY deptno HAVING COUNT(*) > 1;
-- app-side plans (fetch through ScalarDB, residual in H2 Oracle mode)
SELECT ename, NVL(comm, 0) AS comm, sal * 1.1 AS newsal FROM emp WHERE deptno = 30 ORDER BY ename;
SELECT ename, DECODE(deptno, 10, 'A', 30, 'S', '?') AS d, TRUNC(sal / 1000) AS k FROM emp ORDER BY ename;
SELECT DISTINCT deptno FROM emp ORDER BY deptno;
SELECT ename FROM emp WHERE empno IN (SELECT empno FROM bonus WHERE amount > 150);
SELECT UPPER(ename) AS u, CASE WHEN sal > 1500 THEN 'HIGH' ELSE 'LOW' END AS grade FROM emp WHERE SUBSTR(ename, 1, 1) = 'a' OR sal > 2000 ORDER BY u;
SELECT ename, ROW_NUMBER() OVER (PARTITION BY deptno ORDER BY sal DESC) AS rn FROM emp ORDER BY ename;
SELECT ename FROM emp e WHERE EXISTS (SELECT 1 FROM bonus b WHERE b.empno = e.empno) ORDER BY ename;
SELECT ename FROM emp WHERE deptno = 10 UNION SELECT ename FROM emp WHERE sal > 2000;
SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+) AND (d.dname LIKE 'S%' OR d.dname IS NULL) ORDER BY e.ename;
SELECT TO_CHAR(hiredate, 'YYYY-MM') AS ym, ename FROM emp WHERE deptno = 30 ORDER BY ename;
