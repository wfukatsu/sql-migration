# Oracle 固有 SQL の検証レポート

作成日: 2026-09-10
関連文書: `docs/test-report.md` (仕組みとテスト全体)、`docs/app-side-processing-plan.md` (実装計画 v2)

## 1. 目的と方法

Oracle 固有の構文・関数を含む SQL が、この移行の仕組み (変換ツール → ScalarDB SQL、または実行計画 → ScalarDB から fetch → H2 Oracle 互換モードで残余処理) でどこまで扱えるかを、機能カテゴリ別に確かめた。

| 項目 | 内容 |
|---|---|
| 対象 | 読み取り系 62 文 (`difftest/cases/oracle-features.sql`)、書き込み・DDL・PL/SQL 系 17 文 (`difftest/cases/oracle-features-write.sql`) |
| データ | SCOTT スキーマ相当 (emp 9 行、dept 4 行、bonus 2 行)。emp に mgr 列を持ち階層問合せが可能 |
| 移行元 | Oracle Database 23ai Free (Docker、`gvenzl/oracle-free:23-slim-faststart`。Oracle XE の後継の無償版) |
| 移行先 | ScalarDB Cluster 3.19.1 (standalone、トライアルライセンス) + PostgreSQL 16 バックエンド。ScalarDB SQL JDBC 経路 (`--fetcher jdbc`) |
| 判定 | 読み取り系は文ごとに Oracle と ScalarDB 側の結果集合を比較 (ORDER BY が無い文は順序を無視)。書き込み系は変換ツールの分類のみ |
| 手順 | `difftest/run.py ... --json-out` と `difftest/classify.py` → `difftest/report.py` で表を生成 |

## 2. 結果の要約

| 区分 | 文数 | 内訳 |
|---|---|---|
| 読み取り系 PASS | 51 / 62 | ScalarDB SQL に変換して実行 7、実行計画 (fetch → H2) で実行 44 |
| 読み取り系 FAIL | 9 / 62 | H2 Oracle 互換モードに無い構文 8 (CONNECT BY 2、KEEP 1、ROLLUP / CUBE / GROUPING SETS 3、PIVOT / UNPIVOT 2)、型対応の差 1 |
| 読み取り系 変換不可 | 2 / 62 | `(+)` が FROM 側の表に付く形 1、ROWID 1 |
| 書き込み系 変換 | 2 / 17 | 定数ソースの MERGE → UPSERT、TRUNCATE TABLE |
| 書き込み系 変換不可 | 15 / 17 | シーケンス、表ソース MERGE、INSERT ALL、式・副問合せを含む UPDATE/DELETE、RETURNING、ビュー、トリガー、PL/SQL、SAVEPOINT、GRANT、COMMENT |

### 2.1 そのまま動いた Oracle 固有機能 (計画経路、H2 Oracle 互換モード)

NVL / NVL2 / NULLIF / COALESCE、DECODE (NULL 一致を含む)、`||`、SUBSTR (負の開始位置を含む) / INSTR / LENGTH / LPAD / RPAD / TRIM / REPLACE / TRANSLATE / LOWER、ROUND / TRUNC / MOD / CEIL / FLOOR / POWER / ABS / SIGN / GREATEST / LEAST、TO_CHAR (数値書式、日付書式、曜日・月名)、TO_DATE / DATE リテラル / TIMESTAMP リテラル / TO_TIMESTAMP、ADD_MONTHS / LAST_DAY / EXTRACT、日付の加減算、INTERVAL リテラル、ROWNUM (単純な件数制限、入れ子のページング)、OFFSET / FETCH、`(+)` 外部結合 (JOIN 側の表)、再帰 WITH、分析関数 (RANK / DENSE_RANK / ROW_NUMBER / LAG / LEAD / FIRST_VALUE / SUM OVER / RATIO_TO_REPORT)、LISTAGG WITHIN GROUP、MINUS / INTERSECT / UNION ALL、REGEXP_LIKE / REGEXP_SUBSTR / REGEXP_REPLACE、スカラー副問合せ、相関副問合せ、ANY / ALL、NOT EXISTS / NOT IN、行値式 IN、FROM DUAL、空文字列 = NULL の扱い、オプティマイザヒント、CASE 式、ORDER BY の NULLS FIRST と位置指定、NULL の既定ソート順、LIKE ESCAPE、JOIN USING、複数 CTE、FOR UPDATE、SYSDATE。

