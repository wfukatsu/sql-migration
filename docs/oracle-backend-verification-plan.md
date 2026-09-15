# Oracle → ScalarDB + Oracle 検証 計画書

作成日: 2026-09-11
状態: **実施済み** (2026-09-11。ユーザーの指示により計画・実装・実測を続けて行った。結果は `docs/scalardb-backend-comparison.md`)
関連文書: `docs/cassandra-verification-plan.md`、`docs/cassandra-verification-report.md`、`docs/bench-report.md`

## 1. 目的

移行元の Oracle Database をそのまま ScalarDB のバックエンドにする構成 (データは Oracle に置いたまま、アクセスを ScalarDB 経由に切り替える) で、Oracle SQL がどこまで同じ結果を返し、どれだけ遅くなるかを測る。Oracle → ScalarDB + Cassandra の結果と並べ、2 つの構成の検証結果を 1 つの文書にまとめる (`docs/scalardb-backend-comparison.md`)。

## 2. 構成

| 系統 | 構成 |
|---|---|
| 基準 | Oracle Database 23ai Free の `SOURCE` スキーマ (移行元。ハーネスだけが接続する) |
| 比較 | ScalarDB Cluster 3.19.1 (standalone、トライアルライセンス) + **同じ Oracle Database 23ai Free** を JDBC バックエンドにする |

- ScalarDB 3.19 は Oracle Database 23ai / 21c / 19c に対応している (公式の Requirements)。
- Docker の VM は 8 GiB しかないため、Oracle のコンテナをもう 1 つは起動しない。同じインスタンス (PDB `FREEPDB1`) に ScalarDB 専用のユーザー `SCALARDB` を作る。ScalarDB の名前空間 (`difftest`、`bench`、`coordinator`) はそれぞれ Oracle のユーザー (スキーマ) になり、移行元の `SOURCE` スキーマとは表が分かれる。
- **ハーネスはこれまでどおり `SOURCE` にだけ接続し、ScalarDB 側のスキーマには接続しない。** 例外は ScalarDB 用ユーザーの作成 (1 回だけ `SYSTEM` で実行) で、これはデータベースの準備作業にあたる。付与する権限は公式の Requirements に列挙されたもの (CREATE SESSION、CREATE USER、DROP USER、ALTER USER、CREATE / DROP / ALTER ANY TABLE、CREATE / ALTER / DROP ANY INDEX、SELECT / INSERT / UPDATE / DELETE ANY TABLE、CREATE / DROP ANY VIEW) に限る。
- クロスパーティション走査は、JDBC (RDBMS) バックエンドなので PostgreSQL と同じく有効にする (絞り込み・並べ替えも有効)。`scan_fetch_size` などの他の設定も PostgreSQL 用ノードと同じにする。
- ScalarDB Cluster ノードは Oracle 用を 1 つ追加する (ポート 60055、compose の profile `oracle-backend`。移行元の `source-oracle` に依存するため、起動時は profile `oracle` も指定する)。他のノードとは同時に起動しない。

**結果の読み方の注意**: 基準と比較対象が同じ Oracle インスタンスで動く。ネットワーク遅延が無いうえ、両者はバッファキャッシュと CPU (Oracle Free は 2 スレッドまで) を共有する。計測は交互に行うので同時の競合は無いが、ScalarDB が上乗せする分 (Consensus Commit のメタデータ列、コミット時の検証読み取り、Cluster ノードの処理) が最も素直に見える条件である。

## 3. テストケースと手順

Cassandra の検証と同じものを、同じハーネスで実行する (`difftest/backend_compare.sh oracle`)。

| ケース | 規模 |
|---|---|
| `oracle.sql` (17 文)、`oracle-features.sql` (読み取り 62 文) | SCOTT 相当 |
| `bench.sql` (15 文)、`nosql-patterns.sql` (30 文) | 5,000 / 20,000 / 40,000 行、ウォームアップ 3 回 + 15 回の p50 |

変換ツールは `storage="jdbc"` (PostgreSQL と同じ変換結果) で使う。

## 4. 実装

| 対象 | 内容 |
|---|---|
| `difftest/docker-compose.yml` | `schema-loader-oracle` と `scalardb-cluster-oracle` を追加 |
| `difftest/conf/*oracle*.properties` | ScalarDB Core (ホストから)、Schema Loader、Cluster ノード、SQL JDBC クライアントの設定 |
| `difftest/oracle-backend-init.sh` | ScalarDB 用ユーザーを作る (冪等) |
| `difftest/backends.py` | `oracle` バックエンドを追加 |
| `difftest/backend_compare.sh` | 互換性ケースごとに ScalarDB Cluster ノードを再起動する。同じ実行の中で `emp` を列の違う定義で作り直すと、ノードが古い表定義を使い続けるため (Cassandra の検証で見つけた) |
| `difftest/backend_report.py` | 任意の数の系統を並べて表を作れるようにする |

## 5. 成果物

- `docs/scalardb-backend-comparison.md`: Oracle → ScalarDB + Oracle と Oracle → ScalarDB + Cassandra の検証結果のまとめ (ScalarDB + PostgreSQL は参考として併記)
- `docs/cassandra-verification-report.md`: Cassandra 検証の詳細 (作成中のものを完成させる)

## 6. 実施中の計画からの変更 (2026-09-11)

| 変更 | 理由 |
|---|---|
| Oracle バックエンドの ScalarDB の接続プールを小さくした (Cluster ノード: `scalar.db.jdbc.connection_pool.min_idle=5`、`max_total=50`、表メタデータ用・管理用は各 1 / 5。ホスト側の ScalarDB Core クライアントと Schema Loader: 1 / 10) | 既定値 (`min_idle` 20、`max_total` 200) のままでは、1 回目の実行で `ORA-12516` (リスナーが接続を受け付けられない) が起きた。Oracle Database Free のサーバープロセス数の上限は 200 で、移行元の接続とも共有している。Consensus Commit は準備・検証・コミットを最大 128 並列で行うため、ノード 1 つのプールだけで上限に届きうる。PostgreSQL 用ノードは既定値のままなので、並列度の高い処理ではこの差が結果に出る可能性がある (単一クライアントの本計測では小さいと考える) |
| 1 回目の結果は `out/cassandra-verify/oracle-backend.failed-ora12516/` に残した | 5,000 行のベンチ 2 本 (15 / 15、30 / 30 で一致) 以外は接続エラーで実行できなかった |

