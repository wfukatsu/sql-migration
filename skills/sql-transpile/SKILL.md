---
name: sql-transpile
description: >-
  SQLGlot で Source 方言の SQL を AST に抽象化し、Target 方言（PostgreSQL / MySQL / Oracle /
  SQL Server / Snowflake / BigQuery など SQLGlot の 32 方言）または ScalarDB SQL に変換する。
  変換後 SQL、文ごとの変換可否と理由、変換率のレポートを出力する。素の sqlglot.transpile が
  黙って通してしまう構文（ROWNUM、Oracle 外部結合 (+)、CONNECT BY、NEXTVAL、ROWID）や、
  整数除算・日付の引き算・文字列比較のような方言間の意味の差を、直すか検出して報告する。
  トリガー: "SQL を変換して", "Oracle の SQL を PostgreSQL に変換", "ScalarDB 用に SQL を変換",
  "SQL の変換率を出して", "方言を変換", "transpile SQL", "convert SQL to ScalarDB",
  "migrate Oracle SQL to PostgreSQL".
  ScalarDB を Target にしたときは、アプリ側に移す処理の一覧、結果を変えないための注意、設計の提案、
  取得コストの見積もりも出し、--plan-dir で実行計画（ScalarDB から取得して H2 で実行）に分解する。
  対象外: 実データベースへ接続して結果を比較する検証（difftest/run.py、difftest/golden.py capture）、
  性能測定（difftest/bench.py）、SQL の整形だけの依頼。
---

# sql-transpile — SQL 方言変換

SQL を Source 方言で読んで AST に抽象化し、Target 方言または ScalarDB SQL として生成し直す。
変換できたかどうかを 1 文ずつ判定し、理由と変換率をレポートにまとめる。

## Important

- **作業ディレクトリは sql-migration リポジトリのルート**。すべてのコマンドはここから `.venv/bin/python` で実行する
- **素の `sqlglot.transpile()` は信用しない**。Oracle → PostgreSQL で `ROWNUM`・`CONNECT BY`・`NEXTVAL`・`ROWID` はそのまま出力へ通り、`(+)` 外部結合は内部結合に化けて結果が静かに変わる。このスキルは前処理でこれらを直すか、直せないものを ERROR として報告する
- **レポートは変換の証明ではない**。OK は「Target の文法として生成でき、既知の危険構文と意味の差が見つからなかった」という意味にとどまる。重要な SQL は実データで確かめるよう利用者に伝える。テスト用 SQL での実測は `docs/transpile-fix-research.md` にある
- **型に依存する書き換えには表定義が要る**。整数どうしの除算（PostgreSQL は切り捨て、Oracle・MySQL・DuckDB は小数を返す）と日付どうしの引き算（MySQL では日数にならない）は、列の型が分かって初めて直せる。入力スクリプトに `CREATE TABLE` を含めるか、`--schema` を渡す。どちらも無いと WARN `DIVISION` / `DATE_ARITH` になる
- **関数は変換先の組み込み関数一覧で判定する**。PostgreSQL・Oracle・MySQL・DuckDB は実際のデータベースから取った一覧（`scripts/catalogs/`）と照合する。一覧に無い関数は、利用者定義の関数であっても WARN `FUNC_PORTABILITY` になる。一覧の無い Target では、Source 固有の関数名が残っているかで判定するので誤検出がある
- **MySQL の文字列比較は大文字小文字を区別しない**（既定の照合順序）。区別する Target へ変換すると WARN `COLLATION` になる。`--mysql-case-insensitive` を付けると `ILIKE` と `LOWER()` に書き換えるが、索引が使われなくなることがある
- **ScalarDB で変換できない読み取り文は、アプリ側に移す処理をすべて列挙する**。最初に見つけた 1 つだけでなく、CTE の本体やサブクエリの中まで調べる。Oracle の結果と一致させるための注意（`APP_SEMANTICS`）も併せて出る。手で書き換えるときは必ず利用者に伝える
- **実行計画が作れても動くとは限らなかった点は直してある**。H2 が実行できない構文（`CONNECT BY`、`ROLLUP` / `CUBE` / `GROUPING SETS`、`PIVOT` / `UNPIVOT`、`KEEP`）を含む文は計画にせず ERROR `RESIDUAL_H2` にする
- **コストの見積もりは目安**。1 台の PC・単一クライアントで測った「スキャン 1 行 約 25 µs、キー指定 約 5 ms」からの計算で、`SERIALIZABLE` ではスキャンを 2 倍にする。本番の性能を約束するものではないと伝える
- 詳細な書き換え方は `references/dialect-notes.md`、ScalarDB SQL の文法と指摘コードは `references/scalardb-grammar.md`、アプリ側で処理するときの注意と Java の補助クラスは `references/app-side-notes.md` を Read ツールで読む。必要になるまで読まない

