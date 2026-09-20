# エリア別・店舗別の月次売上分析: ScalarDB 構成での性能最大化の調査

`docs/examples/area-sales-analysis-scalardb-conversion.md` で分解した構成（ScalarDB SQL で組織マスタと 1 年分の売上明細を取得し、Java で集計する）について、性能を最大化する方法を調べた結果をまとめる。

**結論:** 性能はほぼ「ScalarDB から読む行数」で決まる。設定の調整では数倍しか縮まらないため、**まず読む行数を減らす設計（月次集計表・組織パスのキャッシュ・締めた月の結果の保存）を入れ、そのうえで fetch size・分離レベル・読み取り専用トランザクション・並列取得を調整する**。

根拠の種類を次のように区別する。

- **実測**: このリポジトリで測った値（1 台の PC 上のコンテナ、単一クライアント、最大 4 万行）
- **公式**: ScalarDB の公式ドキュメントまたは OSS のソースコードで確認した事実（URL を付ける）
- **推定**: 上の 2 つからの計算や推論。測っていない

---

## 1. どこに時間がかかるか

| 処理 | コスト | 根拠 |
|---|---|---|
| ScalarDB のスキャン（全件・範囲） | 1 行あたり約 25 µs。4 万行の全件集計で約 1 秒（Oracle 直接の 300〜400 倍） | 実測 `docs/reports/bench-report.md`, `docs/reports/scalardb-backend-comparison.md` |
| ScalarDB のキー指定（GET・パーティション内の範囲） | 3〜17 ms。表の行数によらず一定 | 実測 同上 |
| アプリ側の集計（`AreaSalesReport.build`） | 10 万行 24 ms、100 万行 150 ms、500 万行 780 ms（1,000 店舗 × 12 か月） | 実測（今回、DB なしで計測） |

アプリ側の計算は取得に比べて小さい。1 年分の明細が 100 万行なら、取得は 1 行 25 µs × 100 万行 ≒ **25 秒**（推定、4 万行での実測からの線形外挿）、集計は 0.15 秒になる。

加えて、ScalarDB の Consensus Commit には明細の読み取りを重くする仕組みがある（公式）。

