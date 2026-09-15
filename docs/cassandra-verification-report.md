# ｋOracle → ScalarDB + Cassandra 変換検証 報告書

作成日: 2026-09-11
関連文書: `docs/cassandra-verification-plan.md` (計画書)、`docs/bench-report.md` (Oracle と ScalarDB + PostgreSQL の比較)、`docs/oracle-sql-report.md` (Oracle 固有 SQL の網羅調査)、`docs/app-side-processing-plan.md` (アプリ側処理のパターン)

## 要約

- **ScalarDB + Cassandra で動くのは、キー (主キー・パーティションキー・セカンダリインデックス) で行を取得できる文である。** クロスパーティション走査は RDBMS バックエンドでのみ使う方針のもと、Cassandra では ScalarDB にキー指定のアクセスだけをさせ、絞り込み・並べ替え・集約はアプリ側 (H2) で行った。この構成で、実行できた文はすべて Oracle と同じ結果を返した (PostgreSQL でも失敗する既知の 2 文を除く)。
- **NoSQL 適性ケース 30 文の判定は、向く 17、条件付き 3、取得不可 10。** 向くのは主キーの読み書き、パーティション内の範囲 (「顧客ごとの最新 N 件」)、キーで駆動する JOIN、キーの `IN` で、40,000 行でも 11〜26 ms と表サイズに依らない。条件付きは低カーディナリティ列のインデックスで、返す行に比例して遅くなる。取得不可は全表の集計・無索引列の検索・全表の上位 N 件・`OFFSET` ページング・非キー条件の一括更新で、キー設計の変更、集計表、ScalarDB Analytics のいずれかが要る。
- **既存の Oracle SQL のうち、Cassandra でそのまま動くものは少ない。** Oracle 固有機能ケース 62 文では 47 文が取得不可になった。Oracle 固有の関数や構文そのものはアプリ側で処理できるため問題にならず、表全体を読む書き方が障害になる。
- **Cassandra では ScalarDB の設定に注意が要る。** パーティションをまたぐ並べ替えを有効にするとノードが起動しない (DB-CORE-10128)。走査の取得単位 `scan_fetch_size` の既定値 10 のままでは走査が 10 倍以上遅い。
- **変換ツールを Cassandra 向けに改修した** (`--storage cassandra`)。パーティションをまたぐ `ORDER BY` はキー指定の取得 + アプリ側の並べ替えに、キーの `IN` はキーごとの取得 (40,000 行で 995 → 36 ms) に変換する。キーで取得できない文は理由付きで変換不可として一覧にする。JDBC 向けの変換結果は変わらない。
- **1 回あたりの応答時間は PostgreSQL バックエンドより遅い。** キーでの読み取りは 1.3〜2.3 倍 (10〜20 ms)、1 行の書き込みは約 5 倍 (約 24 ms)。単一ノードでの計測のため、ノードを増やしたときのスループットは評価していない。


## 1. 何をしたか

計画書のとおり、同じ Oracle SQL と同じデータを 3 つの系統で実行し、結果の一致 (互換性) と応答時間 (性能) を比べた。

| 系統 | 構成 |
|---|---|
| 基準 | Oracle Database 23ai Free |
| 比較 A | ScalarDB Cluster 3.19.1 + PostgreSQL 16 |
| 比較 B | ScalarDB Cluster 3.19.1 + Apache Cassandra 5.0.9 (単一ノード、レプリケーション係数 1) |

比較 B は 2 回測った。

| 計測 | ScalarDB Cluster ノードの設定 | 変換ツール |
|---|---|---|
| **改修前** | クロスパーティション走査あり (絞り込みあり、並べ替えは Cassandra では設定できない) | Cassandra を意識しない (PostgreSQL と同じ変換結果) |
| **改修後** | **クロスパーティション走査なし** (`scalar.db.cross_partition_scan.enabled=false`) | Cassandra 向け (5 章)。ScalarDB にはキー指定のアクセスだけをさせ、絞り込み・並べ替え・集約などは取得後にアプリ側 (H2) で行う |

改修後の設定は「クロスパーティション走査は RDBMS (JDBC) バックエンドでのみ使う」という方針 (2026-09-11 に決定) に従う。ScalarDB のドキュメントでも、非 JDBC のストレージではクロスパーティション走査は SERIALIZABLE を指定しても直列化可能にならないとされている。改修前の計測は、クロスパーティション走査を許した場合にどれだけかかるかの参考値として残す。

