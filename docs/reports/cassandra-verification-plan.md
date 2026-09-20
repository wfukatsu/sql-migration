# Oracle → ScalarDB + Cassandra 変換検証 計画書

作成日: 2026-09-11
状態: **承認済み** (2026-09-11)
関連文書: `docs/reports/bench-report.md` (Oracle と ScalarDB + PostgreSQL の互換性・性能比較)、`docs/reports/oracle-sql-report.md` (Oracle 固有 SQL の網羅調査)、`docs/reports/test-report.md` (仕組みとテスト)

## 1. 目的

これまでの検証は ScalarDB のバックエンドを PostgreSQL にして行った。本検証ではバックエンドを **Apache Cassandra** に替え、同じ Oracle SQL を同じ仕組み (変換ツール → ScalarDB SQL、または実行計画 → ScalarDB から fetch → H2 で残余処理) で実行する。

主眼は **RDBMS 向けに書かれたクエリのうち、どの形が NoSQL バックエンドに向き、どの形が向かないか** を実測で分類することにある。成果物は、クエリの形ごとに「向く / 条件付き / 向かない / 動かない」を示す適性表と、その根拠となる互換性・応答時間の数値である。

## 2. 検証で答える問い

| # | 問い | 事前の見立て |
|---|---|---|
| Q1 | 変換ツールが OK / WARN と判定した ScalarDB SQL は、Cassandra バックエンドでも Oracle と同じ結果を返すか。PostgreSQL では通るのに Cassandra では失敗する文はどれか | 複数パーティションにまたがる `ORDER BY` は失敗する。ScalarDB のクロスパーティション走査の順序付けは JDBC バックエンドのみ対応 (公式ドキュメント「Cross-partition scan configurations」) |
| Q2 | アプリ側処理 (plan 経路) は Cassandra でも同じ結果を返すか | fetch が単純な走査なので通る。ただし fetch 側の述語が Cassandra で使えるかは要確認 |
| Q3 | 前回見えた 3 層 (キー指定の点アクセス / 索引で絞る範囲アクセス / 全表走査) は Cassandra でも同じ形になるか。PostgreSQL バックエンドとの差はどの層で大きいか | 点アクセスの差は小さく、全表走査の差が大きい |
| Q4 | NoSQL 向けのキー設計 (パーティションキー + クラスタリングキー) にすると、RDB 型の設計 (単一主キー + セカンダリインデックス) と比べて何が速くなり、何ができなくなるか | 「パーティション内の範囲 + クラスタリング順の ORDER BY + LIMIT」が最も効く。同じ問いを RDB 型の表で書くと Cassandra では実行できない |
| Q5 | 書き込み (主キー指定の 1 行 INSERT / UPDATE / UPSERT / DELETE、インデックス列の更新) は Cassandra でどれだけかかるか | Consensus Commit の条件付き書き込みは Cassandra では LWT (Paxos) になるため、PostgreSQL より遅い |
| Q6 | Cassandra 固有の注意点が結果に現れるか | 非 JDBC バックエンドのクロスパーティション走査は SERIALIZABLE を指定しても直列化可能性を保証しない (公式ドキュメント「Consensus Commit Protocol」)。結果の値には現れないが、適性表に「整合性の注意」として記す |

## 3. 検証環境

| 系統 | 構成 | 状態 |
|---|---|---|
| 基準 | Oracle Database 23ai Free (`gvenzl/oracle-free:23-slim-faststart`) | 既存 |
| 比較 A | ScalarDB Cluster 3.19.1 (standalone、トライアルライセンス) + PostgreSQL 16 | 既存 |
| 比較 B | ScalarDB Cluster 3.19.1 (standalone、トライアルライセンス) + **Apache Cassandra 5.0 (単一ノード、レプリケーション係数 1)** | 新規 |

Cassandra 5.0 は ScalarDB 3.19 が対応するバージョン (3.0 / 3.11 / 4.1 / 5.0) の最新である。

