-- Benchmark case file (Oracle dialect). Same statements are run against Oracle Database (baseline) and against
-- ScalarDB (converted ScalarDB SQL, or the app-side plan: fetch through ScalarDB + residual SQL in H2).
--
-- Annotations (one per statement, on the lines above it):
--   "at"bench <label>        the label used in the report
--   "at"iterate <literal>    replace this literal with the iteration counter, so each iteration touches a
--                            different row (rather than measuring one cached row over and over)
--   "at"compare rows|count   how the two result sets are compared (default: rows)
CREATE TABLE emp (empno NUMBER(9) PRIMARY KEY, ename VARCHAR2(20), sal NUMBER(9,2), comm NUMBER(9,2), deptno NUMBER(4), hiredate DATE);
CREATE TABLE dept (deptno NUMBER(4) PRIMARY KEY, dname VARCHAR2(20));
CREATE TABLE bonus (empno NUMBER(9) PRIMARY KEY, amount NUMBER(9));
CREATE INDEX idx_emp_deptno ON emp (deptno);

-- @bench: primary-key point lookup
-- @iterate: 4242
SELECT ename, sal, deptno FROM emp WHERE empno = 4242;

-- @bench: secondary-index equality (about 1/40 of the table)
SELECT empno, ename, sal FROM emp WHERE deptno = 7;

-- @bench: secondary-index equality, first 10 rows
-- @compare: count
SELECT empno, ename FROM emp WHERE deptno = 7 AND ROWNUM <= 10;

-- @bench: non-indexed filter (cross-partition scan)
SELECT empno, ename FROM emp WHERE sal > 9900 ORDER BY empno;

-- @bench: primary-key range (no ScalarDB partition-key range scan)
SELECT empno, ename FROM emp WHERE empno >= 1000 AND empno < 1100 ORDER BY empno;

-- @bench: full-table aggregation
SELECT deptno, COUNT(*) AS n, SUM(sal) AS total FROM emp GROUP BY deptno;

-- @bench: join on primary key of the joined table (single driving row)
-- @iterate: 4242
SELECT e.ename, d.dname FROM emp e INNER JOIN dept d ON e.deptno = d.deptno WHERE e.empno = 4242;

-- @bench: join driven by a secondary index
SELECT e.ename, d.dname FROM emp e INNER JOIN dept d ON e.deptno = d.deptno WHERE e.deptno = 7;

-- @bench: expression projection (NVL / arithmetic) over an indexed filter
SELECT ename, NVL(comm, 0) AS comm, sal * 1.1 AS newsal FROM emp WHERE deptno = 7 ORDER BY ename;

-- @bench: DISTINCT over the whole table
SELECT DISTINCT deptno FROM emp ORDER BY deptno;

-- @bench: window function over an indexed filter
SELECT ename, ROW_NUMBER() OVER (PARTITION BY deptno ORDER BY sal DESC, empno) AS rn FROM emp WHERE deptno = 7 ORDER BY ename;

-- @bench: IN subquery over a second table
SELECT ename FROM emp WHERE empno IN (SELECT empno FROM bonus WHERE amount > 9000) ORDER BY ename;

-- @bench: GROUP BY on a date expression
SELECT TO_CHAR(hiredate, 'YYYY') AS y, COUNT(*) AS n FROM emp GROUP BY TO_CHAR(hiredate, 'YYYY') ORDER BY y;

-- @bench: single-row UPDATE by primary key
-- @iterate: 4242
UPDATE emp SET sal = 4321 WHERE empno = 4242;

-- @bench: single-row upsert (Oracle MERGE / ScalarDB UPSERT)
-- @iterate: 900001
MERGE INTO emp t
USING (SELECT 900001 AS empno, 'bench' AS ename, 5000 AS sal, 1 AS deptno FROM dual) s
ON (t.empno = s.empno)
WHEN MATCHED THEN UPDATE SET t.ename = s.ename, t.sal = s.sal, t.deptno = s.deptno
WHEN NOT MATCHED THEN INSERT (empno, ename, sal, deptno) VALUES (s.empno, s.ename, s.sal, s.deptno);
