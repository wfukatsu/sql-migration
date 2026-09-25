--------------------------------------------------------------------------------
-- 03_sql_dml.sql : DML・トランザクション制御・JSON のサンプル
--   参照: SQL Language Reference - INSERT / UPDATE / DELETE / MERGE /
--         SAVEPOINT / SELECT FOR UPDATE、JSON Developer's Guide
--   前提: 00_setup.sql, 01_sql_ddl.sql 実行済み（emp_stage を使用）
--   [ORA] = Oracle 固有 / 他DBへの移行で要注意の構文
--------------------------------------------------------------------------------

--==============================================================================
-- A. INSERT
--==============================================================================
-- A-1. 単一行（シーケンス使用）
INSERT INTO employees (employee_id, first_name, last_name, email, hire_date, job_id, salary, department_id)
VALUES (emp_seq.NEXTVAL, 'Taro', 'Yamada', 'TYAMADA', SYSDATE, 'IT_PROG', 5000, 60);

-- A-2. INSERT ... SELECT
INSERT INTO emp_stage (employee_id, last_name, salary, department_id)
SELECT employee_id, last_name, salary, department_id
FROM   employees
WHERE  department_id = 60;

-- A-3. 複数表への条件付き INSERT（INSERT ALL / FIRST）[ORA]
CREATE TABLE emp_high AS SELECT employee_id, salary FROM employees WHERE 1 = 0;
CREATE TABLE emp_low  AS SELECT employee_id, salary FROM employees WHERE 1 = 0;

INSERT FIRST
  WHEN salary >= 10000 THEN INTO emp_high (employee_id, salary) VALUES (employee_id, salary)
  ELSE                      INTO emp_low  (employee_id, salary) VALUES (employee_id, salary)
SELECT employee_id, salary FROM employees;

-- A-4. INSERT ALL による複数行挿入（23ai 未満で複数行 VALUES の代替）[ORA]
INSERT ALL
  INTO jobs VALUES ('MK_MAN', 'Marketing Manager', 9000, 15000)
  INTO jobs VALUES ('MK_REP', 'Marketing Rep',     4000,  9000)
SELECT * FROM dual;

-- 23ai 以降は表値コンストラクタで複数行挿入可
-- INSERT INTO jobs VALUES ('HR_REP','HR Rep',4000,9000), ('PR_REP','PR Rep',4500,10500);

--==============================================================================
-- B. UPDATE / DELETE
--==============================================================================
-- B-1. 相関副問合せによる更新
UPDATE employees e
SET    salary = salary * 1.05
WHERE  salary < (SELECT AVG(salary) FROM employees x WHERE x.department_id = e.department_id);

-- B-2. 複数列を副問合せで一括更新
UPDATE departments d
SET   (location) = (SELECT 'Tokyo' FROM dual)
WHERE  d.department_id = 99;

-- B-3. 結合更新（インラインビュー更新）[ORA]
--      結合先の主キーで一意に決まる（キー保存表）場合のみ可能
UPDATE (SELECT e.salary, j.min_salary
        FROM   employees e JOIN jobs j ON j.job_id = e.job_id
        WHERE  e.salary < j.min_salary)
SET    salary = min_salary;

-- 23ai 以降は UPDATE ... FROM も利用可
-- UPDATE employees e SET e.salary = j.min_salary FROM jobs j
--  WHERE j.job_id = e.job_id AND e.salary < j.min_salary;

-- B-4. DELETE（ROWID による重複削除の定番パターン）[ORA]
DELETE FROM emp_stage a
WHERE  a.ROWID > (SELECT MIN(b.ROWID) FROM emp_stage b WHERE b.employee_id = a.employee_id);

--==============================================================================
-- C. MERGE（UPSERT）
--==============================================================================
-- ステージ表の内容で employees を更新／存在しなければ挿入
UPDATE emp_stage SET salary = salary + 500;

MERGE INTO employees t
USING (SELECT employee_id, salary FROM emp_stage) s
ON    (t.employee_id = s.employee_id)
WHEN MATCHED THEN
  UPDATE SET t.salary = s.salary
  WHERE  t.salary <> s.salary
  DELETE WHERE t.salary > 20000                 -- [ORA] 更新後に条件で削除
WHEN NOT MATCHED THEN
  INSERT (employee_id, last_name, email, hire_date, job_id, salary)
  VALUES (s.employee_id, 'NEW', 'NEW' || s.employee_id, SYSDATE, 'IT_PROG', s.salary);

--==============================================================================
-- D. DML エラーロギング [ORA]
--    制約違反の行をスキップしてエラー表へ記録し、処理全体は継続する
--==============================================================================
ALTER TABLE emp_stage ADD CONSTRAINT emp_stage_sal_ck CHECK (salary < 10000) ENABLE NOVALIDATE;

INSERT INTO emp_stage (employee_id, last_name, salary, department_id)
SELECT employee_id, last_name, salary, department_id FROM employees
LOG ERRORS INTO emp_err_log ('batch-001') REJECT LIMIT UNLIMITED;

SELECT ora_err_number$, ora_err_tag$, employee_id, salary FROM emp_err_log;

--==============================================================================
-- E. トランザクション制御・ロック
--==============================================================================
SAVEPOINT before_raise;
UPDATE employees SET salary = salary * 2 WHERE department_id = 60;
ROLLBACK TO SAVEPOINT before_raise;       -- 部分ロールバック
COMMIT;

-- 悲観ロック
SELECT employee_id, salary FROM employees WHERE department_id = 80
FOR UPDATE OF salary NOWAIT;              -- ロック取得できなければ即 ORA-00054
ROLLBACK;

-- キュー処理の定番: ロック済み行を飛ばして取得 [ORA]
SELECT order_id FROM orders WHERE status = 'NEW'
FOR UPDATE SKIP LOCKED;
ROLLBACK;

-- 分離レベル（Oracle は READ COMMITTED と SERIALIZABLE のみ）
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
SELECT COUNT(*) FROM employees;
COMMIT;

--==============================================================================
-- F. JSON（12c+、19c で機能強化、21c+ でネイティブ JSON 型）
--==============================================================================
-- F-1. JSON を格納する表（19c 互換: CLOB + IS JSON 制約）
CREATE TABLE products_json (
  id   NUMBER PRIMARY KEY,
  doc  CLOB CONSTRAINT products_json_ck CHECK (doc IS JSON)
);
-- 21c+ なら  doc JSON  と書ける

INSERT INTO products_json VALUES (1,
  '{"name":"ScalarDB","tier":"Enterprise","price":1000,"tags":["db","tx"],"spec":{"ha":true}}');
INSERT INTO products_json VALUES (2,
  '{"name":"ScalarDL","tier":"Standard","price":1500,"tags":["ledger"],"spec":{"ha":false}}');
COMMIT;

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

-- F-5. 部分更新（19c+）
UPDATE products_json
SET    doc = JSON_MERGEPATCH(doc, '{"price":1200,"spec":{"ha":true}}' RETURNING CLOB)
WHERE  id = 2;
COMMIT;

-- 後片付け（このファイル内だけで使った表）
DROP TABLE emp_high PURGE;
DROP TABLE emp_low  PURGE;