### 2.2 検証の過程で仕組み側に加えた改善

| 改善 | 対象 |
|---|---|
| H2 に無い Oracle 関数を Java UDF として登録 (`OracleFunctions`: INITCAP、TO_NUMBER、MONTHS_BETWEEN、NEXT_DAY、REGEXP_COUNT、DAYS_BETWEEN) | 文 13, 21, 22, 23, 48 |
| `TRUNC(date, 'MM')` → `DATE_TRUNC('MONTH', …)`、日付同士の減算 → `DAYS_BETWEEN` (Oracle は日数、H2 は INTERVAL を返す) への静的書き換え | 文 24, 25 |
| JVM ロケールを英語に固定 (TO_CHAR の曜日・月名は NLS 依存) | 文 19 |
| 相関副問合せが外側の表の列を参照する場合に、その列を fetch に含める | 文 49 |
| `FROM DUAL` のみの文は fetch を行わない | 文 54 |
| JOIN USING を ON に書き換える際、USING 列を FROM 表で修飾する (ScalarDB の曖昧列エラー) | 文 61 |
| DATE 列に対する時刻付きリテラル (`TO_TIMESTAMP`、`TIMESTAMP '...'`) の 0 時刻部を除去 (ScalarDB DATE は時刻を持たない) | 文 66 |
| ROLLUP / CUBE / GROUPING SETS、ROWID、`(+)` の危険な書き換え (SQLGlot が CROSS JOIN を生成する形) を変換ツールで検出 | 文 31, 42–44, 64 |
| MINUS / INTERSECT を計画化の対象に追加 | 文 40, 41 |

これらは単体テスト (67 件) にも反映した。

## 3. 結果一覧

### 読み取り系 (Oracle 23ai Free と ScalarDB の結果比較)

