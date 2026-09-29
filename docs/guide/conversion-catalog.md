# SQL と PL/SQL の変換の一覧

SQL と PL/SQL の各項目（構文・関数・データ型・文・例外など）が、変換でどうなるかを項目ごとに引ける一覧です。

- **SQL:** 1 文ずつ ScalarDB SQL に変換します（`scalardb_migrate/`）。変換できない読み取り文は実行計画に分け、それもできなければアプリ側に移します。
- **PL/SQL:** Java に変換します（`plsql/`）。中の SQL 文は、SQL の変換器を通して Repository のメソッドにします。

使い方とコマンドは [SQL の変換と実行計画](sql-conversion.md) と [PL/SQL → Java 変換](plsql-conversion.md)、仕組みは
[アーキテクチャと仕組み](../design/architecture.md) にあります。この一覧は 2026-09-27 のコードから取り、変換器と生成器に実際に通して確かめました。
コードと食い違う所を見つけたら、コードが正です。この一覧を作るときに見つけた不具合（Issue #121〜#130）は直してあります。

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
| PostgreSQL の `MONEY` | DOUBLE | WARN `TYPE` | 通貨の固定小数点で、DOUBLE では小数が正確に持てない。セント単位などの整数を BIGINT に持つことを勧める |
| Oracle の `INTEGER` / `INT` / `SMALLINT` | BIGINT | WARN `TYPE` | Oracle では `NUMBER(38)` の別名。正確に写すなら `NUMBER(p)` で宣言する |
| PostgreSQL・MySQL の `SMALLINT` / `INT` / `INTEGER`、MySQL の `TINYINT` / `MEDIUMINT` | INT | OK | |
| MySQL の `TINYINT(1)` | INT | WARN `TYPE` | 真偽値に使っているなら BOOLEAN を検討する |
| `BIGINT` | BIGINT | OK | |
| MySQL の `INT UNSIGNED` | BIGINT | OK（INFO `TYPE`） | 符号なし 32 ビットは BIGINT に収まる |
| MySQL の `BIGINT UNSIGNED` | BIGINT | WARN `TYPE` | 符号なし 64 ビットの範囲は収まらない |
| Oracle の `FLOAT` / `REAL` | DOUBLE | WARN `TYPE` | Oracle の FLOAT は最大 38 桁の 10 進数（REAL は `FLOAT(63)`）。DOUBLE は約 15 桁 |
| Oracle の `BINARY_FLOAT` | FLOAT | OK（INFO `TYPE`） | IEEE の単精度で、ScalarDB の FLOAT と同じ。NaN と無限大を持てるかは下のデータベース次第 |
| Oracle の `BINARY_DOUBLE`、`DOUBLE` / `DOUBLE PRECISION` | DOUBLE | OK | |
| PostgreSQL の `REAL`、`FLOAT(p)`（p ≤ 24） | FLOAT | OK | 単精度。`FLOAT(10)` は INFO `TYPE` |
| PostgreSQL の `FLOAT`（精度なし）、`FLOAT(p)`（p ≥ 25） | DOUBLE | OK | 倍精度 |
| MySQL の `FLOAT` | FLOAT | OK | |
| `VARCHAR2(n)` / `NVARCHAR2(n)` / `VARCHAR(n)` | TEXT | OK（INFO `TYPE`） | 長さの上限は ScalarDB では守られない |
| `TEXT` / `CLOB` / MySQL の `LONGTEXT` など | TEXT | OK | |
| `CHAR(1)` | TEXT | OK（INFO `TYPE`） | |
| `CHAR(n)` / `NCHAR(n)`、n > 1 | TEXT | WARN `TYPE` | 空白詰めの値は、移行時に trim しないと `=` で当たらなくなる |
| Oracle の `NCLOB` | TEXT | OK | |
| Oracle の `LONG` | TEXT | WARN `TYPE` | 古い文字列の型（最大 2 GB）。整数ではない。Oracle 側では WHERE や索引に使えず、表に 1 列だけ。取り出すときは `TO_LOB` で CLOB にする |
| Oracle の `LONG RAW` | BLOB | WARN `TYPE` | 古いバイナリの型（最大 2 GB）。Oracle 側では WHERE や索引に使えず、表に 1 列だけ。取り出すときは `TO_LOB` で BLOB にする |
| `BLOB` / `BYTEA` / `BINARY(n)` / `VARBINARY(n)` | BLOB | OK | |
| Oracle の `RAW(n)` | BLOB | OK（INFO `TYPE`） | 長さの上限は ScalarDB では守られない |
| `BOOLEAN`、`BIT(1)` | BOOLEAN | OK | |
| `BIT(n)`、n > 1 | BLOB | WARN `TYPE` | |
| Oracle の `DATE` | DATE | WARN `TYPE` | Oracle の DATE は時刻を持つ。時刻を使う列は TIMESTAMP にする |
| PostgreSQL・MySQL の `DATE` | DATE | OK | |
| `TIME` | TIME | OK（INFO `TYPE`） | マイクロ秒まで |
| PostgreSQL の `TIMETZ` | TIME | WARN `TYPE` | 時差が落ちる |
| `TIMESTAMP(p)` / MySQL の `DATETIME(p)`、p ≤ 3 | TIMESTAMP | OK | |
| 同上、p ≥ 4 か精度なし（Oracle・PostgreSQL の既定は 6） | TIMESTAMP | WARN `TYPE` | ミリ秒まで。MySQL の精度なしは 0 として読むので OK |
| MySQL の `TIMESTAMP` | TIMESTAMPTZ | OK（INFO `TYPE`） | MySQL の TIMESTAMP は、セッションの時間帯から UTC に直して持つ瞬間の型なので TIMESTAMPTZ にする。時差を持たない `DATETIME` は TIMESTAMP |
| `TIMESTAMP WITH TIME ZONE` / `WITH LOCAL TIME ZONE` / `TIMESTAMPTZ` | TIMESTAMPTZ | 精度 3 以下は OK、それ以外は WARN `TYPE` | UTC で持ち、ミリ秒まで |
| `JSON` / `JSONB` / `UUID` / `ENUM` / `SET` / `INET` | TEXT | WARN `TYPE` | 文字列として入る。JSON の演算子や列挙の検査は使えない |
| PostgreSQL の `XML`、Oracle の `XMLTYPE` | TEXT | WARN `TYPE` | XML を文字列として持つ。XPath・`XMLTABLE` などの関数は使えない |
| `SERIAL` / `BIGSERIAL` / `SMALLSERIAL` | なし | ERROR `TYPE` | 自動採番は無い。ID はアプリで作る |
| `INTERVAL`、配列（`INT[]`）、MySQL の `YEAR` / `GEOMETRY`、Oracle の `ROWID` 型 | なし | ERROR `TYPE` | 対応する型が無い |

### DDL（表・索引・名前空間）

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| 1 列の主キー `emp_id NUMBER(9) PRIMARY KEY` | その列をパーティションキー | OK | |
| 複合主キー `PRIMARY KEY (customer_id, order_no)` | 先頭をパーティションキー、残りをクラスタリングキー | OK（INFO `KEYS`） | `--keys orders=customer_id/order_no` で分け方を変えられる |
| `--keys` が無い列を指す、パーティションキーが空（`--keys t=`）、同じ列を 2 回 | 変換しない | ERROR `KEYS` | |
| `--keys` の列が元の主キーと違う（`PRIMARY KEY (a, b)` に `--keys t=a`） | `--keys` のとおり | WARN `KEYS` | 行を区別する列が変わる。元では別の行が 1 行になる（2 回目の INSERT が失敗し、UPSERT は上書きする） |
| 主キーの無い表 | 変換しない | ERROR `PK` | 主キーを足すか `--keys` を渡す（渡せば WARN `KEYS` で変換する） |
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
| `CREATE SCHEMA sales` / MySQL の `CREATE DATABASE shop` | `CREATE NAMESPACE sales` / `CREATE NAMESPACE shop` | OK | `IF NOT EXISTS` は残す |
| `DROP SCHEMA` / `DROP DATABASE` | `DROP NAMESPACE` | OK | `IF EXISTS` と `CASCADE` は残す |
| `DROP TABLE a, b` / `TRUNCATE TABLE a, b` | 表ごとに 1 文 | OK | |
| `catalog.schema.table` | `schema.table` | WARN `NAMESPACE` | schema を名前空間にする。DDL・TRUNCATE・DROP も SELECT・INSERT・UPDATE・DELETE も同じ。指摘は表ごとに 1 回 |
| `ALTER TABLE ... ADD COLUMN c VARCHAR(10)`（Oracle は `ADD c ...`） | `ALTER TABLE ... ADD COLUMN c TEXT` | OK | 列の型は上の対応表のとおり。付けた制約は落とし WARN `COL_OPT` |
| Oracle の `ALTER TABLE ... ADD (c1 ..., c2 ...)`（括弧つき） | 1 列ずつの `ADD COLUMN` | OK（2 列以上は INFO `ALTER`） | 列の型は上の対応表のとおり |
| 複数の操作の `ALTER TABLE`（`ADD COLUMN a1 ..., ADD COLUMN a2 ...`、MySQL の `ADD c INT, DROP d` のような混在も） | 1 操作ずつの文 | OK（INFO `ALTER`） | まとめて 1 回ではなくなる。読めない操作が 1 つでもあれば、その操作を名指しして ERROR `ALTER` |
| `ALTER COLUMN ... TYPE`（PostgreSQL）/ `MODIFY COLUMN`（MySQL） | `ALTER COLUMN ... SET DATA TYPE` | WARN `ALTER_TYPE` | 型を変えられるかは下のデータベース次第 |
| Oracle の `ALTER TABLE ... MODIFY c 型` / `MODIFY (c1 型, c2 型)` | 列ごとの `ALTER COLUMN ... SET DATA TYPE` | WARN `ALTER_TYPE` | 付けた `NOT NULL` などは落とし WARN `COL_OPT` |
| Oracle の `MODIFY (c NOT NULL)` など型を変えない `MODIFY` | 変換しない | ERROR `ALTER` | ScalarDB に NOT NULL・DEFAULT・制約は無いので、変換するものが無い。文を落としてアプリで守る |
| `ALTER TABLE ... DROP COLUMN c`、Oracle の `DROP (c1, c2)`、MySQL の `DROP c` | 列ごとの `ALTER TABLE ... DROP COLUMN c` | OK | `IF EXISTS` は残す。主キーの列は ScalarDB で落とせないので ERROR `ALTER`（表定義があるとき）。Oracle の `DROP (c) CASCADE CONSTRAINTS` は ERROR `ALTER` |
| `RENAME COLUMN` / `RENAME TO` | そのまま | OK | |
| 制約・索引・パーティションを変える `ALTER TABLE` | 変換しない | ERROR `ALTER` | |

