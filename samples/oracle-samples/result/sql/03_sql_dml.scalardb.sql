-- converted to scalardb
-- [NOT CONVERTED #1] VALUES column employee_id: sequences are not supported; generate keys in the application (e.g. UUID)
-- --------------------------------------------------------------------------------
-- -- 03_sql_dml.sql : DML・トランザクション制御・JSON のサンプル
-- --   参照: SQL Language Reference - INSERT / UPDATE / DELETE / MERGE /
-- --         SAVEPOINT / SELECT FOR UPDATE、JSON Developer's Guide
-- --   前提: 00_setup.sql, 01_sql_ddl.sql 実行済み（emp_stage を使用）
-- --   [ORA] = Oracle 固有 / 他DBへの移行で要注意の構文
-- --------------------------------------------------------------------------------
-- 
-- --==============================================================================
-- -- A. INSERT
-- --==============================================================================
-- -- A-1. 単一行（シーケンス使用）
-- INSERT INTO employees (employee_id, first_name, last_name, email, hire_date, job_id, salary, department_id)
-- VALUES (emp_seq.NEXTVAL, 'Taro', 'Yamada', 'TYAMADA', SYSDATE, 'IT_PROG', 5000, 60)

-- [NOT CONVERTED #2] INSERT ... SELECT is not supported; read rows in the application then insert
-- -- A-2. INSERT ... SELECT
-- INSERT INTO emp_stage (employee_id, last_name, salary, department_id)
-- SELECT employee_id, last_name, salary, department_id
-- FROM   employees
-- WHERE  department_id = 60

-- [NOT CONVERTED #3] CREATE TABLE ... AS SELECT / LIKE is not supported
-- -- A-3. 複数表への条件付き INSERT（INSERT ALL / FIRST）[ORA]
-- CREATE TABLE emp_high AS SELECT employee_id, salary FROM employees WHERE 1 = 0

-- [NOT CONVERTED #4] CREATE TABLE ... AS SELECT / LIKE is not supported
-- CREATE TABLE emp_low  AS SELECT employee_id, salary FROM employees WHERE 1 = 0

-- [NOT CONVERTED #5] MultitableInserts statements are not supported by ScalarDB SQL
-- INSERT FIRST
--   WHEN salary >= 10000 THEN INTO emp_high (employee_id, salary) VALUES (employee_id, salary)
--   ELSE                      INTO emp_low  (employee_id, salary) VALUES (employee_id, salary)
-- SELECT employee_id, salary FROM employees

-- [NOT CONVERTED #6] MultitableInserts statements are not supported by ScalarDB SQL
-- -- A-4. INSERT ALL による複数行挿入（23ai 未満で複数行 VALUES の代替）[ORA]
-- INSERT ALL
--   INTO jobs VALUES ('MK_MAN', 'Marketing Manager', 9000, 15000)
--   INTO jobs VALUES ('MK_REP', 'Marketing Rep',     4000,  9000)
-- SELECT * FROM dual

-- [NOT CONVERTED #7] SET salary = salary * 1.05: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction
-- -- 23ai 以降は表値コンストラクタで複数行挿入可
-- -- INSERT INTO jobs VALUES ('HR_REP','HR Rep',4000,9000), ('PR_REP','PR Rep',4500,10500);
-- 
-- --==============================================================================
-- -- B. UPDATE / DELETE
-- --==============================================================================
-- -- B-1. 相関副問合せによる更新
-- UPDATE employees e
-- SET    salary = salary * 1.05
-- WHERE  salary < (SELECT AVG(salary) FROM employees x WHERE x.department_id = e.department_id)

-- [NOT CONVERTED #8] unsupported SET clause '(location) = (SELECT 'Tokyo' FROM dual)'
-- -- B-2. 複数列を副問合せで一括更新
-- UPDATE departments d
-- SET   (location) = (SELECT 'Tokyo' FROM dual)
-- WHERE  d.department_id = 99

-- [NOT CONVERTED #9] Invalid expression / Unexpected token. Line 6, Col: 13.
-- -- B-3. 結合更新（インラインビュー更新）[ORA]
-- --      結合先の主キーで一意に決まる（キー保存表）場合のみ可能
-- UPDATE (SELECT e.salary, j.min_salary
--         FROM   employees e JOIN jobs j ON j.job_id = e.job_id
--         WHERE  e.salary < j.min_salary)
-- SET    salary = min_salary

-- [NOT CONVERTED #10] pseudo-column ROWID does not exist in ScalarDB; use the primary key
-- -- 23ai 以降は UPDATE ... FROM も利用可
-- -- UPDATE employees e SET e.salary = j.min_salary FROM jobs j
-- --  WHERE j.job_id = e.job_id AND e.salary < j.min_salary;
-- 
-- -- B-4. DELETE（ROWID による重複削除の定番パターン）[ORA]
-- DELETE FROM emp_stage a
-- WHERE  a.ROWID > (SELECT MIN(b.ROWID) FROM emp_stage b WHERE b.employee_id = a.employee_id)

-- [NOT CONVERTED #11] SET salary = salary + 500: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction
-- --==============================================================================
-- -- C. MERGE（UPSERT）
-- --==============================================================================
-- -- ステージ表の内容で employees を更新／存在しなければ挿入
-- UPDATE emp_stage SET salary = salary + 500

-- [NOT CONVERTED #12] Invalid expression / Unexpected token. Line 7, Col: 8.
-- MERGE INTO employees t
-- USING (SELECT employee_id, salary FROM emp_stage) s
-- ON    (t.employee_id = s.employee_id)
-- WHEN MATCHED THEN
--   UPDATE SET t.salary = s.salary
--   WHERE  t.salary <> s.salary
--   DELETE WHERE t.salary > 20000                 -- [ORA] 更新後に条件で削除
-- WHEN NOT MATCHED THEN
--   INSERT (employee_id, last_name, email, hire_date, job_id, salary)
--   VALUES (s.employee_id, 'NEW', 'NEW' || s.employee_id, SYSDATE, 'IT_PROG', s.salary)

-- [NOT CONVERTED #13] statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)
-- --==============================================================================
-- -- D. DML エラーロギング [ORA]
-- --    制約違反の行をスキップしてエラー表へ記録し、処理全体は継続する
-- --==============================================================================
-- ALTER TABLE emp_stage ADD CONSTRAINT emp_stage_sal_ck CHECK (salary < 10000) ENABLE NOVALIDATE

-- [NOT CONVERTED #14] Invalid expression / Unexpected token. Line 3, Col: 10.
-- INSERT INTO emp_stage (employee_id, last_name, salary, department_id)
-- SELECT employee_id, last_name, salary, department_id FROM employees
-- LOG ERRORS INTO emp_err_log ('batch-001') REJECT LIMIT UNLIMITED

SELECT "ora_err_number$", "ora_err_tag$", employee_id, salary FROM emp_err_log;

-- [NOT CONVERTED #16] Alias statements are not supported by ScalarDB SQL
-- --==============================================================================
-- -- E. トランザクション制御・ロック
-- --==============================================================================
-- SAVEPOINT before_raise

-- [NOT CONVERTED #17] SET salary = salary * 2: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction
-- UPDATE employees SET salary = salary * 2 WHERE department_id = 60

-- [NOT CONVERTED #18] savepoints / named transactions are not supported
-- ROLLBACK TO SAVEPOINT before_raise

COMMIT /* 部分ロールバック */ /* 部分ロールバック */;

/* 悲観ロック */ SELECT employee_id, salary FROM employees WHERE department_id = 80;

ROLLBACK /* ロック取得できなければ即 ORA-00054 */ /* ロック取得できなければ即 ORA-00054 */;

/* キュー処理の定番: ロック済み行を飛ばして取得 [ORA] */ SELECT order_id FROM orders WHERE status = 'NEW';

ROLLBACK;

-- [NOT CONVERTED #24] Set statements are not supported by ScalarDB SQL
-- -- 分離レベル（Oracle は READ COMMITTED と SERIALIZABLE のみ）
-- SET TRANSACTION ISOLATION LEVEL SERIALIZABLE

SELECT COUNT(*) FROM employees;

COMMIT;

CREATE TABLE products_json (
  id DOUBLE PRIMARY KEY,
  doc TEXT
);

/* 21c+ なら  doc JSON  と書ける */ INSERT INTO products_json VALUES (1, '{"name":"ScalarDB","tier":"Enterprise","price":1000,"tags":["db","tx"],"spec":{"ha":true}}');

INSERT INTO products_json VALUES (2, '{"name":"ScalarDL","tier":"Standard","price":1500,"tags":["ledger"],"spec":{"ha":false}}');

COMMIT;

-- [NOT CONVERTED #31] Expecting ). Line 3, Col: 42.
-- -- F-2. 値の取り出し（JSON_VALUE / JSON_QUERY / ドット記法）
-- SELECT JSON_VALUE(doc, '$.name')                    AS name,
--        JSON_VALUE(doc, '$.price' RETURNING NUMBER)  AS price,
--        JSON_QUERY(doc, '$.tags')                    AS tags,
--        p.doc.spec.ha                                AS ha            -- ドット記法（表別名必須）
-- FROM   products_json p
-- WHERE  JSON_EXISTS(doc, '$.tags[*]?(@ == "db")')

-- [APP-SIDE PLAN #32] ScalarDB から取得して H2 で実行する
--   SELECT id, doc FROM hr.products_json;
--   SELECT * FROM "";
-- -- F-3. JSON_TABLE：JSON を行列に展開
-- SELECT p.id, jt.name, jt.tag
-- FROM   products_json p,
--        JSON_TABLE(p.doc, '$'
--          COLUMNS (name VARCHAR2(40) PATH '$.name',
--                   NESTED PATH '$.tags[*]' COLUMNS (tag VARCHAR2(20) PATH '$'))) jt

-- [APP-SIDE PLAN #33] ScalarDB から取得して H2 で実行する
--   SELECT department_id, department_name FROM hr.departments;
--   SELECT employee_id, last_name, department_id FROM hr.employees;
-- -- F-4. リレーショナル → JSON 生成
-- SELECT JSON_OBJECT('dept' VALUE d.department_name,
--                    'members' VALUE JSON_ARRAYAGG(
--                        JSON_OBJECT('id' VALUE e.employee_id, 'name' VALUE e.last_name)
--                        ORDER BY e.employee_id)
--                    ) AS dept_json
-- FROM   departments d JOIN employees e ON e.department_id = d.department_id
-- GROUP  BY d.department_name

-- [NOT CONVERTED #34] Expecting ). Line 3, Col: 79.
-- -- F-5. 部分更新（19c+）
-- UPDATE products_json
-- SET    doc = JSON_MERGEPATCH(doc, '{"price":1200,"spec":{"ha":true}}' RETURNING CLOB)
-- WHERE  id = 2

COMMIT;

DROP TABLE emp_high;

DROP TABLE emp_low;
