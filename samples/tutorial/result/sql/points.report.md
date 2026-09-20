# SQL 変換レポート: oracle → scalardb

- 入力: `samples/tutorial/sql/points.sql`
- 文数: 20　|　OK: 7　|　WARN: 6　|　PLANNED: 4　|　ERROR: 3
- **変換率: 65.0%**（13 / 20 文が scalardb の SQL を出力できた）

| # | 種別 | 状態 | 元の SQL | 変換後 | 指摘 |
|---|---|---|---|---|---|
| 1 | CREATE | ⚠️ WARN | `-- チュートリアル用の Oracle SQL（ポイントカード）。docs/guide/tutorial.md が、この 1 ファイルを sql-transpile で Scala…` | `CREATE TABLE members (   member_id BIGINT PRIMARY KEY,   name TEXT,   rank TEXT,   balance…` | **INFO** TYPE: column member_id: NUMBER(10) -> BIGINT (exact, fits 64-bit)<br>**INFO** NOT_NULL: column member_id: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column name: VARCHAR2(100): length limit is not enforced by ScalarDB TEXT<br>**INFO** NOT_NULL: column name: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column rank: VARCHAR2(10): length limit is not enforced by ScalarDB TEXT<br>**WARN** DEFAULT: column rank: DEFAULT 'REGULAR' dropped; the application must supply the value<br>**INFO** NOT_NULL: column rank: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column balance: NUMBER(10) -> BIGINT (exact, fits 64-bit)<br>**INFO** NOT_NULL: column balance: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column last_seq: NUMBER(6) -> INT (exact, fits 32-bit)<br>**INFO** NOT_NULL: column last_seq: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**WARN** TYPE: column updated_at: Oracle DATE carries a time-of-day component; use TIMESTAMP if the time part is used |
| 2 | CREATE | ⚠️ WARN | `CREATE TABLE point_history (   member_id  NUMBER(10)    NOT NULL,   seq_no     NUMBER(6)  …` | `CREATE TABLE point_history (   member_id BIGINT,   seq_no INT,   points BIGINT,   reason T…` | **INFO** TYPE: column member_id: NUMBER(10) -> BIGINT (exact, fits 64-bit)<br>**INFO** NOT_NULL: column member_id: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column seq_no: NUMBER(6) -> INT (exact, fits 32-bit)<br>**INFO** NOT_NULL: column seq_no: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column points: NUMBER(10) -> BIGINT (exact, fits 64-bit)<br>**INFO** NOT_NULL: column points: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** TYPE: column reason: VARCHAR2(100): length limit is not enforced by ScalarDB TEXT<br>**WARN** TYPE: column created_at: Oracle DATE carries a time-of-day component; use TIMESTAMP if the time part is used<br>**INFO** NOT_NULL: column created_at: NOT NULL dropped (ScalarDB columns are nullable; enforce in the application)<br>**INFO** KEYS: primary key ['member_id', 'seq_no']: first column 'member_id' used as partition key, ['seq_no'] as clustering key(s). Review with --keys if a different split is needed |
| 3 | CREATE | ✅ OK | `CREATE INDEX idx_members_rank ON members (rank)` | `CREATE INDEX ON members (rank)` | **INFO** INDEX: index name 'idx_members_rank' dropped: ScalarDB identifies indexes by table + column |
| 4 | CREATE | ❌ ERROR | `CREATE SEQUENCE member_seq START WITH 1000` | — | **ERROR** DDL: CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB) |
| 5 | INSERT | ✅ OK | `-- ---- 書き込み ---- INSERT INTO members (member_id, name, rank, balance, last_seq, updated_a…` | `/* ---- 書き込み ---- */ INSERT INTO members (member_id, name, rank, balance, last_seq, update…` | **INFO** DATE_LIT: VALUES column updated_at: TO_DATE('2026-01-15', 'YYYY-MM-DD') written as the plain literal '2026-01-15' |
| 6 | INSERT | ❌ ERROR | `INSERT INTO members (member_id, name, rank, balance, last_seq, updated_at) VALUES (member_…` | — | **ERROR** SEQUENCE: VALUES column member_id: sequences are not supported; generate keys in the application (e.g. UUID) |
| 7 | UPDATE | ✅ OK | `UPDATE members SET rank = 'GOLD', updated_at = DATE '2026-02-01' WHERE member_id = 1` | `UPDATE members SET rank = 'GOLD', updated_at = '2026-02-01' WHERE member_id = 1` | **INFO** DATE_LIT: SET updated_at: TO_DATE('2026-02-01', 'YYYY-MM-DD') written as the plain literal '2026-02-01'<br>**INFO** ACCESS: UPDATE: full primary key specified -> GET (single record) |
| 8 | UPDATE | ❌ ERROR | `UPDATE members SET balance = balance + 100 WHERE member_id = 1` | — | **ERROR** RMW: SET balance = balance + 100: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction |
| 9 | DELETE | ✅ OK | `DELETE FROM point_history WHERE member_id = 1 AND seq_no = 3` | `DELETE FROM point_history WHERE member_id = 1 AND seq_no = 3` | **INFO** ACCESS: DELETE: full primary key specified -> GET (single record) |
| 10 | MERGE | ⚠️ WARN | `MERGE INTO members m USING (SELECT 2 AS member_id, 'Tanaka' AS name FROM dual) s    ON (m.…` | `UPSERT INTO members (member_id, name, rank, balance, last_seq) VALUES (2, 'Tanaka', 'REGUL…` | **WARN** MERGE: MERGE rewritten as UPSERT: on an existing row this also overwrites ['rank', 'balance', 'last_seq'], which WHEN MATCHED does not set (it sets ['name']) |
| 11 | SELECT | ✅ OK | `-- ---- 読み取り ---- SELECT member_id, name, balance FROM members WHERE member_id = 1` | `/* ---- 読み取り ---- */ SELECT member_id, name, balance FROM members WHERE member_id = 1` | **INFO** ACCESS: SELECT: full primary key specified -> GET (single record) |
| 12 | SELECT | ✅ OK | `SELECT seq_no, points, reason FROM point_history WHERE member_id = 1 AND seq_no >= 10 ORDE…` | `SELECT seq_no, points, reason FROM point_history WHERE member_id = 1 AND seq_no >= 10 ORDE…` | **INFO** ACCESS: SELECT: full partition key specified -> partition SCAN |
| 13 | SELECT | ✅ OK | `SELECT member_id, name FROM members WHERE rank = 'GOLD'` | `SELECT member_id, name FROM members WHERE rank = 'GOLD'` | **INFO** ACCESS: SELECT: equality on secondary index -> index SCAN |
| 14 | SELECT | ⚠️ WARN | `-- ROWNUM は ORDER BY より先に効く（任意の 3 行を取ってから並べる）。変換後の LIMIT は並べたあとに効くので、結果が変わる SELECT member_…` | `/* ROWNUM は ORDER BY より先に効く（任意の 3 行を取ってから並べる）。変換後の LIMIT は並べたあとに効くので、結果が変わる */ SELECT memb…` | **WARN** ROWNUM: 'ROWNUM <= 3' rewritten to LIMIT 3. Note: Oracle applies ROWNUM before ORDER BY, ScalarDB LIMIT applies after ORDER BY<br>**WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['member_id'] of members -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 15 | SELECT | ⚠️ WARN | `-- 「残高の多い 3 人」のつもりなら、Oracle でもこう書く。こちらは同じ結果になる SELECT member_id, name, balance FROM member…` | `/* 「残高の多い 3 人」のつもりなら、Oracle でもこう書く。こちらは同じ結果になる */ SELECT member_id, name, balance FROM mem…` | **INFO** LIMIT: FETCH FIRST 3 ROWS ONLY rewritten to LIMIT 3<br>**WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['member_id'] of members -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 16 | SELECT | 🧩 PLANNED | `SELECT member_id, NVL(name, '(no name)') AS name, balance FROM members WHERE member_id = 1` | — | **ERROR** PROJECTION: main query: expressions in the select list (NVL(name, '(no name)')) -- compute them in the application<br>**INFO** PLAN_FETCH: GET: SELECT member_id, name, balance FROM members WHERE member_id = 1<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off) |
| 17 | SELECT | 🧩 PLANNED | `SELECT m.member_id, m.name, h.seq_no, h.points   FROM members m, point_history h  WHERE m.…` | — | **WARN** ORACLE_JOIN_MARK: Oracle (+) outer join rewritten as LEFT/RIGHT OUTER JOIN<br>**ERROR** JOIN_KEY: join on point_history does not cover its full primary key ['member_id', 'seq_no'] or a secondary index; ScalarDB rejects this join (DB-SQL-10067)<br>**INFO** PLAN_FETCH: GET: SELECT member_id, name FROM members WHERE member_id = 1<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT member_id, seq_no, points FROM point_history<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P8, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |
| 18 | SELECT | ⚠️ WARN | `SELECT rank, COUNT(*) AS members, SUM(balance) AS total FROM members GROUP BY rank` | `SELECT rank, COUNT(*) AS members, SUM(balance) AS total FROM members GROUP BY rank` | **WARN** CROSS_PARTITION: SELECT: predicates do not cover the partition key ['member_id'] of members -> cross-partition SCAN (requires scalar.db.cross_partition_scan.enabled; filtering/ordering across partitions is only recommended on JDBC backends) |
| 19 | SELECT | 🧩 PLANNED | `SELECT member_id, points,        SUM(points) OVER (PARTITION BY member_id ORDER BY seq_no)…` | — | **ERROR** PROJECTION: projection 'SUM(points) OVER (PARTITION BY member_id ORDER BY seq_no)' is an expression; ScalarDB SQL only selects columns and aggregates. Compute it in the application<br>**ERROR** WINDOW: main query: window functions SUM OVER -- partition, sort and compute in the application (appside.Windows)<br>**INFO** PLAN_FETCH: PARTITION_SCAN: SELECT member_id, seq_no, points FROM point_history WHERE member_id = 1<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off) |
| 20 | SELECT | 🧩 PLANNED | `SELECT member_id, name, balance FROM members WHERE balance > (SELECT AVG(balance) FROM mem…` | — | **ERROR** SUBQUERY: main query: subquery in WHERE -- fetch the inner result first and bind its values<br>**INFO** PLAN_FETCH: CROSS_PARTITION: SELECT member_id, name, balance FROM members<br>**INFO** PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P5, H2 indexes off)<br>**WARN** PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan |

## 指摘の集計

| 重要度 | コード | 件数 |
|---|---|---|
| ERROR | PROJECTION | 2 |
| ERROR | DDL | 1 |
| ERROR | SEQUENCE | 1 |
| ERROR | RMW | 1 |
| ERROR | JOIN_KEY | 1 |
| ERROR | WINDOW | 1 |
| ERROR | SUBQUERY | 1 |
| WARN | CROSS_PARTITION | 3 |
| WARN | TYPE | 2 |
| WARN | PLAN_CROSS_PARTITION | 2 |
| WARN | DEFAULT | 1 |
| WARN | MERGE | 1 |
| WARN | ROWNUM | 1 |
| WARN | ORACLE_JOIN_MARK | 1 |
| INFO | TYPE | 9 |
| INFO | NOT_NULL | 9 |
| INFO | CONFIG | 7 |
| INFO | ACCESS | 5 |
| INFO | COST | 5 |
| INFO | PLAN_FETCH | 5 |
| INFO | PLAN_RESIDUAL | 4 |
| INFO | DATE_LIT | 2 |
| INFO | KEYS | 1 |
| INFO | INDEX | 1 |
| INFO | LIMIT | 1 |

## アプリ側に移す処理

### #4 ❌ ERROR `CREATE SEQUENCE member_seq START WITH 1000`

