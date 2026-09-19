# Oracle → ScalarDB + Oracle / ScalarDB + Cassandra 検証結果のまとめ

作成日: 2026-09-11
関連文書: `docs/oracle-backend-verification-plan.md` (Oracle バックエンドの計画)、`docs/cassandra-verification-plan.md` / `docs/cassandra-verification-report.md` (Cassandra バックエンドの計画と詳細)、`docs/bench-report.md` (前回の ScalarDB + PostgreSQL の比較)

## 要約

| 観点 | Oracle → ScalarDB + Oracle | Oracle → ScalarDB + Cassandra |
|---|---|---|
| 互換性 | `oracle.sql` 17 / 17、Oracle 固有機能 51 / 62 が一致。ScalarDB + PostgreSQL と完全に同じ | 実行できた文はすべて一致。ただし Oracle 固有機能 62 文中 47 文、NoSQL 適性ケース 30 文中 10 文はキーで取得できず実行できない |
| キーで絞る読み書き (40,000 行) | 点読み 4 ms、1 行の書き込み 3 ms (Oracle 直接の 3〜4 倍) | 点読み 17 ms、1 行の書き込み 24 ms (Oracle 直接の約 20 倍) |
| 表全体を読む処理 | 実行できる。絞り込み・並べ替えを Oracle に任せられるものは 15〜36 ms、集計・`DISTINCT`・`OFFSET` など全行を ScalarDB が読むものは 1.0〜1.6 秒 | 実行できない (設計変更が必要) |
| 移行の手間 | 小さい。SQL の書き換えはアプリ側処理 (変換ツールの実行計画) で吸収でき、キー設計はそのままでよい | 大きい。キーで取得できない問いごとに、キー設計・集計表・ScalarDB Analytics のいずれかが要る |

- **NoSQL 適性ケース 30 文のうち、どちらの構成でも速く表サイズに依らない (「向く」) のは同じ 17 文だった。** 主キーの読み書き、パーティション内の範囲 (「顧客ごとの最新 N 件」)、キーで駆動する JOIN、キーの `IN` である。キーで取得する形に書いた SQL は、ScalarDB + Oracle でも ScalarDB + Cassandra でもそのまま動く。
- **分かれるのは、表全体を読む 10 文である。** ScalarDB + Oracle では動くが、応答時間は表サイズに比例する。ScalarDB + Cassandra では、クロスパーティション走査を RDBMS バックエンドでしか使わない方針のため実行できない。
- **ScalarDB + Oracle は、ScalarDB 導入の第一歩として最も手間が小さい。** ただし全行を ScalarDB に読み込む処理 (全表の集計など) は Oracle 直接の 300〜1,600 倍かかり、バックエンドが Oracle でも設計の見直しが要る。
- どちらも単一クライアント・単一ノードでの計測で、Cassandra の利点であるノード追加によるスループットは評価していない。


## 1. 比べた構成

移行元はどちらも Oracle Database 23ai Free で、同じ Oracle SQL を変換ツール (`scalardb_migrate`) で ScalarDB SQL またはアプリ側の実行計画に変換して実行した。

| 構成 | ScalarDB のバックエンド | ScalarDB の設定 | 変換ツール | 位置づけ |
|---|---|---|---|---|
| **Oracle → ScalarDB + Oracle** | 移行元と同じ Oracle Database 23ai Free (ScalarDB 専用のユーザー・スキーマ) | クロスパーティション走査あり (絞り込み・並べ替えあり) | `storage=jdbc` | データを Oracle に置いたまま、アクセスを ScalarDB 経由に切り替える |
| **Oracle → ScalarDB + Cassandra** | Apache Cassandra 5.0.9 (単一ノード) | **クロスパーティション走査なし** (キー指定のアクセスだけ)、`scan_fetch_size=1000` | `storage=cassandra` | データを NoSQL に移す |
| 参考: ScalarDB + PostgreSQL | PostgreSQL 16 | クロスパーティション走査あり | `storage=jdbc` | 前回の検証と同じ構成 (同じ日に測り直した) |
| 参考: ScalarDB + Cassandra (走査あり) | Cassandra 5.0.9 | クロスパーティション走査あり (絞り込みのみ) | `storage=jdbc` | Cassandra でクロスパーティション走査を許した場合の参考値 |

クロスパーティション走査は RDBMS (JDBC) バックエンドでのみ使う方針とした (2026-09-11 決定)。ScalarDB のドキュメントでも、非 JDBC のストレージではクロスパーティション走査は SERIALIZABLE を指定しても直列化可能にならず、並べ替えは JDBC でしか使えない。そのため Cassandra では、ScalarDB にキー (主キー、パーティションキー、セカンダリインデックス) で絞ったアクセスだけをさせ、絞り込み・並べ替え・集約などは取得後にアプリ側 (インメモリ H2) で行う。

