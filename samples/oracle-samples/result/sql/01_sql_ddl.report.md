# SQL 変換レポート: oracle → scalardb

- 入力: `samples/oracle-samples/sql/01_sql_ddl.sql`
- 文数: 39　|　OK: 9　|　WARN: 3　|　ERROR: 27
- **変換率: 30.8%**（12 / 39 文が scalardb の SQL を出力できた）

| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |
|---|---|---|---|---|---|
| 1 | PARSE_ERROR | ❌ ERROR | `-------------------------------------------------------------------------------- -- 01_sql…` | — | **ERROR** PARSE: Required keyword: 'this' missing for <class 'sqlglot.expressions.constraints.DefaultColumnConstraint'>. Line 14, Col: 37. |
| 2 | COMMENT | ❌ ERROR | `COMMENT ON TABLE  sample_ddl       IS 'DDLサンプル用テーブル'` | — | **ERROR** STATEMENT: Comment statements are not supported by ScalarDB SQL |
| 3 | COMMENT | ❌ ERROR | `COMMENT ON COLUMN sample_ddl.price IS '税抜価格'` | — | **ERROR** STATEMENT: Comment statements are not supported by ScalarDB SQL |
| 4 | INSERT | ❌ ERROR | `INSERT INTO sample_ddl (name, price) VALUES ('Widget', 1000)` | — | **ERROR** PK: INSERT must specify the full primary key; missing ['id'] |
| 5 | INSERT | ❌ ERROR | `INSERT INTO sample_ddl (name, price) VALUES ('Gadget', NULL)` | — | **ERROR** PK: INSERT must specify the full primary key; missing ['id'] |
| 6 | SELECT | ⚠️ WARN | `-- price は 0 になる SELECT id, name, price, price_incl FROM sample_ddl` | `/* price は 0 になる */ SELECT id, name, price, price_incl FROM sample_ddl` | **WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['id'] of sample_ddl -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 7 | ALTER | ❌ ERROR | `-- 2. ALTER TABLE：列の追加・変更・名前変更・削除 ALTER TABLE sample_ddl ADD (category VARCHAR2(20) DEFAUL…` | — | **ERROR** ALTER: ALTER TABLE action '(category VARCHAR2(20) DEFAULT 'GENERAL' NOT NULL)' is not supported (constraints, indexes, partitions, engine options ...) |
| 8 | COMMAND | ❌ ERROR | `ALTER TABLE sample_ddl MODIFY (name VARCHAR2(100 CHAR))` | — | **ERROR** UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 9 | ALTER | ✅ OK | `ALTER TABLE sample_ddl RENAME COLUMN note TO description` | `ALTER TABLE sample_ddl RENAME COLUMN note TO description` |  |
| 10 | COMMAND | ❌ ERROR | `ALTER TABLE sample_ddl SET UNUSED (description)` | — | **ERROR** UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 11 | ALTER | ❌ ERROR | `-- [ORA] 論理削除（即時） ALTER TABLE sample_ddl DROP UNUSED COLUMNS` | — | **ERROR** ALTER: ALTER TABLE action 'DROP UNUSED COLUMNS' is not supported (constraints, indexes, partitions, engine options ...) |
| 12 | ALTER | ❌ ERROR | `-- 物理削除  -- 3. 制約の追加・無効化・有効化 ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_name_uk UNIQ…` | — | **ERROR** ALTER: ALTER TABLE action 'ADD CONSTRAINT sample_ddl_name_uk UNIQUE (name)' is not supported (constraints, indexes, partitions, engine options ...) |
| 13 | ALTER | ❌ ERROR | `ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_price_ck CHECK (price >= 0)` | — | **ERROR** ALTER: ALTER TABLE action 'ADD CONSTRAINT sample_ddl_price_ck CHECK (price >= 0)' is not supported (constraints, indexes, partitions, engine options ...) |
| 14 | COMMAND | ❌ ERROR | `ALTER TABLE sample_ddl DISABLE CONSTRAINT sample_ddl_price_ck` | — | **ERROR** UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 15 | COMMAND | ❌ ERROR | `ALTER TABLE sample_ddl ENABLE NOVALIDATE CONSTRAINT sample_ddl_price_ck` | — | **ERROR** UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 16 | CREATE | ❌ ERROR | `-- [ORA] 既存行は検証しない  -- 4. CTAS（CREATE TABLE AS SELECT） CREATE TABLE emp_stage AS   SELECT …` | — | **ERROR** DDL: CREATE TABLE ... AS SELECT / LIKE is not supported |
| 17 | CREATE | ❌ ERROR | `-- 構造のみコピー  -- 5. インデックス CREATE INDEX emp_name_ix       ON employees (last_name, first_nam…` | — | **ERROR** INDEX: ScalarDB secondary indexes are single-column; got ['last_name', 'first_name']. Consider making the leading column a partition key / clustering key instead |
| 18 | CREATE | ❌ ERROR | `-- 複合 CREATE INDEX emp_upper_email_ix ON employees (UPPER(email))` | — | **ERROR** INDEX: ScalarDB secondary indexes are single-column; got ['UPPER(email)']. Consider making the leading column a partition key / clustering key instead |
| 19 | COMMAND | ❌ ERROR | `-- ファンクション索引 CREATE BITMAP INDEX orders_status_bix ON orders (status)` | — | **ERROR** UNPARSED: statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 20 | COMMAND | ❌ ERROR | `-- [ORA] ビットマップ索引(EE) ALTER INDEX emp_name_ix INVISIBLE` | — | **ERROR** UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 21 | COMMAND | ❌ ERROR | `-- [ORA] 不可視索引 ALTER INDEX emp_name_ix VISIBLE` | — | **ERROR** UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 22 | CREATE | ❌ ERROR | `-- 6. ビュー（WITH CHECK OPTION / READ ONLY） CREATE OR REPLACE VIEW emp_it_v AS   SELECT emplo…` | — | **ERROR** DDL: CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 23 | CREATE | ❌ ERROR | `CREATE OR REPLACE VIEW emp_dept_v AS   SELECT e.employee_id, e.last_name, d.department_nam…` | — | **ERROR** DDL: CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 24 | CREATE | ❌ ERROR | `-- 7. シーケンス [ORA] CREATE SEQUENCE audit_seq START WITH 1 INCREMENT BY 1 CACHE 20 NOCYCLE` | — | **ERROR** DDL: CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 25 | SELECT | ✅ OK | `SELECT audit_seq.NEXTVAL, audit_seq.CURRVAL FROM dual` | `SELECT audit_seq.NEXTVAL, audit_seq.CURRVAL FROM dual` | **INFO** SCHEMA: SELECT: table definition unknown, access path (GET / SCAN / cross-partition) not analysed |
| 26 | COMMAND | ❌ ERROR | `ALTER SEQUENCE audit_seq INCREMENT BY 10` | — | **ERROR** UNPARSED: statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 27 | COMMAND | ❌ ERROR | `-- 8. シノニム [ORA] CREATE OR REPLACE SYNONYM staff FOR employees` | — | **ERROR** UNPARSED: statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 28 | SELECT | ✅ OK | `SELECT COUNT(*) FROM staff` | `SELECT COUNT(*) FROM staff` | **INFO** SCHEMA: SELECT: table definition unknown, access path (GET / SCAN / cross-partition) not analysed |
| 29 | CREATE | ❌ ERROR | `-- 9. 一時表 [ORA] --    GLOBAL TEMPORARY：定義は永続、データはセッション/トランザクション単位 CREATE GLOBAL TEMPORARY …` | — | **ERROR** TEMP: temporary tables are not supported |
| 30 | COMMAND | ❌ ERROR | `-- DELETE ROWS ならコミットで消える  -- 10. パーティション表 [ORA]（EE の Partitioning オプション / Free 版でも利用可） CR…` | — | **ERROR** UNPARSED: statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...) |
| 31 | INSERT | ⚠️ WARN | `INSERT INTO sales_part VALUES (1, DATE '2025-12-31', 100)` | `INSERT INTO sales_part VALUES (1, '2025-12-31', 100)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column sale_date: TO_DATE('2025-12-31', 'YYYY-MM-DD') written as the plain literal '2025-12-31' |
| 32 | INSERT | ⚠️ WARN | `INSERT INTO sales_part VALUES (2, DATE '2026-03-15', 200)` | `INSERT INTO sales_part VALUES (2, '2026-03-15', 200)` | **WARN** INSERT_COLS: no column list: ScalarDB uses table definition order; add an explicit column list<br>**INFO** DATE_LIT: VALUES column sale_date: TO_DATE('2026-03-15', 'YYYY-MM-DD') written as the plain literal '2026-03-15' |
| 33 | COMMIT | ✅ OK | `COMMIT` | `COMMIT` |  |
| 34 | SELECT | ✅ OK | `SELECT partition_name, high_value FROM user_tab_partitions WHERE table_name = 'SALES_PART'` | `SELECT partition_name, high_value FROM user_tab_partitions WHERE table_name = 'SALES_PART'` | **INFO** SCHEMA: SELECT: table definition unknown, access path (GET / SCAN / cross-partition) not analysed |
| 35 | TRUNCATETABLE | ✅ OK | `-- 11. TRUNCATE / DROP / FLASHBACK TABLE（ごみ箱から復元）[ORA] TRUNCATE TABLE emp_stage` | `TRUNCATE TABLE emp_stage` |  |
| 36 | DROP | ✅ OK | `DROP TABLE gtt_work` | `DROP TABLE gtt_work` |  |
| 37 | DROP | ✅ OK | `DROP TABLE sales_part` | `DROP TABLE sales_part` |  |
| 38 | PARSE_ERROR | ❌ ERROR | `-- ごみ箱へ FLASHBACK TABLE sales_part TO BEFORE DROP` | — | **ERROR** PARSE: Invalid expression / Unexpected token. Line 2, Col: 26. |
| 39 | DROP | ✅ OK | `-- 復元 DROP TABLE sales_part PURGE` | `DROP TABLE sales_part` |  |

## 指摘の集計

| 重要度 | コード | 件数 |
|---|---|---|
| ERROR | UNPARSED | 10 |
| ERROR | ALTER | 4 |
| ERROR | DDL | 4 |
| ERROR | PARSE | 2 |
| ERROR | STATEMENT | 2 |
| ERROR | PK | 2 |
| ERROR | INDEX | 2 |
| ERROR | TEMP | 1 |
| WARN | INSERT_COLS | 2 |
| WARN | CROSS_PARTITION | 1 |
| INFO | SCHEMA | 3 |
| INFO | DATE_LIT | 2 |
| INFO | CONFIG | 1 |
| INFO | COST | 1 |

## アプリ側に移す処理

### #1 ❌ ERROR `----------------------------------------------------------------------…`

**アプリ側で処理する構文**

- `PARSE` Required keyword: 'this' missing for <class 'sqlglot.expressions.constraints.DefaultColumnConstraint'>. Line 14, Col: 37.

### #2 ❌ ERROR `COMMENT ON TABLE  sample_ddl       IS 'DDLサンプル用テーブル'`

**アプリ側で処理する構文**

- `STATEMENT` Comment statements are not supported by ScalarDB SQL

### #3 ❌ ERROR `COMMENT ON COLUMN sample_ddl.price IS '税抜価格'`

**アプリ側で処理する構文**

- `STATEMENT` Comment statements are not supported by ScalarDB SQL

### #4 ❌ ERROR `INSERT INTO sample_ddl (name, price) VALUES ('Widget', 1000)`

**アプリ側で処理する構文**

- `PK` INSERT must specify the full primary key; missing ['id']

### #5 ❌ ERROR `INSERT INTO sample_ddl (name, price) VALUES ('Gadget', NULL)`

**アプリ側で処理する構文**

- `PK` INSERT must specify the full primary key; missing ['id']

### #6 ⚠️ WARN `-- price は 0 になる SELECT id, name, price, price_incl FROM sample_ddl`

**取得コストの見積もり**

- `COST` full scan of sample_ddl (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #7 ❌ ERROR `-- 2. ALTER TABLE：列の追加・変更・名前変更・削除 ALTER TABLE sample_ddl ADD (category…`

**アプリ側で処理する構文**

- `ALTER` ALTER TABLE action '(category VARCHAR2(20) DEFAULT 'GENERAL' NOT NULL)' is not supported (constraints, indexes, partitions, engine options ...)

### #8 ❌ ERROR `ALTER TABLE sample_ddl MODIFY (name VARCHAR2(100 CHAR))`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #10 ❌ ERROR `ALTER TABLE sample_ddl SET UNUSED (description)`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #11 ❌ ERROR `-- [ORA] 論理削除（即時） ALTER TABLE sample_ddl DROP UNUSED COLUMNS`

**アプリ側で処理する構文**

- `ALTER` ALTER TABLE action 'DROP UNUSED COLUMNS' is not supported (constraints, indexes, partitions, engine options ...)

### #12 ❌ ERROR `-- 物理削除  -- 3. 制約の追加・無効化・有効化 ALTER TABLE sample_ddl ADD CONSTRAINT sam…`

**アプリ側で処理する構文**

- `ALTER` ALTER TABLE action 'ADD CONSTRAINT sample_ddl_name_uk UNIQUE (name)' is not supported (constraints, indexes, partitions, engine options ...)

### #13 ❌ ERROR `ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_price_ck CHECK (price…`

**アプリ側で処理する構文**

- `ALTER` ALTER TABLE action 'ADD CONSTRAINT sample_ddl_price_ck CHECK (price >= 0)' is not supported (constraints, indexes, partitions, engine options ...)

### #14 ❌ ERROR `ALTER TABLE sample_ddl DISABLE CONSTRAINT sample_ddl_price_ck`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #15 ❌ ERROR `ALTER TABLE sample_ddl ENABLE NOVALIDATE CONSTRAINT sample_ddl_price_c…`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #16 ❌ ERROR `-- [ORA] 既存行は検証しない  -- 4. CTAS（CREATE TABLE AS SELECT） CREATE TABLE em…`

**アプリ側で処理する構文**

- `DDL` CREATE TABLE ... AS SELECT / LIKE is not supported

### #17 ❌ ERROR `-- 構造のみコピー  -- 5. インデックス CREATE INDEX emp_name_ix       ON employees (…`

**アプリ側で処理する構文**

- `INDEX` ScalarDB secondary indexes are single-column; got ['last_name', 'first_name']. Consider making the leading column a partition key / clustering key instead

### #18 ❌ ERROR `-- 複合 CREATE INDEX emp_upper_email_ix ON employees (UPPER(email))`

**アプリ側で処理する構文**

- `INDEX` ScalarDB secondary indexes are single-column; got ['UPPER(email)']. Consider making the leading column a partition key / clustering key instead

### #19 ❌ ERROR `-- ファンクション索引 CREATE BITMAP INDEX orders_status_bix ON orders (status)`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #20 ❌ ERROR `-- [ORA] ビットマップ索引(EE) ALTER INDEX emp_name_ix INVISIBLE`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #21 ❌ ERROR `-- [ORA] 不可視索引 ALTER INDEX emp_name_ix VISIBLE`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #22 ❌ ERROR `-- 6. ビュー（WITH CHECK OPTION / READ ONLY） CREATE OR REPLACE VIEW emp_it…`

**アプリ側で処理する構文**

- `DDL` CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB)

### #23 ❌ ERROR `CREATE OR REPLACE VIEW emp_dept_v AS   SELECT e.employee_id, e.last_na…`

**アプリ側で処理する構文**

- `DDL` CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB)

### #24 ❌ ERROR `-- 7. シーケンス [ORA] CREATE SEQUENCE audit_seq START WITH 1 INCREMENT BY …`

**アプリ側で処理する構文**

- `DDL` CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)

### #26 ❌ ERROR `ALTER SEQUENCE audit_seq INCREMENT BY 10`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'ALTER' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #27 ❌ ERROR `-- 8. シノニム [ORA] CREATE OR REPLACE SYNONYM staff FOR employees`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #29 ❌ ERROR `-- 9. 一時表 [ORA] --    GLOBAL TEMPORARY：定義は永続、データはセッション/トランザクション単位 CREA…`

**アプリ側で処理する構文**

- `TEMP` temporary tables are not supported

### #30 ❌ ERROR `-- DELETE ROWS ならコミットで消える  -- 10. パーティション表 [ORA]（EE の Partitioning オプシ…`

**アプリ側で処理する構文**

- `UNPARSED` statement type 'CREATE' is not supported (views, triggers, procedures, sequences, grants, session settings ...)

### #38 ❌ ERROR `-- ごみ箱へ FLASHBACK TABLE sales_part TO BEFORE DROP`

**アプリ側で処理する構文**

- `PARSE` Invalid expression / Unexpected token. Line 2, Col: 26.

