---
name: sql-transpile
description: >-
  SQLGlot で Source 方言の SQL を AST に抽象化し、Target 方言（PostgreSQL / MySQL / Oracle /
  SQL Server / Snowflake / BigQuery など SQLGlot の 32 方言）または ScalarDB SQL に変換する。
  変換後 SQL、文ごとの変換可否と理由、変換率のレポートを出力する。素の sqlglot.transpile が
  黙って通してしまう構文（ROWNUM、Oracle 外部結合 (+)、CONNECT BY、NEXTVAL、ROWID、方言固有の関数）
  を検出して報告する。
  トリガー: "SQL を変換して", "Oracle の SQL を PostgreSQL に変換", "ScalarDB 用に SQL を変換",
  "SQL の変換率を出して", "方言を変換", "transpile SQL", "convert SQL to ScalarDB",
  "migrate Oracle SQL to PostgreSQL".
  対象外: 実データベースへ接続して結果を比較する検証（difftest/run.py）、性能測定（difftest/bench.py）、
  ScalarDB の実行計画への分解（scalardb_migrate.cli --plan-dir）、SQL の整形だけの依頼。
---

# sql-transpile — SQL 方言変換

SQL を Source 方言で読んで AST に抽象化し、Target 方言または ScalarDB SQL として生成し直す。
変換できたかどうかを 1 文ずつ判定し、理由と変換率をレポートにまとめる。

## Important

- **作業ディレクトリは sql-migration リポジトリのルート**。すべてのコマンドはここから `.venv/bin/python` で実行する
- **素の `sqlglot.transpile()` は信用しない**。Oracle → PostgreSQL で `ROWNUM`・`CONNECT BY`・`NEXTVAL`・`ROWID` はそのまま出力へ通り、`(+)` 外部結合は内部結合に化けて結果が静かに変わる。このスキルは前処理でこれらを直すか、直せないものを ERROR として報告する
- **レポートは変換の証明ではない**。OK は「Target の文法として生成でき、既知の危険構文が残っていない」という意味にとどまる。結果の一致までは保証しない。重要な SQL は実データで確かめるよう利用者に伝える
- **関数の移植性チェックは誤検出を許容する**。Source 固有の関数名がそのまま残ると WARN（`FUNC_PORTABILITY`）になるが、Target が同名関数を持っていても WARN になる（例: PostgreSQL の `INITCAP`）。WARN は「要確認」であって「動かない」ではない
- 詳細な書き換え方は `references/dialect-notes.md`、ScalarDB SQL の文法は `references/scalardb-grammar.md` を Read ツールで読む。必要になるまで読まない

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
| ScalarDB | `scalardb`（Target のみ） |

使える方言の一覧は `--help` の末尾に出る。`scalardb` は Target にしか指定できない。

### Step 2: 変換を実行する

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> \
  --source <方言> --target <方言> --out-dir out/transpile
```

ScalarDB を Target にするときは、キー設計の指定と既存スキーマを渡せる。

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> \
  --source oracle --target scalardb --out-dir out/transpile \
  --keys orders=customer_id/order_no \
  --schema existing-schema.json
```

`--keys` は `テーブル=パーティションキー/クラスタリングキー`。`--schema` は ScalarDB Schema Loader 形式の JSON。どちらも ScalarDB 以外の Target では無視される。

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

1. **変換率**。`CONVERTED / TOTAL` と割合
2. **手作業が要る文**（ERROR）。レポート末尾の「手作業が必要な文」節に理由が並んでいる。書き換え方は `references/dialect-notes.md` の該当コードの節を引く
3. **要確認の文**（WARN）。特に `FUNC_PORTABILITY` と `ROWNUM`（ORDER BY を伴う場合は件数が変わりうる）
4. **出力ファイルの場所**

ERROR の文を利用者の代わりに書き換える場合は、書き換え後の SQL をもう一度このスキルに通して OK になることを確かめる。

### Step 5: 同梱コピーの鮮度を確かめる（ScalarDB を Target にしたとき）

ScalarDB 変換は、リポジトリ本体 `scalardb_migrate/` のコピーを `scripts/_scalardb/` に同梱して使っている。本体を改善してもコピーには自動で反映されない。

```bash
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --check
```

終了コード 1（`VENDOR_DRIFT=` が 0 でない）なら、本体と食い違っている。利用者に伝え、取り込むなら次を実行する。

```bash
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --update
```

## 仕組み

Target によって 2 つのバックエンドを使い分ける。どちらも同じ形の結果を返すので、レポートは共通。

```
                       ┌─ target = scalardb ──→ 同梱した ScalarDB 変換ルール一式
入力 SQL ─→ 文分割 ─┤                          （DNF/CNF 正規化・キー設計・型対応）
                       └─ target = その他 ────→ 汎用パス（4 段）
```

汎用パスの 4 段:

1. **前処理** — SQLGlot が直さない構文を AST 上で直す（Oracle `(+)` → `LEFT JOIN`、`ROWNUM <= n` → `LIMIT n`）
2. **生成** — `ErrorLevel.RAISE` で生成し、SQLGlot が未対応と知っている構文を例外にする
3. **残存検査** — 出力に残った危険構文を拾う。構文マーカーの照合、Target が知らない関数の検出、Source 固有の関数名の残存チェック
4. **往復検証** — 生成した SQL が Target 方言としてパースできることを確かめる

## Error Handling

| 状況 | 対処 |
|---|---|
| `ModuleNotFoundError: sqlglot` | リポジトリルートで実行しているか確認し、`.venv/bin/pip install -r requirements.txt` |
| 終了コード 2「ファイルが見つかりません」 | 入力パスをリポジトリルートからの相対パスで指定し直す |
| `invalid choice` | 方言名の綴りを直す。`--help` の末尾に一覧がある |
| `PARSE` の ERROR が大量に出る | `--source` が実際の方言と違う可能性が高い。方言を確認する |
| ScalarDB Target で型の WARN が多い | Source が oracle / postgres / mysql 以外だと型対応表の精度が落ちる。標準エラーの注意書きを利用者に伝える |
| `vendor_sync.py` が「本体が見つかりません」 | スキルがリポジトリの外にコピーされている。リポジトリ内のスキルを使う |

## Output

`--out-dir` を指定すると 3 ファイルを出す。`<stem>` は入力ファイル名から拡張子を除いたもの。

| ファイル | 内容 |
|---|---|
| `<stem>.<target>.sql` | 変換後 SQL。変換できない文は `-- [NOT CONVERTED #n]` の後に原文をコメントで残す |
| `<stem>.report.md` | 文ごとの表（# / 種別 / 状態 / 元の SQL / 変換後 / 指摘）、指摘コードの集計、手作業が必要な文の一覧、変換率 |
| `<stem>.report.json` | `{source, target, summary, results}`。`summary` に `total / ok / warn / error / converted / rate` |

状態の意味:

| 状態 | 意味 |
|---|---|
| OK | Target の SQL を生成でき、指摘なし |
| WARN | 生成できたが確認が要る（意味の差、関数の移植性、ROWNUM と ORDER BY の順序など） |
| ERROR | 生成できない、または危険構文が残った。手作業が要る |

変換率 = (OK + WARN) / 全文数。
