# ScalarDB SQL の文法と変換ルール

`--target scalardb` のときに何を書き換え、何を ERROR にするか、型をどう写すか、各指摘コードが何を意味して次に何をするかをまとめる。ScalarDB を Target にしたレポートを利用者に説明するとき、キー設計や `--storage`・`--plan-dir` の効き方を聞かれたときに読む。ERROR の読み取り文をアプリ側で書き換えるときの注意は `app-side-notes.md` にある。

公式の文法は https://scalardb.scalar-labs.com/docs/latest/scalardb-sql/grammar/ 。ScalarDB SQL を実行するには ScalarDB Cluster が要る。変換そのものは ScalarDB に接続せずに行う。

---

## 文法が絞られていること

ScalarDB は複数のストレージを仮想的に統合し、それらをまたいだトランザクションを実現する。どのストレージにどの処理を振るかが決まるよう、文法は次の範囲に絞られている。変換器はこれに合わない文を ERROR にする。

- 射影に書けるのは列と集約関数（`COUNT` / `SUM` / `AVG` / `MIN` / `MAX`、引数は列か `COUNT(*)`）だけ。式・関数・`CASE` は書けない
- WHERE の右辺はリテラルかバインド変数だけ。列どうしの比較は JOIN の ON でしか書けない
- WHERE は DNF（AND で結んだ組を OR でつなぐ形）か CNF（OR で結んだ組を AND でつなぐ形）で書く
- JOIN は INNER / LEFT / RIGHT（RIGHT は最初の結合だけ）。結合条件は `列 = 列` の AND で、相手の表の主キー全体か副次索引を覆う。WHERE と ORDER BY は FROM の表（RIGHT JOIN では結合先の表）の列だけを指せる
- サブクエリ、CTE、UNION などの集合演算、ウィンドウ関数、DISTINCT、OFFSET は書けない
- `UPDATE SET col = col + 1` のような、列を参照する式での更新はできない。主キーの列は UPDATE できない

---

## 自動で書き換えるもの

