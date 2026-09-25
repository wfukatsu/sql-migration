--------------------------------------------------------------------------------
-- 02_sql_query.sql : 問合せ（SELECT）のサンプル
--   参照: SQL Language Reference - SELECT / Functions / Analytic Functions /
--         Hierarchical Queries / Joins
--   前提: 00_setup.sql 実行済み
--   [ORA] = Oracle 固有 / 他DBへの移行で要注意の構文
--------------------------------------------------------------------------------

--==============================================================================
-- A. 基本
--==============================================================================
-- A-1. DUAL 表 [ORA]（23ai 以降は FROM 句省略も可）
SELECT SYSDATE, SYSTIMESTAMP, USER FROM dual;

-- A-2. 絞り込み・並べ替え（NULL の並び順指定）
SELECT employee_id, last_name, salary, commission_pct
FROM   employees
WHERE  salary BETWEEN 5000 AND 15000
AND    last_name LIKE '%er%'
ORDER  BY commission_pct DESC NULLS LAST, salary DESC;

-- A-3. CASE / DECODE [ORA] / NULL 処理関数
SELECT last_name,
       salary,
       CASE
         WHEN salary >= 15000 THEN 'HIGH'
         WHEN salary >=  7000 THEN 'MID'
         ELSE 'LOW'
       END                                      AS salary_band,
       DECODE(department_id, 60, 'IT', 80, 'Sales', 'Other') AS dept_label,   -- [ORA]
       NVL(commission_pct, 0)                   AS comm_nvl,                  -- [ORA]
       NVL2(commission_pct, 'Y', 'N')           AS has_comm,                  -- [ORA]
       COALESCE(commission_pct, 0)              AS comm_coalesce               -- 標準SQL
FROM   employees;

-- A-4. 文字列・数値・日付関数
SELECT UPPER(last_name)                         AS upper_name,
       INITCAP(email)                           AS initcap_email,
       SUBSTR(last_name, 1, 3)                  AS short_name,
       INSTR(last_name, 'e')                    AS pos_e,
       LPAD(employee_id, 6, '0')                AS padded_id,
       first_name || ' ' || last_name           AS full_name,     -- 連結演算子
       ROUND(salary / 12, 2)                    AS monthly,
       TRUNC(hire_date, 'MM')                   AS hire_month,    -- [ORA] 日付のTRUNC
       ADD_MONTHS(hire_date, 6)                 AS probation_end, -- [ORA]
       TRUNC(MONTHS_BETWEEN(SYSDATE, hire_date) / 12) AS years,   -- [ORA]
       TO_CHAR(hire_date, 'YYYY"年"MM"月"DD"日"') AS hire_jp,
       TO_CHAR(salary, 'FM999,999')             AS salary_fmt
FROM   employees
WHERE  ROWNUM <= 5;                                               -- [ORA]

-- A-5. 正規表現
SELECT email,
       REGEXP_SUBSTR(email, '^[A-Z]')           AS first_char,
       REGEXP_REPLACE(last_name, '[aeiou]', '*') AS masked
FROM   employees
WHERE  REGEXP_LIKE(last_name, '^(K|L)');

--==============================================================================
-- B. 結合
--==============================================================================
-- B-1. 内部結合（ANSI）
SELECT e.last_name, d.department_name
FROM   employees e
JOIN   departments d ON d.department_id = e.department_id;

-- B-2. 外部結合（ANSI）と Oracle 独自の (+) 記法 [ORA]
SELECT e.last_name, d.department_name
FROM   employees e
LEFT   JOIN departments d ON d.department_id = e.department_id;

SELECT e.last_name, d.department_name          -- 上と同じ結果（旧記法）
FROM   employees e, departments d
WHERE  e.department_id = d.department_id(+);

-- B-3. 完全外部結合：部門なし社員・社員なし部門の両方を出す
SELECT e.last_name, d.department_name
FROM   employees e
FULL   OUTER JOIN departments d ON d.department_id = e.department_id
WHERE  e.employee_id IS NULL OR d.department_id IS NULL;

-- B-4. 自己結合（上司名の取得）
SELECT w.last_name AS employee, m.last_name AS manager
FROM   employees w
LEFT   JOIN employees m ON m.employee_id = w.manager_id;

