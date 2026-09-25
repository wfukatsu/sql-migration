# SQL 変換レポート: oracle → scalardb

- 入力: `samples/oracle-samples/sql/02_sql_query.sql`
- 文数: 39　|　OK: 0　|　WARN: 5　|　PLANNED: 23　|　ERROR: 11
- **変換率: 12.8%**（5 / 39 文が scalardb の SQL を出力できた）

| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |
|---|---|---|---|---|---|
| 1 | SELECT | 🧩 PLANNED | `-------------------------------------------------------------------------------- -- 02_sql…` | — | **INFO** IDENT: identifier USER is a ScalarDB SQL keyword; written as "USER" (quoted)<br>**ERROR** PROJECTION: main query: expressions in the select list (SYSDATE; SYSTIMESTAMP) -- compute them in the application<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off) |
| 2 | SELECT | ⚠️ WARN | `-- A-2. 絞り込み・並べ替え（NULL の並び順指定） SELECT employee_id, last_name, salary, commission_pct FROM …` | `/* A-2. 絞り込み・並べ替え（NULL の並び順指定） */ SELECT employee_id, last_name, salary, commission_pct FR…` | **WARN** NULLS: NULLS FIRST/LAST dropped from ORDER BY<br>**WARN** NULLS: NULLS FIRST/LAST dropped from ORDER BY<br>**WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['employee_id'] of employees -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 3 | SELECT | 🧩 PLANNED | `-- A-3. CASE / DECODE [ORA] / NULL 処理関数 SELECT last_name,        salary,        CASE      …` | — | **ERROR** PROJECTION: main query: expressions in the select list (CASE WHEN salary >= 15000 THEN 'HIGH' WHEN salary >= 7000 THEN 'MID' ELSE 'LO...; DECODE(department_id, 60, 'IT', 80, 'Sales', 'Other'); NVL(commission_pct, 0); NVL2(commission_pct, 'Y', 'N'); COALESCE(commission_pct, 0)) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary, commission_pct, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 4 | SELECT | 🧩 PLANNED | `-- A-4. 文字列・数値・日付関数 SELECT UPPER(last_name)                         AS upper_name,        …` | — | **ERROR** PROJECTION: main query: expressions in the select list (UPPER(last_name); INITCAP(email); SUBSTR(last_name, 1, 3); INSTR(last_name, 'e'); LPAD(employee_id, 6, '0'); first_name \|\| ' ' \|\| last_name; ROUND(salary / 12, 2); TRUNC(hire_date, 'MM'); ADD_MONTHS(hire_date, 6); TRUNC(MONTHS_BETWEEN(SYSDATE, hire_date) / 12); TO_CHAR(hire_date, 'YYYY"年"MM"月"DD"日"'); TO_CHAR(salary, 'FM999,999')) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, first_name, last_name, email, hire_date, salary FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 5 | SELECT | 🧩 PLANNED | `-- [ORA]  -- A-5. 正規表現 SELECT email,        REGEXP_SUBSTR(email, '^[A-Z]')           AS fi…` | — | **ERROR** PROJECTION: main query: expressions in the select list (REGEXP_SUBSTR(email, '^[A-Z]'); REGEXP_REPLACE(last_name, '[aeiou]', '*')) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, email FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 6 | SELECT | ⚠️ WARN | `--============================================================================== -- B. 結合 …` | `/* ============================================================================== */ /* B.…` | **WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['employee_id'] of employees -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 7 | SELECT | ⚠️ WARN | `-- B-2. 外部結合（ANSI）と Oracle 独自の (+) 記法 [ORA] SELECT e.last_name, d.department_name FROM   e…` | `/* B-2. 外部結合（ANSI）と Oracle 独自の (+) 記法 [ORA] */ SELECT e.last_name, d.department_name FROM …` | **WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['employee_id'] of employees -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 8 | SELECT | ⚠️ WARN | `SELECT e.last_name, d.department_name          -- 上と同じ結果（旧記法） FROM   employees e, departme…` | `SELECT e.last_name, d.department_name /* 上と同じ結果（旧記法） */ FROM employees AS e LEFT JOIN depa…` | **WARN** ORACLE_JOIN_MARK: Oracle (+) outer join rewritten as LEFT/RIGHT OUTER JOIN<br>**WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['employee_id'] of employees -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 9 | SELECT | 🧩 PLANNED | `-- B-3. 完全外部結合：部門なし社員・社員なし部門の両方を出す SELECT e.last_name, d.department_name FROM   employees …` | — | **ERROR** JOIN: OUTER JOIN is not supported<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, department_id FROM hr.employees<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT department_id, department_name FROM hr.departments<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P8, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 10 | SELECT | ⚠️ WARN | `-- B-4. 自己結合（上司名の取得） SELECT w.last_name AS employee, m.last_name AS manager FROM   employe…` | `/* B-4. 自己結合（上司名の取得） */ SELECT w.last_name AS employee, m.last_name AS manager FROM employ…` | **WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['employee_id'] of employees -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 11 | SELECT | 🧩 PLANNED | `-- B-5. LATERAL / CROSS APPLY（12c+）：部門ごとの高給上位2名 SELECT d.department_name, t.last_name, t.s…` | — | **ERROR** JOIN: joined relation must be a base table (no subqueries)<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT department_id, department_name FROM hr.departments<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P8, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 12 | SELECT | 🧩 PLANNED | `--============================================================================== -- C. 副問合…` | — | **ERROR** PROJECTION: subquery: expressions in the select list (ROUND(AVG(salary))) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 13 | SELECT | 🧩 PLANNED | `-- C-2. 相関副問合せ：部門平均より高い社員 SELECT e.last_name, e.department_id, e.salary FROM   employees e…` | — | **ERROR** SUBQUERY: main query: subquery in WHERE -- fetch the inner result first and bind its values<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P5, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 14 | SELECT | 🧩 PLANNED | `-- C-3. EXISTS / NOT EXISTS SELECT d.department_name FROM   departments d WHERE  NOT EXIST…` | — | **ERROR** NOT: cannot negate 'EXISTS(SELECT 1 FROM employees e WHERE e.department_id = d.department_id)'<br>**ERROR** SUBQUERY: main query: subquery in WHERE -- fetch the inner result first and bind its values<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT department_id, department_name FROM hr.departments<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P5, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 15 | SELECT | 🧩 PLANNED | `-- C-4. 多列 IN SELECT last_name, department_id, salary FROM   employees WHERE  (department_…` | — | **ERROR** SUBQUERY: main query: subquery in WHERE -- fetch the inner result first and bind its values<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P5, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 16 | EXCEPT | 🧩 PLANNED | `-- C-5. 集合演算（MINUS は Oracle 固有 [ORA]、標準は EXCEPT。21c+ は EXCEPT も可） SELECT department_id FRO…` | — | **ERROR** SET_OP: main query: EXCEPT -- run each branch and combine the rows in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT department_id FROM hr.departments<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P6, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 17 | INTERSECT | 🧩 PLANNED | `SELECT job_id FROM employees WHERE department_id = 80 INTERSECT SELECT job_id FROM jobs WH…` | — | **ERROR** SET_OP: main query: INTERSECT -- run each branch and combine the rows in the application<br>**INFO** PLAN_FETCH: INDEX_SCAN: SELECT employee_id, job_id, department_id FROM hr.employees WHERE department_id = 80<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT job_id, min_salary FROM hr.jobs WHERE min_salary >= 6000<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P6, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 18 | SELECT | 🧩 PLANNED | `--============================================================================== -- D. 集計 …` | — | **ERROR** PROJECTION: main query: expressions in the select list (ROUND(AVG(salary))) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, salary, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_UNRESOLVED: employees: columns ['total'] not in schema<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 19 | SELECT | ❌ ERROR | `-- D-2. ROLLUP / CUBE / GROUPING SETS と GROUPING 関数 SELECT CASE GROUPING(department_id) WH…` | — | **ERROR** PROJECTION: main query: expressions in the select list (CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(department_id) END; CASE GROUPING(job_id) WHEN 1 THEN '小計' ELSE job_id END) -- compute them in the application<br>**ERROR** GROUP: main query: GROUP BY ROLLUP -- aggregate each level in the application<br>**ERROR** RESIDUAL_H2: the H2 residual engine cannot run ROLLUP / CUBE / GROUPING SETS (UNION ALL one aggregate per level); implement this part in the application |
| 20 | SELECT | ❌ ERROR | `SELECT department_id, job_id, SUM(salary) FROM   employees GROUP  BY GROUPING SETS ((depar…` | — | **ERROR** GROUP: main query: GROUP BY GROUPING SETS -- aggregate each level in the application<br>**ERROR** RESIDUAL_H2: the H2 residual engine cannot run ROLLUP / CUBE / GROUPING SETS (UNION ALL one aggregate per level); implement this part in the application |
| 21 | SELECT | 🧩 PLANNED | `-- D-3. LISTAGG（文字列集約） SELECT department_id,        LISTAGG(last_name, ', ') WITHIN GROUP …` | — | **ERROR** AGG: aggregate GROUP_CONCAT is not supported (only COUNT, SUM, AVG, MIN, MAX)<br>**ERROR** PROJECTION: main query: expressions in the select list (LISTAGG(last_name, ', ') WITHIN GROUP (ORDER BY last_name)) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1+P7, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 22 | SELECT | ❌ ERROR | `-- D-4. PIVOT / UNPIVOT（11g+） SELECT * FROM  (SELECT department_id, job_id, salary FROM em…` | — | **ERROR** FROM: FROM must reference exactly one base table (no subqueries)<br>**ERROR** SUBQUERY: main query: derived table in FROM -- evaluate it in the application<br>**ERROR** RESIDUAL_H2: the H2 residual engine cannot run PIVOT (conditional aggregation); implement this part in the application |
| 23 | SELECT | ❌ ERROR | `SELECT employee_id, pay_type, amount FROM  (SELECT employee_id, salary, NVL(salary * commi…` | — | **ERROR** FROM: FROM must reference exactly one base table (no subqueries)<br>**ERROR** SUBQUERY: main query: derived table in FROM -- evaluate it in the application<br>**ERROR** PROJECTION: subquery: expressions in the select list (NVL(salary * commission_pct, 0)) -- compute them in the application<br>**ERROR** RESIDUAL_H2: the H2 residual engine cannot run UNPIVOT (UNION ALL); implement this part in the application |
| 24 | SELECT | 🧩 PLANNED | `--============================================================================== -- E. 分析関…` | — | **ERROR** WINDOW: main query: window functions ROW_NUMBER, RANK, DENSE_RANK, SUM OVER, LAG, LEAD, NTILE, RATIO_TO_REPORT -- partition, sort and compute in the application (appside.Windows)<br>**ERROR** PROJECTION: main query: expressions in the select list (ROUND(RATIO_TO_REPORT(salary) OVER (PARTITION BY department_id), 3)) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, hire_date, salary, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_UNRESOLVED: employees: columns ['rn'] not in schema<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 25 | SELECT | 🧩 PLANNED | `-- E-2. 累計・移動平均（ウィンドウ句） SELECT order_date, total,        SUM(total) OVER (ORDER BY order_d…` | — | **ERROR** WINDOW: main query: window functions SUM OVER, AVG OVER -- partition, sort and compute in the application (appside.Windows)<br>**ERROR** PROJECTION: main query: expressions in the select list (ROUND(AVG(total) OVER (ORDER BY order_date ROWS BETWEEN 1 PRECEDING AND 1 FOL...) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT order_id, order_date, total FROM hr.orders<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 26 | SELECT | ❌ ERROR | `-- E-3. KEEP (DENSE_RANK FIRST/LAST) [ORA]：部門内最高給の社員名 SELECT department_id,        MAX(las…` | — | **ERROR** PROJECTION: projection 'MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY salary DESC)' is an expression; ScalarDB SQL only selects columns and aggregates. Compute it in the application<br>**ERROR** KEEP: main query: MAX(last_name) KEEP (DENSE_RANK FIRST ...) -- pick the first/last row per group in the application<br>**ERROR** RESIDUAL_H2: the H2 residual engine cannot run KEEP (DENSE_RANK FIRST/LAST) (ROW_NUMBER() ... = 1); implement this part in the application |
| 27 | SELECT | 🧩 PLANNED | `--============================================================================== -- F. Top…` | — | **ERROR** LIMIT: FETCH ... WITH TIES is not supported: LIMIT n drops the rows that tie with the n-th; fetch in order and keep reading while the sort key is equal<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 28 | SELECT | 🧩 PLANNED | `SELECT last_name, salary FROM employees ORDER  BY salary DESC OFFSET 5 ROWS FETCH NEXT 5 R…` | — | **ERROR** OFFSET: main query: OFFSET -- page with a clustering-key range instead<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P4, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 29 | SELECT | 🧩 PLANNED | `-- 2ページ目（5件/ページ）  -- F-2. 11g 以前の ROWNUM 方式 [ORA]（移行元コードで頻出） SELECT * FROM  (SELECT a.*, R…` | — | **ERROR** FROM: FROM must reference exactly one base table (no subqueries)<br>**ERROR** SUBQUERY: main query: derived table in FROM -- evaluate it in the application<br>**ERROR** SUBQUERY: subquery: derived table in FROM -- evaluate it in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P5, H2 indexes off)<br>**WARN** PLAN_UNRESOLVED: python: ROWNUM in a form SQLite cannot express<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 30 | SELECT | 🧩 PLANNED | `--============================================================================== -- G. WIT…` | — | **ERROR** CTE: WITH dept_stats: evaluate each common table expression in the application (fetch its base tables through ScalarDB SQL)<br>**ERROR** PROJECTION: main query: expressions in the select list (ROUND(s.avg_sal)) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1+P6, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 31 | SELECT | ❌ ERROR | `-- G-2. CONNECT BY による階層問合せ [ORA] SELECT LEVEL,        LPAD(' ', 2 * (LEVEL - 1)) \|\| las…` | — | **ERROR** HIERARCHICAL: main query: START WITH / CONNECT BY with CONNECT_BY_ISLEAF, CONNECT_BY_ROOT, LEVEL, SYS_CONNECT_BY_PATH -- walk the tree in the application (appside.Hierarchy) or precompute it into a table<br>**ERROR** PROJECTION: main query: expressions in the select list (LPAD(' ', 2 * (LEVEL - 1)) \|\| last_name) -- compute them in the application<br>**ERROR** RESIDUAL_H2: the H2 residual engine cannot run CONNECT BY (rewrite as recursive WITH, or walk the tree in the application); implement this part in the application |
| 32 | SELECT | 🧩 PLANNED | `-- G-3. 再帰 WITH（標準SQL、他DBへ移行しやすい書き方） WITH org (employee_id, last_name, manager_id, lvl, pa…` | — | **ERROR** CTE: WITH org: evaluate each common table expression in the application (fetch its base tables through ScalarDB SQL)<br>**ERROR** SET_OP: CTE org: UNION -- run each branch and combine the rows in the application<br>**ERROR** PROJECTION: main query: expressions in the select list (LPAD(' ', 2 * (lvl - 1)) \|\| last_name) -- compute them in the application<br>**ERROR** PROJECTION: CTE org: expressions in the select list (CAST(last_name AS VARCHAR2(4000))) -- compute them in the application<br>**ERROR** PROJECTION: CTE org: expressions in the select list (o.lvl + 1; o.path \|\| '/' \|\| e.last_name) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, manager_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1+P6, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 33 | SELECT | ❌ ERROR | `-- G-4. CONNECT BY LEVEL による連番生成 [ORA]（カレンダー表などで頻出） SELECT DATE '2026-09-01' + LEVEL - 1 A…` | — | **ERROR** HIERARCHICAL: main query: START WITH / CONNECT BY with LEVEL -- walk the tree in the application (appside.Hierarchy) or precompute it into a table<br>**ERROR** PROJECTION: main query: expressions in the select list (TO_DATE('2026-09-01', 'YYYY-MM-DD') + LEVEL - 1) -- compute them in the application<br>**ERROR** RESIDUAL_H2: the H2 residual engine cannot run CONNECT BY (rewrite as recursive WITH, or walk the tree in the application); implement this part in the application |
| 34 | PARSE_ERROR | ❌ ERROR | `--============================================================================== -- H. その他…` | — | **ERROR** PARSE: Invalid expression / Unexpected token. Line 6, Col: 32. |
| 35 | SELECT | 🧩 PLANNED | `-- H-2. サンプリング [ORA] SELECT COUNT(*) FROM employees SAMPLE (50)` | — | **ERROR** CLAUSE: TABLESAMPLE on employees is not supported; it returns a random subset of the rows<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 36 | SELECT | ❌ ERROR | `-- H-3. ROWID [ORA]（重複行削除などで頻出） SELECT ROWID, employee_id FROM employees WHERE ROWNUM <= 3` | — | **ERROR** ROWID: pseudo-column ROWID does not exist in ScalarDB; use the primary key<br>**INFO** PLAN: not decomposable: pseudo-column ROWID cannot be fetched from ScalarDB |
| 37 | PARSE_ERROR | ❌ ERROR | `-- H-4. WITH 句内での PL/SQL 関数定義（12c+）[ORA] WITH   FUNCTION annual(p_sal NUMBER, p_comm NUMBE…` | — | **ERROR** PARSE: Expecting (. Line 3, Col: 17. |
| 38 | COLUMN | ❌ ERROR | `END` | — | **ERROR** STATEMENT: Column statements are not supported by ScalarDB SQL |
| 39 | SELECT | 🧩 PLANNED | `SELECT last_name, annual(salary, commission_pct) AS annual_comp FROM   employees WHERE  de…` | — | **ERROR** PROJECTION: main query: expressions in the select list (ANNUAL(salary, commission_pct)) -- compute them in the application<br>**INFO** PLAN_FETCH: INDEX_SCAN: SELECT employee_id, last_name, salary, commission_pct, department_id FROM hr.employees WHERE department_id = 80<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off) |

## 指摘の集計

| 重要度 | コード | 件数 |
|---|---|---|
| ERROR | PROJECTION | 19 |
| ERROR | SUBQUERY | 7 |
| ERROR | RESIDUAL_H2 | 7 |
| ERROR | SET_OP | 3 |
| ERROR | FROM | 3 |
| ERROR | JOIN | 2 |
| ERROR | GROUP | 2 |
| ERROR | WINDOW | 2 |
| ERROR | CTE | 2 |
| ERROR | HIERARCHICAL | 2 |
| ERROR | PARSE | 2 |
| ERROR | NOT | 1 |
| ERROR | AGG | 1 |
| ERROR | KEEP | 1 |
| ERROR | LIMIT | 1 |
| ERROR | OFFSET | 1 |
| ERROR | CLAUSE | 1 |
| ERROR | ROWID | 1 |
| ERROR | STATEMENT | 1 |
| WARN | PLAN_CROSS_PARTITION | 21 |
| WARN | APP_SEMANTICS | 11 |
| WARN | CROSS_PARTITION | 5 |
| WARN | PLAN_UNRESOLVED | 3 |
| WARN | NULLS | 2 |
| WARN | ORACLE_JOIN_MARK | 1 |
| INFO | CONFIG | 28 |
| INFO | PLAN_FETCH | 27 |
| INFO | COST | 26 |
| INFO | PLAN_RESIDUAL | 23 |
| INFO | DESIGN | 8 |
| INFO | IDENT | 1 |
| INFO | PLAN | 1 |

## アプリ側に移す処理

### #1 🧩 PLANNED `----------------------------------------------------------------------…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (SYSDATE; SYSTIMESTAMP) -- compute them in the application

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+)

### #2 ⚠️ WARN `-- A-2. 絞り込み・並べ替え（NULL の並び順指定） SELECT employee_id, last_name, salary, …`

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #3 🧩 PLANNED `-- A-3. CASE / DECODE [ORA] / NULL 処理関数 SELECT last_name,        salar…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (CASE WHEN salary >= 15000 THEN 'HIGH' WHEN salary >= 7000 THEN 'MID' ELSE 'LO...; DECODE(department_id, 60, 'IT', 80, 'Sales', 'Other'); NVL(commission_pct, 0); NVL2(commission_pct, 'Y', 'N'); COALESCE(commission_pct, 0)) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #4 🧩 PLANNED `-- A-4. 文字列・数値・日付関数 SELECT UPPER(last_name)                         AS…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (UPPER(last_name); INITCAP(email); SUBSTR(last_name, 1, 3); INSTR(last_name, 'e'); LPAD(employee_id, 6, '0'); first_name || ' ' || last_name; ROUND(salary / 12, 2); TRUNC(hire_date, 'MM'); ADD_MONTHS(hire_date, 6); TRUNC(MONTHS_BETWEEN(SYSDATE, hire_date) / 12); TO_CHAR(hire_date, 'YYYY"年"MM"月"DD"日"'); TO_CHAR(salary, 'FM999,999')) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #5 🧩 PLANNED `-- [ORA]  -- A-5. 正規表現 SELECT email,        REGEXP_SUBSTR(email, '^[A-…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (REGEXP_SUBSTR(email, '^[A-Z]'); REGEXP_REPLACE(last_name, '[aeiou]', '*')) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #6 ⚠️ WARN `--====================================================================…`

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #7 ⚠️ WARN `-- B-2. 外部結合（ANSI）と Oracle 独自の (+) 記法 [ORA] SELECT e.last_name, d.depa…`

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #8 ⚠️ WARN `SELECT e.last_name, d.department_name          -- 上と同じ結果（旧記法） FROM   e…`

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #9 🧩 PLANNED `-- B-3. 完全外部結合：部門なし社員・社員なし部門の両方を出す SELECT e.last_name, d.department_na…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `JOIN` OUTER JOIN is not supported

**取得コストの見積もり**

- `COST` full scan of employees, departments (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #10 ⚠️ WARN `-- B-4. 自己結合（上司名の取得） SELECT w.last_name AS employee, m.last_name AS ma…`

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #11 🧩 PLANNED `-- B-5. LATERAL / CROSS APPLY（12c+）：部門ごとの高給上位2名 SELECT d.department_na…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `JOIN` joined relation must be a base table (no subqueries)

**取得コストの見積もり**

- `COST` full scan of departments, employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #12 🧩 PLANNED `--====================================================================…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` subquery: expressions in the select list (ROUND(AVG(salary))) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #13 🧩 PLANNED `-- C-2. 相関副問合せ：部門平均より高い社員 SELECT e.last_name, e.department_id, e.salar…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `SUBQUERY` main query: subquery in WHERE -- fetch the inner result first and bind its values

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #14 🧩 PLANNED `-- C-3. EXISTS / NOT EXISTS SELECT d.department_name FROM   department…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `NOT` cannot negate 'EXISTS(SELECT 1 FROM employees e WHERE e.department_id = d.department_id)'
- `SUBQUERY` main query: subquery in WHERE -- fetch the inner result first and bind its values

**取得コストの見積もり**

- `COST` full scan of departments, employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #15 🧩 PLANNED `-- C-4. 多列 IN SELECT last_name, department_id, salary FROM   employees…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `SUBQUERY` main query: subquery in WHERE -- fetch the inner result first and bind its values

**設計の提案**

- `DESIGN` employees: keep a summary table keyed by (department_id) -- partition key department_id -- updated with each write or by a batch, so the query reads one row per group instead of every employees row; store the count of non-NULL values too so SUM's NULL result can be reproduced

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #16 🧩 PLANNED `-- C-5. 集合演算（MINUS は Oracle 固有 [ORA]、標準は EXCEPT。21c+ は EXCEPT も可） SELE…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `SET_OP` main query: EXCEPT -- run each branch and combine the rows in the application

**取得コストの見積もり**

- `COST` full scan of departments, employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #17 🧩 PLANNED `SELECT job_id FROM employees WHERE department_id = 80 INTERSECT SELECT…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `SET_OP` main query: INTERSECT -- run each branch and combine the rows in the application

**取得コストの見積もり**

- `COST` full scan of jobs (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #18 🧩 PLANNED `--====================================================================…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (ROUND(AVG(salary))) -- compute them in the application

**設計の提案**

- `DESIGN` employees: keep a summary table keyed by (department_id) -- partition key department_id -- updated with each write or by a batch, so the query reads one row per group instead of every employees row; store the count of non-NULL values too so SUM's NULL result can be reproduced

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #19 ❌ ERROR `-- D-2. ROLLUP / CUBE / GROUPING SETS と GROUPING 関数 SELECT CASE GROUPI…`

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(department_id) END; CASE GROUPING(job_id) WHEN 1 THEN '小計' ELSE job_id END) -- compute them in the application
- `GROUP` main query: GROUP BY ROLLUP -- aggregate each level in the application
- `RESIDUAL_H2` the H2 residual engine cannot run ROLLUP / CUBE / GROUPING SETS (UNION ALL one aggregate per level); implement this part in the application

**結果を変えないための注意（意味の差）**

- `APP_SEMANTICS` SUM / AVG / MIN / MAX ignore NULLs and return NULL when every input is NULL; COUNT(col) counts non-NULL values only
- `APP_SEMANTICS` date formatting (TO_CHAR / DATE_FORMAT) uses the session time zone and date language; format in the same zone and locale in the application

### #20 ❌ ERROR `SELECT department_id, job_id, SUM(salary) FROM   employees GROUP  BY G…`

**アプリ側で処理する構文**

- `GROUP` main query: GROUP BY GROUPING SETS -- aggregate each level in the application
- `RESIDUAL_H2` the H2 residual engine cannot run ROLLUP / CUBE / GROUPING SETS (UNION ALL one aggregate per level); implement this part in the application

**結果を変えないための注意（意味の差）**

- `APP_SEMANTICS` SUM / AVG / MIN / MAX ignore NULLs and return NULL when every input is NULL; COUNT(col) counts non-NULL values only

### #21 🧩 PLANNED `-- D-3. LISTAGG（文字列集約） SELECT department_id,        LISTAGG(last_name,…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `AGG` aggregate GROUP_CONCAT is not supported (only COUNT, SUM, AVG, MIN, MAX)
- `PROJECTION` main query: expressions in the select list (LISTAGG(last_name, ', ') WITHIN GROUP (ORDER BY last_name)) -- compute them in the application

**設計の提案**

- `DESIGN` employees: keep a summary table keyed by (department_id) -- partition key department_id -- updated with each write or by a batch, so the query reads one row per group instead of every employees row; store the count of non-NULL values too so SUM's NULL result can be reproduced

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #22 ❌ ERROR `-- D-4. PIVOT / UNPIVOT（11g+） SELECT * FROM  (SELECT department_id, jo…`

**アプリ側で処理する構文**

- `FROM` FROM must reference exactly one base table (no subqueries)
- `SUBQUERY` main query: derived table in FROM -- evaluate it in the application
- `RESIDUAL_H2` the H2 residual engine cannot run PIVOT (conditional aggregation); implement this part in the application

**結果を変えないための注意（意味の差）**

- `APP_SEMANTICS` SUM / AVG / MIN / MAX ignore NULLs and return NULL when every input is NULL; COUNT(col) counts non-NULL values only

### #23 ❌ ERROR `SELECT employee_id, pay_type, amount FROM  (SELECT employee_id, salary…`

**アプリ側で処理する構文**

- `FROM` FROM must reference exactly one base table (no subqueries)
- `SUBQUERY` main query: derived table in FROM -- evaluate it in the application
- `PROJECTION` subquery: expressions in the select list (NVL(salary * commission_pct, 0)) -- compute them in the application
- `RESIDUAL_H2` the H2 residual engine cannot run UNPIVOT (UNION ALL); implement this part in the application

### #24 🧩 PLANNED `--====================================================================…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `WINDOW` main query: window functions ROW_NUMBER, RANK, DENSE_RANK, SUM OVER, LAG, LEAD, NTILE, RATIO_TO_REPORT -- partition, sort and compute in the application (appside.Windows)
- `PROJECTION` main query: expressions in the select list (ROUND(RATIO_TO_REPORT(salary) OVER (PARTITION BY department_id), 3)) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #25 🧩 PLANNED `-- E-2. 累計・移動平均（ウィンドウ句） SELECT order_date, total,        SUM(total) OV…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `WINDOW` main query: window functions SUM OVER, AVG OVER -- partition, sort and compute in the application (appside.Windows)
- `PROJECTION` main query: expressions in the select list (ROUND(AVG(total) OVER (ORDER BY order_date ROWS BETWEEN 1 PRECEDING AND 1 FOL...) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of orders (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #26 ❌ ERROR `-- E-3. KEEP (DENSE_RANK FIRST/LAST) [ORA]：部門内最高給の社員名 SELECT departmen…`

**アプリ側で処理する構文**

- `PROJECTION` projection 'MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY salary DESC)' is an expression; ScalarDB SQL only selects columns and aggregates. Compute it in the application
- `KEEP` main query: MAX(last_name) KEEP (DENSE_RANK FIRST ...) -- pick the first/last row per group in the application
- `RESIDUAL_H2` the H2 residual engine cannot run KEEP (DENSE_RANK FIRST/LAST) (ROW_NUMBER() ... = 1); implement this part in the application

**結果を変えないための注意（意味の差）**

- `APP_SEMANTICS` SUM / AVG / MIN / MAX ignore NULLs and return NULL when every input is NULL; COUNT(col) counts non-NULL values only
- `APP_SEMANTICS` NULLs sort last for ASC and first for DESC (also inside OVER (ORDER BY ...)); Java comparators need an explicit nullsFirst / nullsLast (appside.OracleOrdering)

**設計の提案**

- `DESIGN` employees: keep a summary table keyed by (department_id) -- partition key department_id -- updated with each write or by a batch, so the query reads one row per group instead of every employees row; store the count of non-NULL values too so SUM's NULL result can be reproduced
- `DESIGN` analytical query (aggregation + window functions): if it runs as a report rather than per request, consider ScalarDB Analytics instead of fetching every row through transactions

### #27 🧩 PLANNED `--====================================================================…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `LIMIT` FETCH ... WITH TIES is not supported: LIMIT n drops the rows that tie with the n-th; fetch in order and keep reading while the sort key is equal

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #28 🧩 PLANNED `SELECT last_name, salary FROM employees ORDER  BY salary DESC OFFSET 5…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `OFFSET` main query: OFFSET -- page with a clustering-key range instead

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #29 🧩 PLANNED `-- 2ページ目（5件/ページ）  -- F-2. 11g 以前の ROWNUM 方式 [ORA]（移行元コードで頻出） SELECT * …`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `FROM` FROM must reference exactly one base table (no subqueries)
- `SUBQUERY` main query: derived table in FROM -- evaluate it in the application
- `SUBQUERY` subquery: derived table in FROM -- evaluate it in the application

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #30 🧩 PLANNED `--====================================================================…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `CTE` WITH dept_stats: evaluate each common table expression in the application (fetch its base tables through ScalarDB SQL)
- `PROJECTION` main query: expressions in the select list (ROUND(s.avg_sal)) -- compute them in the application

**設計の提案**

- `DESIGN` employees: keep a summary table keyed by (department_id) -- partition key department_id -- updated with each write or by a batch, so the query reads one row per group instead of every employees row; store the count of non-NULL values too so SUM's NULL result can be reproduced

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #31 ❌ ERROR `-- G-2. CONNECT BY による階層問合せ [ORA] SELECT LEVEL,        LPAD(' ', 2 * (…`

**アプリ側で処理する構文**

- `HIERARCHICAL` main query: START WITH / CONNECT BY with CONNECT_BY_ISLEAF, CONNECT_BY_ROOT, LEVEL, SYS_CONNECT_BY_PATH -- walk the tree in the application (appside.Hierarchy) or precompute it into a table
- `PROJECTION` main query: expressions in the select list (LPAD(' ', 2 * (LEVEL - 1)) || last_name) -- compute them in the application
- `RESIDUAL_H2` the H2 residual engine cannot run CONNECT BY (rewrite as recursive WITH, or walk the tree in the application); implement this part in the application

**結果を変えないための注意（意味の差）**

- `APP_SEMANTICS` NULLs sort last for ASC and first for DESC (also inside OVER (ORDER BY ...)); Java comparators need an explicit nullsFirst / nullsLast (appside.OracleOrdering)
- `APP_SEMANTICS` string ORDER BY follows NLS_SORT: BINARY is code-point order (AL32UTF8 byte order), which String.compareTo breaks for surrogate pairs (appside.OracleOrdering.BINARY); linguistic sorts such as JAPANESE_M need a Collator
- `APP_SEMANTICS` SYS_CONNECT_BY_PATH puts the separator before every value (the path starts with it) and fails with ORA-30004 when a value contains the separator
- `APP_SEMANTICS` CONNECT BY: LEVEL starts at 1 for the START WITH rows; a cycle raises ORA-01436 unless NOCYCLE

**設計の提案**

- `DESIGN` employees: precompute the hierarchy (node id -> parent, level, path) into a table keyed by node id and rebuild it when the tree changes, or cache the tree in the application; the tree is then read by key instead of scanning employees

### #32 🧩 PLANNED `-- G-3. 再帰 WITH（標準SQL、他DBへ移行しやすい書き方） WITH org (employee_id, last_name,…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `CTE` WITH org: evaluate each common table expression in the application (fetch its base tables through ScalarDB SQL)
- `SET_OP` CTE org: UNION -- run each branch and combine the rows in the application
- `PROJECTION` main query: expressions in the select list (LPAD(' ', 2 * (lvl - 1)) || last_name) -- compute them in the application
- `PROJECTION` CTE org: expressions in the select list (CAST(last_name AS VARCHAR2(4000))) -- compute them in the application
- `PROJECTION` CTE org: expressions in the select list (o.lvl + 1; o.path || '/' || e.last_name) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #33 ❌ ERROR `-- G-4. CONNECT BY LEVEL による連番生成 [ORA]（カレンダー表などで頻出） SELECT DATE '2026-…`

**アプリ側で処理する構文**

- `HIERARCHICAL` main query: START WITH / CONNECT BY with LEVEL -- walk the tree in the application (appside.Hierarchy) or precompute it into a table
- `PROJECTION` main query: expressions in the select list (TO_DATE('2026-09-01', 'YYYY-MM-DD') + LEVEL - 1) -- compute them in the application
- `RESIDUAL_H2` the H2 residual engine cannot run CONNECT BY (rewrite as recursive WITH, or walk the tree in the application); implement this part in the application

**結果を変えないための注意（意味の差）**

- `APP_SEMANTICS` CONNECT BY: LEVEL starts at 1 for the START WITH rows; a cycle raises ORA-01436 unless NOCYCLE

**設計の提案**

- `DESIGN` dual: precompute the hierarchy (node id -> parent, level, path) into a table keyed by node id and rebuild it when the tree changes, or cache the tree in the application; the tree is then read by key instead of scanning dual

### #34 ❌ ERROR `--====================================================================…`

**アプリ側で処理する構文**

- `PARSE` Invalid expression / Unexpected token. Line 6, Col: 32.

### #35 🧩 PLANNED `-- H-2. サンプリング [ORA] SELECT COUNT(*) FROM employees SAMPLE (50)`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `CLAUSE` TABLESAMPLE on employees is not supported; it returns a random subset of the rows

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #36 ❌ ERROR `-- H-3. ROWID [ORA]（重複行削除などで頻出） SELECT ROWID, employee_id FROM employe…`

**アプリ側で処理する構文**

- `ROWID` pseudo-column ROWID does not exist in ScalarDB; use the primary key

### #37 ❌ ERROR `-- H-4. WITH 句内での PL/SQL 関数定義（12c+）[ORA] WITH   FUNCTION annual(p_sal …`

**アプリ側で処理する構文**

- `PARSE` Expecting (. Line 3, Col: 17.

### #38 ❌ ERROR `END`

**アプリ側で処理する構文**

- `STATEMENT` Column statements are not supported by ScalarDB SQL

### #39 🧩 PLANNED `SELECT last_name, annual(salary, commission_pct) AS annual_comp FROM  …`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (ANNUAL(salary, commission_pct)) -- compute them in the application

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