| 元の構文 | 変換後 | コード |
|---|---|---|
| `col IN (a, b)` / `col NOT IN (a, b)` | `col = a OR col = b` / `col <> a AND col <> b` | INFO `IN`（NOT IN は出ない） |
| `NOT (...)` | 比較演算子を反転して押し下げる。`NOT BETWEEN` は `col < low OR col > high`。`IS NOT NULL` と `NOT LIKE` はそのまま | なし（反転できなければ ERROR `NOT`） |
| DNF でも CNF でもない AND / OR の入れ子 | DNF と CNF の短いほうにし、括弧を付ける | INFO `NORMAL_FORM` |
| `10 < col` | `col > 10` | なし |
| `WHERE ROWNUM <= n`（`< n`、`= 1`、`<= ?` も） | `LIMIT n`（`< n` は `LIMIT n-1`） | WARN `ROWNUM`（ScalarDB の LIMIT は ORDER BY の後に効く。Oracle の ROWNUM は前） |
| `FETCH FIRST n ROWS ONLY` | `LIMIT n` | INFO `LIMIT` |
| `FROM a, b WHERE a.x = b.y` | `FROM a INNER JOIN b ON a.x = b.y` | WARN `COMMA_JOIN` |
| Oracle の外部結合 `a.x = b.y(+)` | `LEFT JOIN b ON a.x = b.y`（向きによって RIGHT） | WARN `ORACLE_JOIN_MARK` |
| `JOIN ... USING (c)` | `JOIN ... ON a.c = b.c`。結合した列 `c` は、行が全部残る側（内部結合・LEFT は FROM の表、RIGHT は結合先の表）で修飾する | INFO `JOIN` |
| WHERE / ORDER BY が結合先の表だけを指す 2 表の INNER JOIN | FROM と JOIN の表を入れ替える（`SELECT *` は元の列順に展開する） | INFO `JOIN_ORDER` |
| Oracle の `LIKE`（`ESCAPE` が無く、パターンに `\` があるかバインド変数） | `LIKE ... ESCAPE ''`。Oracle には既定のエスケープ文字が無く、ScalarDB の既定は `\` | INFO `LIKE` |
| `ILIKE` | `LIKE`（大文字小文字を区別しない一致は失われる） | WARN `ILIKE` |
| `ON CONFLICT DO UPDATE` / `ON DUPLICATE KEY UPDATE` | `UPSERT INTO` | INFO `UPSERT`。元が一部の列だけを更新していたら WARN（UPSERT は列リストのすべてを上書きする） |
| `REPLACE INTO` | `UPSERT INTO` | WARN `REPLACE`（REPLACE は列リストに無い列を NULL に戻し、UPSERT は残す） |
| 定数 1 行をソースにする `MERGE` | `UPSERT INTO` | WARN `MERGE` |
| `DATE '...'`・`TO_DATE('...', 'YYYY-MM-DD')` などの日付・時刻 | ScalarDB のリテラル（`'YYYY-MM-DD'`、`'YYYY-MM-DD HH:MM:SS.FFF'`）。列の型に合わせて、DATE 列は 0 時の時刻を落とし、TIMESTAMP 列は日付だけのものに 0 時を補い、TIMESTAMPTZ 列は同じ瞬間の UTC（末尾 `Z`）にする | INFO `DATE_LIT`（下の「値とリテラル」） |
| TRUE / FALSE を INT / BIGINT 列へ | `1` / `0` | INFO `BOOL_LIT` |
| `catalog.schema.table` | `schema.table`（schema を namespace にする） | WARN `NAMESPACE` |
| `CREATE SCHEMA` / `CREATE DATABASE`、`DROP SCHEMA` | `CREATE NAMESPACE`、`DROP NAMESPACE` | なし |
| `TRUNCATE TABLE a, b` / `DROP TABLE a, b` | 表ごとに 1 文 | なし |
| 複数の操作の `ALTER TABLE` | 1 操作ずつの `ALTER TABLE`（原子的でなくなる） | INFO `ALTER` |
| MySQL のインライン `INDEX (col)`、`CREATE INDEX idx ON t (col)` | `CREATE INDEX ON t (col)`（索引名は落とす） | INFO `INDEX` |
| ScalarDB SQL の予約語・通常の識別子でない名前 | 二重引用符で囲む（下の「識別子と文字列」） | INFO `IDENT` |

---

## 識別子と文字列

- **引用符は ScalarDB が要る所に付け直す。** ScalarDB SQL ではすべてのキーワード（`type`・`order`・`key`・`user`・型名など）が予約語で、名前として使うには二重引用符で囲む。移行元で引用していなくても、キーワードと同じ名前や `[A-Za-z_][A-Za-z0-9_]*` に合わない名前は `"type"` のように囲み、INFO `IDENT` を出す。ORM がすべての識別子を引用した `"status"` のような名前は、囲まずに出す。バッククォートは引用符として扱わない
- **二重引用符を含む名前**（`"a""b"`）は ScalarDB SQL では書けない。ERROR `IDENT`。移行元で改名する
- **大文字小文字**: ScalarDB は名前を畳まない。移行元で引用していて、方言が畳む形（Oracle は大文字、ほかは小文字）と違う綴りの名前（PostgreSQL の `"UnitPrice"`、Oracle の `"orders"`）は WARN `IDENT`。すべての参照をその綴りで書く。同じ表が `customers` と `Customers` の 2 通りに書かれていても WARN `IDENT`
- **Oracle の `''`**: 変換できた文（OK / WARN）に WARN `SEMANTICS` を出す。Oracle は `''` を NULL として保存し、`= ''` は決して真にならない。ScalarDB は空文字のまま持つ。NULL の意味なら `IS NULL` に書き換える
- **MySQL の照合順序**: 変換できた文の、文字列リテラルとの `=`・`<>`・`LIKE`・`IN` に INFO `SEMANTICS`（既定の照合順序は大文字小文字を区別しない。ScalarDB は厳密に比べる）。列の照合順序が分からないので、判定は変えない
- `--mysql-case-insensitive` は ScalarDB 向けには効かない（無視して標準エラーに注意を出す）

---

## 型の対応

ScalarDB の型は 11 種（`BOOLEAN` / `INT` / `BIGINT` / `FLOAT` / `DOUBLE` / `TEXT` / `BLOB` / `DATE` / `TIME` / `TIMESTAMP` / `TIMESTAMPTZ`）。結果は指摘コード `TYPE` で出る。対応表は Source が oracle / postgres / mysql のときに合わせて作ってある。ほかの Source では標準エラーに注意が出て、精度が落ちることがある。

| 元の型 | ScalarDB | 重要度 |
|---|---|---|
| `NUMBER(p)` / `DECIMAL(p, 0)`、p ≤ 9 | `INT` | INFO |
| 同上、10 ≤ p ≤ 18 | `BIGINT` | INFO |
| 同上、p ≥ 19 | `BIGINT` | WARN（64 ビットを超える値はあふれる） |
| `NUMBER(p, s)` / `DECIMAL(p, s)`、s > 0 | `DOUBLE` | WARN（精度が落ちる） |
| 精度なしの Oracle `NUMBER` / PostgreSQL `NUMERIC` | `DOUBLE` | WARN（どちらも桁数無制限で小数を持てる。MySQL の精度なし `DECIMAL` は `(10,0)` として扱う） |
| Oracle の `INTEGER` / `INT` / `SMALLINT` | `BIGINT` | WARN（Oracle の整数型はすべて `NUMBER(38)`。正確に対応させるなら `NUMBER(p)` と書く） |
| Oracle の `FLOAT` | `DOUBLE` | WARN（2 進精度つきの `NUMBER` で最大 38 桁。IEEE の単精度ではない） |
| MySQL の `TINYINT(1)` | `INT` | WARN（真偽値に使っているなら `BOOLEAN` を検討） |
| 符号なし `BIGINT` | `BIGINT` | WARN（範囲が符号つき 64 ビットを超える） |
| `BIT(n)`、n > 1 | `BLOB` | WARN |
| `VARCHAR2(n)` / `VARCHAR(n)` / `CLOB` / `TEXT` | `TEXT` | INFO（長さの制約は消える） |
| `CHAR(n)`、n > 1 | `TEXT` | WARN（空白で埋められ、埋めた分を無視して比較される。`TEXT` は厳密に比べるので、移行時に trim しないと同じ比較が当たらなくなる） |
| Oracle の `DATE` | `DATE` | WARN（Oracle の DATE は時刻を持つ。時刻を使うなら `TIMESTAMP`） |
| `TIMESTAMP` / `TIMESTAMPTZ`、精度が 4 以上（Oracle と PostgreSQL は精度を書かなければ 6、MySQL は 0） | 同名 | WARN（ミリ秒まで。`TIMESTAMPTZ` は UTC で持つ） |
| `TIMETZ` | `TIME` | WARN（オフセットは落ちる） |
| `JSON` / `JSONB` / `UUID` / `ENUM` / `SET` / `INET` / `XML` | `TEXT` | WARN |
| `SERIAL` / `BIGSERIAL` / `SMALLSERIAL` | — | ERROR（アプリで採番する） |
| `ARRAY` / `INTERVAL` / `GEOMETRY` など上に無い型 | — | ERROR |

**金額の列は注意**。ScalarDB に `DECIMAL` が無いため `DOUBLE` になり、誤差が出る。スケール済みの整数（例: 円なら 1 倍、ドルなら 100 倍）として `BIGINT` に持たせる設計を勧める。

---

## 表定義: 主キー・制約・索引

| 元の定義 | 扱い | コード |
|---|---|---|
| 単一列の主キー | パーティションキー | なし |
| 複合主キー | 先頭列をパーティションキー、残りをクラスタリングキー | INFO `KEYS`（`--keys` で変える） |
| 主キーの無い表 | 変換しない | ERROR `PK` |
| `NOT NULL` | 落とす | INFO `NOT_NULL` |
| `DEFAULT` | 落とす。アプリが値を入れる | WARN `DEFAULT` |
| `UNIQUE`（列・表・`CREATE UNIQUE INDEX`） | 落とす（副次索引は一意性を守らない） | WARN `UNIQUE` |
| `CHECK` | 落とす | WARN `CHECK` |
| `REFERENCES` / `FOREIGN KEY` | 落とす | WARN `FK` |
| そのほかの名前つき制約、表の要素 | 落とす | WARN `CONSTRAINT` / WARN `DDL` |
| `COMMENT` / `COLLATE` / `CHARACTER SET`、そのほかの列の修飾 | 落とす | INFO / WARN `COL_OPT` |
| 表のオプション（`ENGINE=`、表領域など） | 落とす | INFO `TABLE_OPTS` |
| `AUTO_INCREMENT` / `IDENTITY` | 変換しない。アプリで採番する（UUID を TEXT で持つなど） | ERROR `AUTO_INC` |
| 生成列・計算列 | 変換しない | ERROR `GENERATED` |
| 一時表 | 変換しない | ERROR `TEMP` |
| `CREATE TABLE ... AS SELECT` / `LIKE`、型の無い列 | 変換しない | ERROR `DDL` |
| `tx_id`・`tx_state`・`tx_version`・`tx_prepared_at`・`tx_committed_at`、キーでない `before_` で始まる列 | ScalarDB がトランザクションのメタデータに使う名前。移行元で改名する | ERROR `RESERVED_COLUMN` |
| インラインの複数列の索引 | 落とす | WARN `INDEX` |
| `CREATE INDEX` で複数列 | 変換しない（ScalarDB の副次索引は 1 列） | ERROR `INDEX` |
| 表と列を名指ししない `DROP INDEX` | 変換しない（`DROP INDEX ON <表> (<列>)` の形が要る） | ERROR `DROP_INDEX` |
| `ALTER TABLE ... ALTER COLUMN ... TYPE` | 変換する。型の変更ができるかは下のデータベース次第 | WARN `ALTER_TYPE` |
| 列の追加・削除・改名、表の改名、型の変更以外の `ALTER TABLE` の操作（制約・索引・パーティションなど） | 変換しない | ERROR `ALTER` |
| ビュー・シーケンス・トリガー・プロシージャの `CREATE`、表・スキーマ・索引以外の `DROP` | 変換しない | ERROR `DDL`（SQLGlot が文として解析しなかったものは ERROR `UNPARSED`） |

キーの分け方は `--keys 表=パーティションキー/クラスタリングキー`（複数列はカンマ区切り）で上書きできる。存在しない列を指すと ERROR `KEYS`。

```bash
--keys orders=customer_id/order_no     # customer_id でパーティション、order_no で並べる
```

キー設計は移行後の性能を最も大きく左右する。パーティションキーで絞れない問合せは、表全体を走査するクロスパーティション SCAN になる。

---

## アクセスパスとバックエンド

表定義（入力の `CREATE TABLE` か `--schema` の JSON）が分かると、SELECT・UPDATE・DELETE ごとにアクセスパスを判定する。表定義が無ければ INFO `SCHEMA` を出し、アクセスパスと結合のキーを調べない。

| アクセスパス | 条件 | コード |
|---|---|---|
| GET | 主キーのすべての列を `=` で指定 | INFO `ACCESS` |
| パーティション SCAN | パーティションキーを `=` で指定 | INFO `ACCESS` |
| 索引 SCAN | 副次索引の列を `=` で指定し、ORDER BY が無い | INFO `ACCESS` |
| クロスパーティション SCAN | キーを覆う述語が無い、または最上位が OR | WARN `CROSS_PARTITION`（`scalar.db.cross_partition_scan.enabled` が要る） |

パーティション SCAN の ORDER BY がクラスタリングキーの先頭からの並びでないときは WARN `ORDER`（ScalarDB は順序つきのクロスパーティション SCAN に切り替える。JDBC のバックエンドだけ）。WHERE の無い UPDATE / DELETE は WARN `NO_WHERE`。

`--storage` は既定が `jdbc`。`--storage cassandra` を付けると、クロスパーティション SCAN を使わない前提で判定し、上の WARN の代わりに ERROR を出す。読み取り文は実行計画に回り、キーで取得してアプリで処理する形になる。

| コード | 重要度 | 意味 | 次の手 |
|---|---|---|---|
| `NO_CROSS_PARTITION` | ERROR | キーで絞れない走査。JDBC 以外では使わない | SELECT はキーで取得してアプリで処理する。UPDATE / DELETE は先にキーを読んで主キー指定で書く |
| `OR_KEYS` | ERROR | パーティションキーか索引の列の OR / IN。全パーティションの走査になる | キーごとに取得する |
| `ORDER_STORAGE` | ERROR | ORDER BY が順序つきのクロスパーティション走査を要る（DB-CORE-10007） | アプリで並べる |
| `FULL_SCAN` | ERROR | 実行計画で、キーでも索引でも読めない表がある。表ごとに、結合相手のキーで読む方法（CTE の列もたどる）を「read X first, then Y」の形で示す。相手もキーで読めないときは、アプリが既に持っているキーから始める設計（キーの一覧を持つ表など）、集計表、ScalarDB Analytics を提案する | 提案に従ってキーで読むか、集計表を設ける |

---

## 実行計画（`--plan-dir`）

ERROR になった読み取り文（SELECT、UNION などの集合演算、CTE を含む）は、ScalarDB から行を取得してインメモリの H2 で元の SQL を実行する「実行計画」に分解できることが多い。`--plan-dir` を付けたときだけ分解し、分解できた文の状態を PLANNED にする。PLANNED は変換率に含めない。

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> --source oracle --target scalardb \
  --out-dir out --plan-dir out/plans
```