| ケース | 内容 | 規模 |
|---|---|---|
| `difftest/cases/oracle.sql` | 変換ツールの基本ケース 17 文 | SCOTT 相当 |
| `difftest/cases/oracle-features.sql` | Oracle 固有機能の読み取り系 62 文 | SCOTT 相当 |
| `difftest/cases/bench.sql` | 前回のベンチ 15 文 (emp / dept / bonus) | emp 5,000 / 20,000 / 40,000 行 |
| `difftest/cases/nosql-patterns.sql` | 新規。NoSQL 適性ケース 30 文 (15 パターン) | 注文 5,000 / 20,000 / 40,000 行 |

応答時間は 1 つの JVM から Oracle と ScalarDB を交互に計測し、ウォームアップ 3 回のあとの 15 回の p50 を取った。各反復で結果を Oracle と突き合わせている。

## 2. 環境

| 項目 | 値 |
|---|---|
| ホスト | Apple M3 Pro / 36 GiB / macOS (Darwin 25.6.0)、Docker の VM は 8 GiB・11 CPU |
| Oracle | Oracle Database 23ai Free (`gvenzl/oracle-free:23-slim-faststart`) |
| ScalarDB | ScalarDB Cluster 3.19.1 (standalone、トライアルライセンス)、Consensus Commit / SERIALIZABLE |
| PostgreSQL | 16 (Docker) |
| Cassandra | 5.0.9 (`cassandra:5.0`)、単一ノード、ヒープ 1 GiB、`paxos_variant: v1` (イメージの既定値) |
| JVM | OpenJDK 17 (計測側)、ScalarDB Cluster ノードは JDK 21 |

メモリの都合で ScalarDB Cluster ノードは同時に 1 つだけ起動し、バックエンドごとに入れ替えて測った。すべて同一ホスト上のコンテナなので、ネットワーク遅延はほぼゼロである。

## 3. Cassandra で分かった設定上の事実

### 3.1 クロスパーティション走査の並べ替えは「設定すると起動しない」

ScalarDB のドキュメントには「クロスパーティション走査の並べ替えは JDBC データベースでのみ使える」とある。実際には、Cassandra バックエンドで `scalar.db.cross_partition_scan.ordering.enabled=true` にすると **ScalarDB Cluster ノードが起動しない**。

```
java.lang.IllegalArgumentException: DB-CORE-10128: Cross-partition scan with ordering is not supported in Cassandra
```

この設定を外して起動すると、パーティションをまたぐ `ORDER BY` を含む ScalarDB SQL は実行時に失敗する。

```
DB-CORE-10007: Cross-partition scan ordering is not enabled. Operation: ScanAll{... orderings=[amount-DESC, order_id-ASC] ...}
```

PostgreSQL バックエンドでは同じ SQL が通るため、**変換ツールが PostgreSQL 向けに OK / WARN と判定した文が Cassandra では実行できない**。影響の範囲は 4 章、対処は 5 章に書く。

### 3.2 走査の取得単位 (`scalar.db.scan_fetch_size`) の既定値 10 は Cassandra では遅い

ScalarDB はストレージを走査するとき、既定で 10 行ずつ取り出す。Cassandra ではこれが 10 行ごとの往復になり、走査が極端に遅くなった。ScalarDB Cluster ノードに `scalar.db.scan_fetch_size=1000` を設定すると、走査を含む文は 4〜19 倍速くなり、点アクセスは変わらなかった (注文 5,000 行、2 反復の p50、ms)。

| 文 | 既定 (10) | 1000 | 倍率 |
|---|---|---|---|
| N6a 無索引列のフィルタ (全パーティション走査) | 1,693 | 148 | 11.4 |
| N6b 同上 (NoSQL 型の表) | 1,506 | 98 | 15.3 |
| N8a 全表 `GROUP BY` | 1,627 | 129 | 12.6 |
| N8b 全表 `COUNT(*)` | 1,724 | 115 | 15.0 |
| N5a インデックス等値 (表の 1/5、1,000 行) | 450 | 98 | 4.6 |
| N12b インデックス列の `IN` (5 値) | 3,381 | 179 | 18.9 |
| N15 非キー条件の一括 `UPDATE` (49 行) | 1,993 | 438 | 4.5 |
| N1b 主キー 1 件検索 | 24.9 | 25.1 | 1.0 |

本検証の Cassandra 側の数値はすべて `scan_fetch_size=1000` で測った。PostgreSQL 側 (と Oracle バックエンド) の設定は前回のベンチと同じ既定値である。