-- B-5. LATERAL / CROSS APPLY（12c+）：部門ごとの高給上位2名
SELECT d.department_name, t.last_name, t.salary
FROM   departments d
CROSS  APPLY (SELECT e.last_name, e.salary
              FROM   employees e
              WHERE  e.department_id = d.department_id
              ORDER  BY e.salary DESC
              FETCH  FIRST 2 ROWS ONLY) t;

--==============================================================================
-- C. 副問合せ・集合演算
--==============================================================================
-- C-1. スカラー副問合せ
SELECT last_name, salary,
       (SELECT ROUND(AVG(salary)) FROM employees) AS company_avg
FROM   employees;

-- C-2. 相関副問合せ：部門平均より高い社員
SELECT e.last_name, e.department_id, e.salary
FROM   employees e
WHERE  e.salary > (SELECT AVG(x.salary)
                   FROM   employees x
                   WHERE  x.department_id = e.department_id);

-- C-3. EXISTS / NOT EXISTS
SELECT d.department_name
FROM   departments d
WHERE  NOT EXISTS (SELECT 1 FROM employees e WHERE e.department_id = d.department_id);

-- C-4. 多列 IN
SELECT last_name, department_id, salary
FROM   employees
WHERE  (department_id, salary) IN (SELECT department_id, MAX(salary)
                                   FROM   employees
                                   GROUP  BY department_id);

-- C-5. 集合演算（MINUS は Oracle 固有 [ORA]、標準は EXCEPT。21c+ は EXCEPT も可）
SELECT department_id FROM departments
MINUS
SELECT department_id FROM employees;

SELECT job_id FROM employees WHERE department_id = 80
INTERSECT
SELECT job_id FROM jobs WHERE min_salary >= 6000;

--==============================================================================
-- D. 集計
--==============================================================================
-- D-1. GROUP BY / HAVING
SELECT department_id, COUNT(*) AS cnt, SUM(salary) AS total, ROUND(AVG(salary)) AS avg_sal
FROM   employees
GROUP  BY department_id
HAVING COUNT(*) >= 2
ORDER  BY total DESC;

-- D-2. ROLLUP / CUBE / GROUPING SETS と GROUPING 関数
SELECT CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(department_id) END AS dept,
       CASE GROUPING(job_id)        WHEN 1 THEN '小計'   ELSE job_id END                 AS job,
       SUM(salary) AS total
FROM   employees
GROUP  BY ROLLUP (department_id, job_id);

SELECT department_id, job_id, SUM(salary)
FROM   employees
GROUP  BY GROUPING SETS ((department_id), (job_id), ());

-- D-3. LISTAGG（文字列集約）
SELECT department_id,
       LISTAGG(last_name, ', ') WITHIN GROUP (ORDER BY last_name) AS members
FROM   employees
GROUP  BY department_id;

-- D-4. PIVOT / UNPIVOT（11g+）
SELECT *
FROM  (SELECT department_id, job_id, salary FROM employees)
PIVOT (SUM(salary) FOR job_id IN ('IT_PROG' AS it_prog, 'SA_REP' AS sa_rep, 'SA_MAN' AS sa_man));

SELECT employee_id, pay_type, amount
FROM  (SELECT employee_id, salary, NVL(salary * commission_pct, 0) AS commission
       FROM   employees WHERE department_id = 80)
UNPIVOT (amount FOR pay_type IN (salary AS 'SALARY', commission AS 'COMMISSION'));

--==============================================================================
-- E. 分析関数（ウィンドウ関数）
--==============================================================================
SELECT department_id, last_name, salary,
       ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY salary DESC) AS rn,
       RANK()       OVER (PARTITION BY department_id ORDER BY salary DESC) AS rnk,
       DENSE_RANK() OVER (ORDER BY salary DESC)                            AS drnk,
       SUM(salary)  OVER (PARTITION BY department_id)                      AS dept_total,
       ROUND(RATIO_TO_REPORT(salary) OVER (PARTITION BY department_id), 3) AS share,  -- [ORA]
       LAG(salary)  OVER (PARTITION BY department_id ORDER BY hire_date)   AS prev_hire_sal,
       LEAD(last_name) OVER (ORDER BY hire_date)                           AS next_hired,
       NTILE(4)     OVER (ORDER BY salary)                                 AS quartile
FROM   employees
ORDER  BY department_id, rn;

