-- sql-transpile のテスト用 SQL（変換元: Oracle Database）
-- 前半は表とデータの準備。後半がテスト対象の文で、1 文ごとに何を確かめるかを注記している。
-- 実行検証: .venv/bin/python difftest/transpile_verify.py --source oracle

CREATE TABLE dept (deptno NUMBER(2) PRIMARY KEY, dname VARCHAR2(14), loc VARCHAR2(13));
CREATE TABLE emp (
  empno NUMBER(4) PRIMARY KEY, ename VARCHAR2(10), job VARCHAR2(9), mgr NUMBER(4),
  hiredate DATE, sal NUMBER(7,2), comm NUMBER(7,2), deptno NUMBER(2));
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
-- @note: 基本の射影と絞り込み
SELECT ename, sal FROM emp WHERE deptno = 10 ORDER BY ename;
-- @note: NVL は COALESCE に書き換わる
SELECT ename, NVL(comm, 0) AS comm FROM emp ORDER BY empno;
-- @note: NVL2 は CASE に書き換わる
SELECT ename, NVL2(comm, 'Y', 'N') AS has_comm FROM emp ORDER BY empno;
-- @note: DECODE は CASE に書き換わる
SELECT ename, DECODE(deptno, 10, 'ACC', 20, 'RES', 'OTHER') AS d FROM emp ORDER BY empno;
-- @note: 検索 CASE 式
SELECT ename, CASE WHEN sal >= 3000 THEN 'HIGH' WHEN sal >= 1500 THEN 'MID' ELSE 'LOW' END AS grade FROM emp ORDER BY empno;
-- @note: 縦棒 2 本の文字列連結（MySQL では論理和の演算子なので CONCAT が要る）
SELECT ename || '/' || job AS label FROM emp WHERE deptno = 10 ORDER BY label;
-- @note: 文字列関数
SELECT ename, UPPER(job) AS u, LOWER(ename) AS l, LENGTH(ename) AS n, SUBSTR(ename, 2, 3) AS s FROM emp ORDER BY empno;
-- @note: INSTR は方言ごとに POSITION / STRPOS / LOCATE へ
SELECT ename, INSTR(ename, 'A') AS pos FROM emp ORDER BY empno;
-- @note: LPAD と RPAD
SELECT ename, LPAD(ename, 8, '*') AS l, RPAD(job, 10, '.') AS r FROM emp ORDER BY empno;
-- @note: INITCAP（PostgreSQL にはあり、MySQL には無い）
SELECT INITCAP(ename) AS name FROM emp ORDER BY empno;
-- @note: 数値関数
SELECT empno, ROUND(sal / 7, 2) AS r, MOD(empno, 7) AS m, ABS(sal - 2000) AS a, CEIL(sal / 1000) AS c, FLOOR(sal / 1000) AS f FROM emp ORDER BY empno;
-- @note: 整数どうしの除算（Oracle は小数を返す）
SELECT empno, empno / 4 AS q FROM emp ORDER BY empno;
-- @note: 数値の TRUNC（MySQL では TRUNCATE という名前）
SELECT empno, TRUNC(sal / 1000) AS k FROM emp ORDER BY empno;
-- @note: 日付の TRUNC で月初に丸める
SELECT ename, TRUNC(hiredate, 'MM') AS month_start FROM emp ORDER BY empno;
-- @note: EXTRACT
SELECT ename, EXTRACT(YEAR FROM hiredate) AS y FROM emp ORDER BY empno;
-- @note: TO_CHAR による日付の書式化
SELECT ename, TO_CHAR(hiredate, 'YYYY-MM') AS ym FROM emp ORDER BY empno;
-- @note: ADD_MONTHS
SELECT ename, ADD_MONTHS(hiredate, 6) AS later FROM emp ORDER BY empno;
-- @note: LAST_DAY（PostgreSQL には無い）
SELECT ename, LAST_DAY(hiredate) AS eom FROM emp ORDER BY empno;
-- @note: 日付リテラルとの比較
SELECT ename FROM emp WHERE hiredate > DATE '1981-06-30' ORDER BY ename;
-- @note: 集約と HAVING
SELECT deptno, COUNT(*) AS n, SUM(sal) AS total, AVG(sal) AS avg_sal, MAX(sal) AS mx FROM emp GROUP BY deptno HAVING COUNT(*) > 3 ORDER BY deptno;
-- @note: LISTAGG
SELECT deptno, LISTAGG(ename, ',') WITHIN GROUP (ORDER BY ename) AS names FROM emp GROUP BY deptno ORDER BY deptno;
-- @note: ROLLUP（小計行の NULL の並び順が方言で違う）
SELECT deptno, job, SUM(sal) AS total FROM emp GROUP BY ROLLUP (deptno, job) ORDER BY deptno, job;
-- @note: スカラーサブクエリ
SELECT ename FROM emp WHERE sal > (SELECT AVG(sal) FROM emp) ORDER BY ename;
-- @note: 相関サブクエリと EXISTS
SELECT dname FROM dept d WHERE EXISTS (SELECT 1 FROM emp e WHERE e.deptno = d.deptno) ORDER BY dname;
-- @note: IN サブクエリ
SELECT ename FROM emp WHERE deptno IN (SELECT deptno FROM dept WHERE loc = 'DALLAS') ORDER BY ename;
-- @note: ウィンドウ関数 RANK
SELECT ename, deptno, RANK() OVER (PARTITION BY deptno ORDER BY sal DESC) AS rnk FROM emp ORDER BY deptno, rnk, ename;
-- @note: LAG（入社日が重なる社員がいるので empno で順序を確定させる）
SELECT ename, sal, LAG(sal) OVER (ORDER BY hiredate, empno) AS prev_sal FROM emp ORDER BY hiredate, empno;
-- @note: 累計
SELECT ename, sal, SUM(sal) OVER (ORDER BY empno) AS running FROM emp ORDER BY empno;
-- @note: CTE
WITH t AS (SELECT deptno, SUM(sal) AS s FROM emp GROUP BY deptno) SELECT deptno, s FROM t WHERE s > 9000 ORDER BY deptno;
-- @note: 再帰 CTE（Oracle は RECURSIVE キーワードを書かない）
WITH chain (empno, ename, lvl) AS (SELECT empno, ename, 1 FROM emp WHERE mgr IS NULL UNION ALL SELECT e.empno, e.ename, c.lvl + 1 FROM emp e JOIN chain c ON e.mgr = c.empno) SELECT ename, lvl FROM chain ORDER BY lvl, ename;
-- @note: UNION
SELECT job FROM emp WHERE deptno = 10 UNION SELECT job FROM emp WHERE deptno = 20 ORDER BY job;
-- @note: MINUS は EXCEPT に書き換わる
SELECT deptno FROM dept MINUS SELECT deptno FROM emp ORDER BY deptno;
-- @note: INTERSECT
SELECT job FROM emp WHERE deptno = 10 INTERSECT SELECT job FROM emp WHERE deptno = 30 ORDER BY job;
-- @note: 明示的な JOIN
SELECT e.ename, d.dname FROM emp e JOIN dept d ON e.deptno = d.deptno WHERE d.loc = 'CHICAGO' ORDER BY e.ename;
-- @note: Oracle 外部結合。部署 40 は社員がいないので、外部結合なら NULL の行が 1 件出る
SELECT d.dname, e.ename FROM dept d, emp e WHERE d.deptno = e.deptno(+) ORDER BY d.dname, e.ename;
-- @note: 外部結合の記号が FROM の先頭テーブル側に付く形
SELECT d.dname, e.ename FROM emp e, dept d WHERE e.deptno(+) = d.deptno ORDER BY d.dname, e.ename;
-- @note: 伝統的な上位 N 件の書き方。内側の ORDER BY に頼っている
SELECT ename FROM (SELECT ename FROM emp ORDER BY sal DESC) WHERE ROWNUM <= 3;
-- @note: 同じ SELECT で ORDER BY と併用。Oracle は並べる前に 3 件を取る
SELECT ename FROM emp WHERE ROWNUM <= 3 ORDER BY ename;
-- @note: FETCH FIRST
SELECT ename FROM emp ORDER BY sal DESC, ename FETCH FIRST 3 ROWS ONLY;
-- @note: OFFSET と FETCH NEXT
SELECT ename FROM emp ORDER BY empno OFFSET 5 ROWS FETCH NEXT 3 ROWS ONLY;
-- @note: DUAL からの SELECT
SELECT 1 + 1 AS two FROM dual;
-- @note: 正規表現による絞り込み
SELECT ename FROM emp WHERE REGEXP_LIKE(ename, '^[AS]') ORDER BY ename;
-- @note: 列を参照する式での UPDATE
-- @check: SELECT empno, sal FROM emp ORDER BY empno
UPDATE emp SET sal = sal * 1.1 WHERE deptno = 10;
-- @note: 条件つき DELETE
-- @check: SELECT empno FROM emp ORDER BY empno
DELETE FROM emp WHERE comm IS NULL AND deptno = 30;
-- @note: 日付リテラルを含む INSERT
-- @check: SELECT empno, ename, hiredate FROM emp WHERE empno = 8000
INSERT INTO emp (empno, ename, job, hiredate, sal, deptno) VALUES (8000, 'NEWBIE', 'CLERK', DATE '2024-04-01', 1000, 20);
-- @note: サブクエリを含む UPDATE（MySQL は更新対象の表をサブクエリで読めない）
-- @check: SELECT empno, sal FROM emp WHERE empno = 7369
UPDATE emp SET sal = (SELECT MAX(sal) FROM emp WHERE deptno = 20) WHERE empno = 7369;
-- @note: 定数ソースの MERGE による upsert
-- @check: SELECT deptno, dname FROM dept ORDER BY deptno
MERGE INTO dept d USING (SELECT 50 AS deptno, 'R50' AS dname FROM dual) s ON (d.deptno = s.deptno) WHEN MATCHED THEN UPDATE SET d.dname = s.dname WHEN NOT MATCHED THEN INSERT (deptno, dname) VALUES (s.deptno, s.dname);
-- @note: 階層問合せ。再帰 CTE への書き換えが要る
SELECT ename, LEVEL AS lvl FROM emp START WITH mgr IS NULL CONNECT BY PRIOR empno = mgr ORDER BY lvl, ename;
-- @note: 疑似列による行の特定
SELECT ROWID, ename FROM emp WHERE empno = 7839;
-- @note: PIVOT
SELECT * FROM (SELECT deptno, job, sal FROM emp) PIVOT (SUM(sal) FOR job IN ('CLERK' AS clerk, 'MANAGER' AS manager)) ORDER BY deptno;
-- @note: KEEP (DENSE_RANK FIRST)
SELECT deptno, MAX(ename) KEEP (DENSE_RANK FIRST ORDER BY hiredate) AS first_hired FROM emp GROUP BY deptno ORDER BY deptno;
-- @note: シーケンスの作成
CREATE SEQUENCE emp_seq START WITH 9000;
-- @note: シーケンスによる採番
-- @check: SELECT empno, ename FROM emp WHERE ename = 'SEQ'
INSERT INTO emp (empno, ename, deptno) VALUES (emp_seq.NEXTVAL, 'SEQ', 10);
