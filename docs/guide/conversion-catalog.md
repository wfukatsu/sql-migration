# SQL と PL/SQL の変換の一覧

SQL と PL/SQL の各項目（構文・関数・データ型・文・例外など）が、変換でどうなるかを項目ごとに引ける一覧です。

- **SQL:** 1 文ずつ ScalarDB SQL に変換します（`scalardb_migrate/`）。変換できない読み取り文は実行計画に分け、それもできなければアプリ側に移します。
- **PL/SQL:** Java に変換します（`plsql/`）。中の SQL 文は、SQL の変換器を通して Repository のメソッドにします。

使い方とコマンドは [SQL の変換と実行計画](sql-conversion.md) と [PL/SQL → Java 変換](plsql-conversion.md)、仕組みは
[アーキテクチャと仕組み](../design/architecture.md) にあります。この一覧は 2026-09-27 のコードから取り、変換器と生成器に実際に通して確かめました。
コードと食い違う所を見つけたら、コードが正です。いまの動きに不具合があるものは、表の注意の欄に「現状は」と書き、[最後の節](#既知の不具合2026-09-27-時点)にもまとめています。

目次

- [SQL](#sql): [判定の意味](#判定の意味) / [データ型](#データ型) / [DDL](#ddl表索引名前空間) / [SELECT の句](#select-の句) / [アクセスパス](#アクセスパス表定義があるとき) /
  [結合と副問い合わせ](#結合と副問い合わせ) / [集約・ウィンドウ](#集約ウィンドウ) / [関数](#関数) / [日付・時刻のリテラル](#日付時刻のリテラル) /
  [階層問い合わせ・集合演算](#階層問い合わせ集合演算) / [INSERT / UPDATE / DELETE / MERGE](#insert--update--delete--merge) / [ROWNUM・ページング](#rownumページング) /
  [シーケンス](#シーケンス) / [名前](#名前識別子) / [方言ごとの差](#方言ごとの差) / [アプリ側に移す処理](#アプリ側に移す処理) / [断るもの](#断るerrorもの)
- [PL/SQL](#plsql): [判定](#判定auto--review--redesignの意味) / [単位](#単位packageprocedurefunctiontrigger) / [データ型](#データ型-1) / [式と演算子](#式と演算子) /
  [組み込み関数](#組み込み関数式の中) / [制御構造](#制御構造) / [SQL 文](#sql-文) / [cursor](#cursor) / [コレクション](#コレクション) /
  [BULK COLLECT / FORALL](#bulk-collect--forall--save-exceptions) / [例外](#例外) / [トランザクション](#トランザクション) / [動的 SQL](#動的-sql) /
  [組み込みパッケージ](#組み込みパッケージ) / [package の状態](#package-の状態) / [trigger](#trigger) / [条件付きコンパイル](#条件付きコンパイル) / [DB link](#db-link) /
  [行ロック](#行ロックfor-update) / [limits.yaml](#プロジェクトの決定limitsyamlと生成物) / [ルール ID ごと](#ルールが-review--redesign-にするものルール-id-ごと) / [まだ変換しないもの](#まだ変換しないもの)

## SQL

SQL 文の各項目を、変換器（`scalardb_migrate/`）がどう扱うかの一覧です。移行元は Oracle・PostgreSQL・MySQL です。
表の「元の書き方」は、説明のために書いた短い例です。表 `employees`（主キー `emp_id`）と `orders`（主キー `customer_id, order_no`）を使います。

### 判定の意味

文ごとに 4 つの判定のどれかが付きます。判定は、その文に付いた指摘の重さで決まります。

| 判定 | 決まり方 | 次にやること |
|---|---|---|
| OK | ScalarDB SQL に変換できた。指摘は INFO だけ | そのまま使う |
| WARN | 変換できたが、WARN の指摘がある | 指摘を読み、意味や性能の差を確かめる |
| PLANNED | ERROR の指摘がある読み取り文で、実行計画（ScalarDB から取得して H2 で元の SQL を実行）に分けられた | 取得する行数と応答時間を確かめる |
| ERROR | 自動では移行できない | 指摘の対応案に沿って書き直すか、アプリで実装する |

読み方の約束:

- 表の「判定」列は、その項目だけを含む文の判定です。同じ文にほかの項目があれば、重いほうの判定になります。
- 「PLANNED（`PROJECTION`）」は、ERROR の指摘 `PROJECTION` が出るが、読み取り文なので実行計画になる、という意味です。書き込み文なら同じ指摘で ERROR です。
- 実行計画を作るのは SELECT（集合演算・WITH を含む）だけです。`scalardb_migrate.cli` は既定で作ります（`--no-plan` で止める）。sql-transpile スキルの `transpile.py` は `--plan-dir` を付けたときだけ作ります。
- 表定義（入力の `CREATE TABLE` か `--schema`）が無い表は、アクセスパスと結合のキーを調べません（INFO `SCHEMA`）。この場合、下の「アクセスパス」の WARN は出ません。

### データ型

`CREATE TABLE` と `ALTER TABLE` の列の型を、ScalarDB の 11 型に対応させます。指摘コードはすべて `TYPE` です。ERROR の型があると、その `CREATE TABLE` は変換しません。

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `NUMBER(p)` / `NUMERIC(p)` / `DECIMAL(p)`、p ≤ 9 | INT | OK（INFO `TYPE`） | 正確に収まる |
| 同上、10 ≤ p ≤ 18 | BIGINT | OK（INFO `TYPE`） | 正確に収まる |
| 同上、p ≥ 19 | BIGINT | WARN `TYPE` | 64 ビットを超える値はあふれる |
| `NUMBER(p, s)` / `DECIMAL(p, s)`、s > 0 | DOUBLE | WARN `TYPE` | 精度が落ちる。金額は 10^s 倍した整数を BIGINT に持つことを勧める。実行計画の H2 では `NUMERIC(p, s)` として扱う |
| Oracle の精度なし `NUMBER`、PostgreSQL の精度なし `NUMERIC` | DOUBLE | WARN `TYPE` | 桁数無制限の 10 進数は保てない |
| MySQL の精度なし `DECIMAL` | BIGINT | OK（INFO `TYPE`） | MySQL の既定 `(10, 0)` として読む |
| PostgreSQL の `MONEY` | DOUBLE | WARN `TYPE` | 精度なし NUMERIC と同じ扱い |
| Oracle の `INTEGER` / `INT` / `SMALLINT` | BIGINT | WARN `TYPE` | Oracle では `NUMBER(38)` の別名。正確に写すなら `NUMBER(p)` で宣言する |
| PostgreSQL・MySQL の `SMALLINT` / `INT` / `INTEGER`、MySQL の `TINYINT` / `MEDIUMINT` | INT | OK | |
| MySQL の `TINYINT(1)` | INT | WARN `TYPE` | 真偽値に使っているなら BOOLEAN を検討する |
| `BIGINT` | BIGINT | OK | |
| MySQL の `INT UNSIGNED` | BIGINT | OK（INFO `TYPE`） | 符号なし 32 ビットは BIGINT に収まる |
| MySQL の `BIGINT UNSIGNED` | BIGINT | WARN `TYPE` | 符号なし 64 ビットの範囲は収まらない |
| Oracle の `FLOAT` | DOUBLE | WARN `TYPE` | Oracle の FLOAT は最大 38 桁の 10 進数。DOUBLE は約 15 桁 |
| Oracle の `BINARY_FLOAT` | DOUBLE | WARN `TYPE` | 指摘文は Oracle の FLOAT と同じものが出る |
| Oracle の `BINARY_DOUBLE`、`DOUBLE` / `DOUBLE PRECISION` | DOUBLE | OK | |
| PostgreSQL の `REAL` / `FLOAT` | DOUBLE | OK | 精度を書いた `FLOAT(10)` も DOUBLE になる |
| MySQL の `FLOAT` | FLOAT | OK | |
| `VARCHAR2(n)` / `NVARCHAR2(n)` / `VARCHAR(n)` | TEXT | OK（INFO `TYPE`） | 長さの上限は ScalarDB では守られない |
| `TEXT` / `CLOB` / MySQL の `LONGTEXT` など | TEXT | OK | |
| `CHAR(1)` | TEXT | OK（INFO `TYPE`） | |
| `CHAR(n)` / `NCHAR(n)`、n > 1 | TEXT | WARN `TYPE` | 空白詰めの値は、移行時に trim しないと `=` で当たらなくなる |
| Oracle の `NCLOB` | なし | ERROR `TYPE` | 対応する型が無いと判定される |
| `BLOB` / `BYTEA` / `BINARY(n)` / `VARBINARY(n)` | BLOB | OK | |
| Oracle の `RAW(n)` | なし | ERROR `TYPE` | 現状は対応する型が無いと判定される |
| `BOOLEAN`、`BIT(1)` | BOOLEAN | OK | |
| `BIT(n)`、n > 1 | BLOB | WARN `TYPE` | |
| Oracle の `DATE` | DATE | WARN `TYPE` | Oracle の DATE は時刻を持つ。時刻を使う列は TIMESTAMP にする |
| PostgreSQL・MySQL の `DATE` | DATE | OK | |
| `TIME` | TIME | OK（INFO `TYPE`） | マイクロ秒まで |
| PostgreSQL の `TIMETZ` | TIME | WARN `TYPE` | 時差が落ちる |
| `TIMESTAMP(p)` / MySQL の `DATETIME(p)`、p ≤ 3 | TIMESTAMP | OK | |
| 同上、p ≥ 4 か精度なし（Oracle・PostgreSQL の既定は 6） | TIMESTAMP | WARN `TYPE` | ミリ秒まで。MySQL の精度なしは 0 として読むので OK |
| MySQL の `TIMESTAMP` | TIMESTAMPTZ | OK（INFO `TYPE`） | UTC で持つ |
| `TIMESTAMP WITH TIME ZONE` / `WITH LOCAL TIME ZONE` / `TIMESTAMPTZ` | TIMESTAMPTZ | 精度 3 以下は OK、それ以外は WARN `TYPE` | UTC で持ち、ミリ秒まで |
| `JSON` / `JSONB` / `UUID` / `ENUM` / `SET` / `INET`、PostgreSQL の `XML` | TEXT | WARN `TYPE` | 文字列として入る。JSON の演算子や列挙の検査は使えない |
| Oracle の `XMLTYPE` | なし | ERROR `TYPE` | |
| `SERIAL` / `BIGSERIAL` / `SMALLSERIAL` | なし | ERROR `TYPE` | 自動採番は無い。ID はアプリで作る |
| `INTERVAL`、配列（`INT[]`）、MySQL の `YEAR` / `GEOMETRY`、Oracle の `ROWID` 型 | なし | ERROR `TYPE` | 対応する型が無い |

### DDL（表・索引・名前空間）

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| 1 列の主キー `emp_id NUMBER(9) PRIMARY KEY` | その列をパーティションキー | OK | |
| 複合主キー `PRIMARY KEY (customer_id, order_no)` | 先頭をパーティションキー、残りをクラスタリングキー | OK（INFO `KEYS`） | `--keys orders=customer_id/order_no` で分け方を変えられる |
| `--keys` が無い列を指す | 変換しない | ERROR `KEYS` | |
| 主キーの無い表 | 変換しない | ERROR `PK` | 主キーを足すか `--keys` を渡す |
| `NOT NULL` | 落とす | OK（INFO `NOT_NULL`） | アプリで守る |
| `DEFAULT 0` | 落とす | WARN `DEFAULT` | 値はアプリが入れる |
| `UNIQUE`（列・表の制約） | 落とす | WARN `UNIQUE` | 副次索引は一意性を守らない |
| `CHECK (...)` | 落とす | WARN `CHECK` | |
| `REFERENCES` / `FOREIGN KEY` | 落とす | WARN `FK` | 参照整合性は無い |
| そのほかの名前つき制約、表の要素 | 落とす | WARN `CONSTRAINT` / WARN `DDL` | |
| `COMMENT` / `COLLATE` / `CHARACTER SET` | 落とす | OK（INFO `COL_OPT`） | |
| そのほかの列の修飾 | 落とす | WARN `COL_OPT` | |
| `ENGINE=InnoDB` などの表のオプション | 落とす | OK（INFO `TABLE_OPTS`） | |
| `AUTO_INCREMENT` / `GENERATED ... AS IDENTITY` | 変換しない | ERROR `AUTO_INC` | 採番はアプリで行う（UUID を TEXT で持つなど） |
| 生成列 `GENERATED ALWAYS AS (expr) STORED` | 変換しない | ERROR `GENERATED` | |
| 一時表 `CREATE GLOBAL TEMPORARY TABLE` / `CREATE TEMPORARY TABLE` | 変換しない | ERROR `TEMP` | |
| `CREATE TABLE ... AS SELECT` / `CREATE TABLE ... LIKE`、型の無い列 | 変換しない | ERROR `DDL` | |
| 列名 `tx_id` などトランザクションの管理に使う名前、キーでない `before_` で始まる列 | 変換しない | ERROR `RESERVED_COLUMN` | `tx_id`・`tx_state`・`tx_version`・`tx_prepared_at`・`tx_committed_at`。移行元で改名する |
| `CREATE INDEX idx ON employees (dept_id)` | `CREATE INDEX ON employees (dept_id)` | OK（INFO `INDEX`） | 索引の名前は落とす |
| 複数列の `CREATE INDEX` | 変換しない | ERROR `INDEX` | ScalarDB の副次索引は 1 列 |
| `CREATE UNIQUE INDEX` | 普通の副次索引 | WARN `UNIQUE` | 一意性は守られない |
| MySQL のインライン `INDEX (dept_id)` | 別の `CREATE INDEX` 文 | OK（INFO `INDEX`） | |
| インラインの複数列の索引 | 落とす | WARN `INDEX` | |
| `DROP INDEX idx` | 変換しない | ERROR `DROP_INDEX` | ScalarDB は `DROP INDEX ON 表 (列)` の形が要る |
| `CREATE SCHEMA` / MySQL の `CREATE DATABASE shop` | `CREATE NAMESPACE shop` | OK | PostgreSQL の `CREATE SCHEMA sales` は現状、名前が空の `CREATE NAMESPACE ""` になる |
| `DROP SCHEMA` / `DROP DATABASE` | — | ERROR `INTERNAL` | 現状は変換器の内部エラーになる。手で `DROP NAMESPACE` を書く |
| `DROP TABLE a, b` / `TRUNCATE TABLE a, b` | 表ごとに 1 文 | OK | |
| `catalog.schema.table`（DDL の中） | `schema.table` | WARN `NAMESPACE` | schema を名前空間にする。SELECT などの中の 3 つ組の名前は、そのまま残る |
| `ALTER TABLE ... ADD COLUMN c VARCHAR(10)`（Oracle は `ADD c ...`） | `ALTER TABLE ... ADD COLUMN c TEXT` | OK | 列の型は上の対応表のとおり。付けた制約は落とし WARN `COL_OPT` |
| Oracle の `ALTER TABLE ... ADD (c ...)`（括弧つき） | 変換しない | ERROR `ALTER` | 括弧を外して 1 列ずつ書く |
| 複数の操作の `ALTER TABLE`（`ADD COLUMN a1 ..., ADD COLUMN a2 ...`） | 1 操作ずつの文 | OK（INFO `ALTER`） | まとめて 1 回ではなくなる |
| `ALTER COLUMN ... TYPE`（PostgreSQL）/ `MODIFY COLUMN`（MySQL） | `ALTER COLUMN ... SET DATA TYPE` | WARN `ALTER_TYPE` | 型を変えられるかは下のデータベース次第 |
| Oracle の `ALTER TABLE ... MODIFY (...)` | 変換しない | ERROR `UNPARSED` | |
| `ALTER TABLE ... DROP COLUMN c` | — | ERROR `INTERNAL` | 現状は変換器の内部エラーになる。Oracle の `DROP (c)` は ERROR `ALTER` |
| `RENAME COLUMN` / `RENAME TO` | そのまま | OK | |
| 制約・索引・パーティションを変える `ALTER TABLE` | 変換しない | ERROR `ALTER` | |

### SELECT の句

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| 射影が列と `*` だけ | そのまま | OK | |
| 射影に式・関数・`CASE`・キャスト（`SELECT salary * 2 ...`） | — | PLANNED（`PROJECTION`） | ScalarDB SQL の射影は列と集約だけ |
| 別名 `SELECT name AS n ... ORDER BY n` | そのまま | OK | |
| `WHERE salary > 10`（列とリテラル・バインド変数） | そのまま | OK | |
| `WHERE 10 < salary` | `salary > 10` | OK | 左右を入れ替える |
| `WHERE dept_id IN (10, 20)` | `dept_id = 10 OR dept_id = 20` | OK（INFO `IN`） | |
| `WHERE dept_id NOT IN (10, 20)` | `dept_id <> 10 AND dept_id <> 20` | OK | |
| `WHERE NOT (salary > 100)` | `salary <= 100` | OK | 比較を反転して押し下げる。反転できなければ ERROR `NOT` |
| `NOT BETWEEN 1 AND 5` | `salary < 1 OR salary > 5` | OK | |
| PostgreSQL の `BETWEEN SYMMETRIC 10 AND 1` | 2 通りの順の BETWEEN を OR | OK | |
| `IS NULL` / `IS NOT NULL` / `NOT LIKE` | そのまま | OK | |
| AND と OR の入れ子（標準形でない） | DNF と CNF の短いほう、括弧つき | OK（INFO `NORMAL_FORM`） | 直せなければ ERROR `NORMAL_FORM` |
| `WHERE salary > bonus`（列どうし） | — | PLANNED（`COL_COL`） | 列どうしは JOIN の ON でしか書けない |
| `WHERE UPPER(name) = 'A'`（関数・演算） | — | PLANNED（`PRED`） | 取得した後で絞る |
| `WHERE salary > 1 + 2`（値が式） | — | PLANNED（`EXPR`） | 値をアプリで計算してバインドする |
| `WHERE emp_id = 1 AND 1 = 1`（定数の条件） | — | PLANNED（`PRED`） | |
| Oracle の `LIKE`（パターンに `\` があるか、バインド変数） | 末尾に `ESCAPE ''` | OK（INFO `LIKE`） | Oracle には既定のエスケープ文字が無く、ScalarDB は `\` |
| `LIKE ... ESCAPE '!'` | そのまま | OK | |
| PostgreSQL の `ILIKE` / `NOT ILIKE` | `LIKE` / `NOT LIKE` | WARN `ILIKE` | 大文字小文字を区別しない一致は失われる |
| `GROUP BY dept_id` / `HAVING COUNT(*) > 2` | そのまま | OK | HAVING の左辺には集約を書ける |
| `GROUP BY UPPER(name)`（列でない） | — | PLANNED（`GROUP`） | |
| `ORDER BY name` / `ORDER BY COUNT(*)` | そのまま | OK | |
| `ORDER BY UPPER(name)`（式） | — | PLANNED（`ORDER`） | |
| `ORDER BY name NULLS FIRST` | `NULLS` を落とす | WARN `NULLS` | NULL の並ぶ位置を確かめる |
| `FOR UPDATE` / MySQL の `LOCK IN SHARE MODE` | 落とす | WARN `LOCK` | 行ロックに頼る処理は、commit 時の衝突と再試行に変わる |
| オプティマイザヒント `/*+ ... */`、MySQL の `USE INDEX (...)` | 落とす | OK（INFO `HINT`） | |
| MySQL の `SQL_NO_CACHE` / `STRAIGHT_JOIN` | 落とす | OK（INFO `MODIFIER`） | |
| MySQL の `SQL_CALC_FOUND_ROWS` | 落とす | WARN `MODIFIER` | 後の `SELECT FOUND_ROWS()` には別に `COUNT(*)` が要る |
| PostgreSQL の `FROM ONLY employees` | `ONLY` を落とす | WARN `ONLY` | 子の表の行を移さない |
| `SAMPLE (10)` / `TABLESAMPLE` | — | ERROR `CLAUSE`（`RESIDUAL_H2`） | H2 でも実行できない |
| `QUALIFY` / `WINDOW` / `INTO` などの句 | — | ERROR `CLAUSE` | 読み取り文なら実行計画を試す |
| Oracle の `AS OF TIMESTAMP ...` | — | ERROR `PARSE` | 解析できない |
| PostgreSQL の `$1`、JDBC の `?`、`:name` | `?` / `?` / `:name` | OK | 書き換えで `?` の順か数が変わると WARN `BIND_ORDER`（新しい順を指摘文に出す） |

### アクセスパス（表定義があるとき）

SELECT・UPDATE・DELETE ごとに、ScalarDB がどう読むかを判定します。`--storage` の既定は `jdbc` です。

| 条件（例） | 読み方 | 判定と指摘コード | 注意 |
|---|---|---|---|
| 主キーのすべての列を `=` で指定（`WHERE emp_id = 1`） | GET | OK（INFO `ACCESS`） | |
| パーティションキーを `=` で指定（`WHERE customer_id = 1`） | パーティション SCAN | OK（INFO `ACCESS`） | |
| 上に加えて、ORDER BY がクラスタリングキーの先頭からの並び | パーティション SCAN | OK | |
| 上に加えて、ORDER BY がクラスタリングキーの先頭からの並びでない（`ORDER BY amount`） | 順序つきの SCAN | WARN `ORDER` | JDBC のバックエンドだけで動く |
| 副次索引の列を `=` で指定し、ORDER BY が無い | 索引 SCAN | OK（INFO `ACCESS`） | |
| キーを覆う条件が無い、最上位が OR | クロスパーティション SCAN | WARN `CROSS_PARTITION` | `scalar.db.cross_partition_scan.enabled` が要る |
| WHERE の無い UPDATE / DELETE | クロスパーティション SCAN | WARN `NO_WHERE` | |
| `--storage cassandra` で、キーで絞れない SELECT | キーで取得してアプリで処理 | ERROR `NO_CROSS_PARTITION`、取得もできなければ `FULL_SCAN` | `FULL_SCAN` は、どの表から読めばキーで読めるかを示す |
| `--storage cassandra` で、キーの OR / IN（`WHERE emp_id IN (1, 2)`） | キーごとの GET | PLANNED（`OR_KEYS`） | |
| `--storage cassandra` で、順序つきの走査が要る ORDER BY | 取得してアプリで並べる | PLANNED（`ORDER_STORAGE`） | |
| `--storage cassandra` で、キーで絞れない UPDATE / DELETE | — | ERROR `NO_CROSS_PARTITION` | 先にキーを読み、主キー指定で書く |

### 結合と副問い合わせ

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `JOIN ... ON d.dept_id = e.dept_id`（相手の主キー全体か副次索引を覆う） | そのまま | OK | |
| `LEFT JOIN` | そのまま | OK | |
| `RIGHT JOIN`（最初の結合） | そのまま | OK | WHERE と ORDER BY は結合先の表の列だけ |
| 2 つ目以降の `RIGHT JOIN` | — | PLANNED（`JOIN`） | |
| 結合が相手の主キー全体も副次索引も覆わない | — | PLANNED（`JOIN_KEY`） | ScalarDB Cluster が DB-SQL-10067 で断る形 |
| ON が `列 = 列` の AND でない（`AND o.amount > 5`） | — | PLANNED（`JOIN_ON`） | |
| WHERE / ORDER BY が結合先の表の列を指す | — | PLANNED（`JOIN_SCOPE`） | 2 表の INNER JOIN で、指すのが結合先の表だけなら下の入れ替えになる |
| 2 表の INNER JOIN で、WHERE が結合先の表だけを指す | FROM と JOIN の表を入れ替える | OK（INFO `JOIN_ORDER`） | `SELECT *` は元の列順に展開する |
| `JOIN ... USING (dept_id)` | `JOIN ... ON e.dept_id = d.dept_id` | OK（INFO `JOIN`） | 結合した列は、行が全部残る側の表で修飾する |
| `FROM employees e, orders o WHERE e.emp_id = o.customer_id` | `INNER JOIN ... ON ...` | WARN `COMMA_JOIN` | |
| 結合条件の無いカンマ結合 | — | PLANNED（`JOIN`） | |
| Oracle の外部結合 `e.emp_id = o.customer_id(+)` | `LEFT JOIN`（向きで RIGHT） | WARN `ORACLE_JOIN_MARK` | 書き換えられない形は ERROR `ORACLE_JOIN_MARK` |
| `CROSS JOIN` / `NATURAL JOIN` | — | PLANNED（`JOIN`） | |
| `FULL OUTER JOIN` | — | ERROR `JOIN`（`RESIDUAL_H2`） | H2 に FULL JOIN が無い。LEFT と RIGHT を UNION するか、アプリで突き合わせる |
| `LATERAL` / `CROSS APPLY` | — | ERROR（`RESIDUAL_H2`） | 外側の行ごとに内側をアプリで実行する |
| `WHERE dept_id IN (SELECT ...)` / `EXISTS (...)` | — | PLANNED（`SUBQUERY`） | |
| `SET name = (SELECT ...)`（UPDATE の値） | — | ERROR `RMW` | 先に読んで値をリテラルで書く |
| `FROM (SELECT ...)`（派生表） | — | PLANNED（`FROM`・`SUBQUERY`） | |
| `WITH t AS (...) SELECT ...` | — | PLANNED（`CTE`） | 自分を参照する WITH は、H2 向けに `WITH RECURSIVE` を補う |

### 集約・ウィンドウ

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `COUNT(*)` / `COUNT(1)` / `COUNT(col)` / `SUM` / `AVG` / `MIN` / `MAX`（引数が列） | そのまま | OK | |
| `COUNT(DISTINCT dept_id)` | — | PLANNED（`AGG_DISTINCT`） | |
| `SUM(salary * 2)`（引数が式） | — | PLANNED（`AGG`） | |
| そのほかの集約（`LISTAGG`、`STDDEV` など） | — | PLANNED（`AGG`） | |
| PostgreSQL の `COUNT(*) FILTER (WHERE ...)` | — | PLANNED（`PROJECTION`） | |
| `SELECT DISTINCT` / PostgreSQL の `DISTINCT ON` | — | PLANNED（`DISTINCT`） | |
| `RANK() OVER (...)` などのウィンドウ関数 | — | PLANNED（`WINDOW`） | H2 が元の SQL を実行する |
| `MAX(x) KEEP (DENSE_RANK FIRST ORDER BY y)` | — | ERROR `KEEP`（`RESIDUAL_H2`） | `ROW_NUMBER() OVER (...) = 1` の行を選ぶ形に書き直す |
| `GROUP BY ROLLUP` / `CUBE` / `GROUPING SETS`、MySQL の `WITH ROLLUP` | — | ERROR `GROUP`（`RESIDUAL_H2`） | 集約のレベルごとの SELECT を `UNION ALL` |
| `PIVOT` / `UNPIVOT` | — | ERROR `PIVOT`（`RESIDUAL_H2`） | 条件つき集約か `UNION ALL` に書き直す |

### 関数

関数は、種類ではなく書いた場所で扱いが決まります。ScalarDB SQL が書ける関数は、射影と HAVING・ORDER BY の集約（`COUNT` / `SUM` / `AVG` / `MIN` / `MAX`）だけです。

| 書いた場所 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| 射影（`SELECT UPPER(name) ...`） | — | PLANNED（`PROJECTION`） | |
| WHERE / HAVING（`WHERE NVL(salary, 0) > 1`） | — | PLANNED（`PRED`） | |
| GROUP BY / ORDER BY | — | PLANNED（`GROUP` / `ORDER`） | |
| INSERT の VALUES・UPDATE の SET（`VALUES (1, UPPER('a'))`） | — | ERROR `EXPR` | 値をアプリで計算してバインドする |

種類ごとの例と、実行計画やアプリ側で気を付けることです。判定は上の表のとおり、書いた場所で決まります。

| 種類 | 元の書き方（例） | 注意 |
|---|---|---|
| 文字列 | `SUBSTR`、`UPPER`、`LENGTH`、連結 `\|\|` | Oracle の空文字列 `''` は NULL（変換できた文にも WARN `SEMANTICS`）。MySQL の文字列比較は大文字小文字を区別しない（INFO `SEMANTICS`） |
| 数値 | `ROUND(salary)`、`salary / 2` | 丸めは 0 から遠いほうへ（-2.5 は -3）。0 で割ったときは方言で違う（Oracle は失敗、MySQL は NULL）。アプリで書くときの注意として `APP_SEMANTICS` に出る |
| 日付 | `ADD_MONTHS(hired, 1)`、`TRUNC(hired, 'MM')`、日付どうしの引き算 | `ADD_MONTHS` は月末をそろえる（`APP_SEMANTICS`）。実行計画では H2 向けに `TRUNC(d, 'MM')` を `DATE_TRUNC('MONTH', d)`、日付どうしの引き算を `DAYS_BETWEEN` に書き換える |
| 書式・変換 | `TO_CHAR(hired, 'YYYY-MM')`、MySQL の `DATE_FORMAT`、`CAST(x AS ...)`、`name::text` | 日付の書式はセッションのタイムゾーンと言語に従う（`APP_SEMANTICS`）。実行計画では MySQL の `DATE_FORMAT` を H2 の `FORMATDATETIME` に書き換える。値の位置の `CAST('5' AS NUMBER)` は ERROR `UNSUPPORTED` |
| NULL の処理 | `NVL`、`COALESCE`、MySQL の `IFNULL` | 集約は NULL を除き、全部 NULL なら NULL（`APP_SEMANTICS`） |
| 条件 | `CASE WHEN ...`、`DECODE` | |
| 現在時刻 | `SYSDATE`、`SYSTIMESTAMP`、`CURRENT_TIMESTAMP`、`CURRENT_DATE`、`NOW()` | WHERE では PLANNED（`NOW`）、値では ERROR `NOW`。時刻はアプリで計算してバインドする。移行元ではデータベースサーバーの時計を使う |
| 採番 | `seq.NEXTVAL`、PostgreSQL の `nextval('seq')` | 下の「シーケンス」 |

### 日付・時刻のリテラル

値は列の型に合わせて ScalarDB が読める形（`'YYYY-MM-DD'`、`'YYYY-MM-DD HH:MM:SS.FFF'`、TIMESTAMPTZ は末尾 `Z`）に直します。WHERE の比較・INSERT の VALUES・UPDATE の SET が対象です。

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `DATE '2024-01-01'`、PostgreSQL の `'2024-01-01'::date` | `'2024-01-01'` | OK（INFO `DATE_LIT`） | |
| `TO_DATE('2024/01/15', 'YYYY/MM/DD')` | `'2024-01-15'` | OK（INFO `DATE_LIT`） | 書式はここで当てる |
| 書式の無い `TO_DATE('2024-01-15')` | `'2024-01-15'` | WARN `DATE_FMT` | 移行元はセッションの日付書式で読む。ISO の形のときだけ通す |
| `TO_DATE('24-01-15', 'RR-MM-DD')`（直せない書式） | — | ERROR `DATE_FMT`（読み取りなら PLANNED） | アプリで変換してバインドする |
| `TO_DATE(:s, 'YYYY-MM-DD')`（値がバインド変数） | — | ERROR `EXPR`（読み取りなら PLANNED） | |
| DATE 列に 0 時の日時 | 日付だけ | OK（INFO `DATE_LIT`） | |
| DATE 列に 0 時以外の日時 | 日付だけ | WARN `DATE_LIT` | 時刻は落ちる |
| TIMESTAMP 列に日付だけ（`DATE '2024-01-01'`） | `'2024-01-01 00:00:00'` | OK（INFO `DATE_LIT`） | |
| TIMESTAMPTZ 列に時差つき（`'... 10:00:00+09:00'`） | UTC の `'2024-01-01 01:00:00 Z'` | OK（INFO `DATE_LIT`） | |
| TIMESTAMPTZ 列に時差なし | UTC と見なして末尾 `Z` | WARN `TZ_ASSUMED_UTC` | `--session-time-zone Asia/Tokyo` を渡すと、その地域の時刻として UTC に直す（INFO `DATE_LIT`） |
| 時差の無い列（DATE / TIMESTAMP）に時差つきの値 | 時差を落とす | WARN `DATE_LIT` | |
| INT / BIGINT 列に `TRUE` / `FALSE` | `1` / `0` | OK（INFO `BOOL_LIT`） | |

### 階層問い合わせ・集合演算

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `START WITH ... CONNECT BY PRIOR ...`、`LEVEL`、`SYS_CONNECT_BY_PATH` | — | ERROR `HIERARCHICAL`（`RESIDUAL_H2`） | H2 でも実行できない。再帰 WITH に書き直すか、アプリで木をたどる（補助クラス `Hierarchy`）。表に事前計算する提案（`DESIGN`）が付く |
| 再帰 WITH（`WITH h (id, lvl) AS (... UNION ALL ...)`） | — | PLANNED（`CTE`・`SET_OP`） | H2 向けに `WITH RECURSIVE` を補う |
| 再帰 WITH の `SEARCH DEPTH / BREADTH FIRST` | — | ERROR（`RESIDUAL_H2`） | 再帰の順はアプリで決める |
| `UNION` / `UNION ALL` / `INTERSECT` / `MINUS` / `EXCEPT` | — | PLANNED（`SET_OP`） | |

### INSERT / UPDATE / DELETE / MERGE

書き込み文は実行計画にしません。ERROR の文は、アプリで読んでからキーを指定して書く形に直します。

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `INSERT INTO employees (emp_id, name) VALUES (1, 'a')` | そのまま | OK | 複数行の VALUES も同じ |
| 列リストの無い INSERT | そのまま | WARN `INSERT_COLS` | ScalarDB は表の定義の順で受ける |
| 主キーの一部が無い INSERT | — | ERROR `PK` | |
| 値が式・関数（`VALUES (4, 1 + 2)`） | — | ERROR `EXPR` | |
| `INSERT ... SELECT` | — | ERROR `INSERT_SELECT` | |
| MySQL の `INSERT IGNORE` | — | ERROR `INSERT_IGNORE` | |
| `INSERT ... RETURNING` / `UPDATE ... RETURNING` | — | ERROR `RETURNING` | 書いた後に読み直す |
| `ON CONFLICT (emp_id) DO UPDATE SET name = EXCLUDED.name` | `UPSERT INTO` | OK（INFO `UPSERT`） | 列リストの全列を更新していれば OK |
| 同上で、一部の列だけを更新 | `UPSERT INTO` | WARN `UPSERT` | UPSERT は列リストのすべてを上書きする |
| MySQL の `ON DUPLICATE KEY UPDATE name = VALUES(name)` | `UPSERT INTO` | OK / WARN `UPSERT` | 判定は上と同じ決まり |
| 更新が `col = EXCLUDED.col` でない、条件つきの DO UPDATE、主キー以外での衝突 | — | ERROR `UPSERT` | 1 つのトランザクションで読んで判断して書く |
| `ON CONFLICT DO NOTHING` | — | ERROR `DO_NOTHING` | |
| MySQL の `REPLACE INTO` | `UPSERT INTO` | WARN `REPLACE` | REPLACE は列リストに無い列を NULL に戻し、UPSERT は残す |
| `UPDATE employees SET name = 'b' WHERE emp_id = 1` | そのまま | OK | SET の値もリテラルの形を列の型に合わせる |
| `SET salary = salary + 1`（列を参照する） | — | ERROR `RMW` | SELECT → 計算 → リテラルで UPDATE を 1 つのトランザクションで |
| 主キーの列を SET | — | ERROR `PK_UPDATE` | DELETE して新しいキーで INSERT |
| 対応外の SET 句 | — | ERROR `SET` | |
| `UPDATE ... FROM` / MySQL の結合つき UPDATE | — | ERROR `UPDATE_JOIN` | |
| `DELETE ... USING` / MySQL の結合つき DELETE | — | ERROR `DELETE_JOIN` | |
| ORDER BY / LIMIT つきの UPDATE・DELETE | — | ERROR `UPDATE` / `DELETE` | 主キーで対象を絞る |
| `DELETE FROM employees WHERE emp_id = 1` | そのまま | OK | WHERE の扱いは SELECT と同じ |
| WHERE の無い UPDATE / DELETE | そのまま | WARN `NO_WHERE` | |
| 定数 1 行をソースにする `MERGE`（両方の枝が同じ列を書く） | `UPSERT INTO` | WARN `MERGE` | |
| 同上で、WHEN MATCHED が書かない列を INSERT が書く | `UPSERT INTO` | WARN `MERGE` | 既存行のその列も上書きされる。指摘文に列名が出る |
| 表・問い合わせをソースにする MERGE、条件つきの枝、`WHEN MATCHED THEN DELETE`、主キー以外での突き合わせ | — | ERROR `MERGE` | 存在確認と書き込みを 1 つのトランザクションにまとめる |
| `TRUNCATE TABLE orders` | そのまま | OK | |

### ROWNUM・ページング

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `WHERE ROWNUM <= 5` / `ROWNUM = 1` | `LIMIT 5` / `LIMIT 1` | WARN `ROWNUM` | Oracle は ORDER BY の前に数え、LIMIT は後に効く |
| `WHERE ROWNUM < 5` | `LIMIT 4` | WARN `ROWNUM` | |
| `WHERE ROWNUM <= :n` | `LIMIT :n` | WARN `ROWNUM` | `<` とバインド変数の組は ERROR |
| 集約・DISTINCT・GROUP BY・ウィンドウ関数と一緒の ROWNUM | — | PLANNED（`ROWNUM`） | ROWNUM は入力の行、LIMIT は出力の行を数える |
| `ROWNUM > 1`、OR の中、LIMIT との併用、整数でない比較 | — | PLANNED（`ROWNUM`） | |
| 射影の `SELECT ROWNUM, name ...` | そのまま | 検査されない | 現状は列として素通りする。ScalarDB では動かない |
| `FETCH FIRST 3 ROWS ONLY` / `FETCH FIRST ROW ONLY` | `LIMIT 3` / `LIMIT 1` | OK（INFO `LIMIT`） | |
| `LIMIT 10` / `LIMIT ?` | そのまま | OK | |
| `FETCH ... WITH TIES` / `FETCH ... PERCENT` | — | PLANNED（`LIMIT`） | LIMIT では同じ順位の行が落ちる |
| `OFFSET 10 ROWS` / `LIMIT 10 OFFSET 5` / MySQL の `LIMIT 5, 10` | — | PLANNED（`OFFSET`） | クラスタリングキーの範囲でページを送る形に直すとよい |

### シーケンス

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `CREATE SEQUENCE` / `DROP SEQUENCE` | — | ERROR `DDL` | |
| 値の `emp_seq.NEXTVAL` / `CURRVAL`、PostgreSQL の `nextval('seq')` | — | ERROR `SEQUENCE` | アプリで採番する（UUID など） |
| `SELECT emp_seq.NEXTVAL FROM dual` | そのまま | 検査されない | 現状は列として素通りし OK になる。ScalarDB では動かない |
| `SERIAL` 型、`AUTO_INCREMENT`、`IDENTITY` | — | ERROR `TYPE` / `AUTO_INC` | 上の「データ型」「DDL」 |

### 名前（識別子）

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| ScalarDB SQL の予約語と同じ名前（`type`、`order`、`key`、型名など） | `"type"` | OK（INFO `IDENT`） | ScalarDB SQL ではすべてのキーワードが予約語 |
| `[A-Za-z_][A-Za-z0-9_]*` に合わない名前 | 二重引用符で囲む | OK（INFO `IDENT`） | |
| MySQL のバッククォート `` `name` `` | `name` | OK | 必要な所だけ引用符を付け直す |
| ORM が付けた引用符 `"status"` | `status` | OK | |
| 方言が畳む形と違う綴りの引用つきの名前（PostgreSQL の `"Mixed"`） | `Mixed` | WARN `IDENT` | ScalarDB は名前を畳まない。すべての参照を同じ綴りで書く |
| 同じ表を `customers` と `Customers` の 2 通りで書いている | そのまま | WARN `IDENT` | |
| 二重引用符を含む名前 | — | ERROR `IDENT` | 移行元で改名する |
| 疑似列 `ROWID` / `ORA_ROWSCN` | — | ERROR `ROWID` | 主キーで行を特定する。実行計画も作らない（INFO `PLAN`） |

### 方言ごとの差

| 方言 | 項目 | 扱い |
|---|---|---|
| Oracle | 空文字列 `''` | 変換できた文に WARN `SEMANTICS`。Oracle は NULL として持つ |
| Oracle | `LIKE` のエスケープ | `ESCAPE ''` を補う（INFO `LIKE`）。実行計画の H2 では `\` を二重にする |
| Oracle | 外部結合 `(+)`、`ROWNUM`、`CONNECT BY`、`KEEP`、`MINUS` | 上の各表 |
| Oracle | 整数型、`FLOAT`、`DATE`、精度なし `NUMBER` | すべて WARN `TYPE`（上の「データ型」） |
| Oracle | `/` だけの行 | 文の切れ目として扱う |
| Oracle | PL/SQL のブロック（`CREATE PROCEDURE` など、`BEGIN` / `DECLARE` の無名ブロック） | ERROR `PLSQL_BLOCK`。PL/SQL の移行ツールで扱う |
| Oracle | `WITH FUNCTION ...`（WITH 句の PL/SQL） | ERROR `WITH_PLSQL`。関数をアプリに移せば、問い合わせは変換か実行計画にできる |
| PostgreSQL | `ILIKE`、`ONLY`、`DISTINCT ON`、`BETWEEN SYMMETRIC`、`FILTER`、`$1` | 上の各表 |
| PostgreSQL | `ON CONFLICT` | `UPSERT INTO` か ERROR（上の書き込みの表） |
| PostgreSQL | 精度なし `NUMERIC`、`SERIAL`、`TIMETZ`、`JSONB` | 上の「データ型」 |
| MySQL | 文字列の比較（`=`・`<>`・`LIKE`・`IN`） | INFO `SEMANTICS`。MySQL の既定は大文字小文字を区別しない。ScalarDB は厳密に比べる |
| MySQL | `REPLACE INTO`、`INSERT IGNORE`、`ON DUPLICATE KEY UPDATE` | 上の書き込みの表 |
| MySQL | `LIMIT 5, 10`、`WITH ROLLUP`、`SQL_CALC_FOUND_ROWS` | 上の各表 |
| MySQL | `TINYINT(1)`、符号なし整数、精度なし `DECIMAL`、`TIMESTAMP` | 上の「データ型」 |
| すべて | NULL の並ぶ位置 | Oracle・PostgreSQL は昇順で NULL が最後、MySQL は最初。実行計画の H2 では `NULLS FIRST / LAST` を明示して元の並びを保つ |

### アプリ側に移す処理

ERROR か PLANNED になった読み取り文には、変換器が最初につまずいた所だけでなく、文全体（WITH の本体、副問い合わせを含む）を調べた結果が付きます。

| 指摘コード | 重要度 | 内容 |
|---|---|---|
| `CTE`・`SUBQUERY`・`SET_OP`・`HIERARCHICAL`・`WINDOW`・`KEEP`・`PIVOT`・`DISTINCT`・`OFFSET`・`PROJECTION`・`GROUP`・`PRED`・`NOW`・`ORDER` | ERROR | アプリに移す構文を、場所（主問い合わせ、WITH の名前、副問い合わせ）つきで全部挙げる |
| `RESIDUAL_H2` | ERROR | H2 が実行できない構文（`CONNECT BY`、`ROLLUP` など、`PIVOT` / `UNPIVOT`、`KEEP`、`FULL OUTER JOIN`、`LATERAL`、`SAMPLE`、再帰 WITH の `SEARCH`、`JSON_TABLE`）。実行計画を作らない |
| `APP_SEMANTICS` | WARN | アプリで書き直すときに結果を変えないための注意（`LAG` / `LEAD`、0 除算、`ROUND`、集約と NULL、順位、NULL と文字列の並び、`SYS_CONNECT_BY_PATH`、`LEVEL`、`ADD_MONTHS`、日付の書式、現在時刻、空文字列）。ERROR の文にだけ付く |
| `DESIGN` | INFO | 設計の提案（表定義を渡す、階層の事前計算、GROUP BY のキーでの集計表、結合列のキーか索引、JDBC 以外ではキーを持たせる、分析の問い合わせには ScalarDB Analytics） |
| `PLAN_FETCH` / `PLAN_RESIDUAL` | INFO | 実行計画の取得 1 つずつと、H2 が元の SQL を実行すること |
| `PLAN_CROSS_PARTITION` / `PLAN_UNRESOLVED` | WARN | 取得にクロスパーティション SCAN が要る / 表か列を解決できない所がある |
| `PLAN` | INFO | 実行計画に分けられなかった理由 |
| `COST` / `CONFIG` | INFO | 取得コストの見積もり（`--expected-rows`）と推奨設定。クロスパーティション SCAN になる変換済みの SELECT にも付く |
| `ROW_LIMIT` | WARN | 取得の見込み行数が上限（既定 1 万行、`--row-limit`）を超える |
| `COST_DEADLINE` | WARN | 見積もりの合計が ScalarDB Cluster の gRPC の期限（60 秒）を超える |

### 断る（ERROR）もの

文の種類として ScalarDB SQL に無いものです。

| 元の書き方 | 判定と指摘コード | 注意 |
|---|---|---|
| `CREATE VIEW` / `CREATE SEQUENCE` / `CREATE FUNCTION`（PostgreSQL）など、表・索引・名前空間以外の `CREATE` | ERROR `DDL` | |
| 表・スキーマ・索引以外の `DROP` | ERROR `DDL` | |
| `CREATE USER`、Oracle の `ALTER TABLE ... MODIFY` など、SQLGlot が文として解析しないもの | ERROR `UNPARSED` | |
| `GRANT`、`SET search_path ...`、MySQL の `SET NAMES`、`SAVEPOINT` | ERROR `STATEMENT` | 名前つきトランザクションとセーブポイントは ERROR `SAVEPOINT` の場合もある |
| PL/SQL のブロック、`WITH FUNCTION` | ERROR `PLSQL_BLOCK` / `WITH_PLSQL` | 上の「方言ごとの差」 |
| 移行元の方言として読めない文 | ERROR `PARSE` | `--source` を確かめる |
| ScalarDB SQL の生成器が出せない構文が残った | ERROR `UNSUPPORTED` | |
| 引用符の閉じ忘れなどで文に分けられない | ERROR `TOKENIZE` | ファイル全体で 1 件 |
| 変換器が想定していなかった文 | ERROR `INTERNAL` | その文だけが ERROR になり、残りは変換を続ける |

逆に、そのまま通る文は `BEGIN` / `START TRANSACTION`（`BEGIN` にする）、`COMMIT`、`ROLLBACK`、`USE shop` です。

## PL/SQL

PL/SQL の各項目が、`plsql/` の生成器でどんな Java になるかを項目ごとにまとめます。SQL 文そのものの変換（方言の書き換え）は SQL の節で扱います。ここでは、PL/SQL の中の SQL 文が Java のどの呼び出しになるかまでを扱います。

内容は `plsql/` と `runtime-java/src/main/` のコードから取りました。小さな PL/SQL を `python -m plsql.generate` に通して確かめた項目には「（生成で確認）」と書いています。

読み方:

- 「生成される Java」の `Plsql.xxx` は、ランタイムの `com.scalar.migrate.plsql.Plsql` の関数です。
- 「判定への影響」のルール ID は `plsql/rules/*.yaml` のものです。「なし」は、その書き方だけでは判定が下がらないという意味です。AUTO になるには、ほかに実 DB での一致の証拠が要ります。
- 「limits.yaml」は、生成器に渡すプロジェクトの決定のファイルです（`--limits`）。

### 判定（AUTO / REVIEW / REDESIGN）の意味

この節と「ルールが REVIEW / REDESIGN にするもの」の節だけは、ほかの節と列が違います。

| 判定 | 意味 | どう決まるか | 注意 |
|---|---|---|---|
| AUTO | 人が見なくても生成してよい | どのルールも止めず、確信度が 0.95 以上で、比べたシナリオがすべて Oracle と一致したとき | 確信度は 5 つの因子の積です（ルールの網羅、シンボル解決、型解決、ScalarDB で実行できるか、テストの証拠）。証拠（`--evidence`）が無いと testEvidence が 0 になり、AUTO になりません |
| REVIEW | 移行できるかが、まだ決まっていない | REVIEW のルールに当たったとき。または確信度の因子に 0 がある、しきい値に届かない、Oracle と違う結果のシナリオが 1 つでもあるとき | 生成はします。生成器が断った文は `UnsupportedOperationException` を投げる Java になります（コンパイルは通ります） |
| REDESIGN | そのまま写してはいけない。設計を決める | REDESIGN のルールに当たったとき | limits.yaml で決めても、判定は REDESIGN のままです。`decisions.json` の `redesign.state` が「決定済み」に変わります |
| 呼び出し先の判定 | 呼ぶ側は呼び先より安全にならない | 呼び出しでたどれる routine のうち一番悪い判定まで下がる | 理由に `calls X, which is REDESIGN` と出ます |
| 判定を下げない注記 | 助言だけ | `decision: AUTO` のルール（DYN-OPT-002、SCAN-002、SELECT-OPT-001、SEM-011、CUR-OPT-002、BULK-OPT-003、TRG-OPT-003） | 設計・運用への推奨です。移行の可否ではありません |
| 古い証拠 | ソースか生成器が変わった | 記録したハッシュと今のハッシュが違う | その routine は REVIEW に戻り、`staleEvidence` に理由が出ます |

生成の結果は `generation-report.json` にも出ます。ルールは AUTO なのに生成器が断った文がある routine は、実行時に `AUTO but not cleanly generated: <routine>` と表示されます（生成で確認）。

### 単位（package・procedure・function・trigger）

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| package（仕様と本体） | `application` に `<Package>Service`、`infrastructure` に `<Package>Repository` の 2 クラス | なし | Service は制御構造と式、Repository は SQL を持ちます（生成で確認） |
| package の公開 routine / 非公開 routine | public メソッド / private メソッド。名前は camelCase（`get_name` → `getName`） | なし | どのメソッドも `throws Exception` です |
| 単体の procedure / function | 1 つの routine だけを持つ `<Name>Service`（`prc_x` → `PrcXService`） | なし | 生成で確認 |
| IN 引数 | メソッドの引数 | なし | |
| OUT / IN OUT 引数 | 結果の record `<Name>Result` を返す。IN OUT は引数でもある | なし | 例外で抜けたときは record を返さないので、呼び出し側に書きかけの値が見えません（生成で確認） |
| function の戻り値 | メソッドの戻り値。OUT 引数もある function は `<Name>Result(returned, ...)` | なし | |
| 引数の既定値（`DEFAULT`） | procedure の呼び出し文では既定値を補って渡す | なし | 式の中の function 呼び出しで既定値を省くと、引数が足りない Java になり javac で落ちます（生成で確認。まだ変換しないものを参照） |
| 名前付き引数（`p_a => 1`） | procedure の呼び出し文では並べ替えて渡す | なし | 式の中の function 呼び出しでは断ります（生成で確認） |
| オーバーロード | 版ごとに番号を付けたメソッド（`fmt` → `fmt1`、`fmt2`） | 呼び出しがどの版か決まらないと CALL-002（REVIEW） | 引数の数と名前だけで選びます。型だけが違う版は選べません（生成で確認） |
| 宣言部の入れ子の procedure / function | 外側の変数を引数で運ぶ private メソッドに持ち上げる | なし | 入れ子のブロックの DECLARE に書いたものは持ち上げず、LOWER-001（REVIEW） |
| 別の package の routine の呼び出し | 呼ばれる側の Service をコンストラクタで受け取って呼ぶ | 呼び先の判定を引き継ぐ | 生成で確認 |
| package の定数（`CONSTANT`） | `private static final` のフィールド | なし | 生成で確認 |
| trigger | `Trg<Name>Service` の `body(...)` メソッド。`:NEW` / `:OLD` の列が引数 | TRG-001（REDESIGN） | trigger の節を参照 |
| 呼び出し仕様（`LANGUAGE JAVA` / `C`、`EXTERNAL`） | 本体を持たず `UnsupportedOperationException` を投げるメソッド | EXT-002（REDESIGN） | 生成で確認 |
| `AUTHID CURRENT_USER` | 生成物は変わらない | AUTHID-001（REDESIGN） | 単体の routine に書いたものは検出します。package の仕様に書いたものは routine に伝わらず、検出されません（生成で確認） |
| トランザクションの境界 | どのメソッドも begin・commit・rollback をしない。Repository は呼び出し側の `Connection` を受け取る | なし | 境界は呼び出し側が持ちます。Spring の注釈も出しません |
| 元のソースの位置 | 各文の前に `// ファイル:行` のコメント | なし | `traceability.csv` にも出ます |

### データ型

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `NUMBER(p)`（p ≤ 9 / ≤ 18 / > 18） | `Integer` / `Long` / `BigDecimal` | なし | 代入は `Plsql.fitInt` / `fitLong` / `fit` で四捨五入し、桁あふれは ORA-06502 |
| `NUMBER(p, s)`（s > 0） | `BigDecimal` | なし | 代入は `Plsql.fit(v, p, s)`（half-up）。ScalarDB には 10^s 倍した BIGINT で置き、`Plsql.bind` / `Plsql.read` で往復します |
| 精度なしの `NUMBER` | `BigDecimal` | なし | 保存形は TEXT。今のデータが long に収まっても long にしません |
| `INTEGER` / `INT` / `SMALLINT` | `BigDecimal` | なし | 変数への代入は `Plsql.fit(v, 38, 0)` で整数に丸めます。引数と戻り値では丸めません |
| `PLS_INTEGER` / `BINARY_INTEGER` / `SIMPLE_INTEGER` / `NATURAL` など | `Integer` | なし | `Plsql.toInt` が 32 ビットを超えると ORA-01426（`Plsql.NumericOverflow`） |
| `BINARY_FLOAT` / `BINARY_DOUBLE`、`FLOAT` / `REAL` | `Float` / `Double` | なし | 文字にするときは Oracle の書き方（`4.0E+000` など）に合わせます |
| `VARCHAR2(n)` / `NVARCHAR2` / `VARCHAR` / `STRING` | `String` | なし | 代入は `Plsql.fit(v, n, 文字単位か)`。長すぎると ORA-06502。`''` は NULL として扱います |
| `CHAR(n)` | `String` | なし | 代入は `Plsql.pad` で空白を詰めます。比較は `Plsql.unpad` で末尾の空白を無視します（生成で確認） |
| `CLOB` / `NCLOB` / `LONG` | `String` | なし | 大きな値の扱いは別に決めます |
| `RAW` / `LONG RAW` / `BLOB` | `byte[]` | なし | String を経由しません |
| `DATE` | `LocalDateTime` | なし | 時刻を持つので `LocalDate` にしません |
| `TIMESTAMP(p)` | `LocalDateTime` | なし | ScalarDB はミリ秒まで |
| `TIMESTAMP WITH (LOCAL) TIME ZONE` | `OffsetDateTime` | なし | UTC で保存します |
| `BOOLEAN` | `Boolean` | なし | NULL を取るので primitive にしません |
| `SUBTYPE s IS 基底型`（routine の中） | 基底型の Java 型 | なし | `RANGE a..b` は `Plsql.inRange`、`NOT NULL` は `Plsql.notNull`（違反は ORA-06502） |
| `SUBTYPE`（package の仕様） | `Object` になる | なし（型解決の因子も下がらない） | 下書き時点では解決されません（生成で確認） |
| `変数 表.列%TYPE` | その列の Oracle の型に対応する Java 型 | なし | `schema.sql`（Oracle の DDL）から引きます |
| `変数 表%ROWTYPE` / `cursor%ROWTYPE` | 生成した record（`EmpRow`、`CEmpRow`） | なし | 列名が record の要素名です。field への代入は record を作り直します（Java の record は不変） |
| `TYPE t IS RECORD (...)` | 生成した record（`TPair`） | なし | 各 field は NULL か既定値で作ります（生成で確認） |
| コレクション型 | コレクションの節を参照 | | |
| `SYS_REFCURSOR` / `REF CURSOR` | cursor の節を参照 | | |
| `CREATE TYPE ... AS OBJECT`（スキーマのオブジェクト型） | 生成した record。`AS TABLE OF` はその `List` | なし | コンストラクタ `t_point(1, 2)` は `new TPoint(...)`（生成で確認） |
| 解決できない型 | `Object` | 型解決の因子が 0 になり REVIEW | 生成器は型を推測しません |

### 式と演算子

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `a = b`、`<>`、`<`、`<=`、`>`、`>=` | `Plsql.eq(a, b)`、`ne`、`lt`、`le`、`gt`、`ge` | なし | どちらかが NULL なら偽です。文字と数値の比較は、文字を数値に直して比べます（`'10' = 10` は真）。数値にならない文字は ORA-06502 |
| `AND` / `OR` | `&&` / `\|\|` | なし | 「真のときだけ true」を返す比較なので、そのまま使えます |
| `NOT 条件` | 否定を比較まで押し込む（`NOT a = b` → `Plsql.ne(a, b)`） | なし | `!` にすると NULL のときに真になるためです |
| BOOLEAN 変数への代入、BOOLEAN の RETURN | `Plsql.bool3(真の式, 偽の式)` | なし | NULL（UNKNOWN）を保ちます（生成で確認） |
| IF / WHILE の条件の素の BOOLEAN | `Plsql.isTrue(v)` / `Plsql.isFalse(v)` | なし | NULL で落ちません |
| `x IS NULL` / `IS NOT NULL` | `Plsql.isNull(x)` / `Plsql.isNotNull(x)` | なし | `''` も NULL です |
| `a \|\| b` | `Plsql.concat(a, b)` | なし | NULL は空文字として扱い、結果が空なら NULL です |
| `+`、`-`、`*`、`/` | `Plsql.add`、`sub`、`mul`、`div` | なし | NULL は NULL を返します。結果は NUMBER と同じく 40 桁に丸めます。0 で割ると ORA-01476（`Plsql.ZeroDivide`） |
| `DATE + n`、`DATE - n`、`DATE - DATE` | `Plsql.add` / `Plsql.sub` | なし | 日数の足し引きです。DATE どうしの差は日数（小数つき）です |
| 単項の `-x` | `Plsql.neg(x)` | なし | |
| `x IN (...)`、`BETWEEN`、`LIKE`（と `NOT`） | `Plsql.in`、`between`、`like`（`notIn` など） | なし | NULL が絡むと真になりません |
| CASE 式 | 三項演算子（`Plsql.eq(p, 1) ? "one" : "many"`） | なし | 生成で確認 |
| 文字列を NUMBER に代入 | `Plsql.dec(...)` | なし | 数値にならないと ORA-06502（`Plsql.ValueError`） |
| 日付・TIMESTAMP を書式なしで文字にする（`'d=' \|\| d`、`TO_CHAR(d)`） | `Plsql.concat` / `Plsql.text` が Oracle の既定（`DD-MON-RR`、AMERICAN）で書く | SEM-012（REVIEW） | 移行元のセッションの NLS 設定までは確かめていません |
| 数値を文字にする | `Plsql.text(n)` | なし | 1 未満は `.5` のように先頭の 0 を書きません（Oracle と同じ） |
| 丸め（`ROUND`） | `Plsql.round`（half-up） | PL/SQL の式ならなし。移行先 DB が評価する SQL の中なら SEM-001（REVIEW） | |
| `CAST(ts AS DATE)` | `Plsql.castDate`（秒未満を切り捨て） | 移行先 DB が評価する SQL の中なら SEM-009（REVIEW） | |
| 空文字・`NVL`・`RTRIM` を含む SQL | 式を SQL の外へ出して bind にできれば、ランタイムが計算 | 移行先 DB がそのまま評価するなら SEM-003（REVIEW） | 書き込みの境界（`Plsql.bind`）は `''` を NULL にして渡します |

### 組み込み関数（式の中）

`plsql/gen_java/expr.py` の対応表にあるものだけを変換します。表に無い名前は推測せず、生成時に `names the translator could not place` に出して、その文で `UnsupportedOperationException` を投げます。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `NVL`、`NVL2`、`COALESCE` | `Plsql.nvl`、`nvl2`、`coalesce` | なし | |
| `ROUND`、`TRUNC`（数値・日付） | `Plsql.round`、`Plsql.trunc` | なし | |
| `MOD`、`ABS`、`POWER`、`SQRT`、`CEIL`、`FLOOR`、`SIGN` | 同じ名前の `Plsql` の関数 | なし | |
| `GREATEST`、`LEAST` | `Plsql.greatest`、`least` | なし | |
| `UPPER`、`LOWER`、`INITCAP`、`LENGTH`、`SUBSTR`、`INSTR`、`REPLACE`、`LPAD`、`RPAD`、`TRIM`、`LTRIM`、`RTRIM`、`CONCAT`、`CHR`、`ASCII` | 同じ名前の `Plsql` の関数 | なし | NULL と空文字は Oracle と同じに扱います |
| `TO_CHAR(日付, 書式)` | `Plsql.text(v, 書式)` | 書式に DAY・MON・AM など言語で変わる要素があると SEM-008（REVIEW） | 対応する書式は `YYYY-MM-DD`、`YYYY-MM-DD HH24:MI:SS`、`YYYYMM`、`YYYY` だけです。ほかは実行時に `UnsupportedOperationException` |
| `TO_NUMBER(v)` | `Plsql.toNumber(v)` | なし | 書式つきの `TO_NUMBER(v, 書式)` は実行時に `UnsupportedOperationException` |
| `TO_DATE(v, 書式)` | `Plsql.toDate(v, 書式)` | なし | 要素ごとに Oracle と同じ読み方をします（桁の少ない数字、RR の世紀、省いた年月は現在） |
| `ADD_MONTHS`、`LAST_DAY` | `Plsql.addMonths`、`lastDay` | なし | |
| `SYSDATE` | `Plsql.sysdate()` | 1 つの routine で時計を 2 回以上読むと SEM-007（REVIEW） | 時計は `Plsql.setClock` で差し替えられます。宣言部の初期値で読んだ分は SEM-007 の回数に入りません（生成で確認） |
| `SYSTIMESTAMP` | `audit.now()`（引数に `AuditContext audit` が足される） | 列へ書くと SEM-010（REVIEW） | `OffsetDateTime` を返すので、TIMESTAMP を返す function の `RETURN SYSTIMESTAMP` は javac で落ちます（生成で確認） |
| `USER` | `audit.user()` | なし | 何を記録するかは業務の決定です（`AuditContext` は呼び出し側が渡します） |
| `seq.NEXTVAL` | `sequences.next("seq")`（Repository が `Sequences` を受け取る） | なし | 採番は業務とは別のトランザクションで取ります。方式は DDL の `CACHE`（hi/lo）/ `NOCACHE`（counters 表と再試行）から決まります |
| `SQLCODE`、`SQLERRM`、`SQLERRM(n)` | 例外の節を参照 | | |
| `CURRENT_DATE`、`CURRENT_TIMESTAMP`、`LOCALTIMESTAMP`、`AT TIME ZONE` | 変換しない | SEM-002（REVIEW） | セッションのタイムゾーンで値が変わります |
| `SYS_GUID` | 変換しない | SEM-014（REVIEW） | |
| `SYS_CONTEXT` | 変換しない | SEM-013（REDESIGN） | |
| `DECODE`、`MONTHS_BETWEEN`、`RAWTOHEX`、`REGEXP_*`、`NULLIF`、`EXTRACT`、`TO_TIMESTAMP` など、対応表に無い関数 | 変換しない（`UnsupportedOperationException`） | ルールは下がらない | 判定は下がらないので、`AUTO but not cleanly generated` で気づきます。DECODE は Oracle でも PL/SQL の式では使えません |

### 制御構造

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `IF ... ELSIF ... ELSE ... END IF` | `if` / `else if` / `else` | なし | 条件が NULL なら、その枝に入りません |
| CASE 文（単純・検索） | `if` / `else if` の連なり | なし | ELSE が無く、どれにも当たらないと ORA-06592（`Plsql.CaseNotFound`）（生成で確認） |
| `LOOP ... END LOOP` | `while (true)` | なし | |
| `EXIT` / `EXIT WHEN c` | `break;` / `if (c) break;` | なし | |
| `EXIT ラベル WHEN c`（`<<outer>>`） | Java のラベルつき `break outer;` | なし | そのラベルのループを出していないと断ります |
| `CONTINUE` / `CONTINUE WHEN c` | `continue;` / `if (c) continue;` | なし | |
| `FOR i IN a .. b` / `IN REVERSE` | `for (int i = Plsql.loopBound(a), iEnd = Plsql.loopBound(b); ...)` | なし | 上下限は 1 回だけ評価します。NULL の上下限は ORA-06502 |
| `WHILE c LOOP` | `while (c)` | なし | |
| `GOTO` | 変換しない（`UnsupportedOperationException`） | LOWER-002（REDESIGN） | 生成で確認 |
| `NULL;` | `// NULL;` のコメント | なし | |
| 入れ子のブロック（`DECLARE ... BEGIN ... EXCEPTION ... END`） | Java のブロック `{ }`。局所変数はその中だけ。handler は `try` / `catch` | なし | 生成で確認 |
| `RETURN` | `return`。OUT 引数があれば `return new <Name>Result(...)` | なし | |
| 再帰呼び出し | そのまま Java の再帰 | RECUR-001（REVIEW） | スタックと DB の往復の深さを確かめます |

### SQL 文

PL/SQL の中の SQL 文は、Repository のメソッドになります。SQL は SQL 変換器で ScalarDB SQL に直し、名前つきの bind を `Residual.bindNamed` で JDBC の `?` に並べ替えて、呼び出し側の `Connection` の上で実行します。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `SELECT c INTO v FROM t WHERE 主キー = p` | `v = Plsql.fit(... repository.xxxStmt1(p) ...)` | なし | 0 行は `NoDataFoundException`、2 行目があれば `TooManyRowsException`（生成で確認） |
| キーで届かない `SELECT INTO` | 同じ。SQL に `LIMIT 2` を足す | ScalarDB がそのまま実行できれば SELECT-OPT-001（注記）。できなければ SELECT-001（REVIEW） | 0 行と複数行の意味は生成コードが保ちます（生成で確認） |
| 集約の `SELECT COUNT(*) INTO` | 同じ形 | ScalarDB がそのまま実行できなければ SEM-004（REVIEW） | 集約は 0 行でも 1 行返ります |
| `SELECT * INTO rec` / 複数列を複数の変数へ | 列を並べて読み、record を作る / 変数ごとに代入 | なし | 生成で確認 |
| `INSERT` / `UPDATE` / `DELETE`（値が引数・変数） | `rowCount = repository.xxxStmtN(...)`（`executeUpdate`） | なし | 書く値は `Plsql.columnText` / `columnNumber` で列の長さと桁を検査し（ORA-12899 / ORA-01438）、`Plsql.bind(値, ScalarDB の型, scale)` で渡します |
| `INSERT INTO t VALUES rec` / `UPDATE t SET ROW = rec` | 列を 1 つずつ並べた文 | なし | 生成で確認（INSERT） |
| 列を読む式の `UPDATE`（`SET c = c + x`） | 決定が無ければ Repository が `UnsupportedOperationException` | SQL-001（REVIEW） | `rowLocks.optimistic` に書くと「同じトランザクションで読んでから書く」2 文に割ります。複数行なら読んだ行を回すループです（生成で確認） |
| `UPDATE ... RETURNING c INTO v` | 決定が無ければ断る。`rowLocks.optimistic` があれば、読む → 計算する → 書く → 書けたら v に代入 | 決定が無ければ SQL-001（REVIEW） | 生成で確認 |
| `INSERT ... VALUES (seq.NEXTVAL, ...) RETURNING id INTO v` | INSERT の前に番号を取り、それを書いて v に入れる | なし | 生成で確認 |
| `DELETE ... RETURNING c INTO v` | 消す行を先に読み、それから DELETE | なし | 変数へ受けて 2 行以上なら `TooManyRowsException`（生成で確認） |
| `MERGE` | 決定が無ければ SQL 変換器の結果しだい（断られれば `UnsupportedOperationException`） | SEM-006（REVIEW）。断られれば SQL-001 も | `rowLocks.optimistic` があれば「件数を読んで UPDATE か INSERT を選ぶ」に割り、SEM-011（注記）になります（生成で確認） |
| `SQL%ROWCOUNT` | `int rowCount`（DML ごとに更新。`SELECT INTO` のあとは 1） | FORALL・動的 SQL・MERGE・呼び出し先が件数を決めうる routine で読むと SQL-004（REVIEW） | |
| `SQL%FOUND` / `SQL%NOTFOUND` | `rowCount > 0` / `rowCount == 0` | 同上 | 生成で確認 |
| ScalarDB SQL で直接は実行できず、実行計画に分解される文 | `PlanRunner.join(connection, plan, ...)`（ScalarDB から取得して H2 で実行） | SQL-002（REVIEW） | 行数上限と性能を確かめます（生成で確認） |
| ScalarDB が実行できない文 | Repository のメソッドが理由つきで `UnsupportedOperationException` | SQL-001（REVIEW） | |
| 結合 | そのまま実行できれば Repository の 1 文 | そのまま実行できない（警告・実行計画・拒否・スキーマ無し）と SEM-005（REVIEW） | |
| `WHERE p IS NULL OR col = p` | p が NULL のときの文と、等号で絞る文に分け、実行時に選ぶ | なし | ScalarDB SQL は bind の NULL 判定を WHERE に書けないためです |
| DDL の `DEFAULT` 列、`IDENTITY` 列を省いた INSERT | 省いた列を INSERT に足す（IDENTITY は採番） | なし | ScalarDB は主キーの無い INSERT を断るためです |
| CHECK 制約・外部キーのある表への書き込み | `constraints.enforce` に書いた表だけ、書く前に検査（ORA-02290 / ORA-02291 の例外） | 書いていない表は診断 `CONSTRAINT_UNDECIDED`（判定は下がらない） | 外部キーは親の行をキーで読みます。`CREATE TABLE` の中の制約は読みますが、`ALTER TABLE ... ADD CONSTRAINT` の制約では検査を出しませんでした（生成で確認） |
| PL/SQL の変数と表の列が同じ名前 | そのまま生成 | SQL-003（REVIEW） | Oracle は列として読みます |
| データ辞書（`USER_*`、`ALL_*`、`DBA_*`、`V$*`）を読む | SQL をそのまま生成 | DICT-001（REDESIGN） | 移行先には無い表です |
| 同じトランザクションで書いた表を走査する | 生成はするが、ScalarDB が実行時に拒否 | TX-004 / SCAN-001（REDESIGN） | `ScanAfterWriteException` になります |
| パーティションキーで絞れない走査 | そのまま生成 | SCAN-002（注記） | JDBC バックエンドでは通り、Cassandra などでは通りません |

### cursor

ScalarDB にはトランザクションをまたぐ cursor がありません。生成コードは cursor の行を**先に全部読んでから**回します。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| cursor FOR ループ（`FOR r IN c(p) LOOP` / `FOR r IN (SELECT ...) LOOP`） | `for (XxxLoopNRow r : repository.xxxLoopN(p))`。行は生成した record で、`r.col` は `r.col()` | 行数の上限を決めていなければ CUR-002（REVIEW）。決めていれば CUR-OPT-002（注記） | Repository は上限を超えると `IllegalStateException` を投げます。上限は `scanRows`（既定 10000、routine ごと、`notLimited` に理由）で決めます（生成で確認） |
| 明示 cursor の読むだけのループ（`OPEN; LOOP FETCH; EXIT WHEN %NOTFOUND; ...; CLOSE`） | cursor FOR ループに書き換える | 書き換えたうえで CUR-002。routine か呼び先が COMMIT / ROLLBACK / SAVEPOINT を持つと CUR-003（REVIEW） | 生成で確認 |
| 先頭 1 行だけ取る（`OPEN; FETCH INTO; CLOSE`） | `FETCH FIRST 1 ROWS ONLY` の問合せ 1 回。`c%NOTFOUND` は `boolean` の変数 | なし | 生成で確認 |
| 行を数えるだけのループ | `COUNT(*)` の問合せ 1 回 | なし | 生成で確認 |
| 上の形に当てはまらない明示 cursor | `Plsql.Cursor`（`open(行のリスト)`、`fetch()`、`found()`、`notFound()`、`rowCount()`、`isOpen()`、`close()`） | CUR-001（REVIEW） | 開いたまま OPEN は ORA-06511、閉じたまま FETCH は ORA-01001（生成で確認） |
| cursor の属性 `%FOUND`、`%NOTFOUND`、`%ROWCOUNT`、`%ISOPEN` | 上の `Plsql.Cursor` の関数、または生成した `boolean` | なし | |
| ループ本体が、cursor の読む表に書く | 決定が無ければ断る。`rowLocks.optimistic` があれば、先に読んでから書く | 断れば判定は下がらず、生成器の拒否として出る | 「パターン D」。他からの変更は commit で弾かれます（生成で確認） |
| `SYS_REFCURSOR` を `OPEN rc FOR SELECT ...; RETURN rc;` | 行の `List<...Row>` を返すメソッド | 行数上限は CUR-002 と同じ | 呼び出し側は FETCH の代わりに List を受け取ります（生成で確認） |
| 局所の `SYS_REFCURSOR` を OPEN して FETCH するループ | cursor FOR ループ | CUR-002 | 生成で確認 |
| `OPEN rc FOR '定数の文字列' USING p` | 静的な問合せとして生成 | なし | 生成で確認 |
| OUT 引数の `SYS_REFCURSOR`（`OPEN p_rc FOR ...`） | 結果の record の値が `null` のまま返る | 判定は下がらない | 行が呼び出し側に渡りません（生成で確認。まだ変換しないものを参照） |
| `FOR UPDATE` の cursor、`WHERE CURRENT OF c` | 行ロックの節を参照 | LOCK-001 / LOCK-002（REDESIGN） | |

### コレクション

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `TABLE OF x INDEX BY VARCHAR2(n)` | `Map<String, x>`（`Plsql.indexBy()`、キー順の TreeMap） | なし | FIRST / NEXT はキー順に回ります（生成で確認） |
| `TABLE OF x INDEX BY PLS_INTEGER` | `Map<Integer, x>` | なし | 0 や負のキーも取れます（生成で確認） |
| `TABLE OF x`（ネスト表） | `List<x>`（1 始まり）。コンストラクタ `t(1, 2)` は `Plsql.table(...)` | なし | 初期化しないと `null` で、使うと ORA-06531 |
| `VARRAY(n) OF x` | `List<x>` | なし | `v.LIMIT` は定数 n になります（生成で確認） |
| 要素の読み `v(i)` / 書き `v(i) := x` | `Plsql.at(v, i)` / `Plsql.set(v, i, x)` | なし | 範囲外は ORA-06532 / ORA-06533、無いキーは NO_DATA_FOUND、NULL のキーは ORA-06502 |
| `COUNT`、`FIRST`、`LAST`、`NEXT(i)`、`PRIOR(i)`、`EXISTS(i)` | `Plsql.count`、`first`、`last`、`next`、`prior`、`exists` | なし | 生成で確認 |
| `DELETE`、`DELETE(i)`、`DELETE(i, j)` | `Plsql.delete` | なし | ネスト表の途中の DELETE は隙間として持ちます |
| `EXTEND`、`EXTEND(n)`、`EXTEND(n, i)`、`TRIM`、`TRIM(n)` | `Plsql.extend`、`Plsql.trimTable` | なし | 生成で確認 |
| ネスト表どうしの `=` | 要素を多重集合として比べる | なし | |
| コレクションの代入 | 値を複製する | なし | PL/SQL の代入は複製なので |
| record の要素・コレクションの要素の record の field への代入 | record を作り直して置き換える | なし | |
| `PIPELINED` の function と `PIPE ROW` | `List` を返すメソッド。`PIPE ROW` は List に足す | なし | 生成で確認 |
| `TABLE(v)` への `COUNT(*)` | List を回して数える | なし | ほかの `TABLE(v)` の問合せは断ります |

### BULK COLLECT / FORALL / SAVE EXCEPTIONS

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `SELECT ... BULK COLLECT INTO v` | Repository が全行を `List<Object[]>` で返し、`Plsql.column(...)` で List にする | BULK-001（REVIEW） | この形には走査行数の上限の検査が付きません（生成で確認） |
| `FETCH c BULK COLLECT INTO v LIMIT n` のループ | 行を先に全部読み、`Plsql.chunks(行, n)` で n 件ずつ配るループ | 上限を決めていなければ CUR-002 と BULK-003（REVIEW）。決めれば BULK-OPT-003（注記） | LIMIT はもうメモリを守りません。v の要素は行の record になります（下の注意） |
| 件数を数えるだけの分割読み | `COUNT(*)` の問合せ 1 回 | なし | 生成で確認 |
| `SELECT ... BULK COLLECT INTO v` の直後の `FORALL i IN 1 .. v.COUNT <DML>` | 2 つを 1 つの cursor FOR ループにまとめる（診断 `BULK_CHUNKED`） | CUR-002、BULK-003 | 組の外で v や `SQL%ROWCOUNT` を読むと、まとめません（生成で確認） |
| `FORALL i IN 1 .. v.COUNT <DML>` | 要素ごとに 1 回 DML する Java のループ。`rowCount` は合計 | なし | FORALL の 1 往復が要素ごとの往復になり、性能が変わります（生成で確認） |
| `SQL%BULK_ROWCOUNT(i)` | `bulkRowCount` の List | なし | 生成で確認 |
| `FORALL i IN INDICES OF v` / `VALUES OF v` | 変換しない | 判定は下がらない | 生成で確認 |
| `FORALL ... SAVE EXCEPTIONS` | 部分失敗の意味は生成しない | BULK-002（REDESIGN） | `transactions.perIteration` で 1 要素 = 1 トランザクションに割る決定をすると、handler は失敗した 1 要素の記録になります |
| `SQL%BULK_EXCEPTIONS` | 変換しない（`UnsupportedOperationException`） | 判定は下がらない | 生成で確認 |
| `FORALL ... RETURNING BULK COLLECT INTO` | 1 要素ごとに書いた値を List に足す | RMW なので `rowLocks.optimistic` の決定が要る | コード上の対応（#51）。生成では確かめていません |

### 例外

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `EXCEPTION WHEN x THEN ...` | `try { ... } catch (XException e) { ... }` | なし | handler の順を保ち、`WHEN OTHERS` は最後に置きます |
| 定義済み例外 | `NoDataFoundException`（100）、`TooManyRowsException`（-1422）、`DuplicateValueException`（-1）、`InvalidNumberException`（-1722）、`ZeroDivideException`（-1476）、`ValueErrorException`（-6502）、`SubscriptBeyondCountException`（-6533）、`SubscriptOutsideLimitException`（-6532）、`CollectionIsNullException`（-6531）、`InvalidCursorException`（-1001）、`CursorAlreadyOpenException`（-6511）、`CaseNotFoundException`（-6592） | なし | 全部 `MigratedException(code, message)` の子で、Oracle の番号を持ちます。ランタイムの誤り（`Plsql.ZeroDivide` など）は handler のある所で対応する例外に付け替えます（生成で確認） |
| `WHEN DUP_VAL_ON_INDEX` / `WHEN INVALID_NUMBER` | catch は出すが、移行先ではこの例外が自然には起きない | EXC-001（REVIEW） | 明示の RAISE のときだけ走ります。重複 INSERT のあと ScalarDB はトランザクションを続けられません |
| `WHEN VALUE_ERROR` | `catch (ValueErrorException e)` | EXC-001（REVIEW） | 宣言の長さ・桁の超過と、数値にならない文字は届きます。CHAR の詰め物と SQL の中の変換は届きません |
| `WHEN OTHERS` | `catch (MigratedException e)` | `THEN NULL` だけなら EXC-002（REVIEW） | 移行した例外だけを捕まえます。`SQLException` や ScalarDB の競合、Java の不具合は捕まえません |
| ユーザ定義の例外（`e_x EXCEPTION`） | `EXException`（生成器が内部で -900000 台の番号を振る） | なし | `SQLCODE` は 1、`SQLERRM` は `User-Defined Exception` を返します（Oracle と同じ） |
| `PRAGMA EXCEPTION_INIT(e_x, -n)` | `CODE = -n` を持つ `EXException` | なし | 同じ番号の `RAISE_APPLICATION_ERROR` はこのクラスを投げるので、`WHEN e_x` で捕まります |
| `PRAGMA EXCEPTION_INIT(e, -54)`（行ロックが取れない）の handler | catch を出さない | なし | ScalarDB は待たないので起こりません。衝突は commit で分かります |
| `RAISE_APPLICATION_ERROR(-20001, msg)` | `throw new <Package>Error20001Exception(msg)` | なし | 番号を保ちます。同じ番号に 2 つの意味があると `generation-report.json` の `conflicts` に出ます（生成で確認） |
| `RAISE e_x` | `throw new EXException("e_x")` | なし | |
| `RAISE;`（handler の中） | `throw e;`（捕まえた例外をそのまま） | なし | handler の外の `RAISE;` は断ります |
| `SQLCODE` / `SQLERRM` / `SQLERRM(n)` | `Plsql.sqlcode(e.code())` / `Plsql.sqlerrm(e.code(), e.getMessage())` / `Plsql.sqlerrmOf(n)` | なし | 文言は Oracle 26ai で測ったものです（生成で確認） |
| `DBMS_UTILITY.FORMAT_ERROR_BACKTRACE` | `Plsql.errorBacktrace(e)` | なし | 中身は Java のスタックで、PL/SQL の行番号ではありません |
| 処理されない例外 | Java の例外として呼び出し側へ出る | なし | 生成コードは rollback しません。戻すのは呼び出し側です |

### トランザクション

生成コードはトランザクションを開かず、commit も rollback もしません。境界は呼び出し側にあります。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `COMMIT` / `ROLLBACK` / `SAVEPOINT` / `ROLLBACK TO` | 決定が無ければ `UnsupportedOperationException`（止まるのが正しい） | TX-001（REDESIGN） | 生成で確認 |
| ループの中の `COMMIT` | 同上 | TX-001、TX-003（REDESIGN） | 途中まで確定するので、全体の再実行が再実行になりません |
| `transactions.perIteration` に書いた routine | 部品に割る: 対象をキー順に件数つきで読む `<routine>Targets(..., after, batch)`、1 反復の `<routine>One(row)` など。途中の COMMIT は出さない | 判定は REDESIGN のまま（決定済み） | ループは呼び出し側が回します。回し方の例がコメントで付きます（生成で確認） |
| `transactions.callerBoundary` に書いた routine | COMMIT / ROLLBACK / SAVEPOINT をコメントにして省く | 同上 | 途中の ROLLBACK が戻していた分は、呼び出し側が戻さないかぎり残ります（生成で確認） |
| `PRAGMA AUTONOMOUS_TRANSACTION` | 決定が無ければ断る | TX-002（REDESIGN）、COMMIT があれば TX-001 も | |
| `transactions.separate` に書いた routine | 中身だけのメソッド。呼ぶ側は `SeparateTransactions` の口を受け取り、別のトランザクションで呼ぶ | 同上 | 親が rollback しても残る、という意味を保ちます（生成で確認） |
| 採番（`seq.NEXTVAL`） | `Sequences.next`。既定の実装 `CountersSequences` は別のトランザクションで取る | なし | counters 表の行は衝突しやすいので、業務の更新と同じトランザクションに入れません |

### 動的 SQL

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `EXECUTE IMMEDIATE '定数' [INTO v] [USING p]` | 静的な文として Repository に生成 | ScalarDB がそのまま実行できれば DYN-OPT-002（注記）。できなければ DYN-002（REVIEW） | 生成で確認 |
| 本体で定数を代入・連結した変数（`v_sql := '...'; v_sql := v_sql \|\| '...'`） | とりうる文（8 通りまで）を全部生成し、実行時に条件で選ぶ | 同上 | 宣言部の初期値で組んだ文字列はたどりません（生成で確認）。条件の変数を途中で書き換えると断ります |
| 表名などの識別子を連結（`'... FROM ' \|\| p_tab`） | 決定が無ければ断る。`dynamicTables` に表名を書けば表ごとの文を生成し、`UPPER(p_tab)` で選ぶ | DYN-001（REDESIGN） | 書いていない表名は `IllegalArgumentException`（生成で確認） |
| 動的な DDL（`'CREATE TABLE ...'`） | 断る。`ddl.omit` に書けば省く | DYN-002（REVIEW） | スキーマは Schema Loader が持ちます（生成で確認） |
| 動的な PL/SQL ブロック（`'BEGIN ... END;'` の定数） | ブロックを展開して生成 | 中身の判定しだい | 生成で確認 |
| `DBMS_SQL` で `PARSE` の文字列が定数の問合せ | 静的な cursor FOR ループ。`COLUMN_VALUE` は列番号で選ぶ | ループなので CUR-002 | 生成で確認 |
| それ以外の `DBMS_SQL`（DML、実行時に決まる文字列、`BIND_VARIABLE` など） | 変換しない | DYN-003（REDESIGN） | 生成で確認 |
| 権限 | 生成物には出ない | DYN-OPT-002 の注記 | EXECUTE IMMEDIATE に与えていた権限は運用で確かめます |

### 組み込みパッケージ

対応表は `plsql/builtins.py` です。表に無い package の呼び出しは断ります。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `DBMS_OUTPUT.PUT_LINE` / `PUT` / `NEW_LINE` | `Plsql.putLine` / `put` / `newLine` | なし | スレッドごとの出力バッファで、`Plsql.output()` で読みます。日付は既定の NLS の形（SEM-012） |
| `DBMS_RANDOM.VALUE` / `STRING` | `Plsql.randomValue` / `randomString` | SEM-014（REVIEW） | Oracle と同じ値にはなりません。乱数の出所は業務で決めます（生成で確認） |
| `DBMS_UTILITY.GET_TIME` | `Plsql.getTime()` | なし | 差を取るための値で、起点は Oracle と違います |
| `DBMS_SESSION.SLEEP` / `DBMS_LOCK.SLEEP` | `Plsql.sleep(秒)` | なし | 待つ間トランザクションは開いたままです（生成で確認） |
| `DBMS_APPLICATION_INFO.SET_MODULE` / `SET_ACTION` / `SET_CLIENT_INFO` | 何もしない（コメントだけ） | なし | 名前付き引数も並べ替えて読みます |
| `DBMS_STATS.GATHER_SCHEMA_STATS` / `GATHER_TABLE_STATS` | 何もしない（コメントだけ） | なし | ScalarDB にオプティマイザ統計はありません（生成で確認） |
| `SYS_CONTEXT(...)` | 変換しない | SEM-013（REDESIGN） | 要る属性だけを呼び出し側が渡す形に直します（生成で確認） |
| `UTL_HTTP`、`UTL_SMTP`、`UTL_FILE`、`UTL_TCP`、`DBMS_SCHEDULER`、`DBMS_JOB`、`DBMS_AQ`、`DBMS_PIPE`、`DBMS_ALERT`、`DBMS_LOB` | 変換しない | EXT-001（REDESIGN） | 外部への副作用です（生成で確認） |
| `DBMS_SQL` | 動的 SQL の節を参照 | DYN-003 | |
| 解析した範囲に無い routine（他の package、表に無い組み込み） | `UnsupportedOperationException("external call: ...")` | CALL-001（REVIEW） | 呼び先が COMMIT するか、外へ送るか、ロックを取るかが分かりません（生成で確認） |

### package の状態

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| package 変数（仕様・本体の、定数でない変数） | 決定が無ければ、読み書きする文を断る | 使う routine（呼び出しでたどって使うものを含む）が STATE-001（REDESIGN） | Service は 1 つのオブジェクトなので、フィールドに置くと全呼び出しで共有され、意味が変わります（生成で確認） |
| `packageState.carried` に package を書いたとき | 変数を使う routine ごとに、同名の IN OUT 引数として受け取り、結果の record で返す | 判定は REDESIGN のまま（決定済み） | 呼び出し側が値を持ち回ります。別の package から `pkg.var` を直接読む式は断ります（生成で確認） |
| package 本体の初期化部（`BEGIN ... END pkg;`） | 生成しない | その package の全 routine が STATE-002（REDESIGN） | 初めて参照したときに 1 回走る処理の置き場所を決めます（生成で確認） |
| package の定数 | `private static final` | なし | |
| package の型・cursor の宣言 | record・List、各 routine から使える cursor | なし | 生成で確認 |

### trigger

移行先に trigger はありません。生成コードの中で**書き込む側が trigger の本体を呼びます**。掛かるのは生成コードを通る経路だけです。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| 行 trigger（`BEFORE` / `AFTER`、`INSERT` / `UPDATE` / `DELETE`、`FOR EACH ROW`） | `Trg<Name>Service.body(:NEW と :OLD の列 ...)` | trigger 自身が TRG-001（REDESIGN） | 生成で確認 |
| `WHEN (NEW.c <> OLD.c)` | `body` の先頭で、条件が真でなければ return | なし | 生成で確認 |
| 1 行に絞れる書き込み（キーで特定） | 書く側の Service が trigger の Service を受け取り、BEFORE は書く前、AFTER は書いた後（`rowCount > 0` のとき）に呼ぶ。UPDATE・DELETE は先に `:OLD` を読む | 呼ぶ側は trigger の判定を引き継ぐ | 生成で確認 |
| `:NEW.id := seq.NEXTVAL` だけの BEFORE INSERT | 書く側の INSERT の値に織り込む（診断 `TRIGGER_INLINED`） | TRG-OPT-003（注記） | 生成で確認 |
| 条件なしで `:NEW` / `:OLD` だけを読む `:NEW.c := 式` | 書く値に畳み込み、本体も呼ぶ（`TRIGGER_FOLDED`） | 呼ぶ側は TRG-001 を引き継ぐ | 生成で確認 |
| 条件つき・局所変数を読む `:NEW` の代入、`:OLD` への代入 | 呼び出しにできない（`TRIGGER_REDESIGN`） | 書く側が TRG-002（REDESIGN） | |
| 複数行の UPDATE / DELETE、MERGE | 掛けない（`TRIGGER_NOT_APPLIED`） | 書く側が TRG-002（REDESIGN） | 行ごとに発火するものを 1 回の呼び出しにできないためです（生成で確認） |
| `INSERTING` / `UPDATING` / `DELETING`、`UPDATING('列')` | 書く側が静的に決めた真偽値を引数で渡す | なし | |
| `UPDATE OF 列` | その列を SET する書き込みにだけ掛ける | なし | |
| view への `INSTEAD OF` | 本体は生成する | TRG-001 | view へ書く routine に織り込む形はありません |
| 照合 | `TriggerChecks` / `TriggerCheckJob` に照合の問合せ（採番の最大値など） | なし | 生成コードを通らない書き込みを検証で追うためのものです（生成で確認） |

### 条件付きコンパイル

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `$IF $$flag $THEN ... $ELSIF ... $ELSE ... $END` | 読み込むときに、Oracle がコンパイルする側だけを残す | なし（診断 `CONDITIONAL_COMPILATION` に仮定を出す） | 書いていないフラグは NULL（偽）です。値は `conditionalCompilation.flags` に書きます（生成で確認） |
| `DBMS_DB_VERSION.VERSION` などの条件 | 同上 | なし | 既定は 19.0。`conditionalCompilation.dbVersion` で変えます |
| 選ばれた側の `$ERROR` | 記録する | | |
| 解釈できない条件 | 残したままにし、構文エラーになる | parse できなかった unit は AUTO にならない | |

### DB link

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `表@link` を読む・書く | 決定が無ければ SQL に `"emp@link"` がそのまま残る | LINK-001（REDESIGN） | 実行すると移行先で失敗します（生成で確認） |
| `dbLinks.<link>.namespace` を書いたとき | `表@link` を `namespace.表` に書き換え、同じ ScalarDB のトランザクションで読み書き | 判定は REDESIGN のまま（決定済み） | 相手の表も ScalarDB の管理下に置く決定です（生成で確認） |

### 行ロック（FOR UPDATE）

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `SELECT ... FOR UPDATE [NOWAIT / WAIT n / SKIP LOCKED]` | SQL から `FOR UPDATE` を外して生成する。決定が無い routine では、読んだ値を使う式を SQL の外で先に計算しないので、その値で書く UPDATE は ScalarDB に断られる | LOCK-001（REDESIGN）。断られた文は SQL-001 | ロックが守っていた「読んで、判断して、書く」を黙って進めないためです（生成で確認） |
| cursor の宣言の `FOR UPDATE` | 同上 | LOCK-001、LOCK-002（REDESIGN） | 生成で確認 |
| `rowLocks.optimistic` に書いた routine | ロックを落とし、同じトランザクションの中で読んでから書く | 判定は REDESIGN のまま（決定済み） | 衝突は commit で弾かれ、再試行は呼び出し側の責務です。NOWAIT の「すぐ分かる」は「commit で分かる」に変わります（生成で確認） |
| `WHERE CURRENT OF c` | 読んだ行のキーで UPDATE / DELETE | 同上 | 生成で確認 |
| 読んだ値で計算して書く（`SET c = c + x`） | SQL 文の節の「列を読む式の UPDATE」を参照 | | |

### プロジェクトの決定（limits.yaml）と生成物

生成器が推測してはいけないことは、limits.yaml に人が書いたときだけ生成物に反映します。書いていない routine には既定の答えを当てません。

| limits.yaml の項目 | 生成物の変化 | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `scanRows.default` / `routines` / `notLimited` | cursor の行を読む所に上限の検査を入れる | CUR-002 → CUR-OPT-002、BULK-003 → BULK-OPT-003 | `notLimited` でも既定値の検査は「暫定の網」として残ります（生成で確認） |
| `rowLocks.optimistic` | 行ロックを落とす。列を読む UPDATE・RETURNING・MERGE を読んでから書く形に割る | SQL-001 / SEM-006 が外れ、SEM-011 の注記。LOCK-* は REDESIGN のまま | |
| `transactions.perIteration` / `separate` / `callerBoundary` | トランザクションの節を参照 | TX-* は REDESIGN のまま（決定済み） | 1 つの routine に 1 つだけ書けます |
| `dynamicTables` | 識別子を連結する動的 SQL を表ごとの文にする | DYN-001 は REDESIGN のまま | |
| `ddl.omit` | 動的な DDL を省く | | |
| `packageState.carried` | package 変数を引数と結果で運ぶ | STATE-001 は REDESIGN のまま | |
| `constraints.enforce` | CHECK / 外部キーの検査を書く前に入れる | | |
| `dbLinks` | `表@link` を namespace に書き換える | LINK-001 は REDESIGN のまま | |
| `conditionalCompilation` | 条件付きコンパイルのフラグとバージョン | | |

### ルールが REVIEW / REDESIGN にするもの（ルール ID ごと）

この節は列が違います。「当たる書き方」は `plsql/rules/*.yaml` の条件を言い直したものです。

| ルール ID | 判定 | 当たる書き方 | 生成物と、外すための決定 |
|---|---|---|---|
| TX-001 | REDESIGN | routine（と呼び先）の COMMIT / ROLLBACK / SAVEPOINT | 決定が無ければ断る。`transactions.*` で形を決める |
| TX-002 | REDESIGN | `PRAGMA AUTONOMOUS_TRANSACTION` | `transactions.separate` |
| TX-003 | REDESIGN | ループの中の COMMIT / ROLLBACK | `transactions.perIteration` |
| TX-004 | REDESIGN | 同じトランザクションで書いた表を走査する | routine を境界で割る。読み取りをキーにする |
| SCAN-001 | REDESIGN | 呼び先が、この routine の書いた表を走査する | 同上 |
| STATE-001 | REDESIGN | package 変数を（呼び出し経由を含めて）読み書きする | `packageState.carried` |
| STATE-002 | REDESIGN | package 本体の初期化部 | 置き場所を人が決める（生成しない） |
| AUTHID-001 | REDESIGN | routine の `AUTHID CURRENT_USER` | 認証・認可を別に設計する |
| DYN-001 | REDESIGN | 識別子を実行時に組む動的 SQL | `dynamicTables` |
| DYN-003 | REDESIGN | `DBMS_SQL`（定数の問合せに書き換えられなかったもの） | 実行ログから文を洗い出す |
| LOWER-002 | REDESIGN | `GOTO` | 制御構造を組み直す |
| LOCK-001 | REDESIGN | 行ロックのある SQL 文 | `rowLocks.optimistic` |
| LOCK-002 | REDESIGN | 行ロックする cursor の宣言 | 同上 |
| BULK-002 | REDESIGN | `FORALL ... SAVE EXCEPTIONS` | `transactions.perIteration` で要素ごとに割る |
| DICT-001 | REDESIGN | データ辞書（`USER_*` など、`V$*`）を読む | 移行先のメタデータか設定値に置き換える |
| SEM-013 | REDESIGN | `SYS_CONTEXT` | 要る値を呼び出し側が渡す |
| TRG-001 | REDESIGN | trigger そのもの | 検証で追う（照合） |
| TRG-002 | REDESIGN | trigger の掛かる表への書き込みで、呼び出しに置き換えられないもの | 行ごとに渡すものを設計する |
| LINK-001 | REDESIGN | DB link 越しの操作 | `dbLinks` |
| EXT-001 | REDESIGN | `UTL_*`、`DBMS_SCHEDULER`、`DBMS_AQ` など | adapter 経由の外部 Service にする |
| EXT-002 | REDESIGN | 呼び出し仕様（`LANGUAGE JAVA` / `C`、`EXTERNAL`） | 本体をアプリに移す |
| LOWER-001 | REVIEW | lowering がまだ模していない構文（入れ子ブロックの中の subprogram、構文エラーから回復した unit など） | |
| CALL-001 | REVIEW | 解析した範囲に無い routine の呼び出し | 呼び先のソースを加えるか、代替を決める |
| CALL-002 | REVIEW | どの版か決まらないオーバーロードの呼び出し | 名前付き引数で呼ぶ、版ごとに名前を分ける |
| CALL-003 | REVIEW | OUT 引数のある関数を、評価されるか条件で決まる位置で呼ぶ | 条件を IF に分ける |
| SELECT-001 | REVIEW | キーで届かず、ScalarDB がそのまま実行できない `SELECT INTO` | |
| SEM-001 | REVIEW | 移行先 DB が評価する `ROUND` | 式を SQL の外へ出す |
| SEM-002 | REVIEW | `CURRENT_DATE`、`CURRENT_TIMESTAMP`、`LOCALTIMESTAMP`、`SESSIONTIMEZONE`、`DBTIMEZONE`、`AT TIME ZONE` | タイムゾーンを決める |
| SEM-003 | REVIEW | 移行先 DB が評価する空文字の比較・`NVL`・`RTRIM` | 式を SQL の外へ出す |
| SEM-004 | REVIEW | ScalarDB がそのまま実行できない集約の `SELECT INTO` | |
| SEM-005 | REVIEW | そのまま実行できると判定されなかった結合 | |
| SEM-006 | REVIEW | 割っていない `MERGE` | `rowLocks.optimistic` |
| SEM-007 | REVIEW | 時計（SYSDATE など）を文の中で 2 回以上読む | 1 回読んで使い回す |
| SEM-008 | REVIEW | 言語で変わる `TO_CHAR` の書式 | |
| SEM-009 | REVIEW | 移行先 DB が評価する `CAST(... AS DATE)` | |
| SEM-010 | REVIEW | `SYSTIMESTAMP` を列へ書く INSERT / UPDATE / MERGE | 時刻の正確さを業務で決める |
| SEM-012 | REVIEW | 日付・TIMESTAMP を書式なしで文字にする | 書式を明示する |
| SEM-014 | REVIEW | `DBMS_RANDOM`、`SYS_GUID` | 乱数の出所を決める |
| CUR-001 | REVIEW | 書き換えられなかった明示 cursor の OPEN / FETCH / CLOSE | |
| CUR-002 | REVIEW | 行数上限の決まっていない cursor FOR ループ | `scanRows` |
| CUR-003 | REVIEW | 先読みに書き換えた明示 cursor で、routine が COMMIT などを持つ | |
| BULK-001 | REVIEW | `BULK COLLECT` を含む SQL 文 | |
| BULK-003 | REVIEW | 行数上限の決まっていない分割読み | `scanRows` |
| EXC-001 | REVIEW | `DUP_VAL_ON_INDEX`、`INVALID_NUMBER`、`VALUE_ERROR` の handler | 読んでから選ぶ形、事前の検査に書き直す |
| EXC-002 | REVIEW | `WHEN OTHERS THEN NULL` | 無視する例外を名前で書く |
| SQL-001 | REVIEW | ScalarDB SQL で実行できない文 | RMW なら `rowLocks.optimistic` |
| SQL-002 | REVIEW | 実行計画に分解される文 | |
| SQL-003 | REVIEW | 変数と列が同じ名前 | 名前を変える、列を修飾する |
| SQL-004 | REVIEW | 静的な DML 以外が件数を決めうる routine で `SQL%ROWCOUNT` を読む | |
| RECUR-001 | REVIEW | 再帰 | |
| （ルールなし） | REVIEW | 確信度の因子が 0（Unsupported の文、解決できない型・呼び先、ScalarDB が実行できない文、証拠が無い） | |

### まだ変換しないもの

判定が AUTO のままでも、生成物で止まる（または正しく動かない）ものを含みます。生成物では多くが `UnsupportedOperationException` になり、`generation-report.json` の `refused` に出ます。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `GOTO` | `UnsupportedOperationException` | LOWER-002（REDESIGN） | |
| 決定の無い COMMIT / ROLLBACK / SAVEPOINT | 同上 | TX-001 | |
| `FORALL ... INDICES OF` / `VALUES OF` | 同上 | 判定は下がらない | |
| `SQL%BULK_EXCEPTIONS` | 同上 | 判定は下がらない | |
| `DBMS_SQL`（定数の問合せ以外） | 同上 | DYN-003 | |
| とりうる文を数えられない `EXECUTE IMMEDIATE` | 同上 | DYN-002 / DYN-001 | 宣言部の初期値で組んだ文字列も含みます |
| 式の中の function 呼び出しの名前付き引数 | 同上 | 判定は下がらない | 呼び出し文の名前付き引数は変換します |
| 式の中の function 呼び出しで既定値の引数を省く | 引数の足りない Java（javac で落ちる） | 判定は下がらない | 生成で確認 |
| 対応表に無い組み込み関数（`MONTHS_BETWEEN`、`SYS_GUID` など） | `UnsupportedOperationException` | 関数による | 組み込み関数の節を参照 |
| `TO_CHAR` の 4 つ以外の書式、書式つき `TO_NUMBER` | 実行時に `UnsupportedOperationException` | 書式による | |
| 別の package の変数の直接参照（`pkg.var`） | `UnsupportedOperationException` | STATE-001 | `packageState.carried` を書いても断ります |
| package の仕様の `SUBTYPE` | 変数が `Object` になる | 判定は下がらない | |
| OUT 引数の `SYS_REFCURSOR` | 結果の record に `null` が入る | 判定は下がらない | 生成で確認 |
| TIMESTAMP を返す function の `RETURN SYSTIMESTAMP` | javac で落ちる | 判定は下がらない | 生成で確認 |
| `FETCH ... BULK COLLECT INTO 数値のコレクション LIMIT n` の要素 | 要素が行の record になり、`PUT_LINE(v(i))` が record の文字列を出す | CUR-002、BULK-003 | 生成で確認 |
| `%ROWTYPE` の field へ、型の違う NUMBER を代入（`NUMBER(10)` の列に NUMBER の引数） | javac で落ちる（`BigDecimal` と `Long`） | 判定は下がらない | 生成で確認 |
| `FORALL ... SAVE EXCEPTIONS` を `SELECT ... BULK COLLECT` と組にした形 | 1 つのループにまとまり、失敗で止まる | BULK-002 が当たらず REVIEW | 部分失敗の意味が失われます（生成で確認） |
| 入れ子ブロックの DECLARE の subprogram | 本体ごと断る | LOWER-001（REVIEW） | |
| package 本体の初期化部 | 生成しない | STATE-002 | |
| 呼び出し仕様（`LANGUAGE JAVA` など） | `UnsupportedOperationException` | EXT-002 | |
| `UTL_*` などの外部 package | `UnsupportedOperationException` | EXT-001 | |
| `SYS_CONTEXT` | `UnsupportedOperationException` | SEM-013 | |
| 型だけが違うオーバーロードの呼び出し | `UnsupportedOperationException` | CALL-002 | |
| 畳み込めない `:NEW` の代入、`:OLD` への代入 | 書く側が呼ばない | TRG-002 | |
| MULTISET の演算、`TABLE(v)` への `COUNT(*)` 以外の問合せ | 変換しない | | |
| view への書き込みに `INSTEAD OF` trigger を織り込む形 | 無い | | view へ書く文は ScalarDB に view が無いので断られます |

## 既知の不具合（2026-09-27 時点）

この一覧を作るときに変換器と生成器に通して見つけたものです。判定が OK / AUTO のまま、動かない SQL や Java が出るものを含みます。

SQL（`scalardb_migrate/`）

- Oracle の `RAW(n)` が ERROR `TYPE` になる（`BLOB` にできる）。`LONG` は文字列の型なのに BIGINT になる。
- PostgreSQL の `CREATE SCHEMA sales` が `CREATE NAMESPACE ""` になる。`DROP SCHEMA` / `DROP DATABASE` と `ALTER TABLE ... DROP COLUMN` は ERROR `INTERNAL`（変換器の内部エラー）になる。
- 射影の `ROWNUM`（`SELECT ROWNUM, name ...`）と `SELECT seq.NEXTVAL FROM dual` が検査されずに素通りする。
- Oracle の `FROM t PARTITION (p1)` が表の別名として読まれる。
- SELECT などの中の `catalog.schema.table` は 3 つ組のまま、指摘なしで出る。

PL/SQL（`plsql/`）

- OUT 引数の `SYS_REFCURSOR` の行が呼び出し側に渡らない（結果の record が `null`）。判定は AUTO になりうる。
- TIMESTAMP を返す function の `RETURN SYSTIMESTAMP`、式の中の function 呼び出しで既定値の引数を省く形、`%ROWTYPE` の field への型の違う NUMBER の代入が、javac で落ちる。
- `FETCH ... BULK COLLECT INTO 数値のコレクション LIMIT n` で、要素が行の record になる。
- `SELECT ... BULK COLLECT` と組にした `FORALL ... SAVE EXCEPTIONS` で BULK-002 が当たらず、部分失敗の意味が消える。
- package の仕様の `AUTHID CURRENT_USER` と `SUBTYPE` を読まない（AUTHID-001 が当たらない、型が `Object` になる）。
- SEM-007 が宣言部の初期値で読んだ時計を数えない。SQL-004 が、trigger を呼ぶために生成器が足した `SQL%ROWCOUNT` の読みにも当たる。
- `ALTER TABLE ... ADD CONSTRAINT` で足した制約に、`constraints.enforce` の検査が出ない。
