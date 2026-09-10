-- Differential-test case file (PostgreSQL dialect). DDL is applied to the source database verbatim and converted for
-- ScalarDB; every SELECT is executed on both sides and the result sets are compared.
CREATE TABLE emp (empno INT PRIMARY KEY, ename VARCHAR(10), sal DOUBLE PRECISION, comm DOUBLE PRECISION, deptno INT, hiredate DATE);
CREATE TABLE dept (deptno INT PRIMARY KEY, dname VARCHAR(20));
CREATE TABLE bonus (empno INT PRIMARY KEY, amount INT);
CREATE INDEX idx_emp_deptno ON emp (deptno);
-- convertible (ScalarDB SQL, needs the licensed cluster to execute)
SELECT ename, sal FROM emp WHERE empno = 2;
SELECT ename FROM emp WHERE deptno = 30 ORDER BY ename;
-- app-side plans (fetch through ScalarDB, residual in H2)
SELECT ename, COALESCE(comm, 0) AS comm, sal * 1.1 AS newsal FROM emp WHERE deptno = 30 ORDER BY ename;
SELECT DISTINCT deptno FROM emp ORDER BY deptno;
SELECT ename FROM emp WHERE empno IN (SELECT empno FROM bonus WHERE amount > 150);
SELECT d.dname, COUNT(*) AS n, SUM(e.sal) AS total FROM emp e JOIN dept d ON e.deptno = d.deptno GROUP BY d.dname HAVING COUNT(*) > 1;
SELECT UPPER(ename) AS u, CASE WHEN sal > 1500 THEN 'HIGH' ELSE 'LOW' END AS grade FROM emp WHERE SUBSTR(ename, 1, 1) = 'a' OR sal > 2000 ORDER BY u;
SELECT ename FROM emp ORDER BY sal DESC LIMIT 2 OFFSET 1;
SELECT ename FROM emp WHERE deptno = 10 UNION SELECT ename FROM emp WHERE sal > 2000;
SELECT deptno, COUNT(DISTINCT ename) AS d FROM emp GROUP BY deptno ORDER BY deptno;
SELECT ename, ROW_NUMBER() OVER (PARTITION BY deptno ORDER BY sal DESC) AS rn FROM emp ORDER BY ename;
SELECT ename FROM emp e WHERE EXISTS (SELECT 1 FROM bonus b WHERE b.empno = e.empno) ORDER BY ename;
SELECT e.ename, d.dname FROM emp e LEFT JOIN dept d ON e.deptno = d.deptno WHERE d.dname LIKE 'S%' ORDER BY e.ename;
SELECT TO_CHAR(hiredate, 'YYYY-MM') AS ym, ename FROM emp WHERE deptno = 30 ORDER BY ename;
WITH big AS (SELECT * FROM emp WHERE sal > 1000) SELECT COUNT(*) AS n FROM big;