**メモリ制約**: Docker の VM は 8 GiB で、既存コンテナが約 4.4 GiB 使っている (Oracle 2.4 GiB、ScalarDB Cluster 1.3 GiB、`seal-demo-ledger` 0.6 GiB ほか)。そこで次のようにする。

- Cassandra のヒープを 1 GiB に制限する (`MAX_HEAP_SIZE=1G`)。
- ScalarDB Cluster は 2 つを同時に起動せず、バックエンドごとに入れ替えて測る (PostgreSQL 用ノードを止めてから Cassandra 用ノードを起動する)。取り違えを防ぐため、Cassandra 用ノードは別ポート (60054) で公開する。
- 本検証と無関係のコンテナ (`seal-demo-ledger` など) には触れない。それでもメモリが足りなければ、作業を止めて相談する。

既存の制約はそのまま守る。**アプリケーションもハーネスも ScalarDB のバックエンド (Cassandra) には直接接続しない** (`cqlsh` によるデータ確認も行わない)。ハーネスが直接接続するのは移行元の Oracle だけで、期待値の算出に使う。

### 3.1 追加・変更するもの

| 対象 | 内容 |
|---|---|
| `difftest/docker-compose.yml` | `backend-cassandra` (profile `cassandra`) と `scalardb-cluster-cassandra` (profile `cassandra`、ポート 60054) を追加 |
| `difftest/conf/` | Cassandra 用の ScalarDB Core 設定 (ホストから)、Schema Loader 用設定 (コンテナ内から)、Cluster ノードの基本設定、ScalarDB SQL JDBC クライアント設定 (60054 向け) |
| `difftest/make-cluster-conf.sh` | Cassandra 用ノードの設定 (基本設定 + ライセンス行) も生成する |
| `difftest/run.py`、`difftest/bench.py` | `--backend postgres|cassandra` を追加し、使う設定ファイルを切り替える。Schema Loader は Cassandra 向けに `--replication-factor 1` を付ける |
| `difftest/bench.py` | データ生成を `emp/dept/bonus` 固定からケースファイルごとに選べるようにする (4.2 の新ケース用) |
| `difftest/cases/nosql-patterns.sql` | 新規。NoSQL 適性を測るケース (4.2) |
| 変換ツール (`scalardb_migrate/`) | **最初は変更しない。** Q1 で Cassandra 固有の失敗が出たときに限りフェーズ 4 で改修する |

## 4. テストケース

### 4.1 既存ケースを Cassandra で再実行する (互換性)

| ケース | 文数 | PostgreSQL バックエンドでの前回結果 | 見るもの |
|---|---|---|---|
| `difftest/cases/oracle.sql` | 17 | 17 / 17 PASS | Cassandra でも一致するか |
| `difftest/cases/oracle-features.sql` (読み取り系) | 62 | 51 PASS / 9 FAIL / 2 変換不可 | PostgreSQL と判定が変わる文があるか。変わった文は原因を特定する |
| `difftest/cases/bench.sql` | 15 | 15 / 15 PASS | 互換性と性能 (4.3) |

判定は前回と同じく Oracle の結果集合との比較である (`ORDER BY` の無い文は順序を無視する)。加えて、PostgreSQL バックエンドの結果と文ごとに並べ、**Cassandra でだけ変わった文** を抜き出す。

### 4.2 新規: NoSQL 適性ケース (`difftest/cases/nosql-patterns.sql`)

同じ業務データ (顧客と注文) を 2 通りのキー設計で持ち、同じ問いを両方に投げる。

| 表 | 設計 | キー |
|---|---|---|
| `customers` | 共通 | 主キー `customer_id`、`region` にセカンダリインデックス |
| `orders_rdb` | RDB 型 | 主キー `order_id`、`customer_id` と `status` にセカンダリインデックス |
| `orders_by_customer` | NoSQL 型 | パーティションキー `customer_id`、クラスタリングキー `(order_date, order_id)`、`status` にセカンダリインデックス |

