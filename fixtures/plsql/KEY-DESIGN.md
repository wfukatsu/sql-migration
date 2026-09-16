# corpus 表の ScalarDB スキーマ設計（P0-3）

`schema.sql`（Oracle DDL）から `scalardb-schema.json`（Schema Loader JSON）を起こした根拠。
実装計画の P0-3「corpus 表の ScalarDB スキーマ設計」の成果物。

出発点は既存の変換ツールが自動で出したもので、そこから下記 3 点を手で変えた。

```bash
.venv/bin/python -m scalardb_migrate.cli fixtures/plsql/src/schema.sql --dialect oracle --out-dir out
# out/schema.schema.json が自動生成分
```

## 自動生成から変えた 3 点

### 1. 金額列を DOUBLE からスケール済み整数 + BIGINT へ

変換ツールの警告そのものに従った。

> `NUMBER(14, 2)`: ScalarDB has no DECIMAL type; mapped to DOUBLE (precision loss).
> For money, store a scaled integer (x10^2) in BIGINT instead

対象は `customers.credit_limit` / `products.unit_price` / `orders.total_amount` / `payments.amount`。
いずれも**最小単位（×100）の整数**として格納する。`pkg_money_calc` が `ROUND(..., 2)` を half-up で行うため、
DOUBLE のままだと丸めの差が意味的同等性テスト（Phase 3）で落ちる。

生成コード側では `BigDecimal`（scale 2）と BIGINT の間で変換する。この対応は P2-5 の型生成で実装する。

### 2. 時刻成分を持つ DATE 列を TIMESTAMP へ

変換ツールの警告に従った。

> Oracle DATE carries a time-of-day component; use TIMESTAMP if the time part is used

corpus ではすべて `SYSDATE` で書き込んでいるため時刻成分を持つ。対象は `orders.ordered_at` /
`inventory_tx.created_at` / `batch_control.last_run_at` / `customers.registered_on`。
`pkg_money_calc.days_since_order` が `TRUNC(SYSDATE) - TRUNC(v_ordered_at)` をしているので、
時刻成分を落とすとこの関数の結果が変わる。

### 3. 複合索引を単一列索引へ

`CREATE INDEX ix_orders_status ON orders (status, ordered_at)` は通らない。

> ScalarDB secondary indexes are single-column; got ['status', 'ordered_at'].

`status` だけに索引を張り、`ordered_at` の絞り込みは取得後の残余フィルタに回す。
`prc_nightly_close` の `WHERE status = 'SHIPPED' AND ordered_at < :d` がこの形になる。

## 表ごとのキー設計

| 表 | partition key | clustering key | secondary index | 根拠 |
|---|---|---|---|---|
| `customers` | `customer_id` | — | `tier` | 主キーアクセスが中心。`tier` は holdout の `prc_reprice_all` が絞り込みに使うため索引化しないと全件走査になる |
| `products` | `product_id` | — | — | 在庫操作はすべて主キーアクセス |
| `orders` | `order_id` | — | `customer_id`, `status` | corpus の非キー絞り込みはこの 2 列だけ |
| `order_lines` | `order_id` | `line_no ASC` | — | 明細は常に `WHERE order_id = ?` で読む。親キーを partition key に置くとパーティション SCAN 1 回で足りる |
| `payments` | `payment_id` | — | `order_id` | `paid_total` / `last_paid_at` が `order_id` で集約する |
| `inventory_tx` | `tx_id` | — | `product_id` | 追記専用。商品別の参照に備える |
| `audit_log` | `audit_id` | — | — | 追記専用。corpus に読み出しがない |
| `counters` | `counter_name` | — | — | 主キーアクセスのみ |
| `batch_control` | `batch_name` | — | — | 主キーアクセスのみ |

`order_lines` だけ clustering key を置いた。`orders` を `customer_id` の partition key にしなかったのは、
corpus の読み書きが `order_id` 起点に偏っており、顧客単位のパーティションにすると主キーアクセスがすべて
索引経由になるため。

## アクセスパスの洗い出し（変換ツールで判定）

corpus の読み取りを抜き出して、上記スキーマで判定させた結果。**判定は推測ではなくツールの出力である。**

| # | 由来 | 判定 |
|---|---|---|
| 1 | `pkg_order_status.status_of` / `pkg_customer_view.load` / `pkg_stock_reserve` ほか | **GET**（主キー完全一致） |
| 2 | `pkg_order_pricing.order_total`（明細取得）、`pkg_order_lock.cancel` | **partition SCAN**（partition key 完全一致） |
| 3 | `pkg_order_status.status_for_customer`、`pkg_dynamic_search.count_orders`、`pkg_payment.paid_total` / `last_paid_at`、`pkg_bulk_load.collect_open_orders` | **index SCAN**（索引列の等価） |
| 4 | `pkg_order_report.count_by_status` / `mark_reviewed` / `largest_order` | **cross-partition SCAN** |
| 5 | `pkg_order_pricing.customer_tier`、`prc_reprice_all` | **変換不可**（JOIN）→ 実行計画へ分解 |
| 6 | `prc_nightly_close` | **変換不可**（`SYSDATE`）→ アプリで時刻を束縛すれば index SCAN |

### cross-partition SCAN になるのは「並び替え」である

索引列の等価条件だけなら index SCAN で済むが、**非キー列の `ORDER BY` を付けた瞬間に cross-partition SCAN
に落ちる**ことをツールで確認した。

| SQL | 判定 |
|---|---|
| `SELECT total_amount FROM orders WHERE customer_id = 1` | index SCAN |
| `SELECT total_amount FROM orders WHERE customer_id = 1 ORDER BY total_amount DESC` | cross-partition SCAN |
| `SELECT order_id FROM orders WHERE status = 'NEW'` | index SCAN |
| `SELECT order_id FROM orders WHERE status = 'NEW' ORDER BY ordered_at` | cross-partition SCAN |

したがって `pkg_order_report` の 3 routine は、**並び替えをアプリ側へ寄せれば index SCAN に収まる**。
これは P2-4 の判定と P3 の性能計測で効く分かれ目なので、ここに記録しておく。

### 帰結

- JDBC バックエンド（PostgreSQL / Oracle）では `scalar.db.cross_partition_scan.enabled` と
  `filtering.enabled` / `ordering.enabled` が要る。`difftest/conf/scalardb.properties` は既にその設定。
- **Cassandra バックエンドでは #4 の 3 routine がそのままでは動かない**（ユーザールール: Cassandra では
  キーで取ってアプリで処理する）。並び替えをアプリ側へ寄せる書き換えが前提になる。
- 走査になるアクセスには行数上限が要る。既定の 10,000 行は `decomposer` の `DEFAULT_ROW_LIMIT`。

## 再現手順

```bash
# キー設計どおりのスキーマで、corpus の読み取りのアクセスパスを判定し直す
.venv/bin/python -m scalardb_migrate.cli <reads.sql> --dialect oracle \
  --schema fixtures/plsql/scalardb-schema.json --no-plan
```

`tests/test_plsql_scalardb_schema.py` が、スキーマが `SchemaRegistry` で読めること、`schema.sql` の全表・
全列を覆っていること、上表のアクセスパス判定が変わっていないことを検査する。