- 計画は表ごとに 1 つの取得（`fetch`）と、H2 で実行する SQL（`residual`）を持つ。取得ごとに行数の上限（`max_rows`、1 万行）が付く。`transpile.py` にはこの上限を変えるオプションが無い
- 取得ごとに、H2 に作ると結合が速くなる索引の列（`index_columns`: 取得した主キーと、結合・相関サブクエリ・IN 副問合せで比べる列）が付く。索引を作るかは既定でオフ。`--h2-indexes` を付けると計画に `build_indexes: true` が入り、ランタイムが問い合わせの前に索引を作る。数万行以上の表を結合するバッチ処理では速くなる。1 表だけの計画や小さな要求では、索引を作る時間（投入と同程度）とメモリ（H2 のメモリが約 1.6 倍）の分だけ遅くなる
- 次の構文は H2 が実行できないので計画を作らず、ERROR `RESIDUAL_H2` にする: `CONNECT BY`、`ROLLUP` / `CUBE` / `GROUPING SETS`、`PIVOT` / `UNPIVOT`、`KEEP`、`FULL OUTER JOIN`、`LATERAL` / `CROSS APPLY`、`SAMPLE`、再帰 WITH の `SEARCH` 句、`JSON_TABLE`。H2 で動く形への書き換えは `app-side-notes.md`
- `ROWID`・`ORA_ROWSCN` を使う文など、分解できなかった読み取り文には INFO `PLAN` で理由が出る。書き込み文と DDL は分解の対象にしない

