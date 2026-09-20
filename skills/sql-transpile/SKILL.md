---
name: sql-transpile
description: >-
  SQLGlot で Source 方言の SQL を AST に抽象化し、Target 方言（PostgreSQL / MySQL / Oracle /
  SQL Server / Snowflake / BigQuery など SQLGlot の 32 方言）または ScalarDB SQL に変換する。
  変換後 SQL、文ごとの変換可否と理由、変換率のレポートを出力する。素の sqlglot.transpile が
  黙って通してしまう構文（ROWNUM、Oracle 外部結合 (+)、CONNECT BY、NEXTVAL、ROWID）や、
  整数除算・日付の引き算・文字列比較のような方言間の意味の差を、直すか検出して報告する。
  ScalarDB を Target にしたときは、アプリ側に移す処理、結果を変えないための注意、設計の提案、
  取得コストの見積もりも出し、実行計画（ScalarDB から取得して H2 で実行）に分解する。
  使うとき: SQL の方言の変換、ScalarDB 用の SQL への変換、変換率の報告を頼まれたとき。対象外: PL/SQL の変換（plsql-migrate）、実データベースへ接続して結果を比べる検証、性能測定、SQL の整形だけの依頼。
when_to_use: >-
  "SQL を変換して", "Oracle の SQL を PostgreSQL に変換", "ScalarDB 用に SQL を変換",
  "SQL の変換率を出して", "方言を変換", "transpile SQL", "convert SQL to ScalarDB",
  "migrate Oracle SQL to PostgreSQL"。
  仕様の調査から承認・テストまでの一連の流れは migrate-flow スキルが受け持ち、その中でこのスキルの手順を使う。
  対象外: 実データベースへ接続して結果を比較する検証（difftest/run.py、difftest/golden.py capture）、
  性能測定（difftest/bench.py）、SQL の整形だけの依頼。