PostgreSQL バックエンドでも同じ設定を 5,000 行で試したところ、**全表を読む文は 2〜3 倍速くなった** (全表集約 154 → 58 ms、全表 `DISTINCT` 195 → 62 ms、`IN (副問合せ)` 142 → 60 ms、全表 `COUNT(*)` 101 → 40 ms)。全 45 文の中央値では差は無く (比 0.9〜1.0)、点アクセスの一部は遅く出た (主キー 1 件 10 → 22 ms など)。ただしノードを再起動した直後に 1 回だけ測った値なので、ばらつきと区別できない。したがって、**本検証の JDBC バックエンド (PostgreSQL、Oracle) の全表走査の数値は、設定を調整した Cassandra と比べて 2〜3 倍不利な条件で測っている**。Cassandra ほどではないが、JDBC バックエンドでも走査の多い処理では `scan_fetch_size` を見直す価値がある。

### 3.3 点アクセスは 1 回あたり約 25 ms かかり、Paxos v2 にしても変わらなかった

Cassandra バックエンドでは、主キー 1 件の読み取りも 1 行の書き込みも 20〜30 ms かかった (PostgreSQL バックエンドでは 3〜6 ms)。Consensus Commit は Cassandra の軽量トランザクション (LWT) と SERIAL 読み取りを使い、どちらも Paxos を通るためと考え、往復回数が少ない `paxos_variant: v2` を試した。結果は同程度で、改善は見られなかった (5,000 行、2 反復の p50、ms)。

| 文 | Paxos v1 | Paxos v2 |
|---|---|---|
| N1b 主キー 1 件検索 | 25.1 | 28.6 |
| N13a 1 行 INSERT | 25.9 | 26.5 |
| N13b 1 行 UPDATE | 30.0 | 25.0 |
| N13c 1 行 upsert | 25.8 | 23.0 |
| N13d 1 行 DELETE | 25.4 | 22.9 |
| N14 インデックス列の 1 行 UPDATE | 26.9 | 26.4 |

反復数が少ないため差の大小は論じられないが、v2 で桁が変わることはない。そのためイメージの既定値 v1 に戻して本計測を行った。約 25 ms の内訳 (Paxos、コミット時の検証読み取り、Cluster ノードの処理) は本検証では切り分けていない。

### 3.4 クロスパーティション走査を無効にした Cassandra で ScalarDB SQL が実行できる形

改修後の設定 (クロスパーティション走査なし) の ScalarDB Cluster に、形の異なる ScalarDB SQL を直接投げて確かめた (注文 40,000 行)。

| # | 形 | 結果 |
|---|---|---|
| P1 | 主キー 1 件 (`WHERE empno = 7369`) | ✅ |
| P2 | インデックス等値 (`WHERE deptno = 10`) | ✅ |
| P3 | インデックス等値 + 非キー条件 (`deptno = 10 AND sal > 1000`) | ✅ |
| P4 | インデックス等値 + `ORDER BY` | ❌ DB-CORE-10006: Cross-partition scan is not enabled |
| P5 | パーティションキー + クラスタリングキーの範囲 + クラスタリング順の `ORDER BY` + `LIMIT` | ✅ |
| P6 | パーティションキー + 非キー条件 (`customer_id = 101 AND status = 'S1'`) | ✅ |
| P7 | パーティション内の集約 (`COUNT(*)`, `SUM`) | ✅ |
| P8 | インデックス等値 + 非キー条件 (RDB 型の表) | ✅ |
| P9 | インデックス等値での集約 | ✅ |
| P12 | パーティションキーの `OR` (`customer_id = 101 OR customer_id = 102`) | ❌ DB-CORE-10006 |
| P13 | 全表の `COUNT(*)` | ❌ DB-CORE-10006 |
| P14 | 無索引列だけの条件 (`amount > 9990`) | ❌ DB-CORE-10006 |
| P15 | 主キー + 非キー条件 | ✅ |
| P16 | パーティションキー + クラスタリングキーの範囲 + 非キー条件 | ✅ |
| P17 | インデックス等値 + `LIMIT` | ✅ |

**キー (主キー・パーティションキー・インデックス) で絞れていれば、追加の非キー条件や集約はクロスパーティション走査なしで実行できる。** 実行できないのは、キーで絞れない条件、キーの `OR`、キー指定以外の順序での `ORDER BY` である。JOIN の確認 (P10、P11) は確認用 SQL の書き方 (別名に `AS` を付けなかった) で構文エラーになったため、6 章の N9a / N9b / N10a (変換ツールが出力した `AS` 付きの SQL) で確かめた。

## 4. 互換性の結果

Oracle に直接投げた結果を正解とし、ScalarDB 経由の結果を値まで比べた。