1. **射影に関係なく、全列（トランザクション用のメタデータと更新前の値の列を含む）を読む。** LIMIT もストレージには渡さない（[ConsensusCommitUtils.prepareScanForStorage, v3.17.0](https://github.com/scalar-labs/scalardb/blob/v3.17.0/core/src/main/java/com/scalar/db/transaction/consensuscommit/ConsensusCommitUtils.java)）
2. **分離レベルが `SERIALIZABLE` だと、コミット時に読み取ったスキャンをすべてもう一度実行して結果を比べる**（[Consensus Commit](https://scalardb.scalar-labs.com/docs/latest/consensus-commit/)、[Snapshot.java](https://github.com/scalar-labs/scalardb/blob/main/core/src/main/java/com/scalar/db/transaction/consensuscommit/Snapshot.java)）。1 年分の明細を 2 回読むことになる。このリポジトリの検証環境はすべて `SERIALIZABLE` で動かしている
3. **スキャンの取得単位の既定値が 10 行**で、Cluster ノード側とクライアント側の 2 か所にある（[Configurations](https://scalardb.scalar-labs.com/docs/latest/configurations/)、[Cluster Configurations](https://scalardb.scalar-labs.com/docs/latest/scalardb-cluster/scalardb-cluster-configurations/)）

---

## 2. 施策の一覧（効果の大きい順）

| # | 施策 | 期待できる効果 | 根拠 | 代償 |
|---|---|---|---|---|
| A1 | 月次集計表を持ち、明細を読まない | 読む行数が「明細の件数」から「店舗数 × 12」に減る（100 万行 → 1.2 万行なら約 80 分の 1） | 推定（行数に比例するという実測から） | 売上の書き込みごとに集計行も更新する。同じ店舗・月に同時に書くと競合する |
| A2 | 組織の階層（店舗 → 経路・親エリア）を事前に計算して持つ、またはアプリでキャッシュする | 組織マスタの全件スキャン（RDBMS）や、階層ごとのインデックス検索（Cassandra）が消える | 推定 | 組織変更時に作り直す |
| A3 | 締めた月の分析結果を保存し、当月分だけ計算する | 2 回目以降はほぼ GET だけになる | 推定 | 過去の売上が訂正されたときに作り直す |
| A4 | 集計を ScalarDB Analytics（Spark）に任せる | 元の SQL に近い形（ウィンドウ関数など）で、トランザクション処理と切り離して実行できる | 公式（[ScalarDB Analytics](https://scalardb.scalar-labs.com/docs/latest/scalardb-analytics/)）。ウィンドウ関数・CTE の対応と読み取りの一貫性は ScalarDB の文書では未確認 | 別の製品・基盤が要る。オンラインでの即時性は下がる |
| B1 | `scan_fetch_size` を 10 → 1000 程度に上げる（ノードとクライアントの両方） | スキャンが Cassandra で 4〜19 倍、PostgreSQL で 2〜3 倍速くなった | 実測 `docs/reports/cassandra-verification-report.md`（PostgreSQL は 1 回だけの計測） | メモリ使用量が増える |
| B2 | このレポートは `SERIALIZABLE` 以外（`SNAPSHOT` / `READ_COMMITTED`）で動かす | コミット時の再スキャンが無くなる（最大で約半分になる見込み） | 公式（仕組み）。効果の大きさは推定 | 分離レベルはノードの設定なので、同じノードの更新処理にも効く。更新処理の要件と合わせて決める |
| B3 | 読み取り専用トランザクションで開始する | Coordinator へのコミット状態の書き込みが省かれる。ただし `SERIALIZABLE` の再スキャンは省かれない | 公式（3.16.0 以降、[API guide](https://scalardb.scalar-labs.com/docs/latest/api-guide/)、[SQL API guide](https://scalardb.scalar-labs.com/docs/latest/scalardb-sql/sql-api-guide/)） | JDBC で指定する方法（`setReadOnly` など）は未確認 |
| C1 | 店舗ごとの取得を複数スレッドで並列に行う | スレッド数に応じて短くなる見込み | 推定（並列時の性能は未計測） | トランザクションはスレッドをまたいで使えない（公式）ので、スレッドごとに別のトランザクションになり、全体が 1 つの時点の読み取りではなくなる。接続プールと DB の接続上限の設計が要る |
| C2 | Cassandra ではパーティションキーを `(shop_id, sales_month)` にする | パーティションの大きさに上限ができ、月単位で並列に読める | 推定。Cassandra 公式は大きすぎるパーティションを分割するよう推奨（[Data modeling](https://cassandra.apache.org/doc/latest/cassandra/developing/data-modeling/data-modeling_refining.html)） | 期間指定の取得が月の数だけの SQL になる |
| D1 | 明細を読み込みながら月次合計に足し込み、明細のリストを持たない | 速度はほぼ変わらない。メモリが「明細の件数」から「店舗×月」に減る | 推定 | コードが少し複雑になる |
| D2 | ScalarDB SQL の JDBC を使い続ける（Core API に替えない） | 全件取得で Core API より 26〜38% 速かった | 実測 `docs/reports/bench-report.md` | なし |
| D3 | Kubernetes 上では `direct-kubernetes` モードで接続する | ロードバランサーを経由する分の往復が減る | 公式（[Java API ガイド](https://scalardb.scalar-labs.com/docs/latest/scalardb-cluster/developer-guide-for-scalardb-cluster-with-java-api/)）。効果の大きさは未計測 | アプリが同じ Kubernetes クラスタにいる必要がある |

---

## 3. 施策の詳細

### 3.1 A1: 月次集計表

明細の代わりに、店舗×月の合計を持つ表を読む。

```sql
CREATE TABLE shop_monthly_sales (
  shop_id BIGINT,
  sales_month TEXT,          -- 'YYYY-MM'
  total_amount BIGINT,       -- NULL 以外の amount の合計
  amount_count BIGINT,       -- NULL 以外の amount の件数（0 なら元の SQL の SUM は NULL）
  order_count BIGINT,
  PRIMARY KEY (shop_id, sales_month)
);

SELECT sales_month, total_amount, amount_count
FROM shop_monthly_sales
WHERE shop_id = ? AND sales_month >= '2026-01' AND sales_month <= '2026-12';
```

- **元の SQL と結果を一致させる:** `SUM` は NULL だけの月に NULL を返すため、NULL 以外の件数も持つ（`amount_count = 0` なら合計を NULL として扱う）。`AreaSalesReport` の `LAG`・移動平均・`DENSE_RANK` の計算は、そのまま月次合計を入力にして使える
- **更新の方法:** 売上の INSERT と同じトランザクションで集計行を読み、足してから書き込む（ScalarDB SQL は `SET col = col + ?` が使えないため、読み取り → 計算 → 書き込み）
- **競合への対策:** 同じ店舗・月に同時に売上が入ると、Consensus Commit では集計行の書き込みが競合して片方がやり直しになる。売上の書き込み頻度が高い場合は次のどちらかにする
  - 集計行を `(shop_id, sales_month, bucket)` に分け、書き込み側はバケットを分散させ、読み取り側で足す
  - 集計表は売上と同じトランザクションで更新せず、バッチ（例: 15 分ごと、日次締め）で作る。当月の最新分だけ明細から足す

### 3.2 A2: 組織の階層を持つ

組織マスタは変更が少ないので、`CONNECT BY` に当たる計算を毎回しない。

```sql
CREATE TABLE shop_hierarchy (
  shop_id BIGINT PRIMARY KEY,
  parent_id BIGINT,
  shop_name TEXT,
  area_path TEXT
);
```

- 組織の変更時に、`AreaSalesReport.shopsAtLevel3` と同じ計算で作り直す
- 全店舗の一覧を読むのは RDBMS ならクロスパーティションスキャン 1 回（店舗数の行だけ）。Cassandra では全件スキャンを避け、アプリのキャッシュ（組織変更時に更新）から店舗一覧を得る
- Cassandra 版で使っていた `parent_id` のセカンダリインデックスが要らなくなる。インデックスは `SERIALIZABLE` では更新前の値用のインデックスも必要になる（3.16.5 以降、[3.16 release notes](https://scalardb.scalar-labs.com/docs/3.16/releases/release-notes)）ので、無くせるなら運用も軽くなる

### 3.3 B1: fetch size

| プロパティ | 置き場所 | 既定値 |
|---|---|---|
| `scalar.db.scan_fetch_size` | ScalarDB Cluster ノード | 10 |
| `scalar.db.cluster.client.scan_fetch_size` | クライアント（アプリ） | 10 |

ノード側の値は JDBC の `setFetchSize` と Cassandra ドライバーの fetch size に渡される（公式、[JdbcCrudService.java](https://github.com/scalar-labs/scalardb/blob/main/core/src/main/java/com/scalar/db/storage/jdbc/JdbcCrudService.java)）。このリポジトリの PostgreSQL・Oracle 用ノードの設定は既定値のまま（`difftest/conf/scalardb-cluster-node*.properties`）、Cassandra 用だけ 1000 にしている。ScalarDB SQL の JDBC で `Statement.setFetchSize()` が効くかは未確認。

### 3.4 B2・B3: 分離レベルと読み取り専用トランザクション

- **分離レベル:** `scalar.db.consensus_commit.isolation_level`。値は `SNAPSHOT`（既定）、`SERIALIZABLE`、`READ_COMMITTED`（[Configurations](https://scalardb.scalar-labs.com/docs/latest/configurations/)）
  - `SERIALIZABLE` はコミット時に読み取ったスキャンを再実行する。1 年分の明細を読むレポートでは、取得がほぼ 2 回分になる
  - `READ_COMMITTED` は最新でないコミット済みの値を返すことがある代わりに速く、スキャン結果をトランザクション内のメモリに保持しない（[Consensus Commit](https://scalardb.scalar-labs.com/docs/latest/consensus-commit/)、[CrudHandler.java](https://github.com/scalar-labs/scalardb/blob/main/core/src/main/java/com/scalar/db/transaction/consensuscommit/CrudHandler.java)）
  - 分離レベルはノード全体の設定。更新処理で `SERIALIZABLE` が必要なら、分析用に別の設定の ScalarDB Cluster を用意するか、A1〜A3 で読む行数自体を減らす
- **読み取り専用トランザクション:** Core API の `beginReadOnly()`、SQL API の `SqlSession.beginReadOnly()`（3.16.0 以降）。書き込みの無いトランザクションは Coordinator への書き込みが省かれる（`scalar.db.consensus_commit.coordinator.write_omission_on_read_only.enabled`、既定 `true`）。`SERIALIZABLE` の再スキャンは省かれない

### 3.5 C1: 並列取得

- ScalarDB のトランザクションと `SqlSession` はスレッドセーフではない（公式、[SQL API guide](https://scalardb.scalar-labs.com/docs/latest/scalardb-sql/sql-api-guide/)）。並列にするなら、スレッドごとに JDBC 接続とトランザクションを分ける
- 並列にすると、店舗ごとに読み取り時点が変わる。締めた月の分析なら問題になりにくいが、当月分を含むなら許容できるか確認する
- 上限を決める要素: Cluster ノードの `scalar.db.consensus_commit.parallel_executor_count`（既定 128、ノード内の全トランザクションで共有）、ノードから DB への接続プール、DB の接続上限。Oracle Free では既定のプール（最大 200）で `ORA-12516` が出たため、検証環境では最大 50 に絞っている（`docs/reports/oracle-backend-verification-plan.md`）

### 3.6 大量に読む場合の制限

- **gRPC の期限:** `scalar.db.cluster.grpc.deadline_duration_millis` の既定は 60 秒。100 万行を `SERIALIZABLE` で読むと、推定では取得と再スキャンで数十秒になり、この期限に近づく
- **行数の上限:** このリポジトリの取得処理には行数の上限（`RowLimitExceededException`）がある。明細をまとめて読む設計のままでは上限に当たりやすい

---

## 4. 推奨する進め方

1. **読む行数を減らす（A1〜A3）。** 最も効果が大きく、バックエンドが RDBMS でも Cassandra でも効く。月次集計表の更新方法は、売上の書き込み頻度で決める（同じトランザクションか、バッチか）
2. **設定を調整する（B1〜B3）。** `scan_fetch_size` をノードとクライアントの両方で上げる。レポートを読み取り専用トランザクションで動かす。分離レベルは更新処理の要件と合わせて決める
3. **それでも足りなければ並列化する（C1・C2）。**
4. **分析の種類が増える、または期間が長くなる場合は ScalarDB Analytics を検討する（A4）。**

---

## 5. 未確認事項と、測るべきこと

未確認:

- ScalarDB SQL の集約（`SUM` / `GROUP BY`）がストレージに押し下げられるか（SQL 層はソース非公開で、文書にも記載が無い。Core の API に集約が無いため、SQL 層のメモリで計算していると推測される）
- ScalarDB SQL の JDBC で読み取り専用トランザクションや fetch size を指定する方法
- ScalarDB Analytics でのウィンドウ関数・CTE の対応と、ScalarDB 管理下の表を読むときの一貫性
- 並列実行時・10 万行以上での性能（このリポジトリの計測は単一クライアント・最大 4 万行）

計測の案（既存の `difftest/bench.py` と `runtime-java` の Bench を拡張する）:

| ケース | 内容 |
|---|---|
| S0 | 現状（`SERIALIZABLE`、fetch size 10、明細を一括取得） |
| S1 | S0 + fetch size 1000（ノードとクライアント） |
| S2 | S1 + `SNAPSHOT` |
| S3 | S2 + 読み取り専用トランザクション |
| S4 | S3 + 店舗ごとの並列取得（スレッド数 1 / 4 / 16） |
| S5 | 月次集計表を読む |

明細 10 万行・100 万行、店舗 1,000、バックエンドは PostgreSQL・Oracle・Cassandra で測る。計測には Docker と ScalarDB Cluster のライセンス（試用版）が要る。