# 変換そのものは決定的な Python スクリプトが行い、モデルの仕事はコマンドの実行とレポートの要約・説明なので、
# 速くて安い Sonnet で足りる。上書きはスキルを呼んだターンだけで、書き換えや Java 実装の依頼は次のターンに
# セッションのモデルで受ける。利用者に方言や照合順序を確かめることがあるので context: fork にはしない。
model: sonnet
effort: medium
allowed-tools:
  - Read
  - Bash(.venv/bin/python -c "import sqlglot*)
  - Bash(.venv/bin/python skills/sql-transpile/scripts/transpile.py *)
  - Bash(.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --check)
---

# sql-transpile — SQL 方言変換

> **実行場所**: このファイルのコマンドと `allowed-tools` は、sql-migration リポジトリのルートから動かす形（`.venv/bin/python skills/sql-transpile/scripts/...`）で書いてある。スキルのディレクトリは `scalardb_migrate/` を import しない（ScalarDB 変換は `scripts/_scalardb/` の同梱コピーで動く）ので別のプロジェクトへコピーしても変換できるが、そのときは Python（sqlglot 入り）とスクリプトのパスを置いた場所に合わせて読み替え、`allowed-tools` も合わせて直す。

SQL を Source 方言で読んで AST に抽象化し、Target 方言または ScalarDB SQL として生成し直す。
変換できたかどうかを 1 文ずつ判定し、理由と変換率をレポートにまとめる。

## Important

- **作業ディレクトリは sql-migration リポジトリのルート**。すべてのコマンドはここから `.venv/bin/python` で実行する
- **プラグインとして入れたとき（作業ディレクトリが sql-migration のチェックアウトでないとき）**: このスキルの場所は `${CLAUDE_SKILL_DIR}`（置き換わらない環境では、この SKILL.md のあるディレクトリ）で、その 2 つ上が `<root>`。下のコマンドは `.venv/bin/python` を `<root>/bin/python` に、`skills/…` で始まるスクリプトのパスと `fixtures/…`・`samples/…` を `<root>/` からのパスに読み替え、**利用者のプロジェクトを作業ディレクトリにしたまま**動かす（`-m plsql.cli` などはそのままでよい。`bin/python` が `<root>` を import の経路に入れる）。入力と `<out>` は利用者のプロジェクトの側に置き、`<root>` の中には書かない（プラグインの更新で消える）。初回は `bin/python` が仮想環境を作るので 1 分ほどかかる
- **Claude Code 以外（Codex など）で動かすとき**: `allowed-tools` と `when_to_use`（sql-transpile では `model` / `effort` も）は Claude Code 用で、ほかでは無視される。Read / Grep / Bash などの道具の名前は、その環境の同じ働きの道具に読み替える。AskUserQuestion が無ければ、同じ内容（推奨を先頭に、選択肢ごとの影響つき）を本文で聞き、答えを待つ
- **変換はスクリプトに任せ、SQL を自分で変換しない**。このスキルのターンでやるのは、スクリプトの実行、レポートの要約、利用者への確認まで。ERROR の文の書き換えやアプリ側の Java 実装は、利用者が求めたら次の依頼として受ける
- **素の `sqlglot.transpile()` は信用しない**。Oracle → PostgreSQL で `ROWNUM`・`CONNECT BY`・`NEXTVAL`・`ROWID` はそのまま出力へ通り、`(+)` 外部結合は内部結合に化けて結果が静かに変わる。このスキルは前処理でこれらを直すか、直せないものを ERROR として報告する
- **レポートは変換の証明ではない**。OK は「Target の文法として生成でき、既知の危険構文と意味の差が見つからなかった」という意味にとどまる。重要な SQL は実データで確かめるよう利用者に伝える
- **型に依存する書き換えには表定義が要る**。整数どうしの除算（PostgreSQL は切り捨て、Oracle・MySQL・DuckDB は小数を返す）と日付どうしの引き算（MySQL では日数にならない）は、列の型が分かって初めて直せる。入力スクリプトに `CREATE TABLE` を含めるか、`--schema` を渡す。どちらも無いと WARN `DIVISION` / `DATE_ARITH` になる
- **関数は変換先の組み込み関数一覧で判定する**。PostgreSQL・Oracle・MySQL・DuckDB は実際のデータベースから取った一覧（`scripts/catalogs/`）と照合する。一覧に無い関数は、利用者定義の関数であっても WARN `FUNC_PORTABILITY` になる
- **MySQL の文字列比較は大文字小文字を区別しない**（既定の照合順序）。区別する Target へ変換すると WARN `COLLATION` になる。`--mysql-case-insensitive` を付けると `ILIKE` と `LOWER()` に書き換えるが、索引が使われなくなることがある
- **ScalarDB で変換できない読み取り文は、アプリ側に移す処理をすべて列挙する**。CTE の本体やサブクエリの中まで調べた結果と、結果を変えないための注意（`APP_SEMANTICS`）がレポートに出る。手で書き換えるなら必ず利用者に伝える
- **コストの見積もりは目安**。1 台の PC・単一クライアントで測った「スキャン 1 行 約 25 µs、キー指定 約 5 ms」からの計算。本番の性能を約束するものではないと伝える
- 参照資料は必要になったときだけ Read ツールで読む:

  | 資料 | 読むとき |
  |---|---|
  | `references/dialect-notes.md` | 汎用 Target の WARN / ERROR の書き換え方を説明するとき |
  | `references/scalardb-grammar.md` | ScalarDB SQL の文法、指摘コードの意味を説明するとき |
  | `references/app-side-notes.md` | ScalarDB でアプリ側に移す処理の注意と Java の補助クラスを説明するとき |
  | `references/operations.md` | golden での突き合わせ、同梱コピーの同期、関数一覧の作り直し、変換の仕組みを聞かれたとき |

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

使える方言の一覧は `--help` の末尾に出る。

### Step 2: 変換を実行する

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> \
  --source <方言> --target <方言> --out-dir out/transpile
```

利用者が表定義・キー設計・バックエンド・行数を言っていれば、次のオプションを付ける。

| オプション | 効く Target | 付けるとき |
|---|---|---|
| `--schema <json>` | すべて | 入力に `CREATE TABLE` が無く、既存の表定義（Schema Loader の JSON）がある |
| `--mysql-case-insensitive` | ScalarDB 以外 | Source が MySQL で、大文字小文字を区別しない比較を保ちたい |
| `--keys t=p1/c1` | ScalarDB | パーティションキー / クラスタリングキーを指定する |
| `--storage cassandra` | ScalarDB | バックエンドが Cassandra（クロスパーティション走査を使わない前提で判定する） |
| `--plan-dir <dir>` | ScalarDB | 変換できない読み取り文を実行計画に分解する。ScalarDB が Target なら原則付ける |
| `--expected-rows t=N[:K]` | ScalarDB | 表の行数（とキーあたりの行数）が分かっている。見積もりに使う |
| `--isolation` | ScalarDB | 分離レベルが `SERIALIZABLE`（既定）以外 |
| `--h2-indexes` | ScalarDB | 大きな表を結合するバッチ処理。実行計画に H2 の索引を作る指定を入れる（小さな要求では遅くなる） |
| `--session-time-zone ZONE` | ScalarDB | `TZ_ASSUMED_UTC` が出たとき。移行元のセッションのタイムゾーン（`Asia/Tokyo`、`+09:00`）を渡すと、ゾーンの無いリテラルをその時刻として読み、UTC に直す |

例（ScalarDB、Cassandra バックエンド）:

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> \
  --source oracle --target scalardb --out-dir out/transpile --plan-dir out/transpile/plans \
  --storage cassandra --expected-rows sales_transactions=1000000:1000
```

### Step 3: 結果を読む

標準出力の最終 3 行は機械可読（`TOTAL=` / `CONVERTED=` / `RATE=`）。終了コードで次の手を決める。

| 終了コード | 意味 | 次の手 |
|---|---|---|
| 0 | ERROR の文なし | Step 4 は WARN の確認だけ |
| 1 | ERROR の文あり | Step 4 で手作業の要る文を示す |
| 2 | 入力の誤り（無いファイル、UTF-8 でないファイル、読めない `--schema`、形式の違う引数、**変換する文が 1 つも無い**） | Error Handling へ。レポートは出ていない |

1 文の変換中に変換器が想定外の失敗をしても、その文が ERROR `INTERNAL` になるだけで、残りの文は変換される（終了コード 1）。
閉じていない文字列などでスクリプトを文に分けられないときは、全体が 1 件の ERROR `TOKENIZE` になる。

ScalarDB を Target にしたときは、`vendor_sync.py --check` も実行する。終了コード 1 なら同梱コピーが本体と食い違っているので、利用者に伝える（取り込み方は `references/operations.md`）。最終行が `VENDOR_DRIFT=n/a`（終了コード 0）なら、スキルがリポジトリの外に置かれていて比べる本体が無い。変換は同梱コピーで動くので、そのまま進める。

### Step 4: 利用者に報告する

`out/transpile/<入力名>.report.md` を読み、次の順で伝える。

1. **変換率**。`CONVERTED / TOTAL` と割合。`PLANNED`（実行計画あり）は変換率に含めないので、件数を別に伝える
2. **手作業が要る文**（ERROR）と**実行計画の文**（PLANNED）。レポート末尾の「アプリ側に移す処理」節から、文ごとに次を伝える
   - **アプリ側で処理する構文**（`CTE`、`HIERARCHICAL`、`WINDOW`、`PROJECTION`、`GROUP` など）。`RESIDUAL_H2` は H2 でも実行できないのでアプリで実装する。`FULL_SCAN` は Cassandra でキーが無い表で、読む順序の提案が付く
   - **結果を変えないための注意**（`APP_SEMANTICS`）: 0 除算、`ROUND` の丸め方、NULL の並び順など。手で書き換えるなら全部伝える
   - **設計の提案**（`DESIGN`）、**取得コスト**（`COST` / `ROW_LIMIT` / `COST_DEADLINE`）、**推奨設定**（`CONFIG`）
3. **要確認の文**（WARN）。コードごとに次の手が違う
   - `DIVISION` / `DATE_ARITH`: 表定義を渡せば自動で直る。表定義を持っていないか利用者に尋ねる。Oracle → MySQL 以外の `DATE_ARITH` は書き換えない（日数か interval かは元の意図による）ので、式を見せて確かめる
   - `EMPTY_STRING` / `DATE_TIME`: Oracle は `''` を NULL として扱い、DATE は時刻を持つ。その列で空文字と NULL を区別しているか、時刻を使っているかを尋ねる
   - `COLLATION`: 大文字小文字を区別しない比較を保つ必要があるか尋ねる。必要なら `--mysql-case-insensitive` で再変換する
   - `FUNC_PORTABILITY`: 名前の出た関数が Target にあるか、利用者定義の関数かを確かめる
   - `ROWNUM`: ORDER BY を伴う場合は件数の意味が変わりうる。元の意図を確かめる
4. **出力ファイルの場所**

ERROR の文の書き換えを頼まれたら、書き換え後の SQL をもう一度このスキルに通して OK になることを確かめる。アプリ側の Java 実装を書いたら、golden での突き合わせ（`references/operations.md`）を案内する。

## Error Handling

| 状況 | 対処 |
|---|---|
| `ModuleNotFoundError: sqlglot` | リポジトリルートで実行しているか確認し、`.venv/bin/pip install -r requirements.txt` |
| 終了コード 2「ファイルが見つかりません」 | 入力パスをリポジトリルートからの相対パスで指定し直す |
| 終了コード 2「UTF-8 として読めません」 | 利用者に文字コードを確認し、`iconv -f <元の文字コード> -t UTF-8` で変換してから渡す |
| 終了コード 2「変換する文がありません」 | 空のファイルか、コメントだけのファイル。渡すファイルが合っているか確認する |
| ERROR `INTERNAL` | 変換器の不具合。その文を手作業の対象として示し、文を添えて報告するよう利用者に伝える |
| ERROR `TOKENIZE` | メッセージの位置の近くで、文字列リテラルの引用符が閉じているか確認する |
| `invalid choice` | 方言名の綴りを直す。`--help` の末尾に一覧がある |
| `PARSE` の ERROR が大量に出る | `--source` が実際の方言と違う可能性が高い。方言を確認する |
| `DIVISION` の WARN が大量に出る | 表定義が無い。`CREATE TABLE` を入力に含めるか `--schema` を渡して再変換する |
| 利用者定義の関数が `FUNC_PORTABILITY` になる | 仕様どおり。Target にも同じ関数を作るか確認する |
| ScalarDB Target で型の WARN が多い | Source が oracle / postgres / mysql 以外だと型対応表の精度が落ちる。標準エラーの注意書きを伝える |
| `vendor_sync.py --update` が「本体が見つかりません」 | スキルがリポジトリの外にコピーされている。同梱コピーの更新は、リポジトリ内のスキルで行う（`--check` は外でも 0 で終わる） |
| `RESIDUAL_H2` の ERROR | H2 で実行できない構文。アプリで実装するか、`references/app-side-notes.md` の書き換え（再帰 WITH、UNION ALL など）を案内する |
| `FULL_SCAN` の ERROR（`--storage cassandra`） | キーで読めない表。メッセージの「read X first, then Y」に従ってキーで読むか、集計表を設ける |
| `ROW_LIMIT` / `COST_DEADLINE` の WARN | 読む行数が多すぎる。集計表・キー範囲の追加を提案する。行数は `--expected-rows` の値なので、実際の件数を確かめる |

## Output

`--out-dir` を指定すると次を出す。`<stem>` は入力ファイル名から拡張子を除いたもの。

| ファイル | 内容 |
|---|---|
| `<stem>.<target>.sql` | 変換後 SQL。変換できない文は `-- [NOT CONVERTED #n]`、実行計画の文は `-- [APP-SIDE PLAN #n]` と取得用 SQL の後に原文をコメントで残す |
| `<stem>.report.md` | 文ごとの表、指摘コードの集計、アプリ側に移す処理、変換率 |
| `<stem>.report.json` | `{source, target, summary, results}`。`summary` に `total / ok / warn / error / planned / converted / rate` |
| `<plan-dir>/<stem>.<n>.plan.json` | `--plan-dir` 指定時。取得用 SQL、H2 で実行する SQL、`transaction`、`recommended_config` |

レポートの名前に Target は入らない。同じ入力を別の Target へ変換するときは、`--out-dir` を Target ごとに分ける（同じ場所に出すと、レポートは後の変換のもので上書きされる）。`--plan-dir` の `<stem>.*.plan.json` は実行のたびに作り直す。

| 状態 | 意味 |
|---|---|
| OK | Target の SQL を生成でき、指摘なし（INFO は自動で直したことの記録） |
| WARN | 生成できたが確認が要る |
| PLANNED | ScalarDB SQL にはできないが、実行計画で動かせる（`--plan-dir` 指定時だけ） |
| ERROR | 生成できない、または Target に無い構文が残った。手作業が要る |

変換率 = (OK + WARN) / 全文数。