| コード | 重要度 | 意味 |
|---|---|---|
| `PLAN_FETCH` | INFO | ScalarDB からの取得 1 つずつ（アクセスパスと取得用の SQL） |
| `PLAN_RESIDUAL` | INFO | H2 が元の SQL を実行すること（H2 の互換モード、パターン、索引の有無） |
| `PLAN_CROSS_PARTITION` | WARN | 取得のどれかにクロスパーティション走査が要る |
| `PLAN_UNRESOLVED` | WARN | 計画の中に、表か列を解決できなかった所がある |
| `PLAN` | INFO | 分解できなかった理由 |

### 取得コストの見積もり

実行計画の文と、クロスパーティション SCAN になる変換済みの SELECT に付く。数値は ScalarDB Cluster 3.19.1 + PostgreSQL 16、`SERIALIZABLE`、`scan_fetch_size` 10、単一クライアントで測った「スキャン 1 行 約 25 µs、キー指定 1 回 約 5 ms」から計算した目安で、本番の性能を約束しない。

| コード | 重要度 | 意味 |
|---|---|---|
| `COST` | INFO | 取得ごとの読む行数と時間。GET は 5 ms。クロスパーティション SCAN は `--expected-rows 表=N` の N 行、パーティション / 索引 SCAN は `表=N:K` の K 行（キーあたり）を読むとして計算する。`SERIALIZABLE`（`--isolation` の既定）ではコミット時にスキャンを読み直すので、スキャンの分を 2 倍にする。行数が渡されていなければ、渡すよう求める |
| `ROW_LIMIT` | WARN | 取得の見込みの行数が計画の上限（1 万行）を超える。キーの範囲、クラスタリングキーでのページ送り、集計表を検討する |
| `COST_DEADLINE` | WARN | 見積もりの合計が ScalarDB Cluster の gRPC の期限（`scalar.db.cluster.grpc.deadline_duration_millis` の既定 60 秒）を超える |
| `CONFIG` | INFO | 推奨設定。読み取り専用トランザクション（3.16 以降）、スキャンがあれば `scan_fetch_size` を 1000、クロスパーティション SCAN があれば `scalar.db.cross_partition_scan.enabled`、`SERIALIZABLE` でスキャンがあれば分離レベルの影響 |