## Workflow

### Step 0: 環境を確認する

```bash
.venv/bin/python -c "import sqlglot; print('sqlglot', sqlglot.__version__)"
```

失敗したら `.venv/bin/pip install -r requirements.txt` を案内する。

### Step 1: Source と Target を確定する

利用者の依頼から方言を読み取る。はっきりしない場合だけ確認する。

| 利用者の言い方 | 方言名 |
|---|---|
| Oracle | `oracle` |
| PostgreSQL / Postgres | `postgres` |
| MySQL / MariaDB | `mysql` |
| SQL Server | `tsql` |
| DuckDB | `duckdb` |
| ScalarDB | `scalardb`（Target のみ） |

使える方言の一覧は `--help` の末尾に出る。`scalardb` は Target にしか指定できない。

### Step 2: 変換を実行する

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> \
  --source <方言> --target <方言> --out-dir out/transpile
```

入力に `CREATE TABLE` が無く、利用者が既存の表定義を持っている場合は `--schema` で渡す。形式は ScalarDB Schema Loader の JSON。

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> \
  --source postgres --target oracle --out-dir out/transpile --schema existing-schema.json
```

Source が MySQL で、大文字小文字を区別しない比較を Target でも保ちたいときは `--mysql-case-insensitive` を付ける。

ScalarDB を Target にするときは、キー設計の指定も渡せる。

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> \
  --source oracle --target scalardb --out-dir out/transpile \
  --keys orders=customer_id/order_no --schema existing-schema.json
```

ScalarDB を Target にするときは、バックエンド・実行計画・見積もりの前提も渡せる。利用者がバックエンドや行数を言っていれば付ける。

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> \
  --source oracle --target scalardb --out-dir out/transpile \
  --storage cassandra --plan-dir out/transpile/plans \
  --expected-rows sales_transactions=1000000:1000 --isolation SERIALIZABLE
```

| オプション | 効く Target |
|---|---|
| `--schema` | すべて。ScalarDB ではキー設計に、ほかでは型に依存する書き換えに使う |
| `--keys` | ScalarDB のみ。`テーブル=パーティションキー/クラスタリングキー` |
| `--storage` | ScalarDB のみ。`jdbc`（既定、RDBMS）か `cassandra`。cassandra ではクロスパーティションスキャンを使わない前提で判定する |
| `--plan-dir` | ScalarDB のみ。変換できない読み取り文を実行計画に分解し、`<stem>.<n>.plan.json` を書く。指定しないと分解しない |
| `--expected-rows` | ScalarDB のみ。`テーブル=行数[:キーあたりの行数]`。取得コストの見積もりと行数上限の判定に使う |
| `--isolation` | ScalarDB のみ。見積もりの前提にする分離レベル（既定 `SERIALIZABLE`） |
| `--mysql-case-insensitive` | ScalarDB 以外。Source が MySQL のときだけ意味がある |

### Step 3: 結果を読む

標準出力の最終 3 行は機械可読になっている。

```
TOTAL=21
CONVERTED=19
RATE=90.5
```

終了コードで次の手を決める。

| 終了コード | 意味 | 次の手 |
|---|---|---|
| 0 | ERROR の文なし | Step 4 は WARN の確認だけ |
| 1 | ERROR の文あり | Step 4 で手作業の要る文を示す |
| 2 | 実行エラー | Error Handling へ |

### Step 4: 利用者に報告する

`out/transpile/<入力名>.report.md` を読み、次の順で伝える。