データは 1 顧客あたり 40 注文を基本とし、注文 5,000 / 20,000 / 40,000 行の 3 段階で測る (前回のベンチと同じ規模。表サイズに対する伸び方を見るため)。`status` は 5 種類、金額 `amount` は無索引。

| # | 分類 | SQL の形 (Oracle で記述) | 対象表 | 見立て |
|---|---|---|---|---|
| N1 | 点読み | 主キー指定の 1 行 SELECT | 両方 | 向く |
| N2 | パーティション内の範囲 | 顧客 1 人の注文を `order_date` の範囲で絞り、`ORDER BY order_date DESC` で先頭 10 行 | NoSQL 型 | 向く (NoSQL が最も得意な形) |
| N3 | N2 と同じ問い | 同上 | RDB 型 | インデックス走査 + 並べ替え。Cassandra では並べ替えができず失敗、または plan 経路 |
| N4 | パーティション内の集約 | 顧客 1 人の件数・合計 | 両方 | NoSQL 型は向く、RDB 型は条件付き |
| N5 | セカンダリインデックス等値 | `status = 'X'` (表の 1/5)、`region = 'R'` | 両方 | 条件付き (取得行数に比例) |
| N6 | 無索引列のフィルタ | `amount > 9900` | 両方 | 向かない (全パーティション走査) |
| N7 | 複数パーティションにまたがる並べ替え | 全表の `ORDER BY amount DESC` で上位 10 行 | 両方 | Cassandra では動かない見込み → plan 経路なら全表走査 |
| N8 | 全表集約 | `GROUP BY status`、`COUNT(*)`、`DISTINCT` | 両方 | 向かない |
| N9 | キー指定で駆動する JOIN | 注文 1 件 (または顧客 1 人の注文) → `customers` を主キーで結合 | 両方 | 条件付き (駆動側が絞れていれば向く) |
| N10 | キーを使わない JOIN | `region` で絞った顧客と注文の結合 (3 表を含む) | 両方 | 向かない |
| N11 | ページング | `OFFSET n ROWS FETCH NEXT 10 ROWS ONLY` と、パーティション内のキーセット方式 (`order_date < :last` + 先頭 10 行) | 両方 | キーセット方式は向く、OFFSET は向かない |
| N12 | 複数キーの IN | `customer_id IN (…10 個…)` | 両方 | 要確認 (OR に展開されたあとパーティション走査 10 回になるか、全パーティション走査になるか) |
| N13 | 1 行書き込み | 主キー指定の INSERT / UPDATE / DELETE、定数ソースの MERGE (→ UPSERT) | 両方 | 向く (ただし LWT 分の遅さを測る) |
| N14 | インデックス列の更新 | 主キー指定で `status` を更新 | 両方 | 条件付き (ScalarDB は索引列ごとに更新前の値の索引も保つため書き込みが増える) |
| N15 | 非キー条件の一括更新 | `UPDATE … WHERE status = 'X'` | 両方 | 向かない |

`SET amount = amount + 1` のような「読んで書く」更新は、変換ツールがまだ書き込みテンプレートを持たないため測定対象外とする (8 章)。

### 4.3 性能の測り方

前回と同じく、1 つの JVM (`residual-runner bench`) から Oracle と ScalarDB を交互に計測する。接続は計測前に確立して再利用し、ウォームアップ 3 回のあと 15 回の p50 と p95 を取る。主キー指定の文は反復ごとに違うキー値を与える (`@iterate`)。各反復で結果集合を Oracle と突き合わせる。

1 回の計測で比べられる ScalarDB 側は 1 系統なので、**PostgreSQL バックエンドと Cassandra バックエンドを別々に測り、同じデータ・同じ文で並べる。** 前回の PostgreSQL の数値は流用せず、同じ日・同じホストで取り直す。Oracle は両方の回で測り、回ごとのぶれを確認する。

## 5. 適性の判定基準

