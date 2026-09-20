# SQL の変換と実行計画

[文書の入口](../README.md) ｜ [はじめに](getting-started.md) ｜ [SQL の変換](sql-conversion.md) ｜ [PL/SQL の変換](plsql-conversion.md) ｜ [スキル](skills.md) ｜ [検証環境](verification.md)

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
