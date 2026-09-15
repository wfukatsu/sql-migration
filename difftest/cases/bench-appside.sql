-- Benchmark case file (Oracle dialect) for the statements the app-side analysis (scalardb_migrate/appside.py) handles
-- differently from the earlier converter. Runs on the bench data set: difftest/bench.py --dataset bench.
--   * a date range inside a WITH is pushed into the ScalarDB fetch (DATE literals were not recognised before)
--   * GROUP BY ROLLUP is no longer planned for H2, which cannot run it (RESIDUAL_H2)
CREATE TABLE emp (empno NUMBER(9) PRIMARY KEY, ename VARCHAR2(20), sal NUMBER(9,2), comm NUMBER(9,2), deptno NUMBER(4), hiredate DATE);
CREATE TABLE dept (deptno NUMBER(4) PRIMARY KEY, dname VARCHAR2(20));
CREATE TABLE bonus (empno NUMBER(9) PRIMARY KEY, amount NUMBER(9));
CREATE INDEX idx_emp_deptno ON emp (deptno);

-- @bench: date range inside a WITH + window function (about 7 % of emp)
WITH hired AS (SELECT empno, deptno, sal FROM emp WHERE hiredate >= DATE '2020-06-01' AND hiredate < DATE '2021-01-01')
SELECT deptno, empno, sal, RANK() OVER (PARTITION BY deptno ORDER BY sal DESC) AS rnk FROM hired ORDER BY deptno, rnk, empno;

-- @bench: date range inside a WITH + monthly aggregation (about 12 % of emp)
WITH hired AS (SELECT deptno, hiredate, sal FROM emp WHERE hiredate >= DATE '2020-01-01' AND hiredate < DATE '2021-01-01')
SELECT deptno, TO_CHAR(hiredate, 'YYYY-MM') AS ym, SUM(sal) AS total FROM hired GROUP BY deptno, TO_CHAR(hiredate, 'YYYY-MM') ORDER BY deptno, ym;

-- @bench: GROUP BY ROLLUP over an indexed filter (H2 cannot run ROLLUP)
SELECT deptno, SUM(sal) AS total FROM emp WHERE deptno = 3 GROUP BY ROLLUP (deptno) ORDER BY deptno;
