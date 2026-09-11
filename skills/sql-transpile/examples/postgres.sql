-- sql-transpile のテスト用 SQL（変換元: PostgreSQL）
-- 前半は表とデータの準備。後半がテスト対象の文で、1 文ごとに何を確かめるかを注記している。
-- 実行検証: .venv/bin/python difftest/transpile_verify.py --source postgres

CREATE TABLE dept (deptno INTEGER PRIMARY KEY, dname VARCHAR(14), loc VARCHAR(13));
CREATE TABLE emp (
  empno INTEGER PRIMARY KEY, ename VARCHAR(10), job VARCHAR(9), mgr INTEGER,
  hiredate DATE, sal NUMERIC(7,2), comm NUMERIC(7,2), deptno INTEGER);
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
-- @note: ILIKE による大文字小文字を区別しない検索
SELECT ename FROM emp WHERE ename ILIKE 'k%' ORDER BY ename;
-- @note: コロン 2 つによるキャスト
SELECT ename, sal::INTEGER AS s FROM emp ORDER BY empno;
-- @note: COALESCE と NULLIF
SELECT ename, COALESCE(comm, 0) AS c, NULLIF(comm, 0) AS z FROM emp ORDER BY empno;
-- @note: 縦棒 2 本の文字列連結
SELECT ename || '-' || job AS label FROM emp WHERE deptno = 10 ORDER BY label;
-- @note: 整数どうしの除算（PostgreSQL は切り捨てる。Oracle と MySQL は小数を返す）
SELECT empno, empno / 4 AS q FROM emp ORDER BY empno;
-- @note: string_agg
SELECT deptno, string_agg(ename, ',' ORDER BY ename) AS names FROM emp GROUP BY deptno ORDER BY deptno;
-- @note: 集約の FILTER 句
SELECT deptno, COUNT(*) FILTER (WHERE sal > 2000) AS high FROM emp GROUP BY deptno ORDER BY deptno;
-- @note: DISTINCT ON で部署ごとの最高給の社員を取る
SELECT DISTINCT ON (deptno) deptno, ename, sal FROM emp ORDER BY deptno, sal DESC, ename;
-- @note: date_trunc
SELECT ename, date_trunc('month', hiredate) AS m FROM emp ORDER BY empno;
-- @note: EXTRACT
SELECT ename, EXTRACT(YEAR FROM hiredate) AS y FROM emp ORDER BY empno;
-- @note: INTERVAL の加算
SELECT ename, hiredate + INTERVAL '6 months' AS later FROM emp ORDER BY empno;
-- @note: 日付どうしの差（日数）
SELECT ename, DATE '1990-01-01' - hiredate AS days FROM emp ORDER BY empno;
-- @note: to_char による日付の書式化
SELECT ename, to_char(hiredate, 'YYYY-MM') AS ym FROM emp ORDER BY empno;
-- @note: LIMIT と OFFSET
SELECT ename FROM emp ORDER BY empno LIMIT 3 OFFSET 5;
-- @note: ウィンドウ関数
SELECT ename, ROW_NUMBER() OVER (PARTITION BY deptno ORDER BY sal DESC, ename) AS rn FROM emp ORDER BY deptno, rn;
-- @note: 再帰 CTE
WITH RECURSIVE chain AS (SELECT empno, ename, 1 AS lvl FROM emp WHERE mgr IS NULL UNION ALL SELECT e.empno, e.ename, c.lvl + 1 FROM emp e JOIN chain c ON e.mgr = c.empno) SELECT ename, lvl FROM chain ORDER BY lvl, ename;
-- @note: チルダによる正規表現マッチ
SELECT ename FROM emp WHERE ename ~ '^[AS]' ORDER BY ename;
-- @note: generate_series
SELECT n FROM generate_series(1, 3) AS g(n) ORDER BY n;
-- @note: 真偽値の式を射影する
SELECT ename, sal > 2000 AS high FROM emp ORDER BY empno;
-- @note: 複数行の INSERT
-- @check: SELECT deptno, dname FROM dept ORDER BY deptno
INSERT INTO dept VALUES (50, 'R50', 'X'), (60, 'R60', 'Y');
-- @note: ON CONFLICT による upsert
-- @check: SELECT deptno, dname FROM dept ORDER BY deptno
INSERT INTO dept (deptno, dname, loc) VALUES (10, 'ACC2', 'NY') ON CONFLICT (deptno) DO UPDATE SET dname = EXCLUDED.dname;
-- @note: ON CONFLICT DO NOTHING
-- @check: SELECT deptno, dname FROM dept ORDER BY deptno
INSERT INTO dept (deptno, dname, loc) VALUES (10, 'ACC3', 'NY') ON CONFLICT DO NOTHING;
-- @note: 結合つきの UPDATE
-- @check: SELECT empno, sal FROM emp ORDER BY empno
UPDATE emp SET sal = sal + 100 FROM dept WHERE emp.deptno = dept.deptno AND dept.loc = 'DALLAS';
-- @note: RETURNING
DELETE FROM emp WHERE empno = 7369 RETURNING ename;
-- @note: SERIAL 列を持つ表の作成
CREATE TABLE audit_log (id SERIAL PRIMARY KEY, msg VARCHAR(50));
-- @note: シーケンスの作成
CREATE SEQUENCE emp_id_seq START 100;
-- @note: シーケンス関数 nextval
SELECT nextval('emp_id_seq') AS n;