| ケース | 系統 | 一致 | 不一致・エラー | 変換不可 (キーで取得できない) | 変換不可 (その他) |
|---|---|---|---|---|---|
| `oracle.sql` (17 文) | ScalarDB + PostgreSQL | 17 | 0 | 0 | 0 |
| | ScalarDB + Cassandra 改修前 (走査あり) | 13 | 4 | 0 | 0 |
| | ScalarDB + Cassandra 改修後 (走査なし) | 5 | 0 | 12 | 0 |
| `oracle-features.sql` (読み取り 62 文) | ScalarDB + PostgreSQL | 51 | 9 | 0 | 2 |
| | ScalarDB + Cassandra 改修前 (走査あり) | 45 | 15 | 0 | 2 |
| | ScalarDB + Cassandra 改修後 (走査なし) | 11 | 2 | 47 | 2 |

`oracle-features.sql` の PostgreSQL と Cassandra 改修後は、ScalarDB Cluster ノードを起動し直してから単独で実行した結果である (9 章の「初回の実行で起きた事象」を参照)。PostgreSQL の 51 / 9 / 2 は前回の報告 (`docs/oracle-sql-report.md`) と一致する。

**改修前 (Cassandra を意識しない変換、クロスパーティション走査あり) に増えた失敗は、すべて DB-CORE-10007 (パーティションをまたぐ `ORDER BY`)** だった。`oracle.sql` の 4 文 (インデックス等値 + `ORDER BY`、無索引条件 + `ORDER BY`、日付条件 + `ORDER BY`、外部結合 + `ORDER BY`) と、`oracle-features.sql` の 6 文 (#20 TO_DATE、#30 `(+)` 外部結合、#59 NULL の既定ソート順、#60 LIKE ESCAPE、#61 JOIN USING、#66 TO_TIMESTAMP) で、いずれも関数や構文ではなく末尾の `ORDER BY` が原因である。

**改修後 (キー指定の取得 + アプリ側処理) は、実行できた文がすべて Oracle と一致した。** 例外は PostgreSQL でも失敗する 2 文 (#21 `CAST(sal AS VARCHAR2)` が `'2450.0'` になる型対応の差、#43 H2 に CUBE が無い) だけである。`oracle.sql` #6 (インデックス等値 + `ORDER BY`) は、インデックスで取得して H2 で並べ替える計画 (P13) で一致するようになった。

一方、**`oracle-features.sql` の 62 文中 47 文は、キーで取得できないため Cassandra では実行できない。** これらの文は WHERE が無いか、キー以外の条件だけで `emp` 全体を読む。NVL、DECODE、TO_CHAR、分析関数、再帰 WITH、副問合せなど Oracle 固有の関数・構文そのものは、取得した行に対して H2 が処理するため Cassandra でも問題にならない。実行できるかどうかを決めるのは **行をキーで取得できるか** である。実行できた 13 文は、どれも `deptno = 30` (インデックス) や `empno = 7369` (主キー) で絞っている (#5 NVL、#10 `||`、#13 LPAD、#19 TO_CHAR、#23 NEXT_DAY、#26 INTERVAL、#27 ROWNUM、#54 FROM DUAL、#56 ヒント、#63 FOR UPDATE、#65 SYSDATE、および失敗した #21、#43)。


## 5. 変換ツールの改修 (フェーズ 4)

変換ツールに「ScalarDB の背後のストレージ」を指定する引数を加えた (`convert_script(..., storage="cassandra")`、CLI では `--storage cassandra`、ハーネスでは `--convert-storage`)。既定は `jdbc` で、**JDBC 向けの変換結果は改修前とバイト単位で同一** であることを、全ケースファイルと `samples/` で確認した。

`storage="cassandra"` では、ScalarDB に渡してよいのは 3.4 で実行できると確かめた形 (キーで絞ったアクセス。追加の非キー条件と集約は可) だけとし、それ以外は次の規則で扱う。

| 規則 (判定コード) | 対象 | 変換結果 | パターン |
|---|---|---|---|
| ORDER_STORAGE | 集約を含まない SELECT の `ORDER BY` のうち、パーティションキーを等値で指定してクラスタリングキーの先頭から同じ向き (またはすべて逆向き) に並べる形以外 | ScalarDB SQL にせず実行計画にする。fetch は `ORDER BY` を外したキー指定の走査、並べ替えと `LIMIT` は H2 | P13 |
| OR_KEYS | 単一列のパーティションキーまたはインデックス列に対する `IN` / `OR` (値 100 個まで) | 値ごとに 1 つの fetch (パーティション走査またはインデックス走査) に分け、H2 で合成して元の条件を適用する | P14 |
| NO_CROSS_PARTITION | キーでもインデックスでも絞れない SELECT | 実行計画を試みる。キーで取れる表だけなら fetch + H2 | P15 |
| FULL_SCAN | 上のいずれでも、キーで取得できない表が 1 つでも残る SELECT (全表の集約、無索引列だけの条件、全表の上位 N 件、キー指定の無い JOIN 相手の全件取得など) | **変換不可**。キー・インデックスの追加、集計表の保持、ScalarDB Analytics のいずれかが必要 | — |
| NO_CROSS_PARTITION (書き込み) | キーでもインデックスでも絞れない UPDATE / DELETE | **変換不可**。対象キーを先に特定し、主キー指定で書き込む | — |

集約 (`GROUP BY` や集約関数) を含む文の `ORDER BY` は、ScalarDB SQL の層が集約後に並べるため ORDER_STORAGE の対象にしない (キーで絞った集約なら 3.4 の P7 / P9 のとおり実行できる)。

単体テストを 15 件追加した (変換規則 6 件、Cassandra 向け計画の同値性 4 件、キーで取得できない文が変換不可になること 4 件、JDBC では分割しないこと 1 件)。計画の同値性テストは、元の SQL を全データに対して実行した結果と、計画の fetch で取った行だけに残余 SQL を実行した結果が一致することを確かめる。全 160 件が通る。`skills/sql-transpile/scripts/_scalardb/` の同梱コピーも `vendor_sync.py --update` で揃えた。

各ケースの文が改修後にどう変わるか (変換ツールの判定):

| ケース | 文数 | ScalarDB SQL のまま | 実行計画 (キー指定 fetch + H2) | 変換不可 (キーで取得できない) | 変換不可 (元から) |
|---|---|---|---|---|---|
| `oracle.sql` | 17 | 2 | 3 | 12 | 0 |
| `oracle-features.sql` (読み取り) | 62 | 2 | 11 | 47 | 2 |
| `bench.sql` | 15 | 7 | 2 | 6 | 0 |
| `nosql-patterns.sql` | 30 | 17 | 3 | 10 | 0 |

`oracle-features.sql` の文の多くは `emp` 全体を読む (WHERE が無いか、キー以外の条件だけ)。PostgreSQL バックエンドでは表全体を取得して H2 で処理できたが、Cassandra ではキーで取得できないため変換不可になる。

## 6. 性能の結果

### 6.1 パターン別の応答時間 (p50、ms、中央値)

`nosql-patterns.sql` の文を種類ごとにまとめた。Oracle 直接は同じ回に測った値である。

| 種類 | 行数 | Oracle 直接 | ScalarDB + PostgreSQL | ScalarDB + Cassandra 改修前 (走査あり) | ScalarDB + Cassandra 改修後 (走査なし) |
|---|---|---|---|---|---|
| 主キー 1 件の読み取り (N1a、N1b) | 40,000 | 1.3 | 12.6 | 14.7 | 16.9 |
| パーティション内の範囲・キーセット (N2、N11b) | 40,000 | 2.0 | 9.6 | 13.9 | 12.4 |
| キーで駆動する JOIN (N9a、N9b) | 40,000 | 1.6 | 8.0 | 15.3 | 18.6 |
| 1 行の書き込み (N13a〜d、N14) | 40,000 | 1.0 | 5.1 | 23.6 | 23.8 |
| インデックス等値、表の 1/5 (N5a、N5b) | 40,000 | 7.8 | 256 | 237 | 286 |
| キー 5 個の `IN` (N12a、N12b) | 40,000 | 1.0 | 9.0 | 995 | **36** |
| 全表集約 (N8a、N8b) | 40,000 | 1.6 | 935 | 924 | 取得不可 |
| 無索引列のフィルタ (N6a、N6b) | 40,000 | 1.7 | 23 | 871 | 取得不可 |

読み取り方:

- **キーで絞る読み取りは、Cassandra でも PostgreSQL と同じ桁 (10〜20 ms) に収まり、表サイズに依らない。** 5,000 行の時点では Cassandra の方が 2〜4 倍遅いが (点読み 23 ms 対 5.6 ms)、40,000 行では差が 1.3〜2.3 倍に縮んだ。PostgreSQL 側の点読みが 40,000 行で遅く出ているのは計測条件のばらつきと考えられ (前回の報告では 4〜6 ms)、差の小さい値は割り引いて読む必要がある。
- **書き込みは Cassandra が PostgreSQL の約 5 倍 (24 ms 対 5 ms) で、最も差が大きい。** Consensus Commit の条件付き書き込みとコーディネーター表への状態書き込みが、Cassandra では軽量トランザクション (Paxos) になるためと考えられる。
- **キーの `IN` は、変換ツールの分割 (P14) で 995 ms → 36 ms (28 分の 1) になった。** 分割しない場合は全パーティションを走査していた。PostgreSQL (9 ms) との差は、5 回の取得をそれぞれ別の走査として順に実行している分である。
- **無索引列のフィルタは、PostgreSQL では 23 ms、Cassandra (走査あり) では 871 ms と 38 倍の差がある。** PostgreSQL バックエンドでは ScalarDB が条件を SQL の WHERE として PostgreSQL に渡すのに対し、Cassandra では全行を読んでから ScalarDB が絞り込むためである。改修後の構成ではこの形は実行しない (取得不可)。
- **全表集約はどちらのバックエンドでも約 1 秒 / 40,000 行で、差は無い。** 集約は ScalarDB SQL の層で行うため、どちらも全行を ScalarDB に読み込む。ただし PostgreSQL 側は `scan_fetch_size` が既定値 (10) で、調整すれば 2〜3 倍速くなる (3.2)。

### 6.2 既存ベンチ (`bench.sql`、emp 40,000 行、p50 ms)

| # | 文 | Oracle 直接 | ScalarDB + PostgreSQL | ScalarDB + Cassandra 改修前 | ScalarDB + Cassandra 改修後 |
|---|---|---|---|---|---|
| Q5 | 主キー 1 件検索 | 1.0 | 6.2 | 13.4 | 15.0 |
| Q6 | インデックス等値 (表の 1/40、1,000 行) | 3.5 | 34.3 | 52.0 | 61.8 |
| Q7 | インデックス等値 + 先頭 10 行 | 0.9 | 8.7 | 35.1 | 36.1 |
| Q8 | 無索引列の範囲条件 + `ORDER BY` | 2.5 | 21.0 | ❌ DB-CORE-10007 | 取得不可 |
| Q9 | 主キーの範囲 + `ORDER BY` | 1.0 | 7.5 | ❌ DB-CORE-10007 | 取得不可 |
| Q10 | 全表集約 | 3.1 | 997 | 1,054 | 取得不可 |
| Q11 | 主キーで駆動する JOIN | 1.8 | 10.9 | 15.1 | 15.3 |
| Q12 | インデックスで駆動する JOIN | 2.3 | 35.9 | 55.0 | 67.1 |
| Q13 | 式射影 (NVL / 四則) + インデックス条件 (計画) | 2.6 | 38.2 | 57.1 | 63.7 |
| Q14 | 全表 `DISTINCT` (計画) | 7.5 | 1,037 | 1,204 | 取得不可 |
| Q15 | ウィンドウ関数 + インデックス条件 (計画) | 2.6 | 34.0 | 56.9 | 71.1 |
| Q16 | `IN (副問合せ)` (計画) | 2.5 | 1,022 | 1,339 | 取得不可 |
| Q17 | 日付式での `GROUP BY` (計画) | 3.0 | 966 | 1,193 | 取得不可 |
| Q18 | 主キー 1 行 `UPDATE` | 0.9 | 3.1 | 22.3 | 26.7 |
| Q19 | 1 行 upsert (`MERGE` → `UPSERT`) | 1.4 | 2.8 | 22.0 | 25.0 |

前回のベンチで「そのまま移行できる」とした点アクセスとインデックス範囲の文 (Q5〜Q7、Q11〜Q13、Q15、Q18、Q19) は Cassandra でも実行でき、結果も一致した。PostgreSQL 比は読み取りで 1.4〜4 倍、書き込みで 7〜9 倍である。前回「オンラインでは避ける」とした全表走査の文 (Q10、Q14、Q16、Q17) と、パーティションをまたぐ並べ替えの文 (Q8、Q9) は、Cassandra では取得不可になる。

表サイズに対する伸びの全データは `difftest/backend_report.py` の出力 (10 章) にある。


## 7. NoSQL 適性表 (Cassandra、クロスパーティション走査なし)

`nosql-patterns.sql` の 30 文を、改修後の構成 (キー指定のアクセスだけ + アプリ側処理) で判定した。判定は表サイズに対する伸びによる (5 章の基準。「向く」は 5,000 → 40,000 行で p50 の伸びが 30% 以内)。数値は 40,000 行の p50。

| 判定 | パターン | 40,000 行の p50 (ms) | 何が効いているか |
|---|---|---|---|
| **向く** (17 文) | N1a / N1b 主キー 1 件の読み取り | 15 / 19 | 主キー GET。表サイズに依らない |
| | N2 顧客 1 人の直近 10 件 (NoSQL 型: パーティション + クラスタリング順) | 14 | 1 パーティションをクラスタリング順に読んで 10 行で止まる |
| | N11b パーティション内のキーセットページング | 11 | 同上 |
| | N3 同じ問いを RDB 型の表で (インデックスで取得 → H2 で並べ替え) | 17 | インデックスで顧客 1 人分 (40 行) を取り、並べ替えはアプリ側 (P13) |
| | N4a / N4b 顧客 1 人の件数・合計 | 20 / 17 | パーティション内、またはインデックス等値の範囲での集約 |
| | N9a / N9b 主キー・パーティションで駆動する JOIN | 17 / 20 | 駆動側がキーで絞れ、相手表は主キーで引ける |
| | N12a / N12b キー 5 個の `IN` | 36 / 36 | 値ごとのパーティション / インデックス走査に分割 (P14) |
| | N5c 小さい表のインデックス等値 | 12 | 返す行が少ない |
| | N13a〜d、N14 1 行の INSERT / UPDATE / upsert / DELETE、インデックス列の更新 | 17〜26 | 主キー指定の書き込み |
| **条件付き** (3 文) | N5a / N5b 低カーディナリティ列 (5 種類) のインデックス等値 (表の 1/5) | 392 / 180 | 返す行 (8,000 行) に比例して伸びる |
| | N10a インデックスで駆動する JOIN (地域 → 注文) | 476 | 返す行 (4,000 行) に比例して伸びる |
| **取得不可** (10 文) | N6a / N6b 無索引列だけの条件 | — (走査を許すと 973 / 768) | キーでもインデックスでも絞れない |
| | N7a / N7b 全表の上位 10 件 | — (走査を許しても実行不可) | パーティションをまたぐ並べ替え |
| | N8a / N8b / N8c 全表の `GROUP BY` / `COUNT(*)` / `DISTINCT` | — (走査を許すと 1,028 / 820 / 1,178) | 全表を読む |
| | N10b 全表の JOIN + 集約 (地域別売上) | — (走査を許すと 5,239) | 全表を読む |
| | N11a 全表の `OFFSET` ページング | — (走査を許すと 1,164) | 全表を読んで並べる |
| | N15 非キー条件の一括 `UPDATE` | — (走査を許すと 721) | 対象行をキーで特定できない |

同じ文を Oracle に直接投げると、向くに入った文は 0.6〜3.4 ms、条件付きの文は 7〜9 ms、取得不可の文も 0.6〜4.8 ms で終わる (40,000 行、同じ回の計測)。取得不可の文は、Oracle では索引とブロック単位の読み取りで速く終わるが、Cassandra ではそもそも ScalarDB に実行させられない。

## 8. RDBMS のクエリのうち NoSQL (Cassandra) に向くもの

1. **最初の分かれ目は「キーで絞れるか」。** WHERE がパーティションキー (または主キー全体) を等値で指定していれば、追加の非キー条件、集約、クラスタリングキーの範囲、`LIMIT` があっても Cassandra でそのまま動き、表が大きくなっても応答時間は変わらない (3.4 の確認、7 章の「向く」)。
2. **並べ替えはクラスタリングキーの順だけ。** 「顧客ごとの最新 N 件」のように並べ替えの軸をクラスタリングキーにできる問いは、NoSQL 型のキー設計 (N2) がそのまま最速になる。RDB 型の表のまま (N3) でも、インデックスで取れる行が少なければアプリ側で並べ替えて同じ程度 (17 ms 対 14 ms) で済む。
3. **`IN` リストはキーごとの取得に分ける。** キー列の `IN` / `OR` は、ScalarDB SQL のままでは全パーティション走査になる (走査を許した場合で 40,000 行 711 ms)。値ごとの取得に分ければ 36 ms で、表サイズに依らない。変換ツールが自動で分ける (P14)。
4. **インデックスは「返す行が少ない」ときだけ向く。** 低カーディナリティ列 (状態区分など) のインデックス等値は、返す行に比例して遅くなる (表の 1/5 で 392 ms)。その列で頻繁に引くなら、その列をパーティションキーにした表を別に持つ。
5. **表全体を読む文は Cassandra では実行できない。** 全表の集計、無索引列だけの検索、全表の上位 N 件、`OFFSET` ページング、非キー条件の一括更新がこれにあたる。移行するには、集計値を書き込み時に更新する表を持つ、問いごとにキー設計した表を作る (Cassandra の一般的な設計)、分析系は ScalarDB Analytics に回す、のいずれかが要る。変換ツールを `--storage cassandra` で実行すると、これらが FULL_SCAN / NO_CROSS_PARTITION の変換不可として一覧になる。今回の Oracle 固有機能ケースでは 62 文中 47 文、前回のベンチでは 15 文中 6 文がこれにあたった。
6. **1 回あたりの点アクセスは RDBMS より遅い。** 単一ノードの Cassandra では、主キーでの読み取りが 15〜19 ms、1 行の書き込みが 17〜26 ms だった (Oracle 直接で 1〜3 ms)。Consensus Commit が Cassandra の軽量トランザクション (Paxos) を使うためと考えられる。NoSQL を選ぶ利点はノードを増やしたときのスループットと可用性で、1 リクエストの応答時間は短くならない。

## 9. この検証で分からないこと・注意点

- **水平スケールは測っていない。** Cassandra は単一ノード (レプリケーション係数 1) で、ノードを増やしたときのスループットや整合性レベルの影響は分からない。
- **同時実行・競合時の再試行は測っていない。** 単一クライアントの応答時間だけである。
- **JDBC バックエンドの走査は既定の `scan_fetch_size` (10) で測った** ため、全表走査の数値は Cassandra (1000) より 2〜3 倍不利である (3.2)。
- **N2 / N3 / N11b は反復によって 0 行または 10 行を返す。** データの作り方の都合で、顧客によっては条件に合う注文が無い。どの系統も同じデータなので比較は成り立つ。
- **実行計画の文 (N3、N12a、N12b など) は毎回同じキーで測った。** ベンチの反復ごとのキー変更は ScalarDB SQL の文にだけ効くため。
- **初回の実行で起きた 2 つの事象** は測り直した。1 つは、同じ実行の中で `emp` を列の違う定義で作り直したとき、ScalarDB Cluster ノードが古い表定義を使い続けた事象 (「column job does not exist」など)。ケースごとにノードを再起動するようハーネスを直した。もう 1 つは、PostgreSQL バックエンドの 40,000 行の計測の途中でクライアントとノードの gRPC 接続が切れた事象。原因は特定できず、同じ条件で測り直した。
- すべて同一ホストのコンテナで動かしたため、ネットワーク遅延はほぼゼロである。


## 10. 再現手順

```
# 環境 (ライセンス行を difftest/license.properties に置いたうえで)
cd difftest && ./make-cluster-conf.sh
docker compose --profile oracle up -d source-oracle backend-postgres
cd .. && (cd runtime-java && gradle installDist)

# PostgreSQL バックエンド
(cd difftest && docker compose stop scalardb-cluster-cassandra; docker compose --profile cluster up -d scalardb-cluster)
difftest/backend_compare.sh postgres out/cassandra-verify/pg

# Cassandra バックエンド (改修後: クロスパーティション走査なし、変換ツールは storage=cassandra)
(cd difftest && docker compose stop scalardb-cluster; docker compose --profile cassandra up -d)
difftest/backend_compare.sh cassandra out/cassandra-verify/after

# 改修前 (クロスパーティション走査あり、変換は PostgreSQL と同じ) を再現する場合は、Cassandra 用ノードの設定で
# cross_partition_scan.enabled / filtering.enabled を true に戻したうえで
.venv/bin/python difftest/bench.py --case difftest/cases/nosql-patterns.sql --rows 40000 --backend cassandra \
    --convert-storage jdbc --verify-rows 10000 --out out/cassandra-verify/nosql-before-40000

# 表の生成
.venv/bin/python difftest/backend_report.py --run "PostgreSQL=out/cassandra-verify/pg:postgres" \
    --run "Cassandra=out/cassandra-verify/after:cassandra" \
    --reference "Cassandra (走査あり)=out/cassandra-verify/main:cassandra" --judge Cassandra
```

| ファイル | 役割 |
|---|---|
| `difftest/docker-compose.yml` | `backend-cassandra`、`schema-loader-cassandra`、`scalardb-cluster-cassandra` (ポート 60054) を追加 |
| `difftest/conf/*cassandra*.properties` | Cassandra 用の ScalarDB Core / Schema Loader / Cluster ノード / SQL JDBC の設定 |
| `difftest/backends.py` | バックエンドごとの設定ファイルと Schema Loader の選択 |
| `difftest/run.py`、`difftest/bench.py` | `--backend postgres\|cassandra`、`--convert-storage jdbc\|cassandra` を追加 |
| `difftest/cases/nosql-patterns.sql` | NoSQL 適性ケース (データは `bench.py` の `nosql_dataset`) |
| `difftest/backend_compare.sh` | 1 バックエンド分の互換性テストとベンチを順に実行 |
| `difftest/backend_report.py` | 3 系統の結果から本書の表を生成 |