-- E-2. 累計・移動平均（ウィンドウ句）
SELECT order_date, total,
       SUM(total) OVER (ORDER BY order_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total,
       ROUND(AVG(total) OVER (ORDER BY order_date ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING), 1) AS moving_avg
FROM   orders
ORDER  BY order_date;

-- E-3. KEEP (DENSE_RANK FIRST/LAST) [ORA]：部門内最高給の社員名
SELECT department_id,
       MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY salary DESC) AS top_earner,
       MAX(salary) AS max_sal
FROM   employees
GROUP  BY department_id;

--==============================================================================
-- F. Top-N / ページング
--==============================================================================
-- F-1. 12c+ 標準構文
SELECT last_name, salary FROM employees
ORDER  BY salary DESC
FETCH  FIRST 3 ROWS WITH TIES;

SELECT last_name, salary FROM employees
ORDER  BY salary DESC
OFFSET 5 ROWS FETCH NEXT 5 ROWS ONLY;          -- 2ページ目（5件/ページ）

-- F-2. 11g 以前の ROWNUM 方式 [ORA]（移行元コードで頻出）
SELECT *
FROM  (SELECT a.*, ROWNUM rnum
       FROM  (SELECT last_name, salary FROM employees ORDER BY salary DESC) a
       WHERE  ROWNUM <= 10)
WHERE  rnum > 5;

--==============================================================================
-- G. WITH 句・階層問合せ
--==============================================================================
-- G-1. 副問合せのファクタリング
WITH dept_stats AS (
  SELECT department_id, AVG(salary) AS avg_sal
  FROM   employees GROUP BY department_id
)
SELECT e.last_name, e.salary, ROUND(s.avg_sal) AS dept_avg
FROM   employees e JOIN dept_stats s ON s.department_id = e.department_id
WHERE  e.salary > s.avg_sal;

-- G-2. CONNECT BY による階層問合せ [ORA]
SELECT LEVEL,
       LPAD(' ', 2 * (LEVEL - 1)) || last_name  AS org_chart,
       SYS_CONNECT_BY_PATH(last_name, '/')       AS path,      -- [ORA]
       CONNECT_BY_ISLEAF                         AS is_leaf,   -- [ORA]
       CONNECT_BY_ROOT last_name                 AS root_name  -- [ORA]
FROM   employees
START  WITH manager_id IS NULL
CONNECT BY PRIOR employee_id = manager_id
ORDER  SIBLINGS BY last_name;

-- G-3. 再帰 WITH（標準SQL、他DBへ移行しやすい書き方）
WITH org (employee_id, last_name, manager_id, lvl, path) AS (
  SELECT employee_id, last_name, manager_id, 1, CAST(last_name AS VARCHAR2(4000))
  FROM   employees WHERE manager_id IS NULL
  UNION ALL
  SELECT e.employee_id, e.last_name, e.manager_id, o.lvl + 1, o.path || '/' || e.last_name
  FROM   employees e JOIN org o ON e.manager_id = o.employee_id
)
SEARCH DEPTH FIRST BY last_name SET ord
SELECT lvl, LPAD(' ', 2 * (lvl - 1)) || last_name AS org_chart, path
FROM   org
ORDER  BY ord;

-- G-4. CONNECT BY LEVEL による連番生成 [ORA]（カレンダー表などで頻出）
SELECT DATE '2026-09-01' + LEVEL - 1 AS cal_date
FROM   dual
CONNECT BY LEVEL <= 7;

--==============================================================================
-- H. その他 Oracle 固有の問合せ
--==============================================================================
-- H-1. フラッシュバック問合せ [ORA]（UNDO 保持期間内の過去データ参照）
SELECT employee_id, salary
FROM   employees AS OF TIMESTAMP (SYSTIMESTAMP - INTERVAL '1' MINUTE)
WHERE  department_id = 60;

-- H-2. サンプリング [ORA]
SELECT COUNT(*) FROM employees SAMPLE (50);

-- H-3. ROWID [ORA]（重複行削除などで頻出）
SELECT ROWID, employee_id FROM employees WHERE ROWNUM <= 3;

-- H-4. WITH 句内での PL/SQL 関数定義（12c+）[ORA]
WITH
  FUNCTION annual(p_sal NUMBER, p_comm NUMBER) RETURN NUMBER IS
  BEGIN
    RETURN p_sal * 12 * (1 + NVL(p_comm, 0));
  END;
SELECT last_name, annual(salary, commission_pct) AS annual_comp
FROM   employees
WHERE  department_id = 80
/
