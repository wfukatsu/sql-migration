-- sql-transpile のテスト用 SQL（変換元: MySQL）
-- 前半は表とデータの準備。後半がテスト対象の文で、1 文ごとに何を確かめるかを注記している。
-- 実行検証: .venv/bin/python difftest/transpile_verify.py --source mysql

CREATE TABLE dept (deptno INT PRIMARY KEY, dname VARCHAR(14), loc VARCHAR(13));
CREATE TABLE emp (
  empno INT PRIMARY KEY, ename VARCHAR(10), job VARCHAR(9), mgr INT,
  hiredate DATE, sal DECIMAL(7,2), comm DECIMAL(7,2), deptno INT);
INSERT INTO dept VALUES (10, 'ACCOUNTING', 'NEW YORK');
INSERT INTO dept VALUES (20, 'RESEARCH', 'DALLAS');
INSERT INTO dept VALUES (30, 'SALES', 'CHICAGO');
INSERT INTO dept VALUES (40, 'OPERATIONS', 'BOSTON');
INSERT INTO emp VALUES (7839, 'KING', 'PRESIDENT', NULL, DATE '1981-11-17', 5000, NULL, 10);
INSERT INTO emp VALUES (7566, 'JONES', 'MANAGER', 7839, DATE '1981-04-02', 2975, NULL, 20);
INSERT INTO emp VALUES (7698, 'BLAKE', 'MANAGER', 7839, DATE '1981-05-01', 2850, NULL, 30);
INSERT INTO emp VALUES (7782, 'CLARK', 'MANAGER', 7839, DATE '1981-06-09', 2450, NULL, 10);
INSERT INTO emp VALUES (7788, 'SCOTT', 'ANALYST', 7566, DATE '1987-04-19', 3000, NULL, 20);
INSERT INTO emp VALUES (7902, 'FORD', 'ANALYST', 7566, DATE '1981-12-03', 3000, NULL, 20);
INSERT INTO emp VALUES (7369, 'SMITH', 'CLERK', 7902, DATE '1980-12-17', 800, NULL, 20);
INSERT INTO emp VALUES (7499, 'ALLEN', 'SALESMAN', 7698, DATE '1981-02-20', 1600, 300, 30);
INSERT INTO emp VALUES (7521, 'WARD', 'SALESMAN', 7698, DATE '1981-02-22', 1250, 500, 30);
INSERT INTO emp VALUES (7654, 'MARTIN', 'SALESMAN', 7698, DATE '1981-09-28', 1250, 1400, 30);
INSERT INTO emp VALUES (7844, 'TURNER', 'SALESMAN', 7698, DATE '1981-09-08', 1500, 0, 30);
INSERT INTO emp VALUES (7876, 'ADAMS', 'CLERK', 7788, DATE '1987-05-23', 1100, NULL, 20);
INSERT INTO emp VALUES (7900, 'JAMES', 'CLERK', 7698, DATE '1981-12-03', 950, NULL, 30);
INSERT INTO emp VALUES (7934, 'MILLER', 'CLERK', 7782, DATE '1982-01-23', 1300, NULL, 10);