測定値から、各パターンを次のように分類する。閾値は前回のベンチ結果 (点アクセス 3〜6 ms、1,000 行の範囲 20〜36 ms、40,000 行の全表走査 約 1 秒) を基準にした。

| 判定 | 条件 |
|---|---|
| 向く | Cassandra で結果が一致し、**応答時間が表サイズに依らない** (5,000 行 → 40,000 行で ±30% 以内)、かつ p50 が 10 ms 以下 |
| 条件付き | 結果は一致するが、応答時間が取得行数に比例する。取得行数をキー・インデックス・`LIMIT` で抑えられる場合に限り使える |
| 向かない | 結果は一致するが、走査行数が表サイズに比例する (全パーティション走査)。オンライン処理では避ける |
| 動かない | Cassandra でエラーになる、または結果が一致しない (PostgreSQL では通る場合は Cassandra 固有として別記) |

これとは別に、クロスパーティション走査を使う文には「Cassandra では直列化可能性が保証されない」という注意を付ける。

## 6. 進め方

| フェーズ | 内容 | 完了の条件 |
|---|---|---|
| 0. 環境 | Cassandra と Cassandra 用 Cluster ノードを追加し、Schema Loader で Coordinator 表を作る。1 行の書き込みと読み取りで疎通を確かめる | ScalarDB SQL で 1 行 INSERT → SELECT が通る |
| 1. 既存ケースの互換性 | `oracle.sql` と `oracle-features.sql` を `--backend cassandra` で実行し、PostgreSQL バックエンドの結果と文ごとに比べる | 差が出た文すべてに原因が付く |
| 2. 既存ベンチ | `bench.sql` を 5,000 / 20,000 / 40,000 行で、PostgreSQL と Cassandra の両方で測る | 3 系統の表と伸び方の表ができる |
| 3. NoSQL 適性ケース | 4.2 のケースとデータ生成を作り、3 系統で測る | 15 パターンすべてに判定が付く |
| 4. 変換ツールの改修 (条件付き) | フェーズ 1〜3 で Cassandra 固有の失敗が出た場合、変換ツールにバックエンド指定 (`--backend cassandra`) を加え、Cassandra で実行できない形 (複数パーティションの `ORDER BY` など) を plan 経路 (H2 で並べ替え) に回す。単体テストを足し、該当ケースを測り直す | 改修前後の結果を両方記録する |
| 5. 報告 | `docs/reports/cassandra-verification-report.md` を作る。README に再現手順を追記する | 報告書に適性表・数値・再現手順が揃う |

## 7. 成果物

| ファイル | 内容 |
|---|---|
| `docs/reports/cassandra-verification-plan.md` | 本書 |
| `docs/reports/cassandra-verification-report.md` | 検証結果。適性表、互換性の差分、応答時間 (3 系統)、表サイズに対する伸び、移行時の判断基準 |
| `difftest/cases/nosql-patterns.sql` とデータ生成 | NoSQL 適性ケース |
| `difftest/docker-compose.yml`、`difftest/conf/*`、`difftest/run.py`、`difftest/bench.py` | Cassandra バックエンドへの対応 |
| (フェーズ 4 を行う場合) `scalardb_migrate/`、`tests/` | バックエンドを考慮した判定と単体テスト |

コミットはしない。作業ツリーに既にある未コミットの変更 (sql-transpile 関連) には触れない。

## 8. 範囲外と、結果の読み方の注意

- **水平スケールは評価しない。** Cassandra は単一ノード (レプリケーション係数 1) で動かす。ノードを増やしたときのスループット、レプリケーション、整合性レベル (QUORUM など) の影響は測れない。NoSQL を選ぶ主な理由であるスケールアウトの利点はこの検証には現れないので、応答時間の比較は「1 リクエストあたりのコスト」と「機能上の制約」の比較として読む必要がある。
- 同時実行・スループット・競合時の再試行は測らない (前回と同じ)。
- 読んで書く更新 (`SET c = c + 1`)、採番、表ソースの MERGE などの書き込みテンプレートは未実装のため測らない。
- すべて同一ホストのコンテナで動かすため、ネットワーク遅延はほぼゼロである。
- ScalarDB Cluster はトライアルライセンス (評価目的限定) で動かす。Cassandra 用ノードも同じ評価の範囲で使う。

