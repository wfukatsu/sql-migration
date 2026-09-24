# SQL の変換と実行計画

[文書の入口](../README.md) ｜ [はじめに](getting-started.md) ｜ [チュートリアル](tutorial.md) ｜ [SQL の変換](sql-conversion.md) ｜ [PL/SQL の変換](plsql-conversion.md) ｜ [スキル](skills.md) ｜ [検証環境](verification.md)

SQL 文を ScalarDB SQL に変換し、変換できない読み取り文を実行計画（ScalarDB から取得 → H2 で元の SQL）に分解して動かすまでの使い方です。
仕組みは [アーキテクチャ](../design/architecture.md) の 4〜9 章、変換ルールの一覧は [scalardb-grammar.md](../../skills/sql-transpile/references/scalardb-grammar.md) にあります。

## 変換ツール（`scalardb_migrate.cli`）

```bash
.venv/bin/python -m scalardb_migrate.cli <file.sql> --source oracle|postgres|mysql [options]
```

| オプション | 意味 |
|---|---|
| `--source`（`--dialect`） | 移行元の方言 |
| `--out-dir DIR` | 変換後 SQL・レポート・スキーマを書き出す |
| `--plan-dir DIR` | 読み取りの ERROR 文を実行計画に分解し、`<name>.<n>.plan.json` を書く |
| `--no-plan` | 実行計画に分解しない |
| `--schema FILE` | 既存の表定義（ScalarDB Schema Loader の JSON）。アクセスパス分析に使う |
| `--keys t=p1,p2/c1` | 表のパーティションキー / クラスタリングキーを指定する |
| `--storage jdbc\|cassandra` | ScalarDB のバックエンド。`cassandra` ではパーティションをまたぐ `ORDER BY` などを実行計画に回す |
| `--expected-rows t=N[:K]` | 表の行数（とキーあたりの行数）。取得コストの見積もりに使う |
| `--isolation` | 見積もりの前提にする分離レベル（既定 `SERIALIZABLE`） |
| `--row-limit N` | 実行計画が 1 表から取得する行数の上限（既定 10,000） |
| `--h2-indexes` | 実行計画に「H2 に索引を作る」指定を入れる（既定オフ。大きな表を結合するバッチ処理向け） |
| `--session-time-zone ZONE` | 移行元のセッションのタイムゾーン（`Asia/Tokyo`、`+09:00`）。ゾーンの無いリテラルを TIMESTAMPTZ 列に書くとき、そのゾーンの時刻として読んで UTC に直す（指定しないと UTC と仮定し、`TZ_ASSUMED_UTC` を出す） |

出力（`--out-dir`）:

| ファイル | 内容 |
|---|---|
| `<name>.scalardb.sql` | 変換後の SQL。変換できない文は `-- [NOT CONVERTED #n]` のコメントで残す |
| `<name>.report.md` / `.json` | 文ごとの判定、変換結果、指摘（重要度・コード・メッセージ）、アプリ側に移す処理 |
| `<name>.schema.json` | `CREATE TABLE` / `CREATE INDEX` から作った Schema Loader 形式のスキーマ |
| `<plan-dir>/<name>.<n>.plan.json` | 実行計画（取得の SQL、H2 で実行する SQL、ガードレール、推奨設定） |

判定:

| 判定 | 意味 | 次にやること |
|---|---|---|
| **OK** | ScalarDB SQL に変換できた | そのまま使う |
| **WARN** | 変換できたが、意味や性能に注意がある（クロスパーティション走査、精度の損失など） | 指摘を確認する |
| **PLANNED** | ScalarDB SQL にはできないが、実行計画（取得 → H2）で動かせる | 行数と応答時間を確認する |
| **ERROR** | 自動では移行できない | レポートの対応案に沿ってアプリ側で実装する |

## データ型の対応

`CREATE TABLE` と `ALTER TABLE ... ADD / MODIFY COLUMN` の列の型は、`scalardb_migrate/types.py` の `map_type()` が ScalarDB の 11 型
（BOOLEAN / INT / BIGINT / FLOAT / DOUBLE / TEXT / BLOB / DATE / TIME / TIMESTAMP / TIMESTAMPTZ）に対応させます。
対応のたびに重要度（INFO / WARN / ERROR）と指摘文（コード `TYPE`）が付き、ERROR は文全体を ERROR にします。

