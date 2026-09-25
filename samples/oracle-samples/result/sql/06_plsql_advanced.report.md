# SQL 変換レポート: oracle → scalardb

- 入力: `samples/oracle-samples/sql/06_plsql_advanced.sql`
- 文数: 3　|　OK: 1　|　WARN: 1　|　PLANNED: 1　|　ERROR: 0
- **変換率: 66.7%**（2 / 3 文が scalardb の SQL を出力できた）

| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |
|---|---|---|---|---|---|
| 1 | CREATE | ⚠️ WARN | `-------------------------------------------------------------------------------- -- 06_pls…` | `CREATE TABLE bulk_target (   employee_id DOUBLE PRIMARY KEY,   last_name TEXT,   salary DO…` | **WARN** TYPE: column employee_id: NUMBER: unconstrained NUMBER mapped to DOUBLE; exact decimal precision is lost<br>**INFO** TYPE: column last_name: VARCHAR2(25): length limit is not enforced by ScalarDB TEXT<br>**WARN** TYPE: column salary: NUMBER: unconstrained NUMBER mapped to DOUBLE; exact decimal precision is lost<br>**WARN** CHECK: column salary: CHECK dropped; enforce in the application |
| 2 | SELECT | 🧩 PLANNED | `--============================================================================== -- 1. BUL…` | — | **INFO** SCHEMA: SELECT: table definition unknown, access path (GET / SCAN / cross-partition) not analysed<br>**ERROR** UNSUPPORTED: function TABLE is not supported by ScalarDB SQL<br>**INFO** PLAN_FETCH: UNKNOWN: SELECT * FROM ""<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off) |
| 3 | SELECT | ✅ OK | `-- コレクションを SQL で展開（MEMBER OF / TABLE 演算子）  --=============================================…` | `/* コレクションを SQL で展開（MEMBER OF / TABLE 演算子） */ /* ==========================================…` | **INFO** SCHEMA: SELECT: table definition unknown, access path (GET / SCAN / cross-partition) not analysed |

## 指摘の集計

| 重要度 | コード | 件数 |
|---|---|---|
| ERROR | UNSUPPORTED | 1 |
| WARN | TYPE | 2 |
| WARN | CHECK | 1 |
| INFO | SCHEMA | 2 |
| INFO | TYPE | 1 |
| INFO | PLAN_FETCH | 1 |
| INFO | PLAN_RESIDUAL | 1 |
| INFO | CONFIG | 1 |
| INFO | COST | 1 |
| INFO | DESIGN | 1 |

## アプリ側に移す処理

### #2 🧩 PLANNED `--====================================================================…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `UNSUPPORTED` function TABLE is not supported by ScalarDB SQL

**設計の提案**

- `DESIGN` table definitions unknown for : include CREATE TABLE or pass --schema to get access-path checks and key-design advice

**取得コストの見積もり**

- `COST` full scan of  (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

