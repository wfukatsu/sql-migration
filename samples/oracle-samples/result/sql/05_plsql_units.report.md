# SQL 変換レポート: oracle → scalardb

- 入力: `samples/oracle-samples/sql/05_plsql_units.sql`
- 文数: 9　|　OK: 3　|　WARN: 2　|　PLANNED: 1　|　ERROR: 3
- **変換率: 55.6%**（5 / 9 文が scalardb の SQL を出力できた）

| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |
|---|---|---|---|---|---|
| 1 | CREATE | ❌ ERROR | `-------------------------------------------------------------------------------- -- 05_pls…` | — | **INFO** IDENT: identifier USER is a ScalarDB SQL keyword; written as "USER" (quoted)<br>**WARN** TYPE: column audit_id: NUMBER: unconstrained NUMBER mapped to DOUBLE; exact decimal precision is lost<br>**ERROR** AUTO_INC: column audit_id: AUTO_INCREMENT / IDENTITY / SERIAL is not supported; generate keys in the application (e.g. UUID as TEXT) |
| 2 | SELECT | 🧩 PLANNED | `--============================================================================== -- 1. プロシ…` | — | **ERROR** PROJECTION: main query: expressions in the select list (DEPT_NAME_OF(department_id); ANNUAL_COMP(salary, commission_pct)) -- compute them in the application<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT employee_id, last_name, salary, commission_pct, department_id FROM hr.employees<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)<br>**WARN** PLAN_UNRESOLVED: employees: columns ['annual'] not in schema<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 3 | SELECT | ⚠️ WARN | `--============================================================================== -- 3. パッケ…` | `/* ============================================================================== */ /* 3.…` | **WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['audit_id'] of emp_audit -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 4 | CREATE | ❌ ERROR | `--============================================================================== -- 5. トリガ…` | — | **ERROR** DDL: CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 5 | UPDATE | ❌ ERROR | `-- 動作確認 UPDATE employees SET salary = salary * 1.01 WHERE department_id = 60` | — | **ERROR** RMW: SET salary = salary * 1.01: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction |
| 6 | UPDATE | ✅ OK | `UPDATE emp_dept_upd_v SET department_name = 'Sales' WHERE employee_id = 107` | `UPDATE emp_dept_upd_v SET department_name = 'Sales' WHERE employee_id = 107` | **INFO** SCHEMA: UPDATE: table definition unknown, access path (GET / SCAN / cross-partition) not analysed |
| 7 | SELECT | ⚠️ WARN | `SELECT employee_id, action, old_salary, new_salary FROM emp_audit WHERE action <> 'LOG'` | `SELECT employee_id, action, old_salary, new_salary FROM emp_audit WHERE action <> 'LOG'` | **WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['audit_id'] of emp_audit -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 8 | ROLLBACK | ✅ OK | `ROLLBACK` | `ROLLBACK` |  |
| 9 | SELECT | ✅ OK | `-- エラー確認（コンパイルエラーのあるオブジェクト） SELECT name, type, line, position, text FROM user_errors ORDER…` | `/* エラー確認（コンパイルエラーのあるオブジェクト） */ SELECT name, "type", line, position, "text" FROM user_error…` | **INFO** IDENT: identifier type is a ScalarDB SQL keyword; written as "type" (quoted)<br>**INFO** IDENT: identifier text is a ScalarDB SQL keyword; written as "text" (quoted)<br>**INFO** SCHEMA: SELECT: table definition unknown, access path (GET / SCAN / cross-partition) not analysed |

## 指摘の集計

| 重要度 | コード | 件数 |
|---|---|---|
| ERROR | AUTO_INC | 1 |
| ERROR | PROJECTION | 1 |
| ERROR | DDL | 1 |
| ERROR | RMW | 1 |
| WARN | CROSS_PARTITION | 2 |
| WARN | TYPE | 1 |
| WARN | PLAN_UNRESOLVED | 1 |
| WARN | PLAN_CROSS_PARTITION | 1 |
| INFO | IDENT | 3 |
| INFO | CONFIG | 3 |
| INFO | COST | 3 |
| INFO | SCHEMA | 2 |
| INFO | PLAN_FETCH | 1 |
| INFO | PLAN_RESIDUAL | 1 |

## アプリ側に移す処理

### #1 ❌ ERROR `----------------------------------------------------------------------…`

**アプリ側で処理する構文**

- `AUTO_INC` column audit_id: AUTO_INCREMENT / IDENTITY / SERIAL is not supported; generate keys in the application (e.g. UUID as TEXT)

### #2 🧩 PLANNED `--====================================================================…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (DEPT_NAME_OF(department_id); ANNUAL_COMP(salary, commission_pct)) -- compute them in the application

**取得コストの見積もり**

- `COST` full scan of employees (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #3 ⚠️ WARN `--====================================================================…`

**取得コストの見積もり**

- `COST` full scan of emp_audit (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #4 ❌ ERROR `--====================================================================…`

**アプリ側で処理する構文**

- `DDL` CREATE VIEW is not supported (no views, sequences, triggers, procedures in ScalarDB)

### #5 ❌ ERROR `-- 動作確認 UPDATE employees SET salary = salary * 1.01 WHERE department_i…`

**アプリ側で処理する構文**

- `RMW` SET salary = salary * 1.01: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction

### #7 ⚠️ WARN `SELECT employee_id, action, old_salary, new_salary FROM emp_audit WHER…`

**取得コストの見積もり**

- `COST` full scan of emp_audit (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