---

## 指摘コードの一覧

上の節で扱ったもの以外のコード。重要度が複数あるコードは、場合で変わる。

### 読み取り文

| コード | 重要度 | 意味 | 次の手 |
|---|---|---|---|
| `PROJECTION` | ERROR | 射影に式や関数がある | 取得後にアプリで計算する |
| `AGG` | ERROR | 集約関数が COUNT / SUM / AVG / MIN / MAX 以外か、引数が列でなく式（`SUM(qty * price)`） | 同上 |
| `AGG_DISTINCT` | ERROR | `COUNT(DISTINCT …)` | 同上 |
| `PRED` | ERROR | WHERE / HAVING の述語が対応外（左辺が列でない、関数の適用、`IS` の対応外の形、定数の真偽値） | 取得後にアプリで絞る |
| `COL_COL` | ERROR | WHERE の列どうしの比較 | JOIN の ON に移すか、取得後に絞る |
| `EXPR` | ERROR | 値にリテラルとバインド変数以外の式がある | アプリで値を計算してバインドする |
| `NOW` | ERROR | `SYSDATE`・`CURRENT_TIMESTAMP` などの時刻 | 時刻をアプリで計算してバインドする |
| `NOT` | ERROR | 否定を押し下げられない | 否定しない形に書き直す |
| `NORMAL_FORM` | ERROR | WHERE を DNF / CNF に直せなかった | 条件を分けて書き直す |
| `SUBQUERY` / `CTE` / `SET_OP` | ERROR | サブクエリ（`IN (SELECT …)`、派生表を含む）、WITH、UNION / INTERSECT / EXCEPT | 実行計画か、アプリで評価する |
| `DISTINCT` / `OFFSET` | ERROR | `SELECT DISTINCT`、`OFFSET` | アプリで重複を除く / クラスタリングキーの範囲でページを送る |
| `HIERARCHICAL` | ERROR | `START WITH` / `CONNECT BY` | アプリで木をたどるか、階層を表に事前計算する |
| `WINDOW` / `KEEP` / `PIVOT` | ERROR | ウィンドウ関数、`KEEP (DENSE_RANK FIRST/LAST)`、`PIVOT` / `UNPIVOT` | 実行計画か、アプリで処理する |
| `GROUP` | ERROR | `ROLLUP` / `CUBE` / `GROUPING SETS`、列でない GROUP BY | 同上 |
| `ORDER` | ERROR | ORDER BY に列・別名・集約以外の式がある（パーティション SCAN の並びの WARN は上の節） | アプリで並べる |
| `LIMIT` | ERROR | `FETCH … PERCENT`、`FETCH … WITH TIES`（LIMIT n では同順位の行が落ちる）、リテラルでもバインド変数でもない LIMIT | 順に読み、ソートキーが同じ間は読み続ける |
| `ROWNUM` | ERROR | DISTINCT・GROUP BY・集約・ウィンドウ関数と一緒の ROWNUM（ROWNUM は入力の行、LIMIT は出力の行を数える）、整数でない比較、OR の中、LIMIT との併用 | 先に絞ってから、アプリで集約する |
| `FROM` | ERROR | FROM が 1 つの実表でない（派生表など） | 実行計画か、アプリで評価する |
| `JOIN` | ERROR | CROSS / NATURAL / FULL JOIN、結合先が実表でない、RIGHT JOIN が最初の結合でない、結合条件の無いカンマ結合 | 同上 |
| `JOIN_ON` | ERROR | 結合条件が `列 = 列` の AND でない | 同上 |
| `JOIN_SCOPE` | ERROR | WHERE / ORDER BY が結合先の表の列を指している | 同上 |
| `JOIN_KEY` | ERROR | 結合が相手の主キー全体も副次索引も覆っていない（ScalarDB Cluster が DB-SQL-10067 で断る） | 相手の列を主キーにするか索引を足す。読み取りは実行計画に回る |
| `CLAUSE` | ERROR | `TABLESAMPLE`、`QUALIFY`・`WINDOW`・`LATERAL`・`INTO` などの句、表に付く対応外の句（パーティション指定、`AS OF` など） | 句を外して書き直す |
| `LOCK` | WARN | `FOR UPDATE` などのロック句を落とした | 行ロックに頼っていた処理は、commit 時の衝突と再試行に変わる |
| `NULLS` | WARN | ORDER BY の `NULLS FIRST / LAST` を落とした | NULL の並びを確かめる |
| `MODIFIER` | WARN / INFO | MySQL の修飾子を落とした。`SQL_CALC_FOUND_ROWS` は WARN（続く `SELECT FOUND_ROWS()` には別に `COUNT(*)` が要る）、サーバーへの助言だけのものは INFO | — |
| `HINT` | INFO | オプティマイザヒント・索引ヒントを落とした | なし |
| `ONLY` | WARN | PostgreSQL の `ONLY t` を落とした | t に子の表があるなら、その行を t に移さない |