| # | 機能 | SQL (要約) | 変換 | 実行経路 | 結果 | 備考 |
|---|---|---|---|---|---|---|
| 5 | NULL 関数 NVL | `SELECT ename, NVL(comm, 0) AS comm FROM emp WHERE deptno = 30 ORDER BY ename` | PLANNED | 計画 P1 (fetch 3 行) | ✅ PASS |  |
| 6 | NULL 関数 NVL2 | `SELECT ename, NVL2(comm, 'has', 'none') AS c FROM emp ORDER BY ename` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 7 | NULL 関数 NULLIF / COALESCE | `SELECT ename, NULLIF(sal, 3000) AS s, COALESCE(comm, sal, 0) AS c FROM emp ORDER…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 8 | DECODE | `SELECT ename, DECODE(deptno, 10, 'ACC', 20, 'RES', 30, 'SAL', 'OTHER') AS d FROM…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 9 | DECODE の NULL 一致 | `SELECT ename, DECODE(comm, NULL, 'none', 0, 'zero', 'has') AS c FROM emp ORDER B…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 10 | 文字列連結 \|\| | `SELECT ename \|\| '-' \|\| job AS t FROM emp WHERE deptno = 10 ORDER BY t` | PLANNED | 計画 P1 (fetch 3 行) | ✅ PASS |  |
| 11 | 文字列関数 SUBSTR / INSTR / LENGTH | `SELECT ename, SUBSTR(ename, 2, 3) AS s, INSTR(ename, 'A') AS i, LENGTH(ename) AS…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 12 | 文字列関数 SUBSTR 負の開始位置 | `SELECT ename, SUBSTR(ename, -3) AS s FROM emp ORDER BY ename` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 13 | 文字列関数 LPAD / RPAD / TRIM / INITCAP | `SELECT LPAD(ename, 8, '*') AS l, RPAD(job, 10, '.') AS r, TRIM('  x  ') AS t, IN…` | PLANNED | 計画 P1 (fetch 3 行) | ✅ PASS |  |
| 14 | 文字列関数 REPLACE / TRANSLATE / UPPER / LOWER | `SELECT REPLACE(ename, 'A', '@') AS r, TRANSLATE(ename, 'AEIOU', 'aeiou') AS t, L…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 15 | 数値関数 ROUND / TRUNC / MOD / CEIL / FLOOR | `SELECT ename, ROUND(sal / 7, 2) AS r, TRUNC(sal / 7, 1) AS t, MOD(sal, 100) AS m…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 16 | 数値関数 POWER / ABS / SIGN / GREATEST / LEAST | `SELECT ename, POWER(2, deptno / 10) AS p, ABS(sal - 2000) AS a, SIGN(sal - 2000)…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 17 | TO_CHAR 数値書式 | `SELECT ename, TO_CHAR(sal, '99999.99') AS s FROM emp ORDER BY ename` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 18 | TO_CHAR 日付書式 | `SELECT ename, TO_CHAR(hiredate, 'YYYY/MM/DD') AS d, TO_CHAR(hiredate, 'YYYY-MM')…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 19 | TO_CHAR 日付書式 (曜日・月名、NLS 依存) | `SELECT ename, TO_CHAR(hiredate, 'DAY') AS dw, TO_CHAR(hiredate, 'MON') AS mn FRO…` | PLANNED | 計画 P1 (fetch 3 行) | ✅ PASS |  |
| 20 | TO_DATE / DATE リテラル | `SELECT ename FROM emp WHERE hiredate BETWEEN TO_DATE('1981-01-01', 'YYYY-MM-DD')…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 21 | TO_NUMBER / CAST | `SELECT ename, TO_NUMBER('12.5') + sal AS n, CAST(sal AS VARCHAR2(10)) AS s FROM …` | PLANNED | 計画 P1 | ❌ FAIL | 型対応の差: NUMBER(7,2) → DOUBLE のため CAST(sal AS VARCHAR2) が '2450.0' になる。整数値なら INT/BIGINT、金額はスケール済み BIGINT にする |
| 22 | 日付関数 ADD_MONTHS / MONTHS_BETWEEN / LAST_DAY | `SELECT ename, ADD_MONTHS(hiredate, 6) AS a, MONTHS_BETWEEN(DATE '1982-01-01', hi…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 23 | 日付関数 NEXT_DAY | `SELECT ename, NEXT_DAY(hiredate, 'MONDAY') AS n FROM emp WHERE deptno = 10 ORDER…` | PLANNED | 計画 P1 (fetch 3 行) | ✅ PASS |  |
| 24 | 日付関数 EXTRACT / TRUNC(date) | `SELECT ename, EXTRACT(YEAR FROM hiredate) AS y, EXTRACT(MONTH FROM hiredate) AS …` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 25 | 日付演算 (日数の加減算、日付差) | `SELECT ename, hiredate + 30 AS d30, DATE '1982-01-01' - hiredate AS days FROM em…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 26 | INTERVAL リテラル | `SELECT ename, hiredate + INTERVAL '1' YEAR AS y1, hiredate + INTERVAL '2' MONTH …` | PLANNED | 計画 P1 (fetch 3 行) | ✅ PASS |  |
| 27 | ROWNUM (単純な件数制限) | `SELECT ename FROM emp WHERE deptno = 30 AND ROWNUM <= 5` | WARN | ScalarDB SQL | ✅ PASS |  |
| 28 | ROWNUM ページング (入れ子) | `SELECT ename FROM (SELECT a.*, ROWNUM AS rn FROM (SELECT ename FROM emp ORDER BY…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 29 | FETCH FIRST / OFFSET | `SELECT ename FROM emp ORDER BY sal DESC OFFSET 2 ROWS FETCH NEXT 3 ROWS ONLY` | PLANNED | 計画 P4 (fetch 9 行) | ✅ PASS |  |
| 30 | (+) 外部結合 | `SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+) ORDER BY…` | WARN | ScalarDB SQL | ✅ PASS |  |
| 31 | (+) 外部結合 (RIGHT 側、未使用部門の抽出) | `SELECT d.dname FROM emp e, dept d WHERE e.deptno(+) = d.deptno AND e.empno IS NU…` | ERROR | - | 🚫 変換不可 | (+) が FROM 側の表に付く形は自動書き換え不可 (SQLGlot の制限)。LEFT/RIGHT JOIN に手で書き換える |
| 32 | 階層問合せ CONNECT BY / START WITH / LEVEL | `SELECT LPAD(' ', 2 * (LEVEL - 1)) \|\| ename AS tree, LEVEL AS lv FROM emp START W…` | PLANNED | 計画 P1 | ❌ FAIL | H2 未対応構文: 階層問合せ。再帰 WITH に書き換える (文 34 は PASS) |
| 33 | 階層問合せ SYS_CONNECT_BY_PATH | `SELECT ename, SYS_CONNECT_BY_PATH(ename, '/') AS path FROM emp START WITH mgr IS…` | PLANNED | 計画 P1 | ❌ FAIL | H2 未対応構文: 階層問合せ。再帰 WITH に書き換える (文 34 は PASS) |
| 34 | 再帰 WITH (階層問合せの標準形) | `WITH h (empno, ename, mgr, lv) AS (SELECT empno, ename, mgr, 1 FROM emp WHERE mg…` | PLANNED | 計画 P6 (fetch 9 行) | ✅ PASS |  |
| 35 | 分析関数 RANK / DENSE_RANK / ROW_NUMBER | `SELECT ename, RANK() OVER (ORDER BY sal DESC) AS r, DENSE_RANK() OVER (ORDER BY …` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 36 | 分析関数 LAG / LEAD / FIRST_VALUE | `SELECT ename, LAG(sal) OVER (ORDER BY sal) AS prev, LEAD(sal) OVER (ORDER BY sal…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 37 | 分析関数 累計 SUM OVER / 比率 RATIO_TO_REPORT | `SELECT ename, SUM(sal) OVER (PARTITION BY deptno ORDER BY sal ROWS UNBOUNDED PRE…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 38 | LISTAGG WITHIN GROUP | `SELECT deptno, LISTAGG(ename, ',') WITHIN GROUP (ORDER BY ename) AS names FROM e…` | PLANNED | 計画 P7 (fetch 9 行) | ✅ PASS |  |
| 39 | KEEP (DENSE_RANK FIRST) | `SELECT deptno, MAX(ename) KEEP (DENSE_RANK FIRST ORDER BY hiredate) AS first_hir…` | PLANNED | 計画 P1 | ❌ FAIL | H2 未対応構文: KEEP。ウィンドウ関数 (ROW_NUMBER で先頭行) に書き換える |
| 40 | 集合演算 MINUS | `SELECT deptno FROM dept MINUS SELECT deptno FROM emp` | PLANNED | 計画 P6 (fetch 13 行) | ✅ PASS |  |
| 41 | 集合演算 INTERSECT / UNION ALL | `SELECT deptno FROM dept INTERSECT SELECT deptno FROM emp UNION ALL SELECT 99 FRO…` | PLANNED | 計画 P6 (fetch 13 行) | ✅ PASS |  |
| 42 | GROUP BY ROLLUP / GROUPING | `SELECT deptno, job, SUM(sal) AS s, GROUPING(job) AS g FROM emp GROUP BY ROLLUP (…` | PLANNED | 計画 P7 | ❌ FAIL | H2 未対応構文: ROLLUP / CUBE / GROUPING SETS。集約レベルごとの UNION ALL に書き換える |
| 43 | GROUP BY CUBE | `SELECT deptno, job, COUNT(*) AS n FROM emp WHERE deptno IN (10, 20) GROUP BY CUB…` | PLANNED | 計画 P1 | ❌ FAIL | H2 未対応構文: CUBE。集約レベルごとの UNION ALL に書き換える |
| 44 | GROUPING SETS | `SELECT deptno, job, SUM(sal) AS s FROM emp GROUP BY GROUPING SETS ((deptno), (jo…` | PLANNED | 計画 P1 | ❌ FAIL | H2 未対応構文: ROLLUP / CUBE / GROUPING SETS。集約レベルごとの UNION ALL に書き換える |
| 45 | PIVOT | `SELECT * FROM (SELECT deptno, job, sal FROM emp) PIVOT (SUM(sal) FOR job IN ('CL…` | PLANNED | 計画 P1 | ❌ FAIL | H2 未対応構文: PIVOT。CASE 式による条件付き集約に書き換える |
| 46 | UNPIVOT | `SELECT deptno, kind, val FROM (SELECT deptno, SUM(sal) AS total, MAX(sal) AS top…` | PLANNED | 計画 P1 | ❌ FAIL | H2 未対応構文: UNPIVOT。UNION ALL に書き換える |
| 47 | 正規表現 REGEXP_LIKE | `SELECT ename FROM emp WHERE REGEXP_LIKE(ename, '^[A-C].*') ORDER BY ename` | PLANNED | 計画 P2 (fetch 9 行) | ✅ PASS |  |
| 48 | 正規表現 REGEXP_SUBSTR / REGEXP_REPLACE / REGEXP_COUNT | `SELECT ename, REGEXP_SUBSTR(ename, '[AEIOU]+') AS v, REGEXP_REPLACE(ename, '[AEI…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 49 | スカラー副問合せ (SELECT 句) | `SELECT e.ename, (SELECT d.dname FROM dept d WHERE d.deptno = e.deptno) AS dname …` | PLANNED | 計画 P1 (fetch 13 行) | ✅ PASS |  |
| 50 | 相関副問合せ (WHERE 句) | `SELECT e.ename, e.sal FROM emp e WHERE e.sal > (SELECT AVG(sal) FROM emp WHERE d…` | PLANNED | 計画 P5 (fetch 9 行) | ✅ PASS |  |
| 51 | ANY / ALL | `SELECT ename FROM emp WHERE sal > ALL (SELECT sal FROM emp WHERE deptno = 30) OR…` | PLANNED | 計画 P5 (fetch 9 行) | ✅ PASS |  |
| 52 | NOT EXISTS / NOT IN | `SELECT d.dname FROM dept d WHERE NOT EXISTS (SELECT 1 FROM emp e WHERE e.deptno …` | PLANNED | 計画 P1 (fetch 11 行) | ✅ PASS |  |
| 53 | 行値式 IN | `SELECT ename FROM emp WHERE (deptno, job) IN ((10, 'CLERK'), (30, 'SALESMAN')) O…` | PLANNED | 計画 P2 (fetch 9 行) | ✅ PASS |  |
| 54 | FROM DUAL | `SELECT 1 + 1 AS two, 'a' \|\| NULL AS s, LENGTH('') AS l FROM dual` | PLANNED | 計画 P1 (fetch 0 行) | ✅ PASS |  |
| 55 | 空文字列 = NULL の扱い | `SELECT ename FROM emp WHERE comm IS NULL AND NVL('', 'x') = 'x' ORDER BY ename` | PLANNED | 計画 P2 (fetch 7 行) | ✅ PASS |  |
| 56 | オプティマイザヒント | `SELECT /*+ INDEX(emp idx_emp_deptno) */ ename FROM emp WHERE deptno = 20 ORDER B…` | PLANNED | 計画 P1 (fetch 3 行) | ✅ PASS |  |
| 57 | CASE 式 (単純 / 検索) | `SELECT ename, CASE job WHEN 'CLERK' THEN 'C' WHEN 'MANAGER' THEN 'M' ELSE 'X' EN…` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 58 | ORDER BY NULLS FIRST / 位置指定 | `SELECT ename, comm FROM emp ORDER BY 2 DESC NULLS FIRST, 1` | PLANNED | 計画 P1 (fetch 9 行) | ✅ PASS |  |
| 59 | NULL の既定ソート順 (Oracle は昇順で NULL 最後) | `SELECT ename, comm FROM emp ORDER BY comm, ename` | WARN | ScalarDB SQL | ✅ PASS |  |
| 60 | LIKE ESCAPE | `SELECT ename FROM emp WHERE ename LIKE 'S%' ESCAPE '\' ORDER BY ename` | WARN | ScalarDB SQL | ✅ PASS |  |
| 61 | JOIN USING / NATURAL JOIN | `SELECT ename, dname FROM emp JOIN dept USING (deptno) WHERE deptno = 10 ORDER BY…` | WARN | ScalarDB SQL | ✅ PASS |  |
| 62 | 複数 CTE (副問合せのファクタリング) | `WITH d AS (SELECT deptno, AVG(sal) AS avg_sal FROM emp GROUP BY deptno), top AS …` | PLANNED | 計画 P6 (fetch 9 行) | ✅ PASS |  |
| 63 | SELECT ... FOR UPDATE | `SELECT ename, sal FROM emp WHERE empno = 7369 FOR UPDATE` | WARN | ScalarDB SQL | ✅ PASS |  |
| 64 | ROWID | `SELECT ename FROM emp WHERE ROWID IS NOT NULL AND deptno = 10 ORDER BY ename` | ERROR | - | 🚫 変換不可 | ScalarDB に ROWID は無い。主キーで置き換える |
| 65 | SYSDATE / SYSTIMESTAMP (非決定的: 実行のみ確認) | `SELECT ename FROM emp WHERE hiredate < SYSDATE AND deptno = 10 ORDER BY ename` | PLANNED | 計画 P1 (fetch 3 行) | ✅ PASS |  |
| 66 | TIMESTAMP リテラル / TO_TIMESTAMP | `SELECT ename FROM emp WHERE hiredate > TO_TIMESTAMP('1981-06-01 00:00:00', 'YYYY…` | WARN | ScalarDB SQL | ✅ PASS |  |

合計 62 文: PASS 51 (ScalarDB SQL 7、計画 44)、FAIL 9、変換不可 2、その他 0

### 書き込み / DDL / PL/SQL 系 (変換ツールによる分類のみ)

| # | 機能 | SQL (要約) | 分類 | 変換結果または理由 |
|---|---|---|---|---|
| 3 | シーケンス CREATE SEQUENCE / NEXTVAL | `CREATE SEQUENCE emp_seq START WITH 8000 INCREMENT BY 1` | ERROR | DDL: CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 4 | シーケンス NEXTVAL を使う INSERT | `INSERT INTO emp (empno, ename, deptno) VALUES (emp_seq.NEXTVAL, 'NEW',…` | ERROR | SEQUENCE: VALUES column empno: sequences are not supported; generate keys in the application (e.g. UUID) |
| 5 | MERGE (表ソース) | `MERGE INTO emp t USING dept d ON (t.deptno = d.deptno) WHEN MATCHED TH…` | ERROR | MERGE: MERGE from a table/query source is not supported; only constant single-row sources can become UPSERT |
| 6 | MERGE (定数ソース) | `MERGE INTO emp t USING (SELECT 7369 AS empno, 'SMITH2' AS ename FROM d…` | WARN | UPSERT INTO emp (empno, ename) VALUES (7369, 'SMITH2') |
| 7 | INSERT ALL (複数表への挿入) | `INSERT ALL INTO bonus (empno, amount) VALUES (empno, 10) INTO emp_log …` | ERROR | STATEMENT: MultitableInserts statements are not supported by ScalarDB SQL |
| 8 | UPDATE の SYSDATE / 式 | `UPDATE emp SET sal = sal * 1.1, hiredate = SYSDATE WHERE deptno = 10` | ERROR | RMW: SET sal = sal * 1.1: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction |
| 9 | UPDATE 副問合せ | `UPDATE emp e SET sal = (SELECT AVG(sal) FROM emp WHERE deptno = e.dept…` | ERROR | RMW: SET sal = (SELECT AVG(sal) FROM emp WHERE deptno = e.deptno): expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a litera |
| 10 | DELETE 副問合せ | `DELETE FROM emp WHERE deptno IN (SELECT deptno FROM dept WHERE loc = '…` | ERROR | SUBQUERY: IN (subquery) is not supported; fetch the inner result first and bind literals |
| 11 | INSERT ... RETURNING INTO | `INSERT INTO emp (empno, ename, deptno) VALUES (8001, 'Z', 10) RETURNIN…` | ERROR | RETURNING: RETURNING is not supported |
| 12 | TRUNCATE TABLE | `TRUNCATE TABLE bonus` | OK | TRUNCATE TABLE bonus |
| 13 | ALTER TABLE MODIFY (Oracle 構文) | `ALTER TABLE emp MODIFY (ename VARCHAR2(20))` | ERROR | UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 14 | CREATE VIEW | `CREATE OR REPLACE VIEW v_emp AS SELECT ename, sal FROM emp WHERE deptn…` | ERROR | DDL: CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 15 | CREATE TRIGGER (PL/SQL) | `CREATE OR REPLACE TRIGGER trg_emp BEFORE INSERT ON emp FOR EACH ROW BE…` | ERROR | UNPARSED: statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 17 | PL/SQL 無名ブロック | `BEGIN UPDATE emp SET sal = sal + 1 WHERE empno = 7369` | ERROR | PARSE: Invalid expression / Unexpected token. Line 2, Col: 12. |
| 20 | SAVEPOINT / ROLLBACK TO | `SAVEPOINT sp1` | ERROR | STATEMENT: Alias statements are not supported by ScalarDB SQL |
| 21 | GRANT | `GRANT SELECT ON emp TO scott` | ERROR | STATEMENT: Grant statements are not supported by ScalarDB SQL |
| 22 | COMMENT ON | `COMMENT ON TABLE emp IS 'employees'` | ERROR | STATEMENT: Comment statements are not supported by ScalarDB SQL |

合計 17 文: 変換 2、変換不可 15

## 4. 考察

### 4.1 変換できる範囲

Oracle 固有の関数・式の大半は「射影の式」として ScalarDB SQL では書けないが、実行計画に回すと H2 の Oracle 互換モードがそのまま解釈するため、追加実装なしで動く。互換モードに無い関数は Java UDF で補える。今回の 5 関数はいずれも数十行で、Oracle の仕様 (MONTHS_BETWEEN の 31 日基準、NEXT_DAY の「翌日以降」など) を再現した。この UDF 層が計画書にある「関数対応表」の実体となる。

### 4.2 残る制約と対処

| 制約 | 対処 |
|---|---|
| 階層問合せ (CONNECT BY、LEVEL、SYS_CONNECT_BY_PATH、ORDER SIBLINGS BY) | H2 に無い。再帰 WITH に手で書き換える (再帰 WITH は PASS)。SYS_CONNECT_BY_PATH は再帰内の文字列連結で代替 |
| ROLLUP / CUBE / GROUPING SETS / GROUPING | H2 に無い。集約レベルごとの SELECT を UNION ALL で結合する形に書き換える |
| PIVOT / UNPIVOT | H2 に無い。PIVOT は `SUM(CASE WHEN job = 'CLERK' THEN sal END)` の条件付き集約、UNPIVOT は UNION ALL に書き換える |
| KEEP (DENSE_RANK FIRST/LAST) | H2 に無い。ROW_NUMBER() OVER (PARTITION BY … ORDER BY …) = 1 で書き換える |
| `(+)` が FROM 側の表に付く形 (`e.deptno(+) = d.deptno`) | SQLGlot の書き換えが誤った SQL (CROSS JOIN) を作るため検出して変換不可にした。LEFT/RIGHT JOIN に手で書き換える |
| ROWID / ROWSCN | ScalarDB に無い。主キーで置き換える |
| NUMBER(p,s) → DOUBLE による文字列化の差 (`CAST(sal AS VARCHAR2)` が `'2450.0'`) | 整数値の列は INT/BIGINT、金額はスケール済み BIGINT にする設計判断が必要。DOUBLE のままなら書式は TO_CHAR で明示する |
| TO_CHAR の曜日・月名 | Oracle 側の NLS_DATE_LANGUAGE と JVM ロケールを揃える (ランタイムは英語に固定) |
| Oracle DATE の時刻部 | ScalarDB DATE は時刻を持たない。時刻を使う列は TIMESTAMP にする (変換ツールが警告) |

### 4.3 書き込み系

書き込み系 17 文のうち変換できたのは定数ソースの MERGE と TRUNCATE の 2 文で、残りは計画のフェーズ 3 (読み書き更新テンプレート、採番、表ソース MERGE の行ごと UPSERT など) の対象である。ビュー、トリガー、PL/SQL ブロック、GRANT、COMMENT は SQL の変換対象外で、アプリケーションロジックまたは ScalarDB の DCL への移植になる。

### 4.4 注意点

- 計画経路の fetch 行数は多くの文で emp 全件 (9 行) になっている。実データではパーティションキーを含む述語が無い限りクロスパーティション走査になるため、行数上限とキー設計の見直しが前提になる (計画書 3.5 のガードレール)。
- 本レポートの Oracle 側は 23ai Free である。XE 21c との SQL 互換性は高いが、23ai で追加された構文 (`FROM` 省略など) は検証に含めていない。
- ScalarDB Cluster はトライアルライセンス (2026-10-31 まで、評価目的限定) で起動した。

## 5. 再現手順

```
cd difftest && docker compose --profile oracle --profile cluster up -d && cd ..
.venv/bin/python difftest/run.py difftest/cases/oracle-features.sql --dialect oracle --fetcher jdbc --json-out out/oracle-features.jdbc.json
.venv/bin/python difftest/classify.py difftest/cases/oracle-features-write.sql --dialect oracle --json-out out/oracle-features.write.json
.venv/bin/python difftest/report.py out/oracle-features.jdbc.json out/oracle-features.write.json
```