## 2. 測り方

| ケース | 内容 | 規模 |
|---|---|---|
| `difftest/cases/oracle.sql` | 変換ツールの基本ケース 17 文 | SCOTT 相当 |
| `difftest/cases/oracle-features.sql` | Oracle 固有機能の読み取り系 62 文 | SCOTT 相当 |
| `difftest/cases/bench.sql` | 前回のベンチ 15 文 (emp / dept / bonus) | emp 5,000 / 20,000 / 40,000 行 |
| `difftest/cases/nosql-patterns.sql` | NoSQL 適性ケース 30 文。同じ注文データを RDB 型 (代理キー + インデックス) と NoSQL 型 (パーティションキー `customer_id` + クラスタリングキー `order_date, order_id`) の 2 つの表に持つ | 注文 5,000 / 20,000 / 40,000 行 |

- 互換性: 同じ SQL を Oracle に直接投げた結果を正解とし、ScalarDB 経由の結果と値まで比べる (`ORDER BY` の無い文は順序を無視)。
- 性能: 1 つの JVM から Oracle 直接と ScalarDB 経由を交互に実行し、ウォームアップ 3 回のあと 15 回の p50 を取る。主キー指定の文は反復ごとにキーを変える。
- 環境: Apple M3 Pro、Docker の VM 8 GiB。ScalarDB Cluster 3.19.1 (standalone、トライアルライセンス、Consensus Commit / SERIALIZABLE)。すべて同一ホストのコンテナで、ネットワーク遅延はほぼゼロ。ScalarDB Cluster ノードはメモリの都合で同時に 1 つだけ起動した。

## 3. 互換性 (Oracle の結果と一致したか)

| ケース | ScalarDB + Oracle | ScalarDB + Cassandra | 参考: ScalarDB + PostgreSQL |
|---|---|---|---|
| `oracle.sql` (17 文) | **17 一致** | 5 一致、12 取得不可 | 17 一致 |
| `oracle-features.sql` (読み取り 62 文) | **51 一致**、9 不一致、2 変換不可 | 11 一致、2 不一致、47 取得不可、2 変換不可 | 51 一致、9 不一致、2 変換不可 |
| `bench.sql` (15 文、3 規模) | 15 一致 | 9 一致、6 取得不可 | 15 一致 |
| `nosql-patterns.sql` (30 文、3 規模) | 30 一致 | 20 一致、10 取得不可 | 30 一致 |

「取得不可」は、キー (主キー・パーティションキー・セカンダリインデックス) で行を取得できないため、クロスパーティション走査を使わない Cassandra では実行できない文である。変換ツールが `FULL_SCAN` / `NO_CROSS_PARTITION` として変換不可を報告する。

**ScalarDB + Oracle の互換性は ScalarDB + PostgreSQL と完全に同じだった。** 不一致の 9 文も同じ文で、原因はバックエンドではなくアプリ側処理のエンジン (H2) と型対応にある。

| 不一致の文 | 原因 | 対処 |
|---|---|---|
| #32、#33 階層問合せ (CONNECT BY、SYS_CONNECT_BY_PATH) | H2 に無い構文 | 再帰 WITH に書き換える (再帰 WITH は一致) |
| #39 KEEP (DENSE_RANK FIRST) | H2 に無い構文 | ROW_NUMBER() で書き換える |
| #42〜#44 ROLLUP / CUBE / GROUPING SETS | H2 に無い構文 | 集約レベルごとの UNION ALL に書き換える |
| #45、#46 PIVOT / UNPIVOT | H2 に無い構文 | 条件付き集約 / UNION ALL に書き換える |
| #21 `CAST(sal AS VARCHAR2)` | `NUMBER(7,2)` を ScalarDB の DOUBLE に対応させるため `'2450.0'` になる | 金額はスケール済み整数 (BIGINT) にするか、書式を TO_CHAR で明示する |

データを Oracle に置いたままでも、ScalarDB を通すと Oracle の SQL がそのまま動くわけではない。ScalarDB SQL の文法に収まらない文は、ScalarDB から行を取得してアプリ側で処理する (実行計画) ことになる。その範囲では、互換性はバックエンドが Oracle でも PostgreSQL でも変わらない。