### 書き込み文

| コード | 重要度 | 意味 | 次の手 |
|---|---|---|---|
| `PK` | ERROR | 主キーをすべて指定していない INSERT（主キーの無い表も） | 主キーの列を足す |
| `PK_UPDATE` | ERROR | 主キーの列の UPDATE | DELETE して新しいキーで INSERT する |
| `RMW` | ERROR | `SET col = col + 1` のように列を参照する SET | 1 つのトランザクションの中で SELECT → 計算 → リテラルで UPDATE |
| `SET` | ERROR | 対応外の SET 句 | 書き直す |
| `INSERT_COLS` | WARN | 列リストの無い INSERT。ScalarDB は表の定義の順で受ける | 列リストを書く |
| `INSERT_SELECT` / `INSERT_IGNORE` / `DO_NOTHING` / `DELETE_JOIN` / `UPDATE_JOIN` | ERROR | `INSERT … SELECT`、`INSERT IGNORE`、`ON CONFLICT DO NOTHING`、`DELETE … USING / JOIN`、`UPDATE … FROM / JOIN` | アプリで対象を読んでから、キーを指定して書く |
| `INSERT` / `UPDATE` / `DELETE` | ERROR | `INSERT OVERWRITE` などの変形、ORDER BY / LIMIT つきの UPDATE・DELETE | 主キーで対象行を絞る |
| `RETURNING` | ERROR | `RETURNING` | 書いた後に読み直す |
| `SEQUENCE` | ERROR | 値に `NEXTVAL` / `CURRVAL` | アプリで採番する（UUID など） |
| `UPSERT` | INFO / WARN / ERROR | upsert を `UPSERT INTO` にした（INFO）。上書きする列が元より多い、主キーと見なした一意制約の前提（WARN）。条件つきの DO UPDATE、`列 = EXCLUDED.列` でない更新、主キー以外での衝突（ERROR） | ERROR は 1 つのトランザクションで読んで判断して書く |
| `MERGE` | WARN / ERROR | 定数 1 行の MERGE を UPSERT にした（WARN。WHEN MATCHED が設定しない列も上書きする）。表や問合せをソースにする MERGE、条件つきの枝、`WHEN MATCHED THEN DELETE`、主キー以外での突き合わせ（ERROR） | ERROR は存在確認と書き込みを 1 つのトランザクションにまとめる |
| `REPLACE` | WARN | `REPLACE INTO` を UPSERT にした | 列リストに無い列の値が残ってよいか確かめる |
| `BIND_ORDER` | WARN | 書き換えで位置バインド `?` の順か数が変わった | メッセージの対応どおりにバインドし直す |

