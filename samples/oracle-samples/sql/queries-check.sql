-- src/02_sql_query.sql（と 03 の JSON 問合せ）の読み取り文を、実 DB（移行元 Oracle と ScalarDB Cluster）で突き合わせるケース
-- （difftest/run.py の形式）。表の DDL と SELECT だけ。データは queries-check.data.json（00_setup.sql の投入データと同じ）。
-- 決定（2026-09-24）: orders.order_id / order_items.order_id は NUMBER → NUMBER(12)（キーに DOUBLE は使えない。plsql/src/schema.sql と同じ）。
CREATE TABLE jobs (
  job_id      VARCHAR2(10)  CONSTRAINT jobs_pk PRIMARY KEY,
  job_title   VARCHAR2(35)  NOT NULL,
  min_salary  NUMBER(8,2),
  max_salary  NUMBER(8,2)
);

CREATE TABLE departments (
  department_id    NUMBER(4)     CONSTRAINT dept_pk PRIMARY KEY,
  department_name  VARCHAR2(30)  NOT NULL,
  manager_id       NUMBER(6),
  location         VARCHAR2(30)
);

CREATE TABLE employees (
  employee_id    NUMBER(6)     CONSTRAINT emp_pk PRIMARY KEY,
  first_name     VARCHAR2(20),
  last_name      VARCHAR2(25)  NOT NULL,
  email          VARCHAR2(25)  NOT NULL CONSTRAINT emp_email_uk UNIQUE,
  hire_date      DATE          NOT NULL,
  job_id         VARCHAR2(10)  NOT NULL CONSTRAINT emp_job_fk REFERENCES jobs,
  salary         NUMBER(8,2)   CONSTRAINT emp_salary_ck CHECK (salary > 0),
  commission_pct NUMBER(2,2),
  manager_id     NUMBER(6)     CONSTRAINT emp_mgr_fk  REFERENCES employees,
  department_id  NUMBER(4)     CONSTRAINT emp_dept_fk REFERENCES departments
);

CREATE INDEX emp_dept_ix ON employees (department_id);
CREATE INDEX emp_mgr_ix  ON employees (manager_id);

CREATE TABLE orders (
  order_id     NUMBER(12)    CONSTRAINT orders_pk PRIMARY KEY,
  order_date   DATE          NOT NULL,
  employee_id  NUMBER(6)     CONSTRAINT orders_emp_fk REFERENCES employees,
  customer     VARCHAR2(40)  NOT NULL,
  status       VARCHAR2(10)  CONSTRAINT orders_status_ck CHECK (status IN ('NEW','SHIPPED','CANCELLED')),
  total        NUMBER(10,2)
);

CREATE INDEX orders_status_ix ON orders (status);

CREATE TABLE order_items (
  order_id    NUMBER(12)   CONSTRAINT oi_order_fk REFERENCES orders ON DELETE CASCADE,
  line_no     NUMBER(3),
  product     VARCHAR2(40) NOT NULL,
  qty         NUMBER(5)    NOT NULL,
  unit_price  NUMBER(8,2)  NOT NULL,
  CONSTRAINT order_items_pk PRIMARY KEY (order_id, line_no)
);

CREATE TABLE products_json (
  id   NUMBER(12) PRIMARY KEY,
  doc  CLOB CONSTRAINT products_json_ck CHECK (doc IS JSON)
);

-- ---- src/02_sql_query.sql の読み取り文（原文のまま。先頭のコメントも残す） ----
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
WHERE  ROWNUM <= 5;

-- [ORA]

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

-- E-1'. 上の文は、この検証環境の Oracle（26ai Free）では `AS share` が予約語に当たって ORA-00923 になる（サンプル自体の不備）。
--       別名だけを share_pct に変えたもの。分析関数の比較はこちらで行う
SELECT department_id, last_name, salary,
       ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY salary DESC) AS rn,
       RANK()       OVER (PARTITION BY department_id ORDER BY salary DESC) AS rnk,
       DENSE_RANK() OVER (ORDER BY salary DESC)                            AS drnk,
       SUM(salary)  OVER (PARTITION BY department_id)                      AS dept_total,
       ROUND(RATIO_TO_REPORT(salary) OVER (PARTITION BY department_id), 3) AS share_pct,
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
OFFSET 5 ROWS FETCH NEXT 5 ROWS ONLY;

-- 2ページ目（5件/ページ）

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

-- H-4. WITH 句内での PL/SQL 関数定義（12c+）[ORA] は、変換ツールが内側の ; で文を切ってしまうので、このケースには入れない（02 の変換レポートを見る）

-- ---- src/03_sql_dml.sql の JSON 問合せ（F-2 〜 F-4） ----
-- F-2. 値の取り出し（JSON_VALUE / JSON_QUERY / ドット記法）
SELECT JSON_VALUE(doc, '$.name')                    AS name,
       JSON_VALUE(doc, '$.price' RETURNING NUMBER)  AS price,
       JSON_QUERY(doc, '$.tags')                    AS tags,
       p.doc.spec.ha                                AS ha            -- ドット記法（表別名必須）
FROM   products_json p
WHERE  JSON_EXISTS(doc, '$.tags[*]?(@ == "db")');

-- F-3. JSON_TABLE：JSON を行列に展開
SELECT p.id, jt.name, jt.tag
FROM   products_json p,
       JSON_TABLE(p.doc, '$'
         COLUMNS (name VARCHAR2(40) PATH '$.name',
                  NESTED PATH '$.tags[*]' COLUMNS (tag VARCHAR2(20) PATH '$'))) jt;

-- F-4. リレーショナル → JSON 生成
SELECT JSON_OBJECT('dept' VALUE d.department_name,
                   'members' VALUE JSON_ARRAYAGG(
                       JSON_OBJECT('id' VALUE e.employee_id, 'name' VALUE e.last_name)
                       ORDER BY e.employee_id)
                   ) AS dept_json
FROM   departments d JOIN employees e ON e.department_id = d.department_id
GROUP  BY d.department_name;
