# SQL → ScalarDB SQL 移行ツール PoC (SQLGlot ベース)

Oracle / PostgreSQL / MySQL の SQL を [SQLGlot](https://github.com/tobymao/sqlglot) で AST に解析し、
[ScalarDB SQL の文法](https://scalardb.scalar-labs.com/docs/latest/scalardb-sql/grammar/) に
収まるものは変換、収まらないものは理由付きで報告する調査用 PoC です。

```
.venv/bin/python -m scalardb_migrate.cli samples/oracle.sql   --dialect oracle   --out-dir out
.venv/bin/python -m scalardb_migrate.cli samples/postgres.sql --dialect postgres --out-dir out
.venv/bin/python -m scalardb_migrate.cli samples/mysql.sql    --dialect mysql    --out-dir out \
    --keys orders=customer_id/order_no          # パーティションキー / クラスタリングキーの指定
.venv/bin/python -m scalardb_migrate.cli app.sql --dialect postgres --schema existing-schema.json
.venv/bin/python -m pytest -q
```

出力 (`--out-dir`):

| ファイル | 内容 |
|---|---|
| `<name>.scalardb.sql` | 変換後 SQL。変換不能な文は `-- [NOT CONVERTED #n]` コメントとして残す |
| `<name>.report.md` / `.json` | 文ごとの status (OK / WARN / ERROR)、変換結果、指摘 (severity, code, message) |
| `<name>.schema.json` | CREATE TABLE / CREATE INDEX から生成した ScalarDB Schema Loader 形式のスキーマ |

## 構成

| ファイル | 役割 |
|---|---|
| `scalardb_migrate/dialect.py` | SQLGlot の `Dialect` サブクラス `ScalarDB`。Generator 側で ScalarDB 文法に無い構文 (サブクエリ、CASE、関数、OFFSET、CAST など) を `UnsupportedError` にする「厳格な出力側」 |
| `scalardb_migrate/types.py` | ソース型 → ScalarDB 11 型 (BOOLEAN/INT/BIGINT/FLOAT/DOUBLE/TEXT/BLOB/DATE/TIME/TIMESTAMP/TIMESTAMPTZ) の対応と精度警告 |
| `scalardb_migrate/converter.py` | 文種別ごとの解析・書き換えルール (下表) とアクセスパス分析 |
| `scalardb_migrate/schema.py` | テーブル定義レジストリ (DDL から構築、または Schema Loader JSON を読み込み) |
| `scalardb_migrate/cli.py` | CLI とレポート出力 |
| `tests/test_converter.py`, `tests/test_decomposer.py` | ルール単位のテスト (45 件) と分解のオフライン差分テスト (17 件) |

## 変換ルール概要

| ソース構文 | 扱い |
|---|---|
| `IN (a, b, c)` / `NOT IN` | `(col = a OR col = b OR col = c)` / `col <> a AND col <> b` に展開 |
| `NOT (...)` | 比較演算子を反転して押し下げ (`NOT (a = 1)` → `a <> 1`)。`IS NOT NULL` / `NOT LIKE` はそのまま |
| 任意の AND/OR ネスト | ScalarDB が要求する DNF または CNF に正規化し、括弧を付与 |
| `10 < col` | `col > 10` に反転 (右辺はリテラルのみ許可) |
| Oracle `ROWNUM <= n` / `FETCH FIRST n ROWS ONLY` | `LIMIT n` |
| 暗黙結合 `FROM a, b WHERE a.x = b.y` | `INNER JOIN b ON a.x = b.y` |
| Oracle 外部結合 `a.x = b.y(+)` | `LEFT JOIN b ON a.x = b.y` (SQLGlot の `eliminate_join_marks`) |
| `JOIN ... USING (c)` | `JOIN ... ON a.c = b.c` |
| `TO_DATE('2020-01-01','YYYY-MM-DD')` | `'2020-01-01'` (ScalarDB 形式であることを警告付きで要確認) |
| `ON CONFLICT DO UPDATE` / `ON DUPLICATE KEY UPDATE` / `REPLACE INTO` / 定数ソースの `MERGE` | `UPSERT INTO` (意味の差分を WARN) |
| `NUMBER(p)` / `DECIMAL(p,0)` | p ≤ 9 → INT、p ≤ 18 → BIGINT |
| `NUMBER(p,s)` / `DECIMAL(p,s)` | DOUBLE (精度損失を WARN。金額はスケール済み整数 BIGINT を推奨) |
| `VARCHAR(n)` / `CLOB` / `TEXT` | TEXT (長さ制約は消える) |
| `NOT NULL` / `DEFAULT` / `UNIQUE` / `FOREIGN KEY` / `CHECK` | 削除して INFO / WARN (アプリ側で担保) |
| 複合主キー | 先頭列をパーティションキー、残りをクラスタリングキー (`--keys` で上書き) |
| MySQL インライン `INDEX (col)` | 別文 `CREATE INDEX ON t (col)` に分離 |
| `BEGIN` / `START TRANSACTION` / `COMMIT` / `ROLLBACK` | そのまま |

## 変換できないもの (ERROR として報告)

- 射影の式・関数 (`NVL`, `UPPER`, `col * 2`, `CASE`) → アプリ側で計算
- サブクエリ、CTE、UNION、ウィンドウ関数、`DISTINCT`、`OFFSET`、`COUNT(DISTINCT)`
- WHERE の列同士比較 (`a.x = b.y` は JOIN ON でのみ可)、関数適用 (`DATE_FORMAT(col) = ...`)
- `UPDATE SET col = col + 1` (読み取り → 計算 → リテラルで UPDATE を 1 トランザクション内で)
- `UPDATE ... JOIN` / `DELETE ... USING` / `INSERT ... SELECT` / `RETURNING`
- `AUTO_INCREMENT` / `SERIAL` / `IDENTITY` / シーケンス (`NEXTVAL`)
- `ON CONFLICT DO NOTHING` / `INSERT IGNORE`、テーブルソースの `MERGE`
- 複合列インデックス、ビュー、トリガー、ストアドプロシージャ
- 型: ARRAY、INTERVAL、GEOMETRY など (JSON/UUID/ENUM は TEXT に WARN 付きで変換)

## スキーマ情報によるアクセスパス分析

DDL または `--schema` からテーブル定義が分かる場合、各 SELECT/UPDATE/DELETE について
GET (主キー完全指定) / パーティション SCAN / インデックス SCAN / クロスパーティション SCAN (WARN) を判定し、
JOIN の結合条件が相手テーブルの主キーまたはセカンダリインデックスを覆っているかを検査します。

## sql-transpile スキル (任意の方言への変換)

`skills/sql-transpile/` は、SQL を Source 方言から Target 方言 (SQLGlot の 32 方言) または
ScalarDB SQL に変換する Claude Code スキルです。素の `sqlglot.transpile()` が黙って通してしまう構文
(ROWNUM、Oracle 外部結合 `(+)`、CONNECT BY、NEXTVAL、ROWID、方言固有の関数) を前処理で直すか、
直せないものを理由つきで報告し、変換率を出します。

```
.venv/bin/python skills/sql-transpile/scripts/transpile.py samples/oracle.sql \
    --source oracle --target postgres --out-dir out/transpile
.venv/bin/python skills/sql-transpile/scripts/transpile.py samples/oracle.sql \
    --source oracle --target scalardb --out-dir out/transpile
```

ScalarDB 変換は `scalardb_migrate/` のコピーを `skills/sql-transpile/scripts/_scalardb/` に同梱しており、
リポジトリ本体に依存せず単体で動きます。本体を変更したら同梱コピーの鮮度を確認してください。

```
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --check    # 差分があれば終了コード 1
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --update   # 本体から取り込む
```

Claude Code から使うには `ln -s "$PWD/skills/sql-transpile" ~/.claude/skills/sql-transpile` でリンクします。
手順と指摘コードの意味は `skills/sql-transpile/SKILL.md` と `references/` を参照してください。

## 関連ドキュメント

- `docs/app-side-processing-plan.md`: ScalarDB 非対応 SQL をアプリケーション側で処理するための実装計画 (H2 / sqlite3 方式)
- `spikes/h2/ResidualH2.java`: H2 互換モードに元 SQL をそのまま実行する検証 (12/14)
- `spikes/residual_sqlite.py`: Python 標準の sqlite3 で残余処理を実行する検証 (12/14)
- `spikes/residual_duckdb.py`: 比較用の DuckDB 検証 (13/14、ネイティブ依存のため不採用)

## アプリケーション側処理 (計画書の最初の 3 作業の実装状況)

| 作業 | 場所 | 状態 |
|---|---|---|
| 1. Java ランタイム (ScalarDB から fetch → H2 で残余 SQL 実行) | `runtime-java/` | 実装済み。`gradle installDist` で `build/install/residual-runner/bin/residual-runner` を生成。`run` / `load` / `validate` / `sql` サブコマンド。fetch は ScalarDB Core (Apache 2、ライセンス不要) と ScalarDB SQL JDBC (Cluster、要ライセンス) の 2 実装 |
| 2. decomposer (ERROR 文 → 実行計画 JSON) | `scalardb_migrate/decomposer.py` | 実装済み。読み取り系の ERROR 文は `PLANNED` になり、`--plan-dir` で計画 JSON を出力。サンプルでは 21 件中 10 件 |
| 3. 差分テスト環境 | `difftest/` | 実装済み。Docker Compose (移行元 PostgreSQL / ScalarDB バックエンド PostgreSQL / Schema Loader / 任意の ScalarDB Cluster) とハーネス `difftest/run.py` |

```
# 変換 + 実行計画の出力
.venv/bin/python -m scalardb_migrate.cli samples/oracle.sql --dialect oracle --out-dir out --plan-dir out/plans
# 計画のオフライン検証 (H2 で残余 SQL をコンパイルするだけ。DB 不要)
runtime-java/build/install/residual-runner/bin/residual-runner validate --plan out/plans/oracle.8.plan.json
# 差分テスト (移行元 PostgreSQL vs ScalarDB Core → H2)
cd difftest && docker compose up -d source-postgres backend-postgres && cd ..
.venv/bin/python difftest/run.py difftest/cases/postgres.sql --dialect postgres --fetcher core
# ScalarDB SQL 経路 (要ライセンス): トライアルキー https://scalardb.scalar-labs.com/docs/latest/scalar-licensing/trial の
# 2 行 (license_key / license_check_cert_pem) を difftest/license.properties (git 管理外) に置いて Cluster を起動
cd difftest && ./make-cluster-conf.sh && docker compose --profile cluster up -d && cd ..
.venv/bin/python difftest/run.py difftest/cases/postgres.sql --dialect postgres --fetcher jdbc --skip-setup
```

```
# Oracle 直接実行との互換性・性能比較 (要 Oracle + ScalarDB Cluster)
.venv/bin/python difftest/bench.py --rows 20000 --iterations 15 --fetcher jdbc --out out/bench-jdbc
```

```
# Oracle を移行元にする場合 (Oracle Database 23ai Free。XE 21c は ARM64 イメージが無いため後継の無償版を使用)
cd difftest && docker compose --profile oracle up -d source-oracle && cd ..
.venv/bin/python difftest/run.py difftest/cases/oracle.sql --dialect oracle --fetcher jdbc
```

差分テストの結果 (2026-09-10):

| ケース | 経路 | 変換済み文 (ScalarDB SQL) | 計画 (fetch → H2) | 合計 |
|---|---|---|---|---|
| PostgreSQL 15 文 | `--fetcher core` (ライセンス不要) | 3 件 SKIP | 12 件 PASS | 12/12 |
| PostgreSQL 15 文 | `--fetcher jdbc` (トライアルライセンスの Cluster) | 3 件 PASS | 12 件 PASS | 15/15 |
| Oracle 17 文 | `--fetcher core` | 7 件 SKIP | 10 件 PASS | 10/10 |
| Oracle 17 文 | `--fetcher jdbc` | 7 件 PASS | 10 件 PASS | 17/17 |

制約どおり、ハーネスだけが移行元 DB に接続し、ScalarDB のバックエンド DB には ScalarDB 以外の何も接続しません。
- `docs/test-report.md`: 仕組みの概要、テスト内容、テスト結果のまとめ
- `docs/bench-report.md`: Oracle Database 直接実行と ScalarDB 経由の互換性・性能比較 (`difftest/bench.py`)
- 説明資料 (Google スライド 27 枚): アーキテクチャ・仕組み・検証結果をまとめたもの。生成元の仕様は slide-forge の `out/sql-migration/deck.json`
- `docs/oracle-sql-report.md`: Oracle 固有 SQL (読み取り 62 文、書き込み 17 文) の検証レポート
- `docs/diagrams/architecture.drawio`: アーキテクチャ図と処理フロー図 (draw.io 形式、2 ページ)。PNG は `architecture.png` / `flow.png`。再エクスポートは `drawio -x -f png -s 2 -p 1 -o architecture.png architecture.drawio`