### 値とリテラル

| コード | 重要度 | 意味 | 次の手 |
|---|---|---|---|
| `DATE_LIT` | INFO | 日付・時刻のリテラルを ScalarDB のリテラルに書き換えた（0 時の時刻を落とした、0 時を補った、UTC に直した、`--session-time-zone` のゾーンで読んで UTC に直した） | なし |
| `DATE_LIT` | WARN | DATE 列に 0 時以外の時刻を書いている（時刻は落ちる）。TIMESTAMP 列にゾーンつきの値を書いている（ゾーンは落ちる） | 時刻が要るなら列を TIMESTAMP にする |
| `DATE_FMT` | WARN | 書式の無い `TO_DATE('...')`。移行元はセッションの日付書式で読むので、ISO の形の文字列をそのまま書いた | 書式を確かめる |
| `DATE_FMT` | ERROR | 書式が定数でない、ScalarDB のリテラル（`YYYY-MM-DD [HH:MM:SS.FFF]`）に直せない | アプリで変換してバインドする |
| `TZ_ASSUMED_UTC` | WARN | ゾーンの無いリテラルを TIMESTAMPTZ 列に書いている。移行元はセッションのタイムゾーンで読むが、ここでは分からないので UTC と見なした（ScalarDB は末尾 `Z` の TIMESTAMPTZ リテラルしか受け付けない） | `--session-time-zone`（`Asia/Tokyo`、`+09:00`）で再変換する |
| `BOOL_LIT` | INFO | TRUE / FALSE を数値の列に 1 / 0 で書いた | なし |