**アプリ側で処理する構文**

- `DDL` CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)

### #6 ❌ ERROR `INSERT INTO members (member_id, name, rank, balance, last_seq, updated…`

**アプリ側で処理する構文**

- `SEQUENCE` VALUES column member_id: sequences are not supported; generate keys in the application (e.g. UUID)

### #8 ❌ ERROR `UPDATE members SET balance = balance + 100 WHERE member_id = 1`

**アプリ側で処理する構文**

- `RMW` SET balance = balance + 100: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction

### #14 ⚠️ WARN `-- ROWNUM は ORDER BY より先に効く（任意の 3 行を取ってから並べる）。変換後の LIMIT は並べたあとに効くので、結…`

**取得コストの見積もり**

- `COST` full scan of members (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #15 ⚠️ WARN `-- 「残高の多い 3 人」のつもりなら、Oracle でもこう書く。こちらは同じ結果になる SELECT member_id, name,…`

**取得コストの見積もり**

- `COST` full scan of members (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #16 🧩 PLANNED `SELECT member_id, NVL(name, '(no name)') AS name, balance FROM members…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` main query: expressions in the select list (NVL(name, '(no name)')) -- compute them in the application

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+)

### #17 🧩 PLANNED `SELECT m.member_id, m.name, h.seq_no, h.points   FROM members m, point…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `JOIN_KEY` join on point_history does not cover its full primary key ['member_id', 'seq_no'] or a secondary index; ScalarDB rejects this join (DB-SQL-10067)

**取得コストの見積もり**

- `COST` full scan of point_history (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #18 ⚠️ WARN `SELECT rank, COUNT(*) AS members, SUM(balance) AS total FROM members G…`

**取得コストの見積もり**

- `COST` full scan of members (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #19 🧩 PLANNED `SELECT member_id, points,        SUM(points) OVER (PARTITION BY member…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `PROJECTION` projection 'SUM(points) OVER (PARTITION BY member_id ORDER BY seq_no)' is an expression; ScalarDB SQL only selects columns and aggregates. Compute it in the application
- `WINDOW` main query: window functions SUM OVER -- partition, sort and compute in the application (appside.Windows)

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

### #20 🧩 PLANNED `SELECT member_id, name, balance FROM members WHERE balance > (SELECT A…`

実行計画あり: ScalarDB から行を取得し、元の SQL を H2 で実行する（計画の JSON は --plan-dir の出力）。

**アプリ側で処理する構文**

- `SUBQUERY` main query: subquery in WHERE -- fetch the inner result first and bind its values

**取得コストの見積もり**

- `COST` full scan of members (~25 us per row); pass --expected-rows table=N for an estimate

**推奨設定**

- `CONFIG` read-only (DistributedTransactionManager.beginReadOnly / SqlSession.beginReadOnly, 3.16+); scalar.db.scan_fetch_size=1000; scalar.db.cluster.client.scan_fetch_size=1000; scalar.db.cross_partition_scan.enabled=True; SERIALIZABLE re-executes every scan at commit; SNAPSHOT or READ_COMMITTED avoids it but applies to the whole node