-- @tests
-- @note: バッククォートで囲んだ識別子
SELECT `ename`, `sal` FROM `emp` WHERE `deptno` = 10 ORDER BY `ename`;
-- @note: LIMIT の「開始位置, 件数」形式
SELECT ename FROM emp ORDER BY empno LIMIT 5, 3;
-- @note: IFNULL
SELECT ename, IFNULL(comm, 0) AS c FROM emp ORDER BY empno;
-- @note: IF 関数
SELECT ename, IF(sal > 2000, 'HIGH', 'LOW') AS g FROM emp ORDER BY empno;
-- @note: CONCAT と CONCAT_WS
SELECT CONCAT(ename, '-', job) AS a, CONCAT_WS('/', ename, job) AS b FROM emp WHERE deptno = 10 ORDER BY a;
-- @note: 整数どうしの除算（MySQL は小数を返す。PostgreSQL は切り捨てる）
SELECT empno, empno / 4 AS q FROM emp ORDER BY empno;
-- @note: DIV による整数除算
SELECT empno, empno DIV 4 AS q FROM emp ORDER BY empno;
-- @note: DATE_FORMAT
SELECT ename, DATE_FORMAT(hiredate, '%Y-%m') AS ym FROM emp ORDER BY empno;
-- @note: YEAR と MONTH
SELECT ename, YEAR(hiredate) AS y, MONTH(hiredate) AS m FROM emp ORDER BY empno;
-- @note: DATEDIFF（日数の差）
SELECT ename, DATEDIFF(DATE '1990-01-01', hiredate) AS days FROM emp ORDER BY empno;
-- @note: DATE_ADD
SELECT ename, DATE_ADD(hiredate, INTERVAL 6 MONTH) AS later FROM emp ORDER BY empno;
-- @note: STR_TO_DATE
SELECT ename FROM emp WHERE hiredate > STR_TO_DATE('1981-06-30', '%Y-%m-%d') ORDER BY ename;
-- @note: GROUP_CONCAT
SELECT deptno, GROUP_CONCAT(ename ORDER BY ename SEPARATOR ',') AS names FROM emp GROUP BY deptno ORDER BY deptno;
-- @note: WITH ROLLUP
SELECT deptno, SUM(sal) AS total FROM emp GROUP BY deptno WITH ROLLUP;
-- @note: LIKE（MySQL の既定の照合順序は大文字小文字を区別しない）
SELECT ename FROM emp WHERE ename LIKE 'k%' ORDER BY ename;
-- @note: REGEXP による正規表現マッチ
SELECT ename FROM emp WHERE ename REGEXP '^[AS]' ORDER BY ename;
-- @note: ウィンドウ関数
SELECT ename, RANK() OVER (PARTITION BY deptno ORDER BY sal DESC) AS rnk FROM emp ORDER BY deptno, rnk, ename;
-- @note: 再帰 CTE
WITH RECURSIVE chain AS (SELECT empno, ename, 1 AS lvl FROM emp WHERE mgr IS NULL UNION ALL SELECT e.empno, e.ename, c.lvl + 1 FROM emp e JOIN chain c ON e.mgr = c.empno) SELECT ename, lvl FROM chain ORDER BY lvl, ename;
-- @note: 昇順の ORDER BY で NULL が先頭に来る（MySQL の既定）
SELECT ename, comm FROM emp ORDER BY comm, ename;
-- @note: STRAIGHT_JOIN ヒント
SELECT e.ename, d.dname FROM emp e STRAIGHT_JOIN dept d ON e.deptno = d.deptno ORDER BY e.ename;
-- @note: 複数行の INSERT
-- @check: SELECT deptno, dname FROM dept ORDER BY deptno
INSERT INTO dept VALUES (50, 'R50', 'X'), (60, 'R60', 'Y');
-- @note: ON DUPLICATE KEY UPDATE による upsert
-- @check: SELECT deptno, dname FROM dept ORDER BY deptno
INSERT INTO dept (deptno, dname, loc) VALUES (10, 'ACC2', 'NY') ON DUPLICATE KEY UPDATE dname = VALUES(dname);
-- @note: INSERT IGNORE
-- @check: SELECT deptno, dname FROM dept ORDER BY deptno
INSERT IGNORE INTO dept (deptno, dname, loc) VALUES (10, 'ACC3', 'NY');
-- @note: REPLACE INTO
-- @check: SELECT deptno, dname, loc FROM dept ORDER BY deptno
REPLACE INTO dept (deptno, dname, loc) VALUES (20, 'RES2', 'LA');
-- @note: 結合つきの UPDATE
-- @check: SELECT empno, sal FROM emp ORDER BY empno
UPDATE emp e JOIN dept d ON e.deptno = d.deptno SET e.sal = e.sal + 100 WHERE d.loc = 'DALLAS';
-- @note: ORDER BY と LIMIT つきの UPDATE
-- @check: SELECT empno, sal FROM emp ORDER BY empno
UPDATE emp SET sal = sal + 1 ORDER BY empno LIMIT 2;
-- @note: AUTO_INCREMENT 列を持つ表の作成
CREATE TABLE audit_log (id INT AUTO_INCREMENT PRIMARY KEY, msg VARCHAR(50));