### SELECT の句

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| 射影が列と `*` だけ | そのまま | OK | |
| 射影に式・関数・`CASE`・キャスト（`SELECT salary * 2 ...`） | — | PLANNED（`PROJECTION`） | ScalarDB SQL の射影は列と集約だけ |
| 別名 `SELECT name AS n ... ORDER BY n` | `ORDER BY name`（別名の元の列） | OK（INFO `ORDER`） | ScalarDB は列の別名を解決しない。列と同じ名前の別名（`name AS salary ... ORDER BY salary`）は列として読む。集約の別名（`COUNT(*) AS cnt ... ORDER BY cnt`）はそのまま（列と同じ名前なら `ORDER BY COUNT(*)`） |
| `WHERE salary > 10`（列とリテラル・バインド変数） | そのまま | OK | |
| 列の型と違うリテラル（数値の列に `'1'`、整数の列に `2.0`） | 列の型のリテラル（`1`、`2`） | OK（INFO `TYPE_LIT`） | ScalarDB は型の違うリテラルを断る（DB-SQL-10053 / 10054 / 10055） |
| 整数の列と端数のある範囲（`emp_id < 2.5`、`> 2.5`、`BETWEEN 1.5 AND 3.5`） | `<= 2`、`>= 3`、`BETWEEN 2 AND 3` | OK（INFO `TYPE_LIT`） | `<> 2.5` は `IS NOT NULL`（WARN `TYPE_LIT`） |
| 整数の列と端数のある値の `=`、数字でない文字列と数値の列、TEXT の列と数値（`name = 10`）、型の範囲外 | — | PLANNED（`TYPE_MISMATCH`） | 移行元は暗黙の型変換で比べる。計画の取得には押し下げない |
| `= NULL` / `<> NULL` / `NOT IN (1, NULL)` | — | PLANNED（`NULL_CMP`） | 移行元でも行を返さない。ScalarDB は述語の NULL を断る（DB-SQL-10045）。UPDATE・DELETE では ERROR |
| `IN (1, NULL)` | `= 1`（NULL を除く） | WARN `NULL_CMP` | NULL は移行元でも一致しない |
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
| MySQL の `ORDER BY salary`（NULL を持ちうる列） | そのまま | WARN `NULLS` | MySQL は昇順で NULL を最初に、ScalarDB は最後に並べる。主キーの列と、渡した DDL で `NOT NULL` の列には出さない |
| `FOR UPDATE` / MySQL の `LOCK IN SHARE MODE` | 落とす | WARN `LOCK` | 行ロックに頼る処理は、commit 時の衝突と再試行に変わる |
| オプティマイザヒント `/*+ ... */`、MySQL の `USE INDEX (...)` | 落とす | OK（INFO `HINT`） | |
| MySQL の `SQL_NO_CACHE` / `STRAIGHT_JOIN` | 落とす | OK（INFO `MODIFIER`） | |
| MySQL の `SQL_CALC_FOUND_ROWS` | 落とす | WARN `MODIFIER` | 後の `SELECT FOUND_ROWS()` には別に `COUNT(*)` が要る |
| PostgreSQL の `FROM ONLY employees` | `ONLY` を落とす | WARN `ONLY` | 子の表の行を移さない |
| `SAMPLE (10)` / `TABLESAMPLE` | — | ERROR `CLAUSE`（`RESIDUAL_H2`） | H2 でも実行できない |
| `QUALIFY` / `WINDOW` / `INTO` などの句 | — | ERROR `CLAUSE` | 読み取り文なら実行計画を試す |
| Oracle の `FROM employees PARTITION (p1)` / `SUBPARTITION (...)`、MySQL の `PARTITION (p1)` | — | ERROR `CLAUSE` | ScalarDB の表に Oracle のパーティションは無い。パーティションを分ける列の範囲で絞る。実行計画も作らない |
| Oracle の `AS OF TIMESTAMP ...` / `AS OF SCN ...` / `VERSIONS BETWEEN ...`（フラッシュバック問い合わせ） | — | ERROR `CLAUSE` | ScalarDB は過去の版を読めない。要る履歴はアプリが別の表に持つ。SQLGlot が解析できないので、ほかの指摘は出ない |
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
| `SELECT DISTINCT` / PostgreSQL の `DISTINCT ON` | — | PLANNED（`DISTINCT`） | `DISTINCT ON (a) ... ORDER BY a, b DESC` のように並びの式が選択リストに無いときは、H2 が断るので、計画の残りの SQL で並びの式も選び、外側の問い合わせで元の列を返す。`*` の選択リストでは ERROR `RESIDUAL_H2` |
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
| 数値 | `ROUND(salary)`、`salary / 2`、MySQL の `empno DIV 4` | 丸めは 0 から遠いほうへ（-2.5 は -3）。0 で割ったときは方言で違う（Oracle は失敗、MySQL は NULL）。MySQL の `DIV` は 0 の方向へ切り捨てる（指摘文は元の `DIV` の形で式を出す。SQLGlot が書き戻す `CAST(a / b AS SIGNED)` は四捨五入なので、それで書かない。#161）。アプリで書くときの注意として `APP_SEMANTICS` に出る |
| 日付 | `ADD_MONTHS(hired, 1)`、`TRUNC(hired, 'MM')`、日付どうしの引き算 | `ADD_MONTHS` は月末をそろえる（`APP_SEMANTICS`）。実行計画では H2 向けに `TRUNC(d, 'MM')` を `DATE_TRUNC('MONTH', d)` に書き換える。週（Oracle の `IW` / `WW` / `W`、PostgreSQL の `date_trunc('week', d)`）は H2 の `DATE_TRUNC('WEEK')` が日曜始まりなので、始まりの曜日を合わせた `DATEADD` の式にする。PostgreSQL の `date_trunc(単位, DATE の値)` は、PostgreSQL が DATE をセッションの TimeZone の 0 時の timestamptz にして返す（timestamptz の `CAST(.. AS DATE)`、`EXTRACT(HOUR ..)`、`to_char` もセッションの TimeZone で答える）。`--session-time-zone` を渡すと PostgreSQL の計画に `residual.java.time_zone` としてゾーンが載り、ランタイムは H2 のセッションをそのゾーンで開いて TIMESTAMPTZ の値もそのゾーンの時差で入れる（瞬間は同じ）。Asia/Tokyo なら `date_trunc('week', DATE '2024-01-03')` は PostgreSQL と同じ `2024-01-01 00:00:00+09` になる（PostgreSQL 16.15 と H2 2.5.250 で確認、夏時間のある America/Los_Angeles も同じ。#160）。渡さないと H2 のセッションは UTC で、移行元のセッションが UTC 以外なら返す値が違う（`2024-01-01 00:00:00+00`。DATE との比較や並べ替えの結果は同じ）。Oracle と MySQL の計画にはゾーンを載せない。`TRUNC(d, 'DAY')`（`DY`、`D` も）は週の始まりを NLS_TERRITORY が決めるので計画にせず ERROR `RESIDUAL_H2`（`IW` を使うか、アプリで計算する）。H2 が書き換えられない単位も同じ。小数秒の無い `TIMESTAMP '...'` は、H2 が `FF6` の書式で読めないので、小数秒の無い書式で書く（#153）。日付どうしの引き算は Oracle なら `DAYS_BETWEEN`（小数の日数）、PostgreSQL の DATE どうしなら `DATEDIFF('DAY', b, a)`、MySQL（YYYYMMDD の数の引き算）は ERROR `RESIDUAL_H2` |
| 書式・変換 | `TO_CHAR(hired, 'YYYY-MM')`、MySQL の `DATE_FORMAT`、`CAST(x AS ...)`、`name::text` | 日付の書式はセッションのタイムゾーンと言語に従う（`APP_SEMANTICS`）。実行計画では MySQL の `DATE_FORMAT` を H2 の `FORMATDATETIME` に書き換える（`'%Y年%m月%d日'` のような文字も書ける。H2 に無い `%U` などは ERROR `RESIDUAL_H2`）。月や曜日の名前（Oracle の `MON` / `DAY` / `DY` / `AM`、MySQL の `%b` / `%a` / `%p`）は、計画を動かす JVM の言語設定によらず英語で読み書きする（Oracle の NLS_DATE_LANGUAGE の既定 AMERICAN、MySQL の lc_time_names の既定 en_US と同じ。日本語の JVM では H2 が `NOV` を読めなかった、#160）。値の位置の `CAST('5' AS NUMBER)` は ERROR `UNSUPPORTED` |
| 割り算 | `COUNT(*) / 4`、`7 / 2`、`COUNT(*) * 100 / 3`、`c / 4`（`c` は派生表・CTE の `COUNT(*)`） | Oracle・MySQL の実行計画では、H2 が整数どうしを整数で割らないよう、取得した列から来ない整数（整数リテラル、`COUNT`、`LENGTH` など、それらの `MAX` / `MIN` / `SUM`、それを返すスカラー副問合せ、それを持つ派生表・CTE の列）の左辺を `CAST(... AS NUMBER(19))` で包む（#160）。修飾の無い列名で FROM に複数の表があると、どの表の列か決められないので包まない。PostgreSQL は整数の割り算のまま |
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
| `TO_DATE('81-11-17', 'RR-MM-DD')`（2 桁の年） | `'1981-11-17'` | OK（INFO `DATE_LIT`） | Oracle の規則で読む。`RR` は今年に近い世紀（2000〜2049 年なら 50〜99 は 19xx）、`RRRR` は 4 桁ならそのまま、`YY` は今世紀。PostgreSQL・MySQL の 2 桁の年は 1970〜2069。実行計画の H2 は `RR` を今世紀と読むので、定数は残りの SQL でも ISO に直し、列や bind の `RR` は ERROR `RESIDUAL_H2` |
| `TO_DATE('2460000', 'J')`（直せない書式） | — | ERROR `DATE_FMT`（読み取りなら PLANNED） | アプリで変換してバインドする |
| `TO_DATE(:s, 'YYYY-MM-DD')`（値がバインド変数） | — | ERROR `EXPR`（読み取りなら PLANNED） | |
| DATE 列に 0 時の日時 | 日付だけ | OK（INFO `DATE_LIT`） | |
| DATE 列に 0 時以外の日時（INSERT・SET） | 日付だけ | WARN `DATE_LIT` | 時刻は落ちる |
| DATE 列と 0 時以外の日時の比較 | 同じ日付が当たる境界（`< t` → `<= 日付`、`>= t` → `> 日付`、BETWEEN の下限は翌日、`<> t` → `IS NOT NULL`） | WARN `DATE_LIT` | `= t` は常に偽なので PLANNED（`DATE_LIT`）。実行計画の取得も同じ規則で押し下げる |
| 整数の列に小数（INSERT・SET） | 四捨五入した整数 | WARN `TYPE_LIT` | 移行元が格納する値と同じ |
| TEXT の列に数値（INSERT・SET） | 書いたとおりの文字列 | WARN `TYPE_LIT` | 移行元は自分の数の書式で文字にする |
| TIMESTAMP 列に日付だけ（`DATE '2024-01-01'`） | `'2024-01-01 00:00:00'` | OK（INFO `DATE_LIT`） | |
| TIMESTAMPTZ 列に時差つき（`'... 10:00:00+09:00'`） | UTC の `'2024-01-01 01:00:00 Z'` | OK（INFO `DATE_LIT`） | |
| TIMESTAMPTZ 列に時差なし | UTC と見なして末尾 `Z` | WARN `TZ_ASSUMED_UTC` | `--session-time-zone Asia/Tokyo` を渡すと、その地域の時刻として UTC に直す（INFO `DATE_LIT`）。PostgreSQL の実行計画では、H2 もそのゾーンで動かす（`residual.java.time_zone`、#160） |
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
| `DELETE FROM emp WHERE ROWNUM <= 10`（UPDATE も） | — | ERROR `ROWNUM` | ScalarDB に行番号は無い（DB-SQL-10002）。先に `SELECT ... LIMIT n` でキーを読み、主キーで書く |
| Oracle の DB link（`emp@remote`、読み書きとも） | — | ERROR `DBLINK` | 実行計画も作らない。相手の表を ScalarDB に移すか、アプリから相手の DB に問い合わせる |
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
| `WHERE ROWNUM <= :n` | `LIMIT :n` | WARN `ROWNUM`、WARN `LIMIT` | `<` とバインド変数の組は ERROR。0 を渡すと ScalarDB は全件を返す |
| `WHERE ROWNUM <= 0` / `ROWNUM < 1` | — | PLANNED（`LIMIT`） | ScalarDB SQL の `LIMIT 0` は上限なしで全件を返す。計画の H2 は 0 件を返す |
| 集約・DISTINCT・GROUP BY・ウィンドウ関数と一緒の ROWNUM | — | PLANNED（`ROWNUM`） | ROWNUM は入力の行、LIMIT は出力の行を数える |
| `ROWNUM > 1`、OR の中、LIMIT との併用、整数でない比較 | — | PLANNED（`ROWNUM`） | |
| 射影の `SELECT ROWNUM, name ...` | — | PLANNED（`ROWNUM`） | ScalarDB に行番号は無い。実行計画の H2 が番号を振る。計画を作れなければ ERROR `ROWNUM` |
| `FETCH FIRST 3 ROWS ONLY` / `FETCH FIRST ROW ONLY` | `LIMIT 3` / `LIMIT 1` | OK（INFO `LIMIT`） | |
| `LIMIT 10` | そのまま | OK | |
| `LIMIT ?` / `LIMIT :n` | そのまま | WARN `LIMIT` | 0 を渡すと ScalarDB は全件を返す。0 以下ならアプリで問い合わせを飛ばす |
| `LIMIT 0` / `FETCH FIRST 0 ROWS ONLY` | — | PLANNED（`LIMIT`） | ScalarDB SQL の `LIMIT 0` は上限なし |
| `FETCH ... WITH TIES` / `FETCH ... PERCENT` | — | PLANNED（`LIMIT`） | LIMIT では同じ順位の行が落ちる |
| `OFFSET 10 ROWS` / `LIMIT 10 OFFSET 5` / MySQL の `LIMIT 5, 10` | — | PLANNED（`OFFSET`） | クラスタリングキーの範囲でページを送る形に直すとよい |

### シーケンス

