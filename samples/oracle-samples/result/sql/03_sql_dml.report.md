# SQL 変換レポート: oracle → scalardb

- 入力: `samples/oracle-samples/sql/03_sql_dml.sql`
- 文数: 37　|　OK: 9　|　WARN: 6　|　PLANNED: 1　|　ERROR: 21
- **変換率: 40.5%**（15 / 37 文が scalardb の SQL を出力できた）

| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |
|---|---|---|---|---|---|
| 1 | INSERT | ❌ ERROR | `-------------------------------------------------------------------------------- -- 03_sql…` | — | **ERROR** SEQUENCE: VALUES column employee_id: sequences are not supported; generate keys in the application (e.g. UUID) |
| 2 | INSERT | ❌ ERROR | `-- A-2. INSERT ... SELECT INSERT INTO emp_stage (employee_id, last_name, salary, departmen…` | — | **ERROR** INSERT_SELECT: INSERT ... SELECT is not supported; read rows in the application then insert |
| 3 | CREATE | ❌ ERROR | `-- A-3. 複数表への条件付き INSERT（INSERT ALL / FIRST）[ORA] CREATE TABLE emp_high AS SELECT employee…` | — | **ERROR** DDL: CREATE TABLE ... AS SELECT / LIKE is not supported |
| 4 | CREATE | ❌ ERROR | `CREATE TABLE emp_low  AS SELECT employee_id, salary FROM employees WHERE 1 = 0` | — | **ERROR** DDL: CREATE TABLE ... AS SELECT / LIKE is not supported |
| 5 | MULTITABLEINSERTS | ❌ ERROR | `INSERT FIRST   WHEN salary >= 10000 THEN INTO emp_high (employee_id, salary) VALUES (emplo…` | — | **ERROR** STATEMENT: MultitableInserts statements are not supported by ScalarDB SQL |
| 6 | MULTITABLEINSERTS | ❌ ERROR | `-- A-4. INSERT ALL による複数行挿入（23ai 未満で複数行 VALUES の代替）[ORA] INSERT ALL   INTO jobs VALUES ('M…` | — | **ERROR** STATEMENT: MultitableInserts statements are not supported by ScalarDB SQL |
| 7 | UPDATE | ❌ ERROR | `-- 23ai 以降は表値コンストラクタで複数行挿入可 -- INSERT INTO jobs VALUES ('HR_REP','HR Rep',4000,9000), ('PR…` | — | **ERROR** RMW: SET salary = salary * 1.05: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction |
| 8 | UPDATE | ❌ ERROR | `-- B-2. 複数列を副問合せで一括更新 UPDATE departments d SET   (location) = (SELECT 'Tokyo' FROM dual) W…` | — | **ERROR** SET: unsupported SET clause '(location) = (SELECT 'Tokyo' FROM dual)' |
| 9 | PARSE_ERROR | ❌ ERROR | `-- B-3. 結合更新（インラインビュー更新）[ORA] --      結合先の主キーで一意に決まる（キー保存表）場合のみ可能 UPDATE (SELECT e.salary,…` | — | **ERROR** PARSE: Invalid expression / Unexpected token. Line 6, Col: 13. |
| 10 | DELETE | ❌ ERROR | `-- 23ai 以降は UPDATE ... FROM も利用可 -- UPDATE employees e SET e.salary = j.min_salary FROM jo…` | — | **ERROR** ROWID: pseudo-column ROWID does not exist in ScalarDB; use the primary key |
| 11 | UPDATE | ❌ ERROR | `--============================================================================== -- C. MER…` | — | **ERROR** RMW: SET salary = salary + 500: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction |
| 12 | PARSE_ERROR | ❌ ERROR | `MERGE INTO employees t USING (SELECT employee_id, salary FROM emp_stage) s ON    (t.employ…` | — | **ERROR** PARSE: Invalid expression / Unexpected token. Line 7, Col: 8. |
| 13 | COMMAND | ❌ ERROR | `--============================================================================== -- D. DML…` | — | **ERROR** UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 14 | PARSE_ERROR | ❌ ERROR | `INSERT INTO emp_stage (employee_id, last_name, salary, department_id) SELECT employee_id, …` | — | **ERROR** PARSE: Invalid expression / Unexpected token. Line 3, Col: 10. |
| 15 | SELECT | ✅ OK | `SELECT ora_err_number$, ora_err_tag$, employee_id, salary FROM emp_err_log` | `SELECT "ora_err_number$", "ora_err_tag$", employee_id, salary FROM emp_err_log` | **INFO** IDENT: identifier ora_err_number$ is not a plain identifier; written as "ora_err_number$" (quoted)<br>**INFO** IDENT: identifier ora_err_tag$ is not a plain identifier; written as "ora_err_tag$" (quoted)<br>**INFO** SCHEMA: SELECT: table definition unknown, access path (GET / SCAN / cross-partition) not analysed |
| 16 | ALIAS | ❌ ERROR | `--============================================================================== -- E. トラン…` | — | **ERROR** STATEMENT: Alias statements are not supported by ScalarDB SQL |
| 17 | UPDATE | ❌ ERROR | `UPDATE employees SET salary = salary * 2 WHERE department_id = 60` | — | **ERROR** RMW: SET salary = salary * 2: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction |
| 18 | ROLLBACK | ❌ ERROR | `ROLLBACK TO SAVEPOINT before_raise` | — | **ERROR** SAVEPOINT: savepoints / named transactions are not supported |
| 19 | COMMIT | ✅ OK | `-- 部分ロールバック COMMIT` | `COMMIT /* 部分ロールバック */ /* 部分ロールバック */` |  |
| 20 | SELECT | ⚠️ WARN | `-- 悲観ロック SELECT employee_id, salary FROM employees WHERE department_id = 80 FOR UPDATE OF …` | `/* 悲観ロック */ SELECT employee_id, salary FROM employees WHERE department_id = 80` | **WARN** LOCK: FOR UPDATE / locking clause dropped (ScalarDB transactions handle isolation)<br>**INFO** ACCESS: SELECT: equality on secondary index -> index SCAN |
| 21 | ROLLBACK | ✅ OK | `-- ロック取得できなければ即 ORA-00054 ROLLBACK` | `ROLLBACK /* ロック取得できなければ即 ORA-00054 */ /* ロック取得できなければ即 ORA-00054 */` |  |
| 22 | SELECT | ⚠️ WARN | `-- キュー処理の定番: ロック済み行を飛ばして取得 [ORA] SELECT order_id FROM orders WHERE status = 'NEW' FOR UPDA…` | `/* キュー処理の定番: ロック済み行を飛ばして取得 [ORA] */ SELECT order_id FROM orders WHERE status = 'NEW'` | **WARN** LOCK: FOR UPDATE / locking clause dropped (ScalarDB transactions handle isolation)<br>**INFO** ACCESS: SELECT: equality on secondary index -> index SCAN |
| 23 | ROLLBACK | ✅ OK | `ROLLBACK` | `ROLLBACK` |  |
| 24 | SET | ❌ ERROR | `-- 分離レベル（Oracle は READ COMMITTED と SERIALIZABLE のみ） SET TRANSACTION ISOLATION LEVEL SERIAL…` | — | **ERROR** STATEMENT: Set statements are not supported by ScalarDB SQL |
| 25 | SELECT | ⚠️ WARN | `SELECT COUNT(*) FROM employees` | `SELECT COUNT(*) FROM employees` | **WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['employee_id'] of employees -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 26 | COMMIT | ✅ OK | `COMMIT` | `COMMIT` |  |
| 27 | CREATE | ⚠️ WARN | `--============================================================================== -- F. JSO…` | `CREATE TABLE products_json (   id DOUBLE PRIMARY KEY,   doc TEXT )` | **WARN** TYPE: column id: NUMBER: unconstrained NUMBER mapped to DOUBLE; exact decimal precision is lost<br>**WARN** CHECK: column doc: CHECK dropped; enforce in the application |
| 28 | INSERT | ⚠️ WARN | `-- 21c+ なら  doc JSON  と書ける  INSERT INTO products_json VALUES (1,   '{"name":"ScalarDB","ti…` | `/* 21c+ なら  doc JSON  と書ける */ INSERT INTO products_json VALUES (1, '{"name":"ScalarDB","ti…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 29 | INSERT | ⚠️ WARN | `INSERT INTO products_json VALUES (2,   '{"name":"ScalarDL","tier":"Standard","price":1500,…` | `INSERT INTO products_json VALUES (2, '{"name":"ScalarDL","tier":"Standard","price":1500,"t…` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list |
| 30 | COMMIT | ✅ OK | `COMMIT` | `COMMIT` |  |
| 31 | PARSE_ERROR | ❌ ERROR | `-- F-2. 値の取り出し（JSON_VALUE / JSON_QUERY / ドット記法） SELECT JSON_VALUE(doc, '$.name')          …` | — | **ERROR** PARSE: Expecting ). Line 3, Col: 42. |
| 32 | SELECT | ❌ ERROR | `-- F-3. JSON_TABLE：JSON を行列に展開 SELECT p.id, jt.name, jt.tag FROM   products_json p,       …` | — | **ERROR** JOIN: comma join without join condition (cartesian product) is not supported<br>**ERROR** RESIDUAL_H2: the H2 residual engine cannot run JSON_TABLE (H2 has no JSON_TABLE: read the JSON column and unnest it in the application); implement this part in the application |
| 33 | SELECT | 🧩 PLANNED | `-- F-4. リレーショナル → JSON 生成 SELECT JSON_OBJECT('dept' VALUE d.department_name,              …` | — | **ERROR** PROJECTION: main query: expressions in the select list (JSON_OBJECT('dept': d.department_name, 'members': JSON_ARRAYAGG(JSON_OBJECT('...) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT department_id, department_name FROM hr.departments<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 34 | PARSE_ERROR | ❌ ERROR | `-- F-5. 部分更新（19c+） UPDATE products_json SET    doc = JSON_MERGEPATCH(doc, '{"price":1200,"…` | — | **ERROR** PARSE: Expecting ). Line 3, Col: 79. |
| 35 | COMMIT | ✅ OK | `COMMIT` | `COMMIT` |  |
| 36 | DROP | ✅ OK | `-- 後片付け（このファイル内だけで使った表） DROP TABLE emp_high PURGE` | `DROP TABLE emp_high` |  |
| 37 | DROP | ✅ OK | `DROP TABLE emp_low  PURGE` | `DROP TABLE emp_low` |  |

## 指摘の集計

| 重要度 | コード | 件数 |
|---|---|---|
| ERROR | PARSE | 5 |
| ERROR | STATEMENT | 4 |
| ERROR | RMW | 3 |
| ERROR | DDL | 2 |
| ERROR | SEQUENCE | 1 |
| ERROR | INSERT_SELECT | 1 |
| ERROR | SET | 1 |
| ERROR | ROWID | 1 |
| ERROR | UNPARSED | 1 |
| ERROR | SAVEPOINT | 1 |
| ERROR | JOIN | 1 |
| ERROR | RESIDUAL_H2 | 1 |
| ERROR | PROJECTION | 1 |
| WARN | LOCK | 2 |
| WARN | INSERT_COLS | 2 |
| WARN | CROSS_PARTITION | 1 |
| WARN | TYPE | 1 |
| WARN | CHECK | 1 |
| WARN | PLAN_CROSS_PARTITION | 1 |
| INFO | IDENT | 2 |
| INFO | ACCESS | 2 |
| INFO | CONFIG | 2 |
| INFO | COST | 2 |
| INFO | PLAN_FETCH | 2 |
| INFO | SCHEMA | 1 |
| INFO | DESIGN | 1 |
| INFO | PLAN_RESIDUAL | 1 |

## アプリ側に移す処理

### #1 ❌ ERROR `----------------------------------------------------------------------…`

**アプリ側で処理する構文**

- `SEQUENCE` VALUES column employee_id: sequences are not supported; generate keys in the application (e.g. UUID)

### #2 ❌ ERROR `-- A-2. INSERT ... SELECT INSERT INTO emp_stage (employee_id, last_nam…`

**アプリ側で処理する構文**

- `INSERT_SELECT` INSERT ... SELECT is not supported; read rows in the application then insert

### #3 ❌ ERROR `-- A-3. 複数表への条件付き INSERT（INSERT ALL / FIRST）[ORA] CREATE TABLE emp_hig…`

**アプリ側で処理する構文**

- `DDL` CREATE TABLE ... AS SELECT / LIKE is not supported

### #4 ❌ ERROR `CREATE TABLE emp_low  AS SELECT employee_id, salary FROM employees WHE…`

**アプリ側で処理する構文**

- `DDL` CREATE TABLE ... AS SELECT / LIKE is not supported

### #5 ❌ ERROR `INSERT FIRST   WHEN salary >= 10000 THEN INTO emp_high (employee_id, s…`

**アプリ側で処理する構文**

- `STATEMENT` MultitableInserts statements are not supported by ScalarDB SQL

### #6 ❌ ERROR `-- A-4. INSERT ALL による複数行挿入（23ai 未満で複数行 VALUES の代替）[ORA] INSERT ALL   …`

**アプリ側で処理する構文**

- `STATEMENT` MultitableInserts statements are not supported by ScalarDB SQL

### #7 ❌ ERROR `-- 23ai 以降は表値コンストラクタで複数行挿入可 -- INSERT INTO jobs VALUES ('HR_REP','HR R…`

**アプリ側で処理する構文**

- `RMW` SET salary = salary * 1.05: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction

### #8 ❌ ERROR `-- B-2. 複数列を副問合せで一括更新 UPDATE departments d SET   (location) = (SELECT …`

**アプリ側で処理する構文**

- `SET` unsupported SET clause '(location) = (SELECT 'Tokyo' FROM dual)'

### #9 ❌ ERROR `-- B-3. 結合更新（インラインビュー更新）[ORA] --      結合先の主キーで一意に決まる（キー保存表）場合のみ可能 UPDA…`

**アプリ側で処理する構文**

- `PARSE` Invalid expression / Unexpected token. Line 6, Col: 13.

### #10 ❌ ERROR `-- 23ai 以降は UPDATE ... FROM も利用可 -- UPDATE employees e SET e.salary = …`

**アプリ側で処理する構文**

- `ROWID` pseudo-column ROWID does not exist in ScalarDB; use the primary key

### #11 ❌ ERROR `--====================================================================…`

**アプリ側で処理する構文**

- `RMW` SET salary = salary + 500: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction

### #12 ❌ ERROR `MERGE INTO employees t USING (SELECT employee_id, salary FROM emp_stag…`

**アプリ側で処理する構文**

- `PARSE` Invalid expression / Unexpected token. Line 7, Col: 8.

### #13 ❌ ERROR `--====================================================================…`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #14 ❌ ERROR `INSERT INTO emp_stage (employee_id, last_name, salary, department_id) …`

**アプリ側で処理する構文**

- `PARSE` Invalid expression / Unexpected token. Line 3, Col: 10.

### #16 ❌ ERROR `--====================================================================…`

**アプリ側で処理する構文**

- `STATEMENT` Alias statements are not supported by ScalarDB SQL

### #17 ❌ ERROR `UPDATE employees SET salary = salary * 2 WHERE department_id = 60`

**アプリ側で処理する構文**

- `RMW` SET salary = salary * 2: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction

### #18 ❌ ERROR `ROLLBACK TO SAVEPOINT before_raise`

**アプリ側で処理する構文**

- `SAVEPOINT` savepoints / named transactions are not supported

### #24 ❌ ERROR `-- 分離レベル（Oracle は READ COMMITTED と SERIALIZABLE のみ） SET TRANSACTION IS…`

**アプリ側で処理する構文**

- `STATEMENT` Set statements are not supported by ScalarDB SQL

### #25 ⚠️ WARN `SELECT COUNT(*) FROM employees`

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #31 ❌ ERROR `-- F-2. 値の取り出し（JSON_VALUE / JSON_QUERY / ドット記法） SELECT JSON_VALUE(doc,…`

**アプリ側で処理する構文**

- `PARSE` Expecting ). Line 3, Col: 42.

### #32 ❌ ERROR `-- F-3. JSON_TABLE：JSON を行列に展開 SELECT p.id, jt.name, jt.tag FROM   pro…`

**アプリ側で処理する構文**

- `JOIN` comma join without join condition (cartesian product) is not supported
- `RESIDUAL_H2` the H2 residual engine cannot run JSON_TABLE (H2 has no JSON_TABLE: read the JSON column and unnest it in the application); implement this part in the application

**設計の提案**

- `DESIGN` table definitions unknown for : include CREATE TABLE or pass --schema to get access-path checks and key-design advice

### #33 🧩 PLANNED `-- F-4. リレーショナル → JSON 生成 SELECT JSON_OBJECT('dept' VALUE d.department…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (JSON_OBJECT('dept': d.department_name, 'members': JSON_ARRAYAGG(JSON_OBJECT('...) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of departments, employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #34 ❌ ERROR `-- F-5. 部分更新（19c+） UPDATE products_json SET    doc = JSON_MERGEPATCH(d…`

**アプリ側で処理する構文**

- `PARSE` Expecting ). Line 3, Col: 79.