**ScalarDB + Cassandra では、実行できた文はすべて Oracle と一致した** (不一致の 2 文は PostgreSQL・Oracle でも不一致になる #21 と #43)。ただし、キーで行を取得できない文が多い。Oracle 固有機能ケースでは 62 文中 47 文がこれにあたり、どれも WHERE が無いか、キー以外の条件だけで `emp` 全体を読む文である。Oracle 固有の関数・構文そのものは、取得した行に対してアプリ側で処理できるため障害にならない。

## 4. 性能

### 4.1 パターン別の応答時間 (`nosql-patterns.sql`、注文 40,000 行、p50 ms、中央値)

Oracle 直接は ScalarDB + Oracle と同じ回に測った値である。

| 種類 | Oracle 直接 | ScalarDB + Oracle | ScalarDB + Cassandra | 参考: ScalarDB + PostgreSQL |
|---|---|---|---|---|
| 主キー 1 件の読み取り (N1a、N1b) | 0.9 | **4.0** | 16.9 | 12.6 |
| パーティション内の範囲・キーセット (N2、N11b) | 1.4 | **4.0** | 12.4 | 9.6 |
| キーで駆動する JOIN (N9a、N9b) | 1.2 | **5.3** | 18.6 | 8.0 |
| 1 行の書き込み (N13a〜d、N14) | 1.1 | **3.1** | 23.8 | 5.1 |
| キー 5 個の `IN` (N12a、N12b) | 1.2 | **8.3** | 35.9 | 9.0 |
| インデックス等値、表の 1/5 (N5a、N5b) | 7.7 | 214 | 286 | 256 |
| 無索引列のフィルタ 1% (N6a、N6b) | 1.3 | 15.7 | 取得不可 (走査を許すと 871) | 22.7 |
| 全表の上位 10 件 (N7a、N7b) | 1.7 | 18.2 | 取得不可 (走査を許しても実行不可) | 23.5 |
| 非キー条件の一括 `UPDATE` (N15) | 2.2 | 36.1 | 取得不可 (走査を許すと 721) | 50.0 |
| 全表集約 (N8a、N8b) | 1.2 | 988 | 取得不可 (走査を許すと 924) | 935 |
| 全表の JOIN + 集約 (N10b) | 3.6 | 1,615 | 取得不可 (走査を許すと 5,239) | 1,131 |

- **キーで絞る読み書きは、ScalarDB + Oracle が 3〜8 ms、ScalarDB + Cassandra が 11〜36 ms。** どちらも表サイズに依らない。ScalarDB が Oracle 直接に上乗せするのは 2〜4 ms (Consensus Commit のメタデータ、コミット時の検証読み取り、Cluster ノードとの往復) で、Cassandra ではさらに軽量トランザクション (Paxos) の分が乗る。書き込みは Cassandra が ScalarDB + Oracle の約 8 倍 (24 ms 対 3 ms) で、差が最も大きい。
- **無索引列のフィルタ・全表の上位 N 件・非キー条件の一括更新は、JDBC バックエンドでは 15〜50 ms で済む。** ScalarDB が条件と並べ替えを SQL としてバックエンドに渡し、バックエンドが索引や全表走査を自分で速く行うためである。Cassandra では同じ文を実行できない (走査を許しても、絞り込みは全行を読んでから ScalarDB が行うため 721〜871 ms かかる)。
- **全表集約・全表 JOIN・`DISTINCT`・`OFFSET` は、どの構成でも 1〜1.6 秒 / 40,000 行かかる** (Oracle 直接の 300〜1,600 倍)。ScalarDB SQL の層またはアプリ側処理が全行を読み込んで集計するためで、バックエンドが Oracle でも速くならない。JDBC バックエンドは `scan_fetch_size` を既定値 (10) のまま測っており、1000 にすると 2〜3 倍速くなる (`docs/cassandra-verification-report.md` 3.2)。
- ScalarDB + PostgreSQL の点アクセスが ScalarDB + Oracle より遅く出ているが、前回の報告では 4〜6 ms で、今回の値 (12 ms 前後) は計測時の状態によるばらつきと考える。JDBC バックエンド同士の差は小さいと読むのが妥当である。

### 4.2 既存ベンチ (`bench.sql`、emp 40,000 行、p50 ms)

| # | 文 | Oracle 直接 | ScalarDB + Oracle | ScalarDB + Cassandra | 参考: ScalarDB + PostgreSQL |
|---|---|---|---|---|---|
| Q5 | 主キー 1 件検索 | 0.9 | 5.9 | 15.0 | 6.2 |
| Q6 | インデックス等値 (1,000 行) | 3.1 | 34.6 | 61.8 | 34.3 |
| Q7 | インデックス等値 + 先頭 10 行 | 0.6 | 4.5 | 36.1 | 8.7 |
| Q8 | 無索引列の範囲条件 + `ORDER BY` | 1.8 | 25.3 | 取得不可 | 21.0 |
| Q9 | 主キーの範囲 + `ORDER BY` | 1.0 | 11.1 | 取得不可 | 7.5 |
| Q10 | 全表集約 | 3.5 | 1,065 | 取得不可 | 997 |
| Q11 | 主キーで駆動する JOIN | 1.2 | 5.6 | 15.3 | 10.9 |
| Q12 | インデックスで駆動する JOIN | 2.6 | 33.8 | 67.1 | 35.9 |
| Q13 | 式射影 + インデックス条件 (実行計画) | 2.5 | 43.2 | 63.7 | 38.2 |
| Q14 | 全表 `DISTINCT` (実行計画) | 2.6 | 1,185 | 取得不可 | 1,037 |
| Q15 | ウィンドウ関数 + インデックス条件 (実行計画) | 2.5 | 35.9 | 71.1 | 34.0 |
| Q16 | `IN (副問合せ)` (実行計画) | 2.4 | 1,247 | 取得不可 | 1,022 |
| Q17 | 日付式での `GROUP BY` (実行計画) | 3.1 | 1,204 | 取得不可 | 966 |
| Q18 | 主キー 1 行 `UPDATE` | 1.3 | 5.2 | 26.7 | 3.1 |
| Q19 | 1 行 upsert (`MERGE` → `UPSERT`) | 1.4 | 3.4 | 25.0 | 2.8 |

## 5. NoSQL 適性ケースの判定の比較

判定は表サイズ (5,000 → 40,000 行) に対する伸びによる。「向く」は p50 の伸びが 30% 以内、「条件付き」は返す行に比例して伸びるキー・インデックスのアクセス、「全件走査」は実行できるが全パーティションを読むため表に比例して伸びるもの、「取得不可」はキーで取得できず Cassandra では実行しないもの。

| パターン | 文 | ScalarDB + Oracle | ScalarDB + Cassandra |
|---|---|---|---|
| キーで絞る読み書き: 主キーの読み書き、パーティション内の範囲、キーセットページング、キーで駆動する JOIN、キーの `IN`、小さい表のインデックス、RDB 型の表でもインデックスで少数行を取れる並べ替え | N1a、N1b、N2、N3、N4a、N4b、N5c、N9a、N9b、N11b、N12a、N12b、N13a〜d、N14 (17 文) | **向く** (2.8〜8.4 ms) | **向く** (11〜36 ms) |
| 返す行が表に比例するアクセス: 低カーディナリティ列のインデックス等値、インデックスで駆動する JOIN | N5a、N5b、N10a (3 文) | 条件付き (103〜214 ms) | 条件付き (180〜476 ms) |
| 絞り込み・並べ替えをバックエンドに任せられる全件走査: 無索引列のフィルタ、全表の上位 N 件、非キー条件の一括更新 | N6a、N6b、N7a、N7b、N15 (5 文) | 全件走査 (15〜36 ms。バックエンドの SQL で絞るため軽い) | **取得不可** |
| 全行を ScalarDB に読み込む処理: 全表の `GROUP BY` / `COUNT(*)` / `DISTINCT`、全表の JOIN + 集約、`OFFSET` ページング | N8a、N8b、N8c、N10b、N11a (5 文) | 全件走査 (1.0〜1.6 秒) | **取得不可** |

判定ごとの文の集合は 2 つの構成で完全に一致した (向く 17、条件付き 3、残り 10)。違うのは残り 10 文の扱いだけである。

## 決定（2026-09-19 / #20）: PL/SQL 移行の移行先は JDBC バックエンドに限定する

PL/SQL corpus の移行（`plsql/`）は、**JDBC バックエンドを移行先とする**。パーティションをまたぐ走査
（`SCAN-002`、フィルタや並べ替えが主キーで閉じないもの）はそのまま使う——ScalarDB がそれを受けるのは
JDBC バックエンドのときだけである。

* `largest_order`（`WHERE customer_id = ? ORDER BY total_amount DESC` の先頭 1 件）は**今の形のまま**動かす。
  NULL を含めて Oracle と一致している（`report_largest_order_null`）。`NVL(MAX(...))` への書き換えは
  しない——corpus の注文は価格計算の前に `total_amount = NULL` で作られており、NULL が無いという前提が立たない
* 「Cassandra ではキーで取ってアプリ側で処理する」という方針（下の比較）は、この移行には使わない。
  Cassandra を移行先に戻すときは、`SCAN-002` の routine を洗い直すこと

## 6. どちらを選ぶか・移行の判断基準

1. **既存の SQL をできるだけそのまま動かしたいなら ScalarDB + Oracle。** 今回のケースはすべて実行でき、互換性は ScalarDB + PostgreSQL と同じだった。ScalarDB SQL に収まらない文は変換ツールがアプリ側の実行計画にする。キーで絞る処理の上乗せは 2〜4 ms で済む。
2. **ScalarDB + Oracle でも、全行を ScalarDB に読み込む処理は設計を見直す。** 全表の集計・`DISTINCT`・`OFFSET` ページング・全表の JOIN は 40,000 行で 1〜1.6 秒になり、Oracle 直接の 300〜1,600 倍かかる。集計値を持つ表を用意する、キーセットページングに変える、分析系は ScalarDB Analytics に回す、のいずれかにする。一方、無索引列のフィルタや全表の上位 N 件は、Oracle が SQL で処理するので 15〜36 ms で済む。
3. **ScalarDB + Cassandra に移すなら、すべての問いをキーで取得する形にする必要がある。** 変換ツールを `--storage cassandra` で実行すると、キーで取得できない文が `FULL_SCAN` / `NO_CROSS_PARTITION` の変換不可として一覧になり、これが設計変更の対象になる。今回のケースでは Oracle 固有機能 62 文中 47 文、既存ベンチ 15 文中 6 文、NoSQL 適性ケース 30 文中 10 文が該当した。
4. **キーで取得する形に書き直した SQL は、ScalarDB + Oracle と ScalarDB + Cassandra の両方で「向く」になる。** 両構成で「向く」の文の集合が一致したことから、ScalarDB + Oracle の上で SQL をキーで取得する形に直しておけば、同じ SQL のまま Cassandra に移せる。
5. **Cassandra は 1 回あたりの応答時間では不利である。** キーでの読み取りは ScalarDB + Oracle の 3〜4 倍、書き込みは約 8 倍かかった (単一ノード)。Cassandra を選ぶ理由は、ノード追加によるスループットと可用性であり、今回の計測範囲の外にある。

## 7. 注意点

- **ScalarDB + Oracle は、移行元と同じ Oracle インスタンスを別スキーマで使った。** ネットワーク遅延が無く、バッファキャッシュと CPU (Oracle Database Free は 2 スレッドまで) を移行元と共有している。実運用では Oracle 直接の側にもネットワーク往復が乗るため、倍率は今回より小さくなる。
- **Oracle Database Free のサーバープロセス上限 (200) に合わせて、ScalarDB の接続プールを小さくした** (Cluster ノード `max_total` 50)。既定値 (200) のままでは `ORA-12516` で接続できなくなった。本番の Oracle では、ScalarDB のプールの上限 (既定 200、並列コミットは最大 128) を Oracle の `processes` / `sessions` に収まるように決める必要がある。
- **Cassandra は単一ノード (レプリケーション係数 1)** で、ノード追加時のスループット、レプリケーション、整合性レベルの影響は測っていない。
- **同時実行・競合時の再試行は測っていない** (単一クライアント)。
- JDBC バックエンドの走査は `scan_fetch_size` 既定値 (10)、Cassandra は 1000 で測った。
- 計測中の不具合 (Cluster ノードが古い表定義を使い続けた事象、PostgreSQL 40,000 行での gRPC 切断、Oracle の `ORA-12516`) はいずれも原因を取り除くか測り直し、本書の数値には含めていない。詳細は `docs/cassandra-verification-report.md` 9 章と `docs/oracle-backend-verification-plan.md` 6 章。


## 再現手順

```
cd difftest && ./make-cluster-conf.sh && cd ..
# ScalarDB + Oracle
difftest/oracle-backend-init.sh
(cd difftest && docker compose stop scalardb-cluster && docker compose --profile oracle --profile oracle-backend up -d scalardb-cluster-oracle)
difftest/backend_compare.sh oracle out/cassandra-verify/oracle-backend
# ScalarDB + Cassandra (クロスパーティション走査なし)
(cd difftest && docker compose stop scalardb-cluster-oracle && docker compose --profile cassandra up -d)
difftest/backend_compare.sh cassandra out/cassandra-verify/after
# 表の生成
.venv/bin/python difftest/backend_report.py \
    --run "ScalarDB+Oracle=out/cassandra-verify/oracle-backend:oracle" \
    --run "ScalarDB+Cassandra=out/cassandra-verify/after:cassandra" \
    --run "ScalarDB+PostgreSQL=out/cassandra-verify/pg:postgres" \
    --reference "Cassandra (走査あり)=out/cassandra-verify/main:cassandra" \
    --judge ScalarDB+Oracle --judge ScalarDB+Cassandra
```