| 元の書き方 | 変換後 | 判定と指摘コード | 注意 |
|---|---|---|---|
| `CREATE SEQUENCE` / `DROP SEQUENCE` | — | ERROR `DDL` | |
| 値の `emp_seq.NEXTVAL` / `CURRVAL`、PostgreSQL の `nextval('seq')` | — | ERROR `SEQUENCE` | アプリで採番する（UUID など） |
| `SELECT emp_seq.NEXTVAL FROM dual`、`SELECT nextval('seq')` | — | ERROR `SEQUENCE` | 実行計画も作らない。アプリで採番する |
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
| Oracle | 整数型、`FLOAT`、`DATE`、精度なし `NUMBER`、`LONG`、`LONG RAW`、`XMLTYPE` | すべて WARN `TYPE`（上の「データ型」） |
| Oracle | `/` だけの行 | 文の切れ目として扱う |
| Oracle | 数字の bind（`:1`、`:2`。JDBC・OCI、V$SQL の形） | 変換後の SQL では、出てくる順に `?` にする（ScalarDB SQL に数字の bind は無い）。番号の順と違う・同じ番号が繰り返すときは WARN `BIND_ORDER` で渡す順を示す。計画でも文の中の位置で `:1`、`:2`… と名前を付け直し、番号の順と違うときは WARN `BIND_ORDER` で渡す順を示す（#153） |
| Oracle | q 引用（`q'[it's]'`、`nq'{...}'`） | 通常のリテラル（`'it''s'`）に直してから読む |
| Oracle | DB link（`emp@remote`） | ERROR `DBLINK`（上の「INSERT / UPDATE / DELETE / MERGE」） |
| Oracle | PL/SQL のブロック（`CREATE PROCEDURE` など、`BEGIN` / `DECLARE` の無名ブロック） | ERROR `PLSQL_BLOCK`。始まる所から `/` までを 1 文にする（前に `;` で終わる文があっても割らない）。PL/SQL の移行ツールで扱う |
| Oracle | `WITH FUNCTION ...`（WITH 句の PL/SQL） | ERROR `WITH_PLSQL`。関数をアプリに移せば、問い合わせは変換か実行計画にできる |
| PostgreSQL | `ILIKE`、`ONLY`、`DISTINCT ON`、`BETWEEN SYMMETRIC`、`FILTER`、`$1` | 上の各表 |
| PostgreSQL | `ON CONFLICT` | `UPSERT INTO` か ERROR（上の書き込みの表） |
| PostgreSQL | 精度なし `NUMERIC`、`SERIAL`、`TIMETZ`、`JSONB` | 上の「データ型」 |
| MySQL | 文字列の比較（`=`・`<>`・`LIKE`・`IN`） | INFO `SEMANTICS`。MySQL の既定は大文字小文字を区別しない。ScalarDB は厳密に比べる。数値の列と比べる `'5'` は数値に直すので出さない |
| MySQL | `REPLACE INTO`、`INSERT IGNORE`、`ON DUPLICATE KEY UPDATE` | 上の書き込みの表 |
| MySQL | `LIMIT 5, 10`、`WITH ROLLUP`、`SQL_CALC_FOUND_ROWS` | 上の各表 |
| MySQL | `TINYINT(1)`、符号なし整数、精度なし `DECIMAL`、`TIMESTAMP` | 上の「データ型」 |
| すべて | NULL の並ぶ位置 | Oracle・PostgreSQL は昇順で NULL が最後、MySQL は最初。実行計画の H2 では `NULLS FIRST / LAST` を明示して元の並びを保つ。変換した MySQL の文には WARN `NULLS`（上の「SELECT の句」） |

### アプリ側に移す処理

ERROR か PLANNED になった読み取り文には、変換器が最初につまずいた所だけでなく、文全体（WITH の本体、副問い合わせを含む）を調べた結果が付きます。

| 指摘コード | 重要度 | 内容 |
|---|---|---|
| `CTE`・`SUBQUERY`・`SET_OP`・`HIERARCHICAL`・`WINDOW`・`KEEP`・`PIVOT`・`DISTINCT`・`OFFSET`・`PROJECTION`・`GROUP`・`PRED`・`NOW`・`ORDER` | ERROR | アプリに移す構文を、場所（主問い合わせ、WITH の名前、副問い合わせ）つきで全部挙げる |
| `RESIDUAL_H2` | ERROR | H2 が実行できない構文（`CONNECT BY`、`ROLLUP` など、`PIVOT` / `UNPIVOT`、`KEEP`、`FULL OUTER JOIN`、`LATERAL`、`SAMPLE`、再帰 WITH の `SEARCH`、`JSON_TABLE`）。実行計画を作らない |
| `APP_SEMANTICS` | WARN | アプリで書き直すときに結果を変えないための注意（`LAG` / `LEAD`、0 除算、MySQL の `DIV`、`ROUND`、集約と NULL、順位、NULL と文字列の並び、`SYS_CONNECT_BY_PATH`、`LEVEL`、`ADD_MONTHS`、日付の書式、現在時刻、空文字列）。ERROR の文にだけ付く |
| `DESIGN` | INFO | 設計の提案（表定義を渡す、階層の事前計算、GROUP BY のキーでの集計表、結合列のキーか索引、JDBC 以外ではキーを持たせる、分析の問い合わせには ScalarDB Analytics） |
| `PLAN_FETCH` / `PLAN_RESIDUAL` | INFO | 実行計画の取得 1 つずつと、H2 が元の SQL を実行すること |
| `PLAN_CROSS_PARTITION` / `PLAN_UNRESOLVED` | WARN | 取得にクロスパーティション SCAN が要る / 表か列を解決できない所がある（`python:` で始まるものは INFO。Python の参照実装（SQLite）だけの制限で、Java のランタイムには関係しない） |
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
| `CREATE USER` など、SQLGlot が文として解析しないもの | ERROR `UNPARSED` | `ALTER TABLE` は操作ごとに読み直す（上の DDL） |
| `GRANT`、`SET search_path ...`、MySQL の `SET NAMES`、`SAVEPOINT` | ERROR `STATEMENT` | 名前つきトランザクションとセーブポイントは ERROR `SAVEPOINT` の場合もある |
| PL/SQL のブロック、`WITH FUNCTION` | ERROR `PLSQL_BLOCK` / `WITH_PLSQL` | 上の「方言ごとの差」 |
| 移行元の方言として読めない文。文でなく式として読めたもの（綴りを誤った `SELEC * FRM t` など） | ERROR `PARSE` | `--source` と綴りを確かめる |
| ScalarDB SQL の生成器が出せない構文が残った | ERROR `UNSUPPORTED` | |
| 引用符の閉じ忘れなどで文に分けられない | ERROR `TOKENIZE` | `;` で終わる行ごとに分け直し、読めた文は変換する。読めない文だけが ERROR。ScalarDB 以外の Target（`--target postgres` など）でも同じ（#160） |
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
| 引数の既定値（`DEFAULT`） | 呼び出し文でも式の中の function 呼び出しでも、省いた引数に既定値を補って渡す。リテラル（NULL・数・文字列・TRUE / FALSE）はそのまま書き、式（`DEFAULT SYSDATE`、`DEFAULT pkg.c_limit`、`DEFAULT next_no()`）は呼ばれる側の Service に作る `defaultOf<Routine><引数>()` を呼んで渡す | 省いた `DEFAULT SYSDATE` は、呼ぶ側の時計の読みとして SEM-007 に数える（呼び出し文のとき） | Oracle と同じく、省いた呼び出しのたびに、呼ばれる側の宣言の場所の名前で評価します。すべての引数に既定値がある function は、括弧なしの呼び出し（`pkg.label \|\| 'x'`）でも補います。package の**変数**を既定値に持つもの（`DEFAULT g_level`）は、`packageState.carried` で運ぶと決めたときだけ、呼ぶ側が運んでいる値を渡します（省く呼び出しをする routine も運ぶ側になります）。決めていなければ、既定値が `USER` / `SYSTIMESTAMP` を読むとき、sequence を採るとき、routine を呼ぶ既定値を 2 つ以上省くとき（Oracle は評価の順を決めていない）は、今までどおりその文を断ります（生成で確認、#141） |
| 名前付き引数（`p_a => 1`） | 呼び出し文でも式の中の function 呼び出しでも、引数の順に並べ替えて渡す | なし | 式の中で並べ替えるのは、同じ package の routine と、ほかの module の routine（オーバーロードの無いもの）です（生成で確認） |
| オーバーロード | 版ごとに番号を付けたメソッド（`fmt` → `fmt1`、`fmt2`） | 呼び出しがどの版か決まらないと CALL-002（REVIEW） | 引数の数と名前だけで選びます。型だけが違う版は選べません（生成で確認） |
| 宣言部の入れ子の procedure / function（入れ子のブロックの DECLARE に書いたものを含む） | 外側の変数を引数で運ぶ private メソッドに持ち上げる。外側の routine が開いた cursor を FETCH するものには、その cursor の状態（`Plsql.Cursor`）を引数で渡す。package の routine や外側の routine と同じ名前のものは `<外側の名前>_<名前>` で持ち上げ、それが見える範囲（外側の本体と handler、自分自身、後に宣言した兄弟）の呼び出しをその名前に向ける | なし | 同じ名前の入れ子は、外側の routine の中では入れ子のほうを、`pkg.f` と書けば package のほうを、ほかの routine は package のほうを呼びます（Oracle 26ai で確認、#160）。持ち上げられないもの（名前がほかの入れ子の subprogram と重なる、外側の例外を使う、外側と同じ cursor を両方で FETCH する、外側の handler が読む変数に代入する、内側のブロックが同じ名前を宣言し直す）は LOWER-001（REVIEW）。型だけが違うオーバーロードは持ち上げません（生成で確認） |
| ラベル・routine 名で修飾した名前（`<<outer>>` の `outer.x`、ループの `outer_loop.i`、`dept_name.department_name`） | 修飾が指す宣言の Java 名。内側で隠された変数は Java の別名（`x_2` など）で宣言されているので、修飾した参照はもとの `x` を指す | なし | PL/SQL の文・式・INTO の先で解決します。SQL 文の中でブロックのラベルで修飾した名前は、まだ列として読むので断られます（生成で確認） |
| 別の package の routine の呼び出し | 呼ばれる側の Service をコンストラクタで受け取って呼ぶ | 呼び先の判定を引き継ぐ | 生成で確認 |
| package の定数（`CONSTANT`） | `private static final` のフィールド | なし | 値がリテラルの定数だけです。値が式の定数（`gc_line_feed CONSTANT VARCHAR2(1) := chr(10)`）はフィールドにせず、それを読む文を断ります（以前は無いフィールドを参照する Java になっていた）（生成で確認） |
| trigger | `Trg<Name>Service` の `body(...)` メソッド。`:NEW` / `:OLD` の列が引数 | TRG-001（REDESIGN） | trigger の節を参照 |
| 呼び出し仕様（`LANGUAGE JAVA` / `C`、`EXTERNAL`） | 本体を持たず `UnsupportedOperationException` を投げるメソッド | EXT-002（REDESIGN） | 生成で確認 |
| `AUTHID CURRENT_USER` | 生成物は変わらない | AUTHID-001（REDESIGN） | 単体の routine に書いたものと、package の仕様に書いたもの（本体の routine すべてに当たる）を検出します |
| トランザクションの境界 | どのメソッドも begin・commit・rollback をしない。Repository は呼び出し側の `Connection` を受け取る | なし | 境界は呼び出し側が持ちます。Spring の注釈も出しません |
| 元のソースの位置 | 各文の前に `// ファイル:行` のコメント | なし | `traceability.csv` にも出ます |