### 文として扱えないもの

| コード | 重要度 | 意味 | 次の手 |
|---|---|---|---|
| `WITH_PLSQL` | ERROR | Oracle 12c 以降の、WITH 句に PL/SQL の関数・プロシージャを持つ問合せ。`/` だけの行までを 1 文として扱い、変換しない | 関数をアプリ側へ移せば、問合せは変換か実行計画にできる |
| `PLSQL_BLOCK` | ERROR | PL/SQL のブロック（ストアドプログラムか無名ブロック）。SQL 文ではない | plsql-migrate スキルで移行する |
| `SAVEPOINT` | ERROR | SAVEPOINT、名前つきトランザクション | 使わない形にする |
| `STATEMENT` / `UNPARSED` | ERROR | ScalarDB SQL に無い種類の文。SQLGlot が文として解析しなかったもの（ビュー、トリガー、シーケンス、GRANT、セッションの設定など） | 設計を変えるか、ScalarDB の管理手段で行う |
| `TABLE` | ERROR | 表名が来るべき所に表名が無い | 文を見直す |
| `ROWID` | ERROR | `ROWID` / `ROWSCN` / `ORA_ROWSCN` | 主キーで行を特定する |
| `PARSE` / `UNSUPPORTED` | ERROR | Source 方言として読めない / ScalarDB SQL の生成器が出せない構文が残った | `--source` を確かめる / 書き直す |

### アプリ側に移す処理

ERROR の読み取り文には、文全体（CTE の本体、サブクエリを含む）を調べた結果が付く。変換器が最初につまずいた 1 か所だけでなく、アプリに移すものをすべて挙げる。コードは上の「読み取り文」と同じ（`CTE`、`SUBQUERY`、`SET_OP`、`HIERARCHICAL`、`WINDOW`、`KEEP`、`PIVOT`、`DISTINCT`、`OFFSET`、`PROJECTION`、`GROUP`、`PRED`、`NOW`、`ORDER`）で、どのスコープ（主問合せ、CTE 名、サブクエリ）かと、射影と GROUP BY は式そのもの、WHERE は関数名が出る。レポートでは次の 3 つと合わせて「アプリ側に移す処理」節にまとまる。

| コード | 重要度 | 意味 |
|---|---|---|
| `APP_SEMANTICS` | WARN | アプリで書き換えるときに結果を変えないための注意（`app-side-notes.md` の表の行に当たる）。実行計画になった文には付かない（H2 が元の SQL を実行するので、元の意味を保つ） |
| `DESIGN` | INFO | 設計の提案。表定義の無い表、階層の事前計算、GROUP BY のキーでの集計表、結合列のキー・索引、JDBC 以外ではすべてのアクセスにキーを持たせること、集約とウィンドウ関数の分析問合せには ScalarDB Analytics |
| `SEMANTICS` | WARN / INFO | 変換できた文のほうに付く、文字列の意味の差（上の「識別子と文字列」） |
