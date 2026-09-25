-- converted to scalardb
-- [APP-SIDE PLAN #1] ScalarDB から取得して H2 で実行する

-- --------------------------------------------------------------------------------
-- -- 02_sql_query.sql : 問合せ（SELECT）のサンプル
-- --   参照: SQL Language Reference - SELECT / Functions / Analytic Functions /
-- --         Hierarchical Queries / Joins
-- --   前提: 00_setup.sql 実行済み
-- --   [ORA] = Oracle 固有 / 他DBへの移行で要注意の構文
-- --------------------------------------------------------------------------------
-- 
-- --==============================================================================
-- -- A. 基本
-- --==============================================================================
-- -- A-1. DUAL 表 [ORA]（23ai 以降は FROM 句省略も可）
-- SELECT SYSDATE, SYSTIMESTAMP, USER FROM dual

/* A-2. 絞り込み・並べ替え（NULL の並び順指定） */ SELECT employee_id, last_name, salary, commission_pct FROM employees WHERE salary BETWEEN 5000 AND 15000 AND last_name LIKE '%er%' ORDER BY commission_pct DESC, salary DESC;

-- [APP-SIDE PLAN #3] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary, commission_pct, department_id FROM hr.employees;
-- -- A-3. CASE / DECODE [ORA] / NULL 処理関数
-- SELECT last_name,
--        salary,
--        CASE
--          WHEN salary >= 15000 THEN 'HIGH'
--          WHEN salary >=  7000 THEN 'MID'
--          ELSE 'LOW'
--        END                                      AS salary_band,
--        DECODE(department_id, 60, 'IT', 80, 'Sales', 'Other') AS dept_label,   -- [ORA]
--        NVL(commission_pct, 0)                   AS comm_nvl,                  -- [ORA]
--        NVL2(commission_pct, 'Y', 'N')           AS has_comm,                  -- [ORA]
--        COALESCE(commission_pct, 0)              AS comm_coalesce               -- 標準SQL
-- FROM   employees

-- [APP-SIDE PLAN #4] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, first_name, last_name, email, hire_date, salary FROM hr.employees;
-- -- A-4. 文字列・数値・日付関数
-- SELECT UPPER(last_name)                         AS upper_name,
--        INITCAP(email)                           AS initcap_email,
--        SUBSTR(last_name, 1, 3)                  AS short_name,
--        INSTR(last_name, 'e')                    AS pos_e,
--        LPAD(employee_id, 6, '0')                AS padded_id,
--        first_name || ' ' || last_name           AS full_name,     -- 連結演算子
--        ROUND(salary / 12, 2)                    AS monthly,
--        TRUNC(hire_date, 'MM')                   AS hire_month,    -- [ORA] 日付のTRUNC
--        ADD_MONTHS(hire_date, 6)                 AS probation_end, -- [ORA]
--        TRUNC(MONTHS_BETWEEN(SYSDATE, hire_date) / 12) AS years,   -- [ORA]
--        TO_CHAR(hire_date, 'YYYY"年"MM"月"DD"日"') AS hire_jp,
--        TO_CHAR(salary, 'FM999,999')             AS salary_fmt
-- FROM   employees
-- WHERE  ROWNUM <= 5

-- [APP-SIDE PLAN #5] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, email FROM hr.employees;
-- -- [ORA]
-- 
-- -- A-5. 正規表現
-- SELECT email,
--        REGEXP_SUBSTR(email, '^[A-Z]')           AS first_char,
--        REGEXP_REPLACE(last_name, '[aeiou]', '*') AS masked
-- FROM   employees
-- WHERE  REGEXP_LIKE(last_name, '^(K|L)')

/* ============================================================================== */ /* B. 結合 */ /* ============================================================================== */ /* B-1. 内部結合（ANSI） */ SELECT e.last_name, d.department_name FROM employees AS e JOIN departments AS d ON d.department_id = e.department_id;

/* B-2. 外部結合（ANSI）と Oracle 独自の (+) 記法 [ORA] */ SELECT e.last_name, d.department_name FROM employees AS e LEFT JOIN departments AS d ON d.department_id = e.department_id;

SELECT e.last_name, d.department_name /* 上と同じ結果（旧記法） */ FROM employees AS e LEFT JOIN departments AS d ON e.department_id = d.department_id;

-- [APP-SIDE PLAN #9] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, department_id FROM hr.employees;
--   SELECT department_id, department_name FROM hr.departments;
-- -- B-3. 完全外部結合：部門なし社員・社員なし部門の両方を出す
-- SELECT e.last_name, d.department_name
-- FROM   employees e
-- FULL   OUTER JOIN departments d ON d.department_id = e.department_id
-- WHERE  e.employee_id IS NULL OR d.department_id IS NULL

/* B-4. 自己結合（上司名の取得） */ SELECT w.last_name AS employee, m.last_name AS manager FROM employees AS w LEFT JOIN employees AS m ON m.employee_id = w.manager_id;

-- [APP-SIDE PLAN #11] ScalarDB から取得して H2 で実行する
--   SELECT department_id, department_name FROM hr.departments;
--   SELECT employee_id, last_name, salary, department_id FROM hr.employees;
-- -- B-5. LATERAL / CROSS APPLY（12c+）：部門ごとの高給上位2名
-- SELECT d.department_name, t.last_name, t.salary
-- FROM   departments d
-- CROSS  APPLY (SELECT e.last_name, e.salary
--               FROM   employees e
--               WHERE  e.department_id = d.department_id
--               ORDER  BY e.salary DESC
--               FETCH  FIRST 2 ROWS ONLY) t

-- [APP-SIDE PLAN #12] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary FROM hr.employees;
-- --==============================================================================
-- -- C. 副問合せ・集合演算
-- --==============================================================================
-- -- C-1. スカラー副問合せ
-- SELECT last_name, salary,
--        (SELECT ROUND(AVG(salary)) FROM employees) AS company_avg
-- FROM   employees

-- [APP-SIDE PLAN #13] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary, department_id FROM hr.employees;
-- -- C-2. 相関副問合せ：部門平均より高い社員
-- SELECT e.last_name, e.department_id, e.salary
-- FROM   employees e
-- WHERE  e.salary > (SELECT AVG(x.salary)
--                    FROM   employees x
--                    WHERE  x.department_id = e.department_id)

-- [APP-SIDE PLAN #14] ScalarDB から取得して H2 で実行する
--   SELECT department_id, department_name FROM hr.departments;
--   SELECT employee_id, department_id FROM hr.employees;
-- -- C-3. EXISTS / NOT EXISTS
-- SELECT d.department_name
-- FROM   departments d
-- WHERE  NOT EXISTS (SELECT 1 FROM employees e WHERE e.department_id = d.department_id)

-- [APP-SIDE PLAN #15] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary, department_id FROM hr.employees;
-- -- C-4. 多列 IN
-- SELECT last_name, department_id, salary
-- FROM   employees
-- WHERE  (department_id, salary) IN (SELECT department_id, MAX(salary)
--                                    FROM   employees
--                                    GROUP  BY department_id)

-- [APP-SIDE PLAN #16] ScalarDB から取得して H2 で実行する
--   SELECT department_id FROM hr.departments;
--   SELECT employee_id, department_id FROM hr.employees;
-- -- C-5. 集合演算（MINUS は Oracle 固有 [ORA]、標準は EXCEPT。21c+ は EXCEPT も可）
-- SELECT department_id FROM departments
-- MINUS
-- SELECT department_id FROM employees

-- [APP-SIDE PLAN #17] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, job_id, department_id FROM hr.employees WHERE department_id = 80;
--   SELECT job_id, min_salary FROM hr.jobs WHERE min_salary >= 6000;
-- SELECT job_id FROM employees WHERE department_id = 80
-- INTERSECT
-- SELECT job_id FROM jobs WHERE min_salary >= 6000

-- [APP-SIDE PLAN #18] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, salary, department_id FROM hr.employees;
-- --==============================================================================
-- -- D. 集計
-- --==============================================================================
-- -- D-1. GROUP BY / HAVING
-- SELECT department_id, COUNT(*) AS cnt, SUM(salary) AS total, ROUND(AVG(salary)) AS avg_sal
-- FROM   employees
-- GROUP  BY department_id
-- HAVING COUNT(*) >= 2
-- ORDER  BY total DESC

-- [NOT CONVERTED #19] main query: expressions in the select list (CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(department_id) END; CASE GROUPING(job_id) WHEN 1 THEN '小計' ELSE job_id END) -- compute them in the application; main query: GROUP BY ROLLUP -- aggregate each level in the application; the H2 residual engine cannot run ROLLUP / CUBE / GROUPING SETS (UNION ALL one aggregate per level); implement this part in the application
-- -- D-2. ROLLUP / CUBE / GROUPING SETS と GROUPING 関数
-- SELECT CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(department_id) END AS dept,
--        CASE GROUPING(job_id)        WHEN 1 THEN '小計'   ELSE job_id END                 AS job,
--        SUM(salary) AS total
-- FROM   employees
-- GROUP  BY ROLLUP (department_id, job_id)

-- [NOT CONVERTED #20] main query: GROUP BY GROUPING SETS -- aggregate each level in the application; the H2 residual engine cannot run ROLLUP / CUBE / GROUPING SETS (UNION ALL one aggregate per level); implement this part in the application
-- SELECT department_id, job_id, SUM(salary)
-- FROM   employees
-- GROUP  BY GROUPING SETS ((department_id), (job_id), ())

-- [APP-SIDE PLAN #21] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, department_id FROM hr.employees;
-- -- D-3. LISTAGG（文字列集約）
-- SELECT department_id,
--        LISTAGG(last_name, ', ') WITHIN GROUP (ORDER BY last_name) AS members
-- FROM   employees
-- GROUP  BY department_id

-- [NOT CONVERTED #22] FROM must reference exactly one base table (no subqueries); main query: derived table in FROM -- evaluate it in the application; the H2 residual engine cannot run PIVOT (conditional aggregation); implement this part in the application
-- -- D-4. PIVOT / UNPIVOT（11g+）
-- SELECT *
-- FROM  (SELECT department_id, job_id, salary FROM employees)
-- PIVOT (SUM(salary) FOR job_id IN ('IT_PROG' AS it_prog, 'SA_REP' AS sa_rep, 'SA_MAN' AS sa_man))

-- [NOT CONVERTED #23] FROM must reference exactly one base table (no subqueries); main query: derived table in FROM -- evaluate it in the application; subquery: expressions in the select list (NVL(salary * commission_pct, 0)) -- compute them in the application; the H2 residual engine cannot run UNPIVOT (UNION ALL); implement this part in the application
-- SELECT employee_id, pay_type, amount
-- FROM  (SELECT employee_id, salary, NVL(salary * commission_pct, 0) AS commission
--        FROM   employees WHERE department_id = 80)
-- UNPIVOT (amount FOR pay_type IN (salary AS 'SALARY', commission AS 'COMMISSION'))

-- [APP-SIDE PLAN #24] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, hire_date, salary, department_id FROM hr.employees;
-- --==============================================================================
-- -- E. 分析関数（ウィンドウ関数）
-- --==============================================================================
-- SELECT department_id, last_name, salary,
--        ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY salary DESC) AS rn,
--        RANK()       OVER (PARTITION BY department_id ORDER BY salary DESC) AS rnk,
--        DENSE_RANK() OVER (ORDER BY salary DESC)                            AS drnk,
--        SUM(salary)  OVER (PARTITION BY department_id)                      AS dept_total,
--        ROUND(RATIO_TO_REPORT(salary) OVER (PARTITION BY department_id), 3) AS share,  -- [ORA]
--        LAG(salary)  OVER (PARTITION BY department_id ORDER BY hire_date)   AS prev_hire_sal,
--        LEAD(last_name) OVER (ORDER BY hire_date)                           AS next_hired,
--        NTILE(4)     OVER (ORDER BY salary)                                 AS quartile
-- FROM   employees
-- ORDER  BY department_id, rn

-- [APP-SIDE PLAN #25] ScalarDB から取得して H2 で実行する
--   SELECT order_id, order_date, total FROM hr.orders;
-- -- E-2. 累計・移動平均（ウィンドウ句）
-- SELECT order_date, total,
--        SUM(total) OVER (ORDER BY order_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total,
--        ROUND(AVG(total) OVER (ORDER BY order_date ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING), 1) AS moving_avg
-- FROM   orders
-- ORDER  BY order_date

-- [NOT CONVERTED #26] projection 'MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY salary DESC)' is an expression; ScalarDB SQL only selects columns and aggregates. Compute it in the application; main query: MAX(last_name) KEEP (DENSE_RANK FIRST ...) -- pick the first/last row per group in the application; the H2 residual engine cannot run KEEP (DENSE_RANK FIRST/LAST) (ROW_NUMBER() ... = 1); implement this part in the application
-- -- E-3. KEEP (DENSE_RANK FIRST/LAST) [ORA]：部門内最高給の社員名
-- SELECT department_id,
--        MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY salary DESC) AS top_earner,
--        MAX(salary) AS max_sal
-- FROM   employees
-- GROUP  BY department_id

-- [APP-SIDE PLAN #27] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary FROM hr.employees;
-- --==============================================================================
-- -- F. Top-N / ページング
-- --==============================================================================
-- -- F-1. 12c+ 標準構文
-- SELECT last_name, salary FROM employees
-- ORDER  BY salary DESC
-- FETCH  FIRST 3 ROWS WITH TIES

-- [APP-SIDE PLAN #28] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary FROM hr.employees;
-- SELECT last_name, salary FROM employees
-- ORDER  BY salary DESC
-- OFFSET 5 ROWS FETCH NEXT 5 ROWS ONLY

-- [APP-SIDE PLAN #29] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary FROM hr.employees;
-- -- 2ページ目（5件/ページ）
-- 
-- -- F-2. 11g 以前の ROWNUM 方式 [ORA]（移行元コードで頻出）
-- SELECT *
-- FROM  (SELECT a.*, ROWNUM rnum
--        FROM  (SELECT last_name, salary FROM employees ORDER BY salary DESC) a
--        WHERE  ROWNUM <= 10)
-- WHERE  rnum > 5

-- [APP-SIDE PLAN #30] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary, department_id FROM hr.employees;
-- --==============================================================================
-- -- G. WITH 句・階層問合せ
-- --==============================================================================
-- -- G-1. 副問合せのファクタリング
-- WITH dept_stats AS (
--   SELECT department_id, AVG(salary) AS avg_sal
--   FROM   employees GROUP BY department_id
-- )
-- SELECT e.last_name, e.salary, ROUND(s.avg_sal) AS dept_avg
-- FROM   employees e JOIN dept_stats s ON s.department_id = e.department_id
-- WHERE  e.salary > s.avg_sal

-- [NOT CONVERTED #31] main query: START WITH / CONNECT BY with CONNECT_BY_ISLEAF, CONNECT_BY_ROOT, LEVEL, SYS_CONNECT_BY_PATH -- walk the tree in the application (appside.Hierarchy) or precompute it into a table; main query: expressions in the select list (LPAD(' ', 2 * (LEVEL - 1)) || last_name) -- compute them in the application; the H2 residual engine cannot run CONNECT BY (rewrite as recursive WITH, or walk the tree in the application); implement this part in the application
-- -- G-2. CONNECT BY による階層問合せ [ORA]
-- SELECT LEVEL,
--        LPAD(' ', 2 * (LEVEL - 1)) || last_name  AS org_chart,
--        SYS_CONNECT_BY_PATH(last_name, '/')       AS path,      -- [ORA]
--        CONNECT_BY_ISLEAF                         AS is_leaf,   -- [ORA]
--        CONNECT_BY_ROOT last_name                 AS root_name  -- [ORA]
-- FROM   employees
-- START  WITH manager_id IS NULL
-- CONNECT BY PRIOR employee_id = manager_id
-- ORDER  SIBLINGS BY last_name

-- [APP-SIDE PLAN #32] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, manager_id FROM hr.employees;
-- -- G-3. 再帰 WITH（標準SQL、他DBへ移行しやすい書き方）
-- WITH org (employee_id, last_name, manager_id, lvl, path) AS (
--   SELECT employee_id, last_name, manager_id, 1, CAST(last_name AS VARCHAR2(4000))
--   FROM   employees WHERE manager_id IS NULL
--   UNION ALL
--   SELECT e.employee_id, e.last_name, e.manager_id, o.lvl + 1, o.path || '/' || e.last_name
--   FROM   employees e JOIN org o ON e.manager_id = o.employee_id
-- )
-- SEARCH DEPTH FIRST BY last_name SET ord
-- SELECT lvl, LPAD(' ', 2 * (lvl - 1)) || last_name AS org_chart, path
-- FROM   org
-- ORDER  BY ord

-- [NOT CONVERTED #33] main query: START WITH / CONNECT BY with LEVEL -- walk the tree in the application (appside.Hierarchy) or precompute it into a table; main query: expressions in the select list (TO_DATE('2026-09-01', 'YYYY-MM-DD') + LEVEL - 1) -- compute them in the application; the H2 residual engine cannot run CONNECT BY (rewrite as recursive WITH, or walk the tree in the application); implement this part in the application
-- -- G-4. CONNECT BY LEVEL による連番生成 [ORA]（カレンダー表などで頻出）
-- SELECT DATE '2026-09-01' + LEVEL - 1 AS cal_date
-- FROM   dual
-- CONNECT BY LEVEL <= 7

-- [NOT CONVERTED #34] Invalid expression / Unexpected token. Line 6, Col: 32.
-- --==============================================================================
-- -- H. その他 Oracle 固有の問合せ
-- --==============================================================================
-- -- H-1. フラッシュバック問合せ [ORA]（UNDO 保持期間内の過去データ参照）
-- SELECT employee_id, salary
-- FROM   employees AS OF TIMESTAMP (SYSTIMESTAMP - INTERVAL '1' MINUTE)
-- WHERE  department_id = 60

-- [APP-SIDE PLAN #35] ScalarDB から取得して H2 で実行する
--   SELECT employee_id FROM hr.employees;
-- -- H-2. サンプリング [ORA]
-- SELECT COUNT(*) FROM employees SAMPLE (50)

-- [NOT CONVERTED #36] pseudo-column ROWID does not exist in ScalarDB; use the primary key
-- -- H-3. ROWID [ORA]（重複行削除などで頻出）
-- SELECT ROWID, employee_id FROM employees WHERE ROWNUM <= 3

-- [NOT CONVERTED #37] Expecting (. Line 3, Col: 17.
-- -- H-4. WITH 句内での PL/SQL 関数定義（12c+）[ORA]
-- WITH
--   FUNCTION annual(p_sal NUMBER, p_comm NUMBER) RETURN NUMBER IS
--   BEGIN
--     RETURN p_sal * 12 * (1 + NVL(p_comm, 0))

-- [NOT CONVERTED #38] Column statements are not supported by ScalarDB SQL
-- END

-- [APP-SIDE PLAN #39] ScalarDB から取得して H2 で実行する
--   SELECT employee_id, last_name, salary, commission_pct, department_id FROM hr.employees WHERE department_id = 80;
-- SELECT last_name, annual(salary, commission_pct) AS annual_comp
-- FROM   employees
-- WHERE  department_id = 80
