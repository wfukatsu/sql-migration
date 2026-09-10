-- Oracle-specific SQL feature survey. Each statement carries a "@feature:" tag used by the report generator.
CREATE TABLE emp (empno NUMBER(4) PRIMARY KEY, ename VARCHAR2(10), job VARCHAR2(9), mgr NUMBER(4), sal NUMBER(7,2), comm NUMBER(7,2), deptno NUMBER(2), hiredate DATE);
CREATE TABLE dept (deptno NUMBER(2) PRIMARY KEY, dname VARCHAR2(14), loc VARCHAR2(13));
CREATE TABLE bonus (empno NUMBER(4) PRIMARY KEY, amount NUMBER(5));
CREATE INDEX idx_emp_deptno ON emp (deptno);
-- @feature: NULL 関数 NVL
SELECT ename, NVL(comm, 0) AS comm FROM emp WHERE deptno = 30 ORDER BY ename;
-- @feature: NULL 関数 NVL2
SELECT ename, NVL2(comm, 'has', 'none') AS c FROM emp ORDER BY ename;
-- @feature: NULL 関数 NULLIF / COALESCE
SELECT ename, NULLIF(sal, 3000) AS s, COALESCE(comm, sal, 0) AS c FROM emp ORDER BY ename;
-- @feature: DECODE
SELECT ename, DECODE(deptno, 10, 'ACC', 20, 'RES', 30, 'SAL', 'OTHER') AS d FROM emp ORDER BY ename;
-- @feature: DECODE の NULL 一致
SELECT ename, DECODE(comm, NULL, 'none', 0, 'zero', 'has') AS c FROM emp ORDER BY ename;
-- @feature: 文字列連結 ||
SELECT ename || '-' || job AS t FROM emp WHERE deptno = 10 ORDER BY t;
-- @feature: 文字列関数 SUBSTR / INSTR / LENGTH
SELECT ename, SUBSTR(ename, 2, 3) AS s, INSTR(ename, 'A') AS i, LENGTH(ename) AS l FROM emp ORDER BY ename;
-- @feature: 文字列関数 SUBSTR 負の開始位置
SELECT ename, SUBSTR(ename, -3) AS s FROM emp ORDER BY ename;
-- @feature: 文字列関数 LPAD / RPAD / TRIM / INITCAP
SELECT LPAD(ename, 8, '*') AS l, RPAD(job, 10, '.') AS r, TRIM('  x  ') AS t, INITCAP(ename) AS i FROM emp WHERE deptno = 10 ORDER BY ename;
-- @feature: 文字列関数 REPLACE / TRANSLATE / UPPER / LOWER
SELECT REPLACE(ename, 'A', '@') AS r, TRANSLATE(ename, 'AEIOU', 'aeiou') AS t, LOWER(job) AS j FROM emp ORDER BY ename;
-- @feature: 数値関数 ROUND / TRUNC / MOD / CEIL / FLOOR
SELECT ename, ROUND(sal / 7, 2) AS r, TRUNC(sal / 7, 1) AS t, MOD(sal, 100) AS m, CEIL(sal / 1000) AS c, FLOOR(sal / 1000) AS f FROM emp ORDER BY ename;
-- @feature: 数値関数 POWER / ABS / SIGN / GREATEST / LEAST
SELECT ename, POWER(2, deptno / 10) AS p, ABS(sal - 2000) AS a, SIGN(sal - 2000) AS s, GREATEST(sal, 2000) AS g, LEAST(sal, 2000) AS l FROM emp ORDER BY ename;
-- @feature: TO_CHAR 数値書式
SELECT ename, TO_CHAR(sal, '99999.99') AS s FROM emp ORDER BY ename;
-- @feature: TO_CHAR 日付書式
SELECT ename, TO_CHAR(hiredate, 'YYYY/MM/DD') AS d, TO_CHAR(hiredate, 'YYYY-MM') AS ym FROM emp ORDER BY ename;
-- @feature: TO_CHAR 日付書式 (曜日・月名、NLS 依存)
SELECT ename, TO_CHAR(hiredate, 'DAY') AS dw, TO_CHAR(hiredate, 'MON') AS mn FROM emp WHERE deptno = 10 ORDER BY ename;
-- @feature: TO_DATE / DATE リテラル
SELECT ename FROM emp WHERE hiredate BETWEEN TO_DATE('1981-01-01', 'YYYY-MM-DD') AND DATE '1981-06-30' ORDER BY ename;
-- @feature: TO_NUMBER / CAST
SELECT ename, TO_NUMBER('12.5') + sal AS n, CAST(sal AS VARCHAR2(10)) AS s FROM emp WHERE deptno = 10 ORDER BY ename;
-- @feature: 日付関数 ADD_MONTHS / MONTHS_BETWEEN / LAST_DAY
SELECT ename, ADD_MONTHS(hiredate, 6) AS a, MONTHS_BETWEEN(DATE '1982-01-01', hiredate) AS m, LAST_DAY(hiredate) AS l FROM emp ORDER BY ename;
-- @feature: 日付関数 NEXT_DAY
SELECT ename, NEXT_DAY(hiredate, 'MONDAY') AS n FROM emp WHERE deptno = 10 ORDER BY ename;
-- @feature: 日付関数 EXTRACT / TRUNC(date)
SELECT ename, EXTRACT(YEAR FROM hiredate) AS y, EXTRACT(MONTH FROM hiredate) AS m, TRUNC(hiredate, 'MM') AS t FROM emp ORDER BY ename;
-- @feature: 日付演算 (日数の加減算、日付差)
SELECT ename, hiredate + 30 AS d30, DATE '1982-01-01' - hiredate AS days FROM emp ORDER BY ename;
-- @feature: INTERVAL リテラル
SELECT ename, hiredate + INTERVAL '1' YEAR AS y1, hiredate + INTERVAL '2' MONTH AS m2 FROM emp WHERE deptno = 10 ORDER BY ename;
-- @feature: ROWNUM (単純な件数制限)
SELECT ename FROM emp WHERE deptno = 30 AND ROWNUM <= 5;
-- @feature: ROWNUM ページング (入れ子)
SELECT ename FROM (SELECT a.*, ROWNUM AS rn FROM (SELECT ename FROM emp ORDER BY sal DESC) a WHERE ROWNUM <= 4) WHERE rn > 2;
-- @feature: FETCH FIRST / OFFSET
SELECT ename FROM emp ORDER BY sal DESC OFFSET 2 ROWS FETCH NEXT 3 ROWS ONLY;
-- @feature: (+) 外部結合
SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+) ORDER BY e.ename;
-- @feature: (+) 外部結合 (RIGHT 側、未使用部門の抽出)
SELECT d.dname FROM emp e, dept d WHERE e.deptno(+) = d.deptno AND e.empno IS NULL ORDER BY d.dname;
-- @feature: 階層問合せ CONNECT BY / START WITH / LEVEL
SELECT LPAD(' ', 2 * (LEVEL - 1)) || ename AS tree, LEVEL AS lv FROM emp START WITH mgr IS NULL CONNECT BY PRIOR empno = mgr ORDER SIBLINGS BY ename;
-- @feature: 階層問合せ SYS_CONNECT_BY_PATH
SELECT ename, SYS_CONNECT_BY_PATH(ename, '/') AS path FROM emp START WITH mgr IS NULL CONNECT BY PRIOR empno = mgr ORDER BY ename;
-- @feature: 再帰 WITH (階層問合せの標準形)
WITH h (empno, ename, mgr, lv) AS (SELECT empno, ename, mgr, 1 FROM emp WHERE mgr IS NULL UNION ALL SELECT e.empno, e.ename, e.mgr, h.lv + 1 FROM emp e JOIN h ON e.mgr = h.empno) SELECT ename, lv FROM h ORDER BY lv, ename;
-- @feature: 分析関数 RANK / DENSE_RANK / ROW_NUMBER
SELECT ename, RANK() OVER (ORDER BY sal DESC) AS r, DENSE_RANK() OVER (ORDER BY sal DESC) AS d, ROW_NUMBER() OVER (PARTITION BY deptno ORDER BY sal DESC) AS n FROM emp ORDER BY ename;
-- @feature: 分析関数 LAG / LEAD / FIRST_VALUE
SELECT ename, LAG(sal) OVER (ORDER BY sal) AS prev, LEAD(sal) OVER (ORDER BY sal) AS nxt, FIRST_VALUE(ename) OVER (PARTITION BY deptno ORDER BY sal DESC) AS top FROM emp ORDER BY ename;
-- @feature: 分析関数 累計 SUM OVER / 比率 RATIO_TO_REPORT
SELECT ename, SUM(sal) OVER (PARTITION BY deptno ORDER BY sal ROWS UNBOUNDED PRECEDING) AS cum, ROUND(RATIO_TO_REPORT(sal) OVER (PARTITION BY deptno), 3) AS ratio FROM emp ORDER BY ename;
-- @feature: LISTAGG WITHIN GROUP
SELECT deptno, LISTAGG(ename, ',') WITHIN GROUP (ORDER BY ename) AS names FROM emp GROUP BY deptno ORDER BY deptno;
-- @feature: KEEP (DENSE_RANK FIRST)
SELECT deptno, MAX(ename) KEEP (DENSE_RANK FIRST ORDER BY hiredate) AS first_hired FROM emp GROUP BY deptno ORDER BY deptno;
-- @feature: 集合演算 MINUS
SELECT deptno FROM dept MINUS SELECT deptno FROM emp;
-- @feature: 集合演算 INTERSECT / UNION ALL
SELECT deptno FROM dept INTERSECT SELECT deptno FROM emp UNION ALL SELECT 99 FROM dual;
-- @feature: GROUP BY ROLLUP / GROUPING
SELECT deptno, job, SUM(sal) AS s, GROUPING(job) AS g FROM emp GROUP BY ROLLUP (deptno, job) ORDER BY deptno, job;
-- @feature: GROUP BY CUBE
SELECT deptno, job, COUNT(*) AS n FROM emp WHERE deptno IN (10, 20) GROUP BY CUBE (deptno, job) ORDER BY deptno, job;
-- @feature: GROUPING SETS
SELECT deptno, job, SUM(sal) AS s FROM emp GROUP BY GROUPING SETS ((deptno), (job)) ORDER BY deptno, job;
-- @feature: PIVOT
SELECT * FROM (SELECT deptno, job, sal FROM emp) PIVOT (SUM(sal) FOR job IN ('CLERK' AS clerk, 'MANAGER' AS manager)) ORDER BY deptno;
-- @feature: UNPIVOT
SELECT deptno, kind, val FROM (SELECT deptno, SUM(sal) AS total, MAX(sal) AS top FROM emp GROUP BY deptno) UNPIVOT (val FOR kind IN (total AS 'T', top AS 'M')) ORDER BY deptno, kind;
-- @feature: 正規表現 REGEXP_LIKE
SELECT ename FROM emp WHERE REGEXP_LIKE(ename, '^[A-C].*') ORDER BY ename;
-- @feature: 正規表現 REGEXP_SUBSTR / REGEXP_REPLACE / REGEXP_COUNT
SELECT ename, REGEXP_SUBSTR(ename, '[AEIOU]+') AS v, REGEXP_REPLACE(ename, '[AEIOU]', '_') AS r, REGEXP_COUNT(ename, 'L') AS c FROM emp ORDER BY ename;
-- @feature: スカラー副問合せ (SELECT 句)
SELECT e.ename, (SELECT d.dname FROM dept d WHERE d.deptno = e.deptno) AS dname FROM emp e ORDER BY e.ename;
-- @feature: 相関副問合せ (WHERE 句)
SELECT e.ename, e.sal FROM emp e WHERE e.sal > (SELECT AVG(sal) FROM emp WHERE deptno = e.deptno) ORDER BY e.ename;
-- @feature: ANY / ALL
SELECT ename FROM emp WHERE sal > ALL (SELECT sal FROM emp WHERE deptno = 30) OR sal < ANY (SELECT sal FROM emp WHERE job = 'CLERK') ORDER BY ename;
-- @feature: NOT EXISTS / NOT IN
SELECT d.dname FROM dept d WHERE NOT EXISTS (SELECT 1 FROM emp e WHERE e.deptno = d.deptno) AND d.deptno NOT IN (10, 20) ORDER BY d.dname;
-- @feature: 行値式 IN
SELECT ename FROM emp WHERE (deptno, job) IN ((10, 'CLERK'), (30, 'SALESMAN')) ORDER BY ename;
-- @feature: FROM DUAL
SELECT 1 + 1 AS two, 'a' || NULL AS s, LENGTH('') AS l FROM dual;
-- @feature: 空文字列 = NULL の扱い
SELECT ename FROM emp WHERE comm IS NULL AND NVL('', 'x') = 'x' ORDER BY ename;
-- @feature: オプティマイザヒント
SELECT /*+ INDEX(emp idx_emp_deptno) */ ename FROM emp WHERE deptno = 20 ORDER BY ename;
-- @feature: CASE 式 (単純 / 検索)
SELECT ename, CASE job WHEN 'CLERK' THEN 'C' WHEN 'MANAGER' THEN 'M' ELSE 'X' END AS j, CASE WHEN sal >= 3000 THEN 'high' WHEN sal >= 1500 THEN 'mid' ELSE 'low' END AS band FROM emp ORDER BY ename;
-- @feature: ORDER BY NULLS FIRST / 位置指定
SELECT ename, comm FROM emp ORDER BY 2 DESC NULLS FIRST, 1;
-- @feature: NULL の既定ソート順 (Oracle は昇順で NULL 最後)
SELECT ename, comm FROM emp ORDER BY comm, ename;
-- @feature: LIKE ESCAPE
SELECT ename FROM emp WHERE ename LIKE 'S%' ESCAPE '\' ORDER BY ename;
-- @feature: JOIN USING / NATURAL JOIN
SELECT ename, dname FROM emp JOIN dept USING (deptno) WHERE deptno = 10 ORDER BY ename;
-- @feature: 複数 CTE (副問合せのファクタリング)
WITH d AS (SELECT deptno, AVG(sal) AS avg_sal FROM emp GROUP BY deptno), top AS (SELECT deptno FROM d WHERE avg_sal > 2000) SELECT e.ename FROM emp e JOIN top t ON e.deptno = t.deptno ORDER BY e.ename;
-- @feature: SELECT ... FOR UPDATE
SELECT ename, sal FROM emp WHERE empno = 7369 FOR UPDATE;
-- @feature: ROWID
SELECT ename FROM emp WHERE ROWID IS NOT NULL AND deptno = 10 ORDER BY ename;
-- @feature: SYSDATE / SYSTIMESTAMP (非決定的: 実行のみ確認)
SELECT ename FROM emp WHERE hiredate < SYSDATE AND deptno = 10 ORDER BY ename;
-- @feature: TIMESTAMP リテラル / TO_TIMESTAMP
SELECT ename FROM emp WHERE hiredate > TO_TIMESTAMP('1981-06-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS') AND hiredate < TIMESTAMP '1982-06-01 00:00:00' ORDER BY ename;