| 移行元の型 | ScalarDB の型 | 重要度 | 理由・注意 |
|---|---|---|---|
| TINYINT / SMALLINT / INT（MySQL, PostgreSQL） | INT | INFO | MySQL の `TINYINT(1)` は真偽値に使われることが多いので WARN（BOOLEAN を検討） |
| INTEGER / INT / SMALLINT（Oracle） | BIGINT | WARN | Oracle の整数型は `NUMBER(38)` の別名。64 ビットを超える値はあふれる。`NUMBER(p)` で宣言すると正確に対応する |
| BIGINT | BIGINT | INFO | 符号なし 32 ビット（`INT UNSIGNED`）も BIGINT に収まる |
| BIGINT UNSIGNED | BIGINT | WARN | 符号なし 64 ビットの範囲は ScalarDB の BIGINT（符号あり）を超える |
| DECIMAL / NUMBER / NUMERIC(p, 0) | p ≤ 9: INT、p ≤ 18: BIGINT | INFO | 桁数だけで決める。正確に収まる |
| DECIMAL / NUMBER / NUMERIC(p, 0)、p > 18 | BIGINT | WARN | 64 ビットを超える値はあふれる |
| DECIMAL / NUMBER / NUMERIC(p, s)、s > 0 | DOUBLE | WARN | ScalarDB に DECIMAL が無い。精度が落ちるので、金額は 10^s 倍した整数を BIGINT に入れることを提案する。実行計画（H2）ではこの列を `NUMERIC(p, s)` として扱い、`2450` が `2450.0` になるのを防ぐ |
| 精度なしの NUMBER（Oracle）/ NUMERIC（PostgreSQL） | DOUBLE | WARN | 桁数も小数も無制限なので、正確な 10 進精度は保てない。MySQL の `DECIMAL` だけは既定の (10, 0) として読む |
| FLOAT（Oracle） | DOUBLE | WARN | Oracle の FLOAT は最大 38 桁の 10 進 NUMBER。DOUBLE では約 15 桁 |
| FLOAT（PostgreSQL、精度なしか 25 以上） | DOUBLE | INFO | PostgreSQL では倍精度 |
| FLOAT / REAL（その他） | FLOAT | INFO | |
| DOUBLE / DOUBLE PRECISION / BINARY_DOUBLE | DOUBLE | INFO | |
| CHAR(n) / NCHAR(n)、n > 1 | TEXT | WARN | CHAR は空白詰めで、比較は空白を無視する。TEXT は厳密に比較するので、移行時にデータを trim しないと同じ比較が一致しなくなる |
| VARCHAR / VARCHAR2 / NVARCHAR / TEXT 系 | TEXT | INFO | 長さの上限は ScalarDB では強制されない |
| BINARY / VARBINARY / BLOB / RAW / BYTEA | BLOB | INFO | |
| DATE（Oracle） | DATE | WARN | Oracle の DATE は時刻を持つ。時刻を使うなら TIMESTAMP にする |
| DATE（その他） | DATE | INFO | |
| TIME | TIME | INFO | マイクロ秒（6 桁）まで |
| TIME WITH TIME ZONE | TIME | WARN | ScalarDB の TIME にタイムゾーンは無く、オフセットが落ちる |
| TIMESTAMP / DATETIME、精度 3 以下 | TIMESTAMP | INFO | |
| TIMESTAMP / DATETIME、精度 4 以上（Oracle・PostgreSQL の既定は 6） | TIMESTAMP | WARN | ScalarDB はミリ秒まで。MySQL だけは精度なしを 0 として読む |
| TIMESTAMP WITH (LOCAL) TIME ZONE / TIMESTAMPTZ | TIMESTAMPTZ | INFO（精度 4 以上は WARN） | UTC で保存し、ミリ秒まで |
| BOOLEAN / BIT(1) | BOOLEAN | INFO | |
| BIT(n)、n > 1 | BLOB | WARN | 対応する型が無い |
| JSON / JSONB / UUID / ENUM / SET / INET / XML | TEXT | WARN | 文字列として入る。JSON の演算子や列挙の検査は使えない |
| SERIAL / BIGSERIAL / SMALLSERIAL | INT / BIGINT | **ERROR** | 自動採番は無い。ID はアプリで生成する |
| そのほか（INTERVAL、配列、ユーザ定義型など） | なし | **ERROR** | 対応する型が無い |

型のほかに、日付時刻のリテラルも列の型に合わせて直します。Oracle の DATE リテラルの時刻部分は DATE 列では落とし、日付だけのリテラルは TIMESTAMP 列では `00:00:00` を補い、
TIMESTAMPTZ 列のリテラルは ScalarDB が受け付ける唯一の形 `'YYYY-MM-DD HH:MM:SS Z'` に UTC 変換して書きます（ゾーンの無いリテラルの読み方は `--session-time-zone`）。

PL/SQL から生成する Java の型（`NUMBER(p, s)` を `BigDecimal` と 10^s 倍した BIGINT にするなど）は別の対応表 `plsql/gen_java/types.py` で決めます。
小数の扱いが SQL 変換（DOUBLE）と違うのは意図的で、考え方は [PL/SQL 移行基盤の設計](../design/plsql-migration-platform-design.md) の 5.3「型表現」にあります。

## 実行計画を動かす（`residual-runner`）

```bash
runtime-java/build/install/residual-runner/bin/residual-runner run --plan out/plans/oracle.8.plan.json \
    --properties difftest/conf/scalardb-sql-jdbc.properties --fetcher jdbc [--h2-indexes] [--param name=value]
```

| サブコマンド | 役割 |
|---|---|
| `run` | 計画を実行し、結果を JSON で出す。`--fetcher core`（ScalarDB Core API、ライセンス不要）/ `jdbc`（ScalarDB SQL、Cluster とライセンスが要る） |
| `validate` | H2 で元の SQL をコンパイルするだけ（DB 不要） |
| `load` | JSON の行を ScalarDB Core 経由で表に入れる |
| `sql` | ScalarDB SQL を 1 文実行する |
| `bench` | 移行元 DB と ScalarDB の応答時間を同じ JVM から測る |

結果は標準出力に JSON で、ログ（ScalarDB のログを含む）は標準エラーに WARN 以上だけ出ます。詳しいログが要るときは `RESIDUAL_RUNNER_OPTS=-Dorg.slf4j.simpleLogger.defaultLogLevel=info` を付けて実行します。