## 9. リスク

| リスク | 対処 |
|---|---|
| メモリ不足 (Docker VM 8 GiB) | Cassandra のヒープを 1 GiB に制限し、Cluster ノードは同時に 1 つだけ起動する。それでも足りなければ相談する |
| Cassandra への投入が遅い (1 行ごとに LWT) | 投入のトランザクションあたりの行数を調整する。40,000 行が現実的な時間で入らなければ規模を下げ、報告書に記す |
| ScalarDB SQL の Cassandra 固有の未知の制約 | それ自体が検証結果になる。エラー内容をそのまま記録する |
| 前回の「1 回だけ全件 FAIL」事象の再発 | 同じ手順で再実行し、再現性を確かめてから結果を採る |

## 10. 決定事項 (2026-09-11 承認)

| 項目 | 決定 |
|---|---|
| Cassandra のバージョン | ScalarDB 3.19 が対応する最新の **5.0** |
| 2 系統の測り方 | Cluster ノードを同時に起動せず、バックエンドごとに入れ替えて測る |
| フェーズ 4 (変換ツールの改修) | 含める。Cassandra 固有の失敗が出た場合に行う |
| データ規模 | 5,000 / 20,000 / 40,000 行 |

## 11. 実施中の計画からの変更 (2026-09-11)

| 変更 | 理由 |
|---|---|
| Cassandra 用ノードでは `scalar.db.cross_partition_scan.ordering.enabled=false` | `true` だと ScalarDB Cluster ノードが起動しない (DB-CORE-10128)。PostgreSQL 用ノードと同じ設定にはできない |
| Cassandra 用の設定に `scalar.db.scan_fetch_size=1000` を追加 | 既定値 10 では走査が 10 行ごとの往復になり、5,000 行の走査で 10 倍以上遅い。調整しないまま比べると「Cassandra が遅い」ではなく「設定が不適切」を測ることになる (報告書 3.2) |
| Paxos v2 を試し、採用しなかった | 点アクセスが約 25 ms かかる原因の候補として試したが、改善しなかった (報告書 3.3) |
| 適性の判定基準の「p50 が 10 ms 以下」を外した | 前回の PostgreSQL バックエンドの点アクセス (3〜6 ms) を前提にした閾値で、Cassandra では点アクセスでも約 25 ms かかるため、全パターンが「向く」に届かなくなる。表サイズに対する伸びだけで判定し、絶対値は表に併記する |
| 改修前の Cassandra 計測はコードのスナップショットから実行した | フェーズ 4 の改修を並行して進めても、改修前の計測に混ざらないようにするため |
| **Cassandra ではクロスパーティション走査を使わない** (ユーザー指示、2026-09-11) | クロスパーティション走査は RDBMS (JDBC) バックエンドでのみ使う。Cassandra 用ノードは `scalar.db.cross_partition_scan.enabled=false` とし、ScalarDB にはキー指定のアクセス (主キー GET / パーティション走査 / インデックス走査) だけをさせる。絞り込み・並べ替え・集約などは取得後にアプリ側 (H2) で行う。キーもインデックスも使えない文 (全表の集約、無索引列だけの条件、全表の上位 N 件、非キー条件の一括更新) は取得自体ができないため「動かない (設計変更が必要)」と判定し、クロスパーティション走査を許した場合の実測値 (改修前の計測) を参考として併記する |
| フェーズ 4 の再計測を上記の設定で行う | 改修前 (クロスパーティション走査あり、変換は PostgreSQL と同じ) と、改修後 (クロスパーティション走査なし、キー指定の取得 + アプリ側処理) を比べる。クロスパーティション走査を許したままの改修後計測は行わない |