1. **変換率**。`CONVERTED / TOTAL` と割合。`PLANNED`（実行計画あり）の文は変換率に含めないので、件数を別に伝える
2. **手作業が要る文**（ERROR）と**実行計画の文**（PLANNED）。レポート末尾の「アプリ側に移す処理」節に、文ごとに次の 5 つがまとまっている。書き換え方は `references/dialect-notes.md`（汎用）と `references/app-side-notes.md`（ScalarDB）を引く
   - **アプリ側で処理する構文**: `CTE`、`HIERARCHICAL`、`WINDOW`、`PROJECTION`、`GROUP` など。どの CTE・サブクエリの中にあるかも書いてある。`RESIDUAL_H2` は H2 でも実行できないのでアプリで実装する。`FULL_SCAN` は Cassandra でキーが無い表で、「どの表のキーで読めばよいか」の提案が付く
   - **結果を変えないための注意**（`APP_SEMANTICS`）: 0 除算、`ROUND` の丸め方、NULL の並び順、文字列の並び順、`LAG` が暦の前月ではないこと など。手で書き換えるなら全部伝える
   - **設計の提案**（`DESIGN`）: 集計表、階層の事前計算、結合列のキー・インデックス、ScalarDB Analytics
   - **取得コストの見積もり**（`COST` / `ROW_LIMIT` / `COST_DEADLINE`）: 行数を渡したときだけ数値が出る。`ROW_LIMIT`（計画の行数上限 1 万行超え）と `COST_DEADLINE`（60 秒の gRPC 期限超え）は設計の見直しが要る
   - **推奨設定**（`CONFIG`）: 読み取り専用トランザクション、`scan_fetch_size`、分離レベルの影響
3. **要確認の文**（WARN）。コードごとに次の手が違う
   - `DIVISION` / `DATE_ARITH`: 表定義を渡せば自動で直る。表定義を持っていないか利用者に尋ねる
   - `COLLATION`: 大文字小文字を区別しない比較を保つ必要があるか尋ねる。必要なら `--mysql-case-insensitive` で再変換する
   - `FUNC_PORTABILITY`: 名前の出た関数が Target にあるか、利用者定義の関数かを確かめる
   - `ROWNUM`: ORDER BY を伴う場合は件数の意味が変わりうる。元の意図を確かめる
4. **出力ファイルの場所**

ERROR の文を利用者の代わりに書き換える場合は、書き換え後の SQL をもう一度このスキルに通して OK になることを確かめる。

### Step 4b: アプリ側の実装を Oracle の結果と突き合わせる（ScalarDB でアプリ側に移したとき）

アプリ側の Java 実装を書いたら、Oracle の結果と比べる方法を利用者に案内する。Oracle で 1 回だけ正解（入力の表と結果）を取り、以降は DB なしで比べる。

```bash
# 1 回だけ（Oracle が要る）
.venv/bin/python difftest/golden.py capture --setup <setup.sql> --query <query.sql> \
  --tables <表1>,<表2> --out difftest/golden/<名前>
# 以降（DB 不要。runtime-java を gradle installDist しておく）
.venv/bin/python difftest/golden.py check --golden difftest/golden/<名前> --impl <実装クラス名>
```

実装クラスは `com.scalar.migrate.appside.AppSideQuery` を実装する。例は `runtime-java/src/main/java/com/scalar/migrate/examples/AreaSalesReport.java`。`capture` を代わりに実行するのは、利用者が Oracle の接続先を用意しているときだけ。

### Step 5: 同梱コピーの鮮度を確かめる（ScalarDB を Target にしたとき）

ScalarDB 変換は、リポジトリ本体 `scalardb_migrate/` のコピーを `scripts/_scalardb/` に同梱して使っている。本体を改善してもコピーには自動で反映されない。

```bash
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --check
```

終了コード 1（`VENDOR_DRIFT=` が 0 でない）なら、本体と食い違っている。利用者に伝え、取り込むなら次を実行する。

```bash
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --update
```

### Step 6: 関数一覧を作り直す（Target のバージョンが違うとき）

関数一覧は PostgreSQL 16、Oracle Database 23ai Free、MySQL 8.4、DuckDB 1.5.5 から取った。利用者の Target のバージョンが大きく違い、`FUNC_PORTABILITY` の結果が疑わしいときだけ作り直す。作り直すと、指定した方言の `scripts/catalogs/<方言>.json` が上書きされる。

```bash
.venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --duckdb
.venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --postgres postgresql://user:pass@host:5432/db
```

接続先は利用者に確認する。Oracle は `--oracle user/pass@host:1521/service`、MySQL は `--mysql user:pass@host:3306`。

## 仕組み

Target によって 2 つのバックエンドを使い分ける。どちらも同じ形の結果を返すので、レポートは共通。

```
                       ┌─ target = scalardb ──→ 同梱した ScalarDB 変換ルール一式
入力 SQL ─→ 文分割 ─┤                          （DNF/CNF 正規化・キー設計・型対応）
                       └─ target = その他 ────→ 汎用パス（下の 6 段）
```

汎用パスの 6 段:

1. **解析** — Source 方言で AST にする。スクリプト内の `CREATE TABLE` から表定義を集め、型の判定に使う
2. **変換元の検査** — Target に持ち込めない構文（`CONNECT BY`、`ROWID`、`NEXTVAL`、`KEEP` など）を AST で拾う。コメントや文字列リテラルの中の語には反応しない
3. **前処理** — SQLGlot が直さない構文を AST 上で直す。外部結合 `(+)`、`ROWNUM`、再帰 CTE、`FROM dual`、日付リテラル、整数除算、`INTERVAL`、`DISTINCT ON`、`TRUNC` の書式、引用符付きの識別子など
4. **生成** — `ErrorLevel.RAISE` で生成し、SQLGlot が未対応と知っている構文を例外にする
5. **変換先の検査** — Target が持っていない構文（`MERGE`、`ON CONFLICT`、`RETURNING` など）と、Target の組み込み関数一覧に無い関数を拾う
6. **往復検証** — 生成した SQL が Target 方言としてパースできることを確かめる

## Error Handling

| 状況 | 対処 |
|---|---|
| `ModuleNotFoundError: sqlglot` | リポジトリルートで実行しているか確認し、`.venv/bin/pip install -r requirements.txt` |
| 終了コード 2「ファイルが見つかりません」 | 入力パスをリポジトリルートからの相対パスで指定し直す |
| `invalid choice` | 方言名の綴りを直す。`--help` の末尾に一覧がある |
| `PARSE` の ERROR が大量に出る | `--source` が実際の方言と違う可能性が高い。方言を確認する |
| `DIVISION` の WARN が大量に出る | 表定義が無い。`CREATE TABLE` を入力に含めるか `--schema` を渡して再変換する |
| 利用者定義の関数が `FUNC_PORTABILITY` になる | 仕様どおり。Target にも同じ関数を作るか確認する |
| ScalarDB Target で型の WARN が多い | Source が oracle / postgres / mysql 以外だと型対応表の精度が落ちる。標準エラーの注意書きを利用者に伝える |
| `vendor_sync.py` が「本体が見つかりません」 | スキルがリポジトリの外にコピーされている。リポジトリ内のスキルを使う |
| `build_catalogs.py` が失敗する | 必要なドライバ（duckdb / psycopg / oracledb / pymysql）と接続先を確認する。失敗した方言の一覧は変わらない |
| `RESIDUAL_H2` の ERROR | H2 で実行できない構文。計画は作らない。アプリで実装するか、`references/app-side-notes.md` の書き換え（再帰 WITH、UNION ALL など）を案内する |
| `FULL_SCAN` の ERROR（`--storage cassandra`） | キーで読めない表。メッセージの「read X first, then Y」に従ってキーで読むか、集計表・参照用の表を設ける。循環する場合は、アプリが既に持っているキーから始める設計が要る |
| `ROW_LIMIT` / `COST_DEADLINE` の WARN | 読む行数が多すぎる。集計表・キー範囲の追加を提案する。行数は `--expected-rows` の値なので、利用者に実際の件数を確かめる |

## Output

`--out-dir` を指定すると 3 ファイルを出す。`<stem>` は入力ファイル名から拡張子を除いたもの。

| ファイル | 内容 |
|---|---|
| `<stem>.<target>.sql` | 変換後 SQL。変換できない文は `-- [NOT CONVERTED #n]`、実行計画の文は `-- [APP-SIDE PLAN #n]` と取得用 SQL の後に原文をコメントで残す |
| `<stem>.report.md` | 文ごとの表（# / 種別 / 状態 / 元の SQL / 変換後 / 指摘）、指摘コードの集計、アプリ側に移す処理（文ごとに構文・意味の差・設計・コスト・設定）、変換率 |
| `<stem>.report.json` | `{source, target, summary, results}`。`summary` に `total / ok / warn / error / planned / converted / rate` |
| `<plan-dir>/<stem>.<n>.plan.json` | `--plan-dir` 指定時。取得用 SQL、H2 で実行する SQL、`transaction`（読み取り専用）、`recommended_config` |

状態の意味:

| 状態 | 意味 |
|---|---|
| OK | Target の SQL を生成でき、指摘なし（INFO は自動で直したことの記録） |
| WARN | 生成できたが確認が要る（型が分からない除算、関数の有無、照合順序、ROWNUM と ORDER BY の順序など） |
| PLANNED | ScalarDB SQL にはできないが、実行計画（ScalarDB から取得して H2 で実行）で動かせる。`--plan-dir` を指定したときだけ出る |
| ERROR | 生成できない、または Target に無い構文が残った。手作業が要る |

変換率 = (OK + WARN) / 全文数。