### データ型

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `NUMBER(p)`（p ≤ 9 / ≤ 18 / > 18） | `Integer` / `Long` / `BigDecimal` | なし | 代入は `Plsql.fitInt` / `fitLong` / `fit` で四捨五入し、桁あふれは ORA-06502 |
| `NUMBER(p, s)`（s > 0） | `BigDecimal` | なし | 代入は `Plsql.fit(v, p, s)`（half-up）。ScalarDB には 10^s 倍した BIGINT で置き、`Plsql.bind` / `Plsql.read` で往復します |
| 精度なしの `NUMBER` | `BigDecimal` | なし | 保存形は TEXT。今のデータが long に収まっても long にしません |
| `INTEGER` / `INT` / `SMALLINT` | `BigDecimal` | なし | 変数への代入は `Plsql.fit(v, 38, 0)` で整数に丸めます。引数と戻り値では丸めません |
| `PLS_INTEGER` / `BINARY_INTEGER` / `NATURAL` など | `Integer` | なし | 代入は `Plsql.toInt` が 32 ビットを超えると ORA-01426（`Plsql.NumericOverflow`）。演算は式と演算子の節を参照 |
| `SIMPLE_INTEGER` | `Integer` | なし | NOT NULL（`Plsql.notNull`）。演算は 32 ビットで折り返し、例外になりません（`2147483647 + 1` は `-2147483648`。Oracle 26ai で確認） |
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
| `SUBTYPE`（package の仕様） | 基底型の Java 型 | なし | routine の中の SUBTYPE と同じ扱いです。別の package の SUBTYPE（`pkg.t`）も引きます。引数と戻り値の型にしたときは、Oracle と同じく NOT NULL と数値の RANGE だけを受け継ぎ、長さ・精度は受け継ぎません |
| `変数 表.列%TYPE` | その列の Oracle の型に対応する Java 型 | なし | `schema.sql`（Oracle の DDL）から引きます |
| `変数 表%ROWTYPE` / `cursor%ROWTYPE` | 生成した record（`EmpRow`、`CEmpRow`） | なし | 列名が record の要素名です。field への代入は record を作り直します（Java の record は不変）。`NUMBER(p[,s])` の field へ入れる値は、同じ型の変数と同じく `Plsql.fit` / `fitLong` / `fitInt` で丸めて桁を検査し、field の Java の型（`NUMBER(10)` なら `Long`）にします（生成で確認） |
| `TYPE t IS RECORD (...)` | 生成した record（`TPair`） | なし | 各 field は NULL か既定値で作ります（生成で確認） |
| コレクション型 | コレクションの節を参照 | | |
| `SYS_REFCURSOR` / `REF CURSOR` | cursor の節を参照 | | |
| `CREATE TYPE ... AS OBJECT`（スキーマのオブジェクト型） | 生成した record。`AS TABLE OF` はその `List` | なし | コンストラクタ `t_point(1, 2)` は `new TPoint(...)`（生成で確認） |
| 解決できない型（どこにも宣言の無い名前、対応の無い型） | `Object` | 型解決の因子が 0 になり REVIEW | 生成器は型を推測しません。`SYS_REFCURSOR`、`REF CURSOR` の型、`ROWID` など、意図して `Object` にする型は因子を下げません |

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
| PLS_INTEGER（FOR ループの添字を含む）どうし、PLS_INTEGER と 32 ビットに収まる整数リテラル、整数リテラルどうしの `+`、`-`、`*`、単項の `-` | 1 回の演算ごとに `Plsql.plsInteger(...)` | なし | 途中で 32 ビットを超えると ORA-01426 です（`v + 1 - 1` は v が 2147483647 なら例外。最後に範囲へ戻っても同じ。`2147483647 + 1` や `65536 * 65536` のようにリテラルだけでも同じ。-2147483648 の `-p` も同じ）。`/` と、32 ビットを超えるリテラルや NUMBER との演算は NUMBER です（Oracle 26ai で 53 通りを確認、#160） |
| SIMPLE_INTEGER どうし、SIMPLE_INTEGER と整数リテラルの `+`、`-`、`*`、SIMPLE_INTEGER の単項の `-` | 1 回の演算ごとに下位 32 ビットを取る（`Integer.valueOf(((Number) ...).intValue())`） | なし | 2 の補数で折り返します（`s * 2147483647` も、-2147483648 の `-s` も）。PLS_INTEGER と混ぜた演算（`s + p`、`p + s`、`s * p`）は PLS_INTEGER として ORA-01426 になります。符号を付けたリテラルは PLS_INTEGER の式なので、`s - 1` は折り返し、`s + (-1)` は ORA-01426 です。左から順に型が決まるので、`s + 1 + p` は先に折り返し、`p + s + 1` は例外です（Oracle 26ai で確認、#160） |
| 中置の `n MOD j` | `Plsql.mod(n, j)`（関数の `MOD(n, j)` と同じ） | なし | `*` と `/` と同じ強さで結びます（`a + b MOD 3 * 2` は `a + ((b MOD 3) * 2)`） |
| `DATE + n`、`DATE - n`、`DATE - DATE` | `Plsql.add` / `Plsql.sub` | なし | 日数の足し引きです。DATE どうしの差は日数（小数つき）です |
| 単項の `-x` | `Plsql.neg(x)` | なし | |
| `x IN (...)`、`BETWEEN`、`LIKE`（と `NOT`） | `Plsql.in`、`between`、`like`（`notIn` など） | なし | NULL が絡むと真になりません |
| `x LIKE p ESCAPE c` | `Plsql.like(x, p, c)`（`notLike`） | なし | 26ai の PL/SQL で測った動き: ESCAPE が NULL なら UNKNOWN、1 文字でない（`''` も）と ORA-06502、エスケープ文字のあとが `%`・`_`・自分以外か、パターンの最後にあると LIKE は偽・NOT LIKE は真（SQL なら ORA-01424）（#140） |
| 問い合わせ指令 `$$PLSQL_UNIT`、`$$PLSQL_LINE`、`$$flag` | 単位の名前（大文字）の文字列、行番号の数、`limits.yaml` の `conditionalCompilation.flags` の値（無ければ NULL） | なし | `$$PLSQL_LINE` は読み込むときに、単位の中の行番号（`PROCEDURE` などのある行が 1）に置き換えます。桁は空白で埋め、列はずらしません。`$$PLSQL_CCFLAGS` はフラグを 1 つも決めていなければ NULL、決めていれば断ります（Oracle の書き方を再現しない）。ほかの `$$PLSQL_CODE_TYPE` などは移行元の設定なので断ります（#140） |
| CASE 式 | 三項演算子（`Plsql.eq(p, 1) ? "one" : "many"`） | なし | 生成で確認 |
| 文字列を NUMBER に代入 | `Plsql.dec(...)` | なし | 数値にならないと ORA-06502（`Plsql.ValueError`） |
| 日付・TIMESTAMP を書式なしで文字にする（`'d=' \|\| d`、`TO_CHAR(d)`） | `Plsql.concat` / `Plsql.text` / `Plsql.timestampText` が `limits.yaml` の `nls` の `dateFormat` / `timestampFormat` / `timestampTzFormat`（決めていなければ Oracle の既定 `DD-MON-RR` など、AMERICAN）で書く | `nls` を決めていなければ SEM-012（REVIEW）。決めていれば外れる（NLS_DECIDED） | TIMESTAMP(p) の `FF` は宣言の桁、TIMESTAMP(0) は小数点ごと書きません（#94、#157） |
| 数値を文字にする | `Plsql.text(n)` | なし | 1 未満は `.5` のように先頭の 0 を書きません（Oracle と同じ）。小数点は `nls.numericCharacters` の 1 文字目（`,.` なら `1234,5`）。文字を数値にする暗黙の変換も同じ文字を読みます（#157）。固定小数点で 100 文字を超える数は、仮数を 0 で埋めて全体を 100 文字にした指数形式で書きます（`1e100` は `1.000…000E+100`。PL/SQL の書き方で、Oracle 26ai で測った。SQL の `TO_CHAR(n)` は 40 文字で丸める別の規則。#161） |
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
| `UPPER`、`LOWER`、`INITCAP`、`LENGTH`、`SUBSTR`、`INSTR`、`REPLACE`、`LPAD`、`RPAD`、`TRIM`、`LTRIM`、`RTRIM`、`CONCAT`、`CHR`、`ASCII` | 同じ名前の `Plsql` の関数 | なし | NULL と空文字は Oracle と同じに扱います。`CHR` / `ASCII` の数はコードポイントでなく AL32UTF8 のバイト列です（`ASCII('あ')` は 14909826、`CHR(12354)` は `'0B'`。Oracle 26ai で測った、#161）。負の数と 2^32 以上の `CHR` は ORA-06502。Oracle は文字にならないバイト列（`CHR(128)`）もそのまま返しますが、ランタイムは持てないので ORA-06502 で断ります |
| `TO_CHAR(日付, 書式)` | `Plsql.text(v, 書式)`。DATE / TIMESTAMP と宣言した変数は `Plsql.textDate` / `Plsql.textTimestamp` | 書式に DAY・MON・AM など言語で変わる要素があり、`nls` を決めていなければ SEM-008（REVIEW）。ランタイムが断る形なら SEM-015（REVIEW） | 書式モデルの全体を Oracle 26ai で測ったとおりに書きます（#157、`OracleFormat`）: `YYYY YYY YY Y RRRR RR SYYYY Y,YYY IYYY IYY IY I CC SCC Q MM MON MONTH RM WW W IW D DD DDD DY DAY J HH HH12 HH24 MI SS SSSSS FF FF1-9 X AM PM A.M. P.M. AD BC A.D. B.C. TZH TZM TZR TZD YEAR SYEAR DS DL TS`、`FM`（切り替え）、`FX`、`"文字"`、句読点、`SP` / `TH` / `SPTH`、名前の大文字小文字（`Month` → `September`）と詰め物（英語は 9 文字）。言語は `nls.dateLanguage`（AMERICAN / ENGLISH / JAPANESE）、`D` と DS / DL / TS は `nls.territory`（AMERICA / JAPAN）。Oracle が断る書式は同じ番号（ORA-01821・01801・01822）。FF・X・TZH・TZM は DATE では ORA-01821、TIMESTAMP の TZR はセッションのタイムゾーン（UTC）。PL/SQL の `TO_CHAR(t, 'FF')` は宣言の桁によらず 9 桁です。断るもの: 1582-10-15 より前の日付（Oracle はユリウス暦で数える）、型の分からない値（record の列など）で秒の端数が 0 のものへの FF・X・TZR |
| `TO_CHAR(数値, 書式)` | `Plsql.text(v, 書式)` | ランタイムが断る形なら SEM-015（REVIEW） | 書式モデルの全体（#157）: `9 0 , . G D $ L C U S MI PR B EEEE V X RN TM TM9 TME`、`FM`。丸めは 0 から遠い方、入らなければ幅いっぱいの `#`、幅は「要素の数 + 符号の 1 桁」、`L` / `U` は 10 バイト・`C` は 7 バイト（`¥` は 2 バイトとして数える。データベースの文字コードは AL32UTF8 を前提）。`G` / `D` は `nls.numericCharacters`、`L` は `nls.currency`、`C` は `nls.isoCurrency`、`U` は `nls.dualCurrency`。Oracle が断る書式は ORA-01481。断るもの: `FM` と `B` を一緒に使う書式、数字も小数点も無い書式（`L` だけなど） |
| `TO_NUMBER(v)` | `Plsql.toNumber(v)` | なし | 小数点は `nls.numericCharacters` の 1 文字目だけを読みます |
| `TO_NUMBER(v, 書式)` | `Plsql.toNumber(v, 書式)` | なし | 書式どおりに読めなければ ORA-06502（`Plsql.ValueError`。VALUE_ERROR で捕まる）: 桁区切りは書式の位置に要り、`0` の桁は省けず、小数の桁は少なくてよく多いと誤り、前の空白は読み飛ばし後ろの空白は誤り、`RN`・`TM`・`V` は読めません（26ai で測定、#157）。SQL 文の中の書式つき `TO_NUMBER` は SQL の外へ出しません（出すと ORA-01722 と ORA-06502 の違いが要るため） |
| `TO_DATE(v, 書式)` | `Plsql.toDate(v, 書式)` | なし | 要素ごとに Oracle と同じ読み方をします（桁の少ない数字、RR の世紀、省いた年月は現在、入力が時刻の要素の前で終わるのは可・日付の要素の前で終わると ORA-01840）。読めないときは Oracle と同じ番号の `Plsql.FunctionError`（ORA-01830・01841・01843・01847・01839・01850・01849・01851・01852・01855・01858）で、VALUE_ERROR では捕まりません。WHEN OTHERS では `SQLCODE` がその番号になります（#140。以前は ORA-06502） |
| `TO_TIMESTAMP(v[, 書式])` | `Plsql.toTimestamp(v, 書式)` | なし | TO_DATE の読み方に `FF` / `FFn`（小数秒。桁が多いと ORA-01830）と `X`（`nls.numericCharacters` の小数点）を足したもの。書式を省くと `nls.timestampFormat`（既定 `DD-MON-RR HH.MI.SSXFF AM`、#140） |
| `TO_DATE` の NLS | 同上 | なし | 書式を省くと `nls.dateFormat`（既定 `DD-MON-RR`）。月の名前と午前・午後は `nls.dateLanguage` のもの（JAPANESE なら `9月`、`午前`）。`MM` は月の名前も読み、名前のあとの空白は読み飛ばします。4 桁を読んだ `RR` のあとに残りがあると ORA-01861（#157） |
| `ADD_MONTHS`、`LAST_DAY` | `Plsql.addMonths`、`lastDay` | なし | |
| `MONTHS_BETWEEN(d1, d2)` | `Plsql.monthsBetween` | なし | 同じ日か両方が月末なら整数（時刻は無視）、ほかは 31 日を 1 か月とした小数を NUMBER と同じ 40 桁に丸めます。規則は実行計画の H2 が使う `OracleFunctions.monthsBetween` と共有します。TIMESTAMP は秒未満を落とし、文字は TO_DATE の既定の書式で読みます（#140） |
| `EXTRACT(field FROM d)` | `Plsql.extract("YEAR", d)` | なし | YEAR・MONTH・DAY・HOUR・MINUTE・SECOND（小数つき）と、WITH TIME ZONE の TIMEZONE_HOUR・TIMEZONE_MINUTE。WITH TIME ZONE の日時の field は Oracle と同じく UTC のものです。INTERVAL（日時の差）からの EXTRACT は、生成コードでは差が日数なので断ります。TIMEZONE_REGION / ABBR も断ります（#140） |
| `NULLIF(a, b)` | `Plsql.nullif` | なし | 比較は `=` と同じ（`NULLIF('1', 1)` は NULL、数値にならない文字は ORA-06502）（#140） |
| `LENGTHB(s)` | `Plsql.lengthb` | なし | データベースの文字集合が AL32UTF8 である前提で、UTF-8 のバイト数を数えます（'日本a' は 7）。ほかの文字集合の移行元では値が変わります（#140） |
| `TRANSLATE(s, from, to)` | `Plsql.translate` | なし | 文字（コードポイント）ごとの置き換え。`to` に対応の無い文字は消え、引数のどれかが NULL（`''` も）なら NULL（#140） |
| `BITAND(a, b)` | `Plsql.bitand` | なし | 整数部（切り捨て）どうしの 2 の補数のビット積。-2^127〜2^127-1 の外は ORA-06502（#140） |
| `RAWTOHEX(r)`、`HEXTORAW(s)` | `Plsql.rawToHex`、`Plsql.hexToRaw`（RAW は `byte[]`） | なし | PL/SQL の意味です。PL/SQL の `RAWTOHEX('ab')` は文字を 16 進として読んで 'AB'（SQL では文字のバイトで '6162'）。16 進でない文字は ORA-06502。奇数桁は先頭に 0 を足します。RAW を文字にすると大文字の 16 進、RAW どうしの `=` はバイトの比較です（#140） |
| `REGEXP_LIKE`、`REGEXP_SUBSTR`、`REGEXP_REPLACE`、`REGEXP_INSTR`、`REGEXP_COUNT` | `Plsql.regexpLike` など。パターンは実行時に `OracleRegex` が Java の正規表現に訳す | なし | Oracle の方言に合わせます: POSIX の文字クラス（`[[:digit:]]` など。Unicode の文字も含む）、括弧式の中の `\` は文字そのもの、`\n` `\t` `\Q` などは文字そのもの、前に何も無い `*` は無視、区間でない `{` は文字、改行は LF だけ、`'x'` は括弧の外の空白だけを消す、`'i'` でも `[[:upper:]]` / `[[:lower:]]` は大文字小文字を区別、置換文字列の後方参照は `\1`〜`\9`（`$1` は文字）。数の引数は四捨五入し、範囲外は ORA-01428、誤ったパターンは ORA-12725〜12732、誤った match_parameter は ORA-01760。等価クラス `[[=e=]]`、`(?` で始まる括弧、量指定子の重ね（`a*+`）は Oracle と同じ意味にできないので、実行時に `UnsupportedOperationException` を投げます。REGEXP_LIKE は NULL を取る条件です（#140） |
| `SYSDATE` | `Plsql.sysdate()` | 1 つの routine で時計を 2 回以上読むと SEM-007（REVIEW） | 時計は `Plsql.setClock` で差し替えられます。宣言部の初期値（`v DATE := SYSDATE`）で読んだ分も回数に入ります |
| `SYSTIMESTAMP` | `audit.now()`（引数に `AuditContext audit` が足される） | 列へ書くと SEM-010（REVIEW） | `OffsetDateTime` を返します。TIMESTAMP の戻り値・変数へは `Plsql.moment(audit.now())`、DATE へは `Plsql.castDate(audit.now())` で入れます。Oracle と同じく、値の持つタイムゾーン（呼び出し側が渡す時計のもの。移行元ではデータベースサーバーの OS のもの）での日時を残してゾーンを落とし、DATE は秒未満も落とします。この向きの変換にはセッションのタイムゾーンは関わりません（生成で確認） |
| `USER` | `audit.user()` | なし | 何を記録するかは業務の決定です（`AuditContext` は呼び出し側が渡します） |
| `seq.NEXTVAL` | `sequences.next("seq")`（Repository が `Sequences` を受け取る） | なし | 採番は業務とは別のトランザクションで取ります。方式は DDL の `CACHE`（hi/lo）/ `NOCACHE`（counters 表と再試行）から決まります |
| `SQLCODE`、`SQLERRM`、`SQLERRM(n)` | 例外の節を参照 | | |
| `CURRENT_DATE`、`CURRENT_TIMESTAMP`、`LOCALTIMESTAMP`、`AT TIME ZONE` | 変換しない | SEM-002（REVIEW） | セッションのタイムゾーンで値が変わります |
| `SYS_GUID()` | `Plsql.sysGuid()`（16 バイトの `byte[]`） | SEM-014（REVIEW） | 値は乱数（`SecureRandom`）です。Oracle はホストとプロセスと連番から作ります。どちらにするかは移行先で決めます（#140） |
| `SYS_CONTEXT` | 変換しない | SEM-013（REDESIGN） | |
| `DECODE`、`DUMP` | 変換しない（`UnsupportedOperationException`） | ルールは下がらない | Oracle でも PL/SQL の式では使えません（PLS-00204）。理由をそう書いて断ります（#140） |
| `SYS.STANDARD.BITAND(...)` など `SYS.STANDARD.` / `STANDARD.` を付けた組み込み | 付けない名前と同じ | なし | 同じ名前の package の関数が組み込みを隠しているときの書き方です。解析の範囲に無い routine の呼び出し（CALL-001）にも数えません（#140） |
| 対応表に無い関数（`SOUNDEX`、`NUMTODSINTERVAL` など） | 変換しない（`UnsupportedOperationException`） | ルールは下がらない | 判定は下がらないので、`AUTO but not cleanly generated` で気づきます |

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
| `GOTO` | 前へ飛ぶものは、GOTO を含む文からラベルの手前までを包むラベルつきブロック `L: { ... break L; }`。後ろへ飛ぶものは、ラベルの文から GOTO を含む文までを包むラベルつきループ `L: while (true) { ... continue L; ... break L; }` | なし（組み直せないものは LOWER-002（REDESIGN）） | 前へ飛ぶ範囲が後ろへ飛ぶ範囲の途中から始まって交差するなど、入れ子にできない形は `UnsupportedOperationException` と理由を残します。Oracle が拒む GOTO（IF・LOOP・ブロックの中へ飛ぶ、文の無いラベル）も同じです（生成で確認） |
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
| `SELECT ... INTO v(i)`（コレクションの要素へ） | `Plsql.set(v, i, 値)`。`SELECT * INTO emp_tab(1)` の行はコレクションの要素の record として作る | なし | `v(i) := x` と同じ置き方です（生成とコンパイルで確認） |
| `INSERT` / `UPDATE` / `DELETE`（値が引数・変数） | `rowCount = repository.xxxStmtN(...)`（`executeUpdate`） | なし | 書く値は `Plsql.columnText` / `columnNumber` で列の長さと桁を検査し（ORA-12899 / ORA-01438）、`Plsql.bind(値, ScalarDB の型, scale)` で渡します |
| `INSERT INTO t VALUES rec` / `UPDATE t SET ROW = rec` | 列を 1 つずつ並べた文 | なし | 生成で確認（INSERT） |
| 列を読む式の `UPDATE`（`SET c = c + x`） | 決定が無ければ Repository が `UnsupportedOperationException` | SQL-001（REVIEW） | `rowLocks.optimistic` に書くと「同じトランザクションで読んでから書く」2 文に割ります。複数行なら読んだ行を回すループです（生成で確認） |
| `UPDATE ... RETURNING c INTO v` | 決定が無ければ断る。`rowLocks.optimistic` があれば、読む → 計算する → 書く → 書けたら v に代入 | 決定が無ければ SQL-001（REVIEW） | 生成で確認 |
| `INSERT ... VALUES (seq.NEXTVAL, ...) RETURNING id INTO v` | INSERT の前に番号を取り、それを書いて v に入れる | なし | 生成で確認 |
| `DELETE ... RETURNING c INTO v` | 消す行を先に読み、それから DELETE | なし | 変数へ受けて 2 行以上なら `TooManyRowsException`（生成で確認） |
| `MERGE` | 決定が無ければ SQL 変換器の結果しだい（断られれば `UnsupportedOperationException`） | SEM-006（REVIEW）。断られれば SQL-001 も | `rowLocks.optimistic` があれば「件数を読んで UPDATE か INSERT を選ぶ」に割り、SEM-011（注記）になります（生成で確認） |
| `SQL%ROWCOUNT` | `int rowCount`（DML ごとに更新。`SELECT INTO` のあとは 1。実行計画を通る `SELECT INTO` も同じ） | FORALL・動的 SQL・MERGE・呼び出し先が件数を決めうる routine で読むと SQL-004（REVIEW） | 数えるのは利用者が書いた読みだけです。trigger を呼ぶ前や RETURNING の書き換えで生成器が足した `SQL%ROWCOUNT > 0` と、trigger の呼び出しは数えません |
| `SQL%FOUND` / `SQL%NOTFOUND` | `rowCount > 0` / `rowCount == 0` | 同上 | 生成で確認 |
| ScalarDB SQL で直接は実行できず、実行計画に分解される文 | `PlanRunner.join(connection, plan, ...)`（ScalarDB から取得して H2 で実行） | SQL-002（REVIEW） | 行数上限と性能を確かめます（生成で確認） |
| ScalarDB が実行できない文 | Repository のメソッドが理由つきで `UnsupportedOperationException` | SQL-001（REVIEW） | |
| 結合 | そのまま実行できれば Repository の 1 文 | そのまま実行できない（警告・実行計画・拒否・スキーマ無し）と SEM-005（REVIEW） | |
| `WHERE p IS NULL OR col = p` | p が NULL のときの文と、等号で絞る文に分け、実行時に選ぶ | なし | ScalarDB SQL は bind の NULL 判定を WHERE に書けないためです |
| DDL の `DEFAULT` 列、`IDENTITY` 列を省いた INSERT | 省いた列を INSERT に足す（IDENTITY は採番） | なし | ScalarDB は主キーの無い INSERT を断るためです |
| CHECK 制約・外部キー・UNIQUE のある表への書き込み、子の表の外部キーが指す親の DELETE | `constraints.enforce` に書いた表だけ、書く前に検査（NOT NULL は ORA-01400 / ORA-01407、CHECK は ORA-02290、外部キーは ORA-02291 の例外。この順）。子のある親の DELETE は、外部キーが指すキーを等号で名指す DELETE なら、消す前に子の行を数えて ORA-02292（#154） | 書いていない表は診断 `CONSTRAINT_UNDECIDED` で CONS-001（REVIEW）。決めた表でも検査していないもの（解析器が読めない CHECK の条件や書き込みの文を含む。#161 までは黙って飛ばしていた）は `CONSTRAINT_NOT_GUARDED` で CONS-002（REVIEW） | 外部キーは親の行をキーで読みます。UPDATE は書く列に掛かる制約だけを見ます。`CREATE TABLE` の中の制約、`ALTER TABLE ... ADD [CONSTRAINT 名前] CHECK / FOREIGN KEY / UNIQUE`、`CREATE UNIQUE INDEX` を読みます。名前の無い制約は `<表>_check<n>` / `<表>_fk<n>` / `<表>_unique<n>` と呼びます。NOT NULL だけの表は未決に数えません（決めた表でだけ検査します） |
| PL/SQL の変数と表の列が同じ名前 | そのまま生成 | SQL-003（REVIEW） | Oracle は列として読みます |
| データ辞書（`USER_*`、`ALL_*`、`DBA_*`、`V$*`）を読む | SQL をそのまま生成 | DICT-001（REDESIGN） | 移行先には無い表です |
| 同じトランザクションで書いた表を走査する | 生成はするが、ScalarDB が実行時に拒否 | TX-004 / SCAN-001（REDESIGN） | `ScanAfterWriteException` になります。数えるのは走査（キーで届かない読み）だけで、キーで読むのは数えません。`COMMIT` と `ROLLBACK`（`ROLLBACK TO` を除く）のあとの読みは新しいトランザクションなので数えません。どの道でも通る `COMMIT` だけが効き、IF の片方の枝や、回らないかもしれないループの中の `COMMIT` は効きません。ScalarDB のスキーマ（`--scalardb-schema`）が無いときは読み方が分からないので、書いた表の読みをすべて TX-004 にします |
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
| OUT 引数の `SYS_REFCURSOR`（`OPEN p_rc FOR SELECT ...`） | 行を読み、`List<...Row>` を結果の record の要素にして返す | 行数上限は CUR-002 と同じ | `RETURN rc` の形と同じく、呼び出し側は FETCH の代わりに List を受け取ります（生成で確認） |
| OUT 引数の `SYS_REFCURSOR` を 2 回以上 OPEN する（続けて、IF / ELSE の分岐で、handler で）。どの OPEN も同じ列を選ぶ | OPEN ごとに行を読んで引数に入れ直す。結果の record は最後に走った OPEN の行を持つ。行の record は最初の OPEN のものを共有する | 行数上限は CUR-002 と同じ | Oracle は最後に走った OPEN を呼び出し側へ渡します（26ai 23.26.3 で確認）。前の OPEN の行も読むので、行数上限はどの OPEN にも掛かります。列は名前と、行の record での Java の型で比べます（#160。生成した Java を H2 で動かして確認） |
| OUT 引数の `SYS_REFCURSOR` を routine の中で FETCH する、CLOSE する、または列の違う問合せで OPEN する | 断る。OPEN の所で `UnsupportedOperationException` を投げる（理由つき） | CUR-004（REDESIGN） | Oracle では、FETCH した routine の呼び出し側は続きの行から読み（%ROWCOUNT も続きから）、CLOSE した routine の呼び出し側は最初の FETCH で ORA-01001 になり、列の違う OPEN は最後に走った OPEN の列が渡ります（26ai 23.26.3 で確認）。どれも全部の行の List 1 つでは表せないので、黙って null を返さずに断ります。CLOSE したあとでもう一度 OPEN する形（Oracle は後の OPEN を渡す）も、経路ごとの順序を見ないと区別できないので断ります。以前は routine の中の cursor に読むだけで、引数は null で返っていました（#160） |
| `FOR UPDATE` の cursor、`WHERE CURRENT OF c` | 行ロックの節を参照 | LOCK-001 / LOCK-002（REDESIGN） | |

### コレクション

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `TABLE OF x INDEX BY VARCHAR2(n)` | `Map<String, x>`（`Plsql.indexBy()`、キー順の TreeMap） | なし | FIRST / NEXT はキー順に回ります（生成で確認） |
| `TABLE OF x INDEX BY PLS_INTEGER` | `Map<Integer, x>` | なし | 0 や負のキーも取れます（生成で確認） |
| `TABLE OF x`（ネスト表） | `List<x>`（1 始まり）。コンストラクタ `t(1, 2)` は `Plsql.table(...)` | なし | 初期化しないと `null` で、使うと ORA-06531 |
| `VARRAY(n) OF x` | `List<x>`。コンストラクタ `t(1, 2)` は `Plsql.varray(n, ...)` で、上限 n を持つ List（`Plsql.Varray`）を作る。BULK COLLECT で入れるときは `Plsql.varrayOf(n, ...)` | なし | `v.LIMIT` は定数 n になります。上限は代入（複製）・引数・外側のコレクションの要素になっても値について回ります。n を超える要素のコンストラクタ、n を超える EXTEND、n を超える添字は ORA-06532（EXTEND は 1 つも足しません）。n を超える行の BULK COLLECT は ORA-22165（Oracle 26ai で確認、#160） |
| 要素の読み `v(i)` / 書き `v(i) := x` | `Plsql.at(v, i)` / `Plsql.set(v, i, x)` | なし | 1 未満と VARRAY の上限を超える添字は ORA-06532、COUNT を超える添字は ORA-06533、無いキーは NO_DATA_FOUND、NULL のキーは ORA-06502 |
| `COUNT`、`FIRST`、`LAST`、`NEXT(i)`、`PRIOR(i)`、`EXISTS(i)` | `Plsql.count`、`first`、`last`、`next`、`prior`、`exists` | なし | 生成で確認 |
| `DELETE`、`DELETE(i)`、`DELETE(i, j)` | `Plsql.delete` | なし | ネスト表の途中の DELETE は隙間として持ちます |
| `EXTEND`、`EXTEND(n)`、`EXTEND(n, i)`、`TRIM`、`TRIM(n)` | `Plsql.extend`、`Plsql.trimTable` | なし | VARRAY の上限を超える EXTEND は ORA-06532（上の VARRAY の行）。生成で確認 |
| ネスト表どうしの `=` | 要素を多重集合として比べる | なし | |
| コレクションの代入 | 値を複製する | なし | PL/SQL の代入は複製なので |
| record の要素・コレクションの要素の record の field への代入 | record を作り直して置き換える | なし | |
| `PIPELINED` の function と `PIPE ROW` | `List` を返すメソッド。`PIPE ROW` は List に足す | なし | 生成で確認 |
| `TABLE(v)` を 1 つだけ読む問合せ（`SELECT [BULK COLLECT] INTO`、`WHERE`、`ORDER BY`、`FETCH FIRST n ROWS ONLY`、`COUNT(*)`、cursor FOR ループ、明示 cursor の OPEN / FETCH） | SQL にせず、生成コードが要素を回して絞り、並べ、列を取る（`Plsql.tableRows`、`Plsql.orderRows`）。行は Repository の行と同じ `List<Object[]>` で、INTO・BULK COLLECT・cursor にそのまま渡す | なし（診断 `TABLE_COLLECTION`） | 26ai で実測した Oracle の動きに合わせています: 行は添字の順（ネスト表の隙間は飛ばす）、`TABLE(NULL)` は 0 行、昇順は NULL が最後・降順は NULL が先、文字列はバイナリ順。スカラーのコレクションの列は `COLUMN_VALUE`、record のコレクションは field が列です（生成とコンパイルで確認） |
| 上の形に当てはまらない `TABLE(...)`（表との結合、GROUP BY、DISTINCT、副問い合わせ、`COUNT(*)` 以外の集約、function の結果 `TABLE(f(x))`、routine が宣言していないコレクション） | 解析のときに理由つきで断る（Repository のメソッドが `UnsupportedOperationException`） | SQL-001（REVIEW）。診断 `TABLE_QUERY` に理由 | 以前は実行計画に回り、実行時に ScalarDB が落としていました（#135） |

### BULK COLLECT / FORALL / SAVE EXCEPTIONS

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `SELECT ... BULK COLLECT INTO v` | Repository が全行を `List<Object[]>` で返し、`Plsql.column(...)` で List にする | BULK-001（REVIEW） | この形には走査行数の上限の検査が付きません（生成で確認） |
| `FETCH c BULK COLLECT INTO v LIMIT n` のループ | 行を先に全部読み、`Plsql.chunks(行, n)` で n 件ずつ配るループ | 上限を決めていなければ CUR-002 と BULK-003（REVIEW）。決めれば BULK-OPT-003（注記） | LIMIT はもうメモリを守りません。v が数値などのコレクションなら要素は列の値、record（`c%ROWTYPE` など）のコレクションなら行の record です（生成で確認） |
| 件数を数えるだけの分割読み | `COUNT(*)` の問合せ 1 回 | なし | 生成で確認 |
| `SELECT ... BULK COLLECT INTO v` の直後の `FORALL i IN 1 .. v.COUNT <DML>` | 2 つを 1 つの cursor FOR ループにまとめる（診断 `BULK_CHUNKED`） | CUR-002、BULK-003。`SAVE EXCEPTIONS` 付きなら BULK-002（REDESIGN） | 組の外で v や `SQL%ROWCOUNT` を読むと、まとめません。`SAVE EXCEPTIONS` の診断はまとめたループに移すので、BULK-002 は当たり続けます（生成で確認） |
| `FORALL i IN lo .. hi <DML>`（`1 .. v.COUNT`、`v.FIRST .. v.LAST`、定数、式） | 添字の列（`Plsql.forallRange(lo, hi)`）を作ってから、添字ごとに 1 回 DML する Java のループ。`rowCount` は合計 | なし | 境界は 1 回だけ評価します。NULL の境界と `lo > hi` は何もせず、`SQL%ROWCOUNT` も前の値のままです。文の前に、本体が読むコレクションに要素があるかを確かめ、無ければ ORA-22160（`Plsql.ElementNotExist`）で、それより前の要素の文は実行済みのまま残り `SQL%ROWCOUNT` にも数えます（26ai で実測）。FORALL の 1 往復が要素ごとの往復になり、性能が変わります（生成とコンパイルで確認） |
| `FORALL i IN INDICES OF v [BETWEEN a AND b]` | `Plsql.indicesOf(v[, a, b])` の添字を回す | なし | 要素のある添字だけを昇順に回します（隙間は飛ばす）。`BETWEEN` の境界が NULL なら何もしません。v が NULL なら ORA-06531（26ai で実測。生成とコンパイルで確認） |
| `FORALL i IN VALUES OF p` | `Plsql.valuesOf(p)` の値を、p の順に添字として回す | なし | 同じ値は 2 回回します。値が NULL か、その要素が無ければ ORA-22160。p が NULL なら ORA-06531。空の INDICES OF / VALUES OF は `SQL%ROWCOUNT` を 0 にします（26ai で実測。生成とコンパイルで確認） |
| `SQL%BULK_ROWCOUNT(i)` | `Plsql.bulkRowCount(bulkRowCount, i)`。`bulkRowCount` は FORALL が回した添字をキーにした Map | なし | 回していない添字は ORA-06532（26ai で実測。Oracle は NULL の添字で ORA-06530、生成コードは ORA-06502） |
| `FORALL ... SAVE EXCEPTIONS` | 部分失敗の意味は生成しない | BULK-002（REDESIGN） | `SELECT ... BULK COLLECT` と組にしてループにまとめた形でも同じです。`transactions.perIteration` で 1 要素 = 1 トランザクションに割る決定をすると、handler は失敗した 1 要素の記録になります。割るのは `1 .. v.COUNT` の FORALL だけです。`SQL%BULK_EXCEPTIONS(j).ERROR_INDEX` は**何回目の文か**（1 から数える。添字ではない、26ai で実測）で、割ったあとは呼び出し側が数えて渡します |
| `SQL%BULK_EXCEPTIONS` | 変換しない（`UnsupportedOperationException`） | 判定は下がらない | 生成で確認 |
| `FORALL ... RETURNING BULK COLLECT INTO` | 1 要素ごとに書いた値を List に足す | RMW なので `rowLocks.optimistic` の決定が要る | コード上の対応（#51）。生成では確かめていません |

### 例外

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `EXCEPTION WHEN x THEN ...` | `try { ... } catch (XException e) { ... }` | なし | handler の順を保ち、`WHEN OTHERS` は最後に置きます |
| 定義済み例外 | `NoDataFoundException`（100）、`TooManyRowsException`（-1422）、`DuplicateValueException`（-1）、`InvalidNumberException`（-1722）、`ZeroDivideException`（-1476）、`ValueErrorException`（-6502）、`SubscriptBeyondCountException`（-6533）、`SubscriptOutsideLimitException`（-6532）、`CollectionIsNullException`（-6531）、`InvalidCursorException`（-1001）、`CursorAlreadyOpenException`（-6511）、`CaseNotFoundException`（-6592） | なし | 全部 `MigratedException(code, message)` の子で、Oracle の番号を持ちます。ランタイムの誤り（`Plsql.ZeroDivide` など）は handler のある所で対応する例外に付け替えます（生成で確認） |
| `WHEN DUP_VAL_ON_INDEX` / `WHEN INVALID_NUMBER` | catch は出すが、移行先ではこの例外が自然には起きない | EXC-001（REVIEW） | 明示の RAISE のときだけ走ります。重複 INSERT のあと ScalarDB はトランザクションを続けられません |
| `PRAGMA EXCEPTION_INIT(e, 番号)` で DB の誤りに結んだ例外の handler（-1、-1400、-1407、-1438、-1722、-2290、-2291、-2292、-6502、-12899） | catch は出すが、移行先ではその番号の誤りが起きない | EXC-001（REVIEW） | `constraints.enforce` の検査がその番号を投げる routine（呼び先を含む）では当たりません |
| `WHEN VALUE_ERROR` | `catch (ValueErrorException e)` | EXC-001（REVIEW） | 宣言の長さ・桁の超過と、数値にならない文字は届きます。CHAR の詰め物と SQL の中の変換は届きません |
| `WHEN OTHERS` | `catch (MigratedException e)` | `THEN NULL` だけなら EXC-002（REVIEW）。それ以外の処理で書き込み（DML、書き込む routine の呼び出し）を囲むと EXC-003（REVIEW） | 移行した例外だけを捕まえます。`SQLException` や ScalarDB の競合、Java の不具合は捕まえません。Oracle では重複・NOT NULL・長さなど DB の誤りもここで捕まえていました。最後に `RAISE;` で投げ直す handler は EXC-003 に当たりません |
| ユーザ定義の例外（`e_x EXCEPTION`） | `EXException`（生成器が内部で -900000 台の番号を振る） | なし | `SQLCODE` は 1、`SQLERRM` は `User-Defined Exception` を返します（Oracle と同じ） |
| `PRAGMA EXCEPTION_INIT(e_x, -n)` | `CODE = -n` を持つ `EXException` | なし | 同じ番号の `RAISE_APPLICATION_ERROR` はこのクラスを投げるので、`WHEN e_x` で捕まります |
| `PRAGMA EXCEPTION_INIT(e, -54)`（行ロックが取れない）の handler | catch を出さない | なし | ScalarDB は待たないので起こりません。衝突は commit で分かります |
| `RAISE_APPLICATION_ERROR(-20001, msg)` | `throw new <Package>Error20001Exception(msg)` | なし | 番号を保ちます。同じ番号に 2 つの意味があると `generation-report.json` の `conflicts` に出ます（生成で確認） |
| `RAISE_APPLICATION_ERROR(c_err, msg)`（番号を定数の名前や式で書く） | 変換しない（`UnsupportedOperationException`） | LOWER-001（REVIEW） | 例外のクラスを番号で決めるためです。番号を数字で書くか、`PRAGMA EXCEPTION_INIT` の例外を RAISE します。解析した範囲に無い routine の呼び出し（CALL-001）とは数えません |
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
| `EXECUTE IMMEDIATE '定数' [INTO v] [USING p]` | 静的な文として Repository に生成 | ScalarDB がそのまま実行できれば DYN-OPT-002（注記）。できなければ DYN-002（REVIEW）。畳んだ文には静的な文と同じ規則が当たります（`FOR UPDATE` は LOCK-001、書いた表の走査は SCAN-001 / TX-004 など） | 生成で確認 |
| 畳んだ文が trigger・制約のある表へ書く | 文はそのまま生成し、trigger の呼び出しも制約の検査も入れない | trigger は TRG-002（REDESIGN）、制約は CONS-001 / CONS-002（REVIEW） | 静的な文に書き直せば、trigger の呼び出しと検査が入ります（#148） |
| 本体で定数を代入・連結した変数（`v_sql := '...'; v_sql := v_sql \|\| '...'`） | とりうる文（8 通りまで）を全部生成し、実行時に条件で選ぶ | 同上 | 宣言部の初期値で組んだ文字列はたどりません（生成で確認）。条件の変数を途中で書き換えると断ります |
| 表名などの識別子を連結（`'... FROM ' \|\| p_tab`） | 決定が無ければ断る。`dynamicTables` に表名を書けば表ごとの文を生成し、`UPPER(p_tab)` で選ぶ | DYN-001（REDESIGN） | 書いていない表名は `IllegalArgumentException`（生成で確認）。`plsql.cli --limits` の判定も表ごとの文を見るので、展開できた文に DYN-002 は付きません |
| 列名・ORDER BY の式や方向・WHERE の断片・PL/SQL ブロックの routine 名を連結（`' WHERE ' \|\| p_col \|\| ' = :1'`、`' ORDER BY ' \|\| p_col \|\| ' ' \|\| p_dir`、`'BEGIN ' \|\| l_fn \|\| '(...); END;'`） | 決定が無ければ断る。連結する項が 1 つなら、`dynamicTables` に受け付ける名前（列名でもよい）を書けば名前ごとの文を生成する | DYN-001（REDESIGN、設計書 §6.8。#157 までは DYN-002） | 項が 2 つ以上（列と方向など）や WHERE の断片そのものは `dynamicTables` では書けません。allowlist 型の query builder に作り直します |
| 値を連結（`'... = ''' \|\| p \|\| ''''`、`'... = ' \|\| p_id`、`IN (' \|\| p_list \|\| ')'`） | 断る | DYN-002（REVIEW） | 連結した項が文のどこに入るか（値か識別子か）は、項ごとに前後の SQL から判定します（`plsql/dynamic.py` の `holes`） |
| 動的な DDL（`'CREATE TABLE ...'` など） | 断る。`ddl.omit` に書けば省く | DYN-004（REDESIGN）。`ddl.omit` に書いた routine は当たりません | スキーマは Schema Loader が持ちます。Oracle の DDL は前後で COMMIT します |
| 動的な `'TRUNCATE TABLE t'`（STORAGE の指定だけは付いてよい） | 全行の DELETE（`DELETE FROM t`）にする | 診断 `TRUNCATE_AS_DELETE`（INFO）。外部キーが指す表なら CONS-002 | Oracle の TRUNCATE は前後で COMMIT して取り消せませんが、移行先の DELETE はトランザクションの一部で、失敗すれば一緒に戻ります。DELETE の trigger は Oracle と同じく掛けません。Oracle は外部キーが指す表の TRUNCATE を ORA-02266 で断ります（#154） |
| 動的な PL/SQL ブロック（`'BEGIN ... END;'` の定数） | ブロックを展開して生成 | 中身の判定しだい | 生成で確認 |
| `DBMS_SQL` で `PARSE` の文字列が定数の問合せ | 静的な cursor FOR ループ。`COLUMN_VALUE` は列番号で選ぶ | ループなので CUR-002 | 生成で確認 |
| それ以外の `DBMS_SQL`（DML、実行時に決まる文字列、`BIND_VARIABLE` など） | 変換しない | DYN-003（REDESIGN） | 生成で確認 |
| 権限 | 生成物には出ない | DYN-OPT-002 の注記 | EXECUTE IMMEDIATE に与えていた権限は運用で確かめます |

### 組み込みパッケージ

対応表は `plsql/builtins.py` です。表に無い package の呼び出しは断ります。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `DBMS_OUTPUT.PUT_LINE` / `PUT` / `NEW_LINE` | `Plsql.putLine` / `put` / `newLine` | なし | スレッドごとの出力バッファです。既定では捨てます（ENABLE していない Oracle のセッションと同じ）。読むときは `Plsql.enableOutput(上限のバイト数)` で有効にし、`Plsql.output()` で読み、`Plsql.disableOutput()` で止めます。上限を超えると ORA-20000（ORU-10027）です。日付は既定の NLS の形（SEM-012） |
| `DBMS_RANDOM.VALUE` / `STRING` | `Plsql.randomValue` / `randomString` | SEM-014（REVIEW） | Oracle と同じ値にはなりません。乱数の出所は業務で決めます（生成で確認） |
| `DBMS_UTILITY.GET_TIME` | `Plsql.getTime()` | なし | 差を取るための値で、起点は Oracle と違います |
| `DBMS_SESSION.SLEEP` / `DBMS_LOCK.SLEEP` | `Plsql.sleep(秒)` | なし | 待つ間トランザクションは開いたままです（生成で確認） |
| `DBMS_APPLICATION_INFO.SET_MODULE` / `SET_ACTION` / `SET_CLIENT_INFO` | 何もしない（コメントだけ） | なし | 名前付き引数も並べ替えて読みます |
| `DBMS_STATS.GATHER_SCHEMA_STATS` / `GATHER_TABLE_STATS` | 何もしない（コメントだけ） | なし | ScalarDB にオプティマイザ統計はありません（生成で確認） |
| `SYS_CONTEXT(...)` | 変換しない | SEM-013（REDESIGN） | 要る属性だけを呼び出し側が渡す形に直します（生成で確認） |
| `DBMS_LOB.GETLENGTH` / `SUBSTR` / `INSTR` | `Plsql.lobGetLength` / `lobSubstr` / `lobInstr` | なし | CLOB は `String`、BLOB は `byte[]` のまま、値に同じことをします。Oracle の DBMS_LOB の決まりに合わせ、NULL や範囲外の引数は例外でなく NULL、`SUBSTR` の既定は 32767 文字・1 文字目、負の offset で後ろから探すことはしません（Oracle 26ai で実測）。空の CLOB（`EMPTY_CLOB()`）は NULL と区別できません |
| `UTL_HTTP`、`UTL_SMTP`、`UTL_FILE`、`UTL_TCP`、`DBMS_SCHEDULER`、`DBMS_JOB`、`DBMS_AQ`、`DBMS_PIPE`、`DBMS_ALERT`、`DBMS_LOB`（上の行の関数を除く） | 変換しない | EXT-001（REDESIGN） | 外部への副作用です。`DBMS_LOB` は引数を書き換える `APPEND` / `CREATETEMPORARY` などと、ファイルを読む `LOADCLOBFROMFILE` などが残ります（生成で確認） |
| `DBMS_SQL` | 動的 SQL の節を参照 | DYN-003 | |
| 解析した範囲に無い routine（他の package、表に無い組み込み） | `UnsupportedOperationException("external call: ...")` | CALL-001（REVIEW） | 呼び先が COMMIT するか、外へ送るか、ロックを取るかが分かりません（生成で確認）。次は呼び出しと数えません: 要素や field のコレクション・メソッド（`v(2).EXTEND`、`v(1).DELETE(1)`）、package が宣言した型の構築子（`pkg.names('a', 'b')`）。`STANDARD.TO_CHAR` のように `STANDARD.` を付けて書いた組み込み関数は、生成器がまだ読まないので CALL-001 のままです |

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
| 条件つき・局所変数を読む `:NEW` の代入 | 呼び出しにできない（`TRIGGER_REDESIGN`） | 書く側が TRG-002（REDESIGN） | |
| `:OLD` への代入（`:OLD.c := 式`、`SELECT ... INTO :OLD.c`） | trigger を変換しない。ファイルは parse できなかったものと同じく `failedFiles` に出て、診断 `ORA_04085`（ERROR）が付く | trigger は判定に出ない。書く側にも掛けない | 元のソースの誤りです。Oracle でも `CREATE TRIGGER` が ORA-04085 で失敗し、trigger は作られません（Oracle 26ai で確認）。ソースを直してから変換します |
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
| `rowLocks.optimistic` に書いた routine | ロックを落とし、同じトランザクションの中で読んでから書く | 判定は REDESIGN のまま（決定済み） | 衝突は commit で弾かれ、再試行は呼び出し側の責務です。NOWAIT の「すぐ分かる」は「commit で分かる」に変わります（生成で確認）。**FOR UPDATE と同じ保証になるのは SERIALIZABLE で動かすときだけ**です。SNAPSHOT / READ_COMMITTED では、ロックして読んだだけで書かない行を他が変えても commit が通ります（write skew。3.19.1 の実クラスタで確認、#157）。生成コードのロックを落とした文にこの前提のコメントが付き、決めることは CALL-7 です |
| `WHERE CURRENT OF c` | 読んだ行のキーで UPDATE / DELETE | 同上 | 生成で確認 |
| 読んだ値で計算して書く（`SET c = c + x`） | SQL 文の節の「列を読む式の UPDATE」を参照 | | |

### プロジェクトの決定（limits.yaml）と生成物

生成器が推測してはいけないことは、limits.yaml に人が書いたときだけ生成物に反映します。書いていない routine には既定の答えを当てません。

| limits.yaml の項目 | 生成物の変化 | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| `scanRows.default` / `routines` / `notLimited` | cursor の行を読む所に上限の検査を入れる | CUR-002 → CUR-OPT-002、BULK-003 → BULK-OPT-003 | `notLimited` でも既定値の検査は「暫定の網」として残ります（生成で確認） |
| `rowLocks.optimistic` | 行ロックを落とす。列を読む UPDATE・RETURNING・MERGE を読んでから書く形に割る | SQL-001 / SEM-006 が外れ、SEM-011 の注記。LOCK-* は REDESIGN のまま | 前提は SERIALIZABLE で動かすこと（CALL-7、#157） |
| `transactions.perIteration` / `separate` / `callerBoundary` | トランザクションの節を参照 | TX-* は REDESIGN のまま（決定済み） | 1 つの routine に 1 つだけ書けます |
| `dynamicTables` | 識別子を 1 つ連結する動的 SQL を、書いた名前（表名・列名）ごとの文にする | DYN-001 は REDESIGN のまま。表ごとの文を判定するので、展開できた文の DYN-002 は外れる（`plsql.cli --limits` も同じ） | |
| `ddl.omit` | 動的な DDL を省く | | |
| `packageState.carried` | package 変数を引数と結果で運ぶ | STATE-001 は REDESIGN のまま | |
| `constraints.enforce` | NOT NULL / CHECK / 外部キーの検査を書く前に入れる。親をキーで消す DELETE には子を数える検査を入れる | CONS-001 が外れる。UNIQUE と、キーで名指さない親の DELETE、`ON DELETE CASCADE` / `SET NULL` は検査せず CONS-002 | |
| `dbLinks` | `表@link` を namespace に書き換える | LINK-001 は REDESIGN のまま | |
| `conditionalCompilation` | 条件付きコンパイルのフラグとバージョン | | |
| `nls` | 生成した Service がクラスの初期化で `Plsql.useNls(...)` を呼び、日付・数値と文字の変換（`TO_CHAR` / `TO_NUMBER` / `TO_DATE` / `TO_TIMESTAMP` と書式なしの変換）を決めた設定で行う | ランタイムが計算する変換の SEM-008 / SEM-012 が外れる（NLS_DECIDED）。移行先 DB が評価する SQL に残った変換は外れない | project に 1 つ。`reason` だけが必須で、書かない値は地域の既定。言語・地域はランタイムが知るものだけ書けます（#157） |

### ルールが REVIEW / REDESIGN にするもの（ルール ID ごと）

この節は列が違います。「当たる書き方」は `plsql/rules/*.yaml` の条件を言い直したものです。

| ルール ID | 判定 | 当たる書き方 | 生成物と、外すための決定 |
|---|---|---|---|
| TX-001 | REDESIGN | routine（と呼び先）の COMMIT / ROLLBACK / SAVEPOINT | 決定が無ければ断る。`transactions.*` で形を決める |
| TX-002 | REDESIGN | `PRAGMA AUTONOMOUS_TRANSACTION` | `transactions.separate` |
| TX-003 | REDESIGN | ループの中の COMMIT / ROLLBACK | `transactions.perIteration` |
| TX-004 | REDESIGN | 同じトランザクションで書いた表を走査する（キーで読むもの、COMMIT のあとの読みは数えない。ScalarDB のスキーマが無ければ、書いた表の読みすべて） | routine を境界で割る。読み取りをキーにする |
| SCAN-001 | REDESIGN | 呼び先が、この routine の書いた表を走査する | 同上 |
| STATE-001 | REDESIGN | package 変数を（呼び出し経由を含めて）読み書きする | `packageState.carried` |
| STATE-002 | REDESIGN | package 本体の初期化部 | 置き場所を人が決める（生成しない） |
| AUTHID-001 | REDESIGN | routine の `AUTHID CURRENT_USER`（package の仕様に書いたものは本体の routine すべて） | 認証・認可を別に設計する |
| DYN-001 | REDESIGN | 識別子や SQL の構文を実行時に組む動的 SQL（表名・列名・ORDER BY の式と方向・WHERE の断片・PL/SQL ブロックの routine 名。値の位置だけなら DYN-002） | `dynamicTables`（連結する項が 1 つのとき）。ほかは query builder に作り直す |
| DYN-003 | REDESIGN | `DBMS_SQL`（定数の問合せに書き換えられなかったもの） | 実行ログから文を洗い出す |
| DYN-004 | REDESIGN | 畳んだ動的 SQL が DDL（`CREATE`、`DROP`、`ALTER` など。表だけの `TRUNCATE TABLE` は全行の DELETE にするので当たらない） | `ddl.omit`（省いてよい DDL のとき） |
| LOWER-002 | REDESIGN | ブロックとループに組み直せなかった `GOTO`（範囲が交差して入れ子にできない、Oracle が拒む飛び先） | 制御構造を組み直す |
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
| LOWER-001 | REVIEW | lowering がまだ模していない構文（持ち上げられない入れ子の subprogram、構文エラーから回復した unit など） | |
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
| SEM-007 | REVIEW | 時計（SYSDATE など）を、文と宣言部の初期値で合わせて 2 回以上読む | 1 回読んで使い回す |
| SEM-008 | REVIEW | 言語で変わる `TO_CHAR` の書式（`nls` を決めていない、または移行先 DB が評価する SQL に残ったもの） | `nls` |
| SEM-009 | REVIEW | 移行先 DB が評価する `CAST(... AS DATE)` | |
| SEM-010 | REVIEW | `SYSTIMESTAMP` を列へ書く INSERT / UPDATE / MERGE | 時刻の正確さを業務で決める |
| SEM-012 | REVIEW | 日付・TIMESTAMP を書式なしで文字にする（`nls` を決めていない、または移行先 DB が評価する SQL に残ったもの） | `nls`。または書式を明示する |
| SEM-014 | REVIEW | `DBMS_RANDOM`、`SYS_GUID` | 乱数の出所を決める |
| SEM-015 | REVIEW | ランタイムが実装しない書式の形（`FM` と `B`、数字の無い数値書式）、型の分からない値への `FF` / `X` / `TZR` | 書式を書き直す。値を TIMESTAMP の変数に入れる |
| CUR-001 | REVIEW | 書き換えられなかった明示 cursor の OPEN / FETCH / CLOSE | |
| CUR-002 | REVIEW | 行数上限の決まっていない cursor FOR ループ | `scanRows` |
| CUR-003 | REVIEW | 先読みに書き換えた明示 cursor で、routine が COMMIT などを持つ | |
| CUR-004 | REDESIGN | OUT 引数の `SYS_REFCURSOR` を routine の中で FETCH / CLOSE する、または列の違う問合せで OPEN する（生成器が断る） | 読んだ行と残りの行の渡し方を決めて書き直す。CLOSE を消す。問合せごとに OUT 引数を分ける |
| BULK-001 | REVIEW | `BULK COLLECT` を含む SQL 文 | |
| BULK-003 | REVIEW | 行数上限の決まっていない分割読み | `scanRows` |
| EXC-001 | REVIEW | `DUP_VAL_ON_INDEX`、`INVALID_NUMBER`、`VALUE_ERROR` の handler。`PRAGMA EXCEPTION_INIT` でそれらや制約の誤り（-1400、-2290、-2291、-2292 など）の番号に結んだ例外の handler | 読んでから選ぶ形、事前の検査に書き直す。CHECK と外部キーは `constraints.enforce` |
| EXC-002 | REVIEW | `WHEN OTHERS THEN NULL` | 無視する例外を名前で書く |
| EXC-003 | REVIEW | `WHEN OTHERS THEN <NULL 以外>` が書き込みを囲む（投げ直す handler を除く） | 捕まえたい DB の誤りを名前で書く、書く前に検査する |
| CONS-001 | REVIEW | CHECK / 外部キー / UNIQUE のある表への書き込み、子のある親の DELETE で、`constraints.enforce` に無い表 | `constraints.enforce` |
| CONS-002 | REVIEW | 決めた表の制約のうち書く前に検査しないもの（UNIQUE、キーで名指さない親の DELETE、`ON DELETE CASCADE` / `SET NULL`、書く値が文から読めない、動的 SQL の文、外部キーの指す表の TRUNCATE） | 設計する（先に読む、呼び出し側で保証する） |
| SQL-001 | REVIEW | ScalarDB SQL で実行できない文 | RMW なら `rowLocks.optimistic` |
| SQL-002 | REVIEW | 実行計画に分解される文 | |
| SQL-003 | REVIEW | 変数と列が同じ名前 | 名前を変える、列を修飾する |
| SQL-004 | REVIEW | 静的な DML 以外が件数を決めうる routine で、利用者が書いた `SQL%ROWCOUNT` を読む（生成器が足した読みと trigger の呼び出しは数えない） | |
| RECUR-001 | REVIEW | 再帰 | |
| （ルールなし） | REVIEW | 確信度の因子が 0（Unsupported の文、解決できない型・呼び先、ScalarDB が実行できない文、証拠が無い） | |

### まだ変換しないもの

判定が AUTO のままでも、生成物で止まる（または正しく動かない）ものを含みます。生成物では多くが `UnsupportedOperationException` になり、`generation-report.json` の `refused` に出ます。

| PL/SQL の書き方 | 生成される Java | 判定への影響（ルール ID） | 注意 |
|---|---|---|---|
| ブロックとループに組み直せない `GOTO` | `UnsupportedOperationException` | LOWER-002（REDESIGN） | 組み直せるものは制御構造の節を参照 |
| 決定の無い COMMIT / ROLLBACK / SAVEPOINT | 同上 | TX-001 | |
| `SQL%BULK_EXCEPTIONS` | 同上 | 判定は下がらない | |
| `DBMS_SQL`（定数の問合せ以外） | 同上 | DYN-003 | |
| とりうる文を数えられない `EXECUTE IMMEDIATE` | 同上 | DYN-002 / DYN-001 | 宣言部の初期値で組んだ文字列も含みます |
| UNIQUE（主キー以外）、キーで名指さない親の DELETE（ORA-02292）、`ON DELETE CASCADE` / `SET NULL` | 検査しない（`constraints.enforce` に書いても） | CONS-001 / CONS-002 | 守るには書く前に読む必要があります。子の行を消す・NULL にする処理は生成しません |
| 畳んだ動的 SQL の書き込みへの trigger の呼び出しと制約の検査 | 入れない | TRG-002 / CONS-001 / CONS-002 | 静的な文に書き直せば入ります |
| 対応表に無い組み込み関数（`SOUNDEX`、`NUMTODSINTERVAL` など）、PL/SQL では使えない `DECODE` / `DUMP` | `UnsupportedOperationException` | 関数による | 組み込み関数の節を参照 |
| INTERVAL 型と、日時の差からの `EXTRACT` | `UnsupportedOperationException` | 判定は下がらない | 生成コードでは日時の差が日数（数値）です |
| Oracle と同じ意味にできない正規表現（等価クラス `[[=e=]]`、`(?` で始まる括弧、量指定子の重ね） | 実行時に `UnsupportedOperationException` | 判定は下がらない | パターンは実行時に訳すので、生成時には分かりません |
| 書式モデルのうちランタイムが実装しない形（`FM` と `B`、数字の無い数値書式、1582-10-15 より前の日付、型の分からない値の `FF` / `X` / `TZR`） | 実行時に要素を名指しした `UnsupportedOperationException` | SEM-015（生成時に分かるもの） | #157 |
| 既定値を省いた呼び出しのうち、既定値が package の変数（`packageState.carried` の決定が無いとき）、`USER` / `SYSTIMESTAMP`、sequence を読むもの、routine を呼ぶ既定値を 2 つ以上省くもの | 同上 | 判定は下がらない | ほかの式の既定値は、呼ばれる側の `defaultOf...()` で補います（単位の節を参照） |
| 別の package の変数の直接参照（`pkg.var`） | `UnsupportedOperationException` | STATE-001 | `packageState.carried` を書いても断ります |
| 持ち上げられない入れ子の subprogram（単位の節の条件） | 本体ごと断る | LOWER-001（REVIEW） | |
| SQL 文の中でブロックのラベルで修飾した名前（`SELECT outer.x ...`） | ScalarDB が断る文になる | SQL-001 | routine 名での修飾は bind にします |
| package 本体の初期化部 | 生成しない | STATE-002 | |
| 呼び出し仕様（`LANGUAGE JAVA` など） | `UnsupportedOperationException` | EXT-002 | |
| `UTL_*` などの外部 package | `UnsupportedOperationException` | EXT-001 | |
| `SYS_CONTEXT` | `UnsupportedOperationException` | SEM-013 | |
| 型だけが違うオーバーロードの呼び出し | `UnsupportedOperationException` | CALL-002 | |
| 畳み込めない `:NEW` の代入 | 書く側が呼ばない | TRG-002 | |
| `:OLD` への代入 | trigger を変換しない（Oracle でも ORA-04085 で作れない） | 診断 `ORA_04085`、`failedFiles` | ソースを直す |
| MULTISET の演算 | 変換しない | | |
| `TABLE(v)` の問合せのうち、コレクションの節の形に当てはまらないもの | 解析のときに理由つきで断る | SQL-001（REVIEW） | 結合・集約・function の結果など。コレクションの節を参照 |
| view への書き込みに `INSTEAD OF` trigger を織り込む形 | 無い | | view へ書く文は ScalarDB に view が無いので断られます |
