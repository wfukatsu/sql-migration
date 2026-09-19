# Oracle Database と ScalarDB 経由の互換性・性能比較

作成日: 2026-09-10（数値は 2026-09-19 に取り直した。レビュー #27 の修正後の `main`、Issue #28）
関連文書: `docs/test-report.md` (仕組みとテスト報告)、`docs/oracle-sql-report.md` (Oracle 固有 SQL の網羅調査)、`docs/app-side-processing-plan.md` (実装計画)

## 1. 何を測ったか

同じデータ、同じ SQL を 2 つの経路で実行し、**結果が一致するか (互換性)** と **応答時間の差 (性能)** を同時に測った。

| 経路 | 実行方法 |
|---|---|
| Oracle (基準) | 元の SQL を Oracle Database 23ai Free に Oracle JDBC Thin ドライバで実行 |
| ScalarDB SQL | 変換ツールが出力した ScalarDB SQL を ScalarDB JDBC ドライバ (Cluster) で実行 |
| plan (アプリ側処理) | ScalarDB SQL に収まらない文。1 トランザクション内で ScalarDB から行を取得し、インメモリ H2 (Oracle 互換モード) で元 SQL を実行 |

計測は 1 つの JVM (`residual-runner bench`) から行い、Oracle と ScalarDB の接続はどちらも計測前に確立して再利用する (コネクションプールを持つアプリケーションを想定)。Oracle と ScalarDB は反復ごとに交互に実行する。ウォームアップ 3 回のあとの 15 回の実測値から p50 を取り、計測の前に捨てる 1 回を回して ScalarDB Cluster のノードを温める。反復ごとに行数を記録し、最後の反復の結果集合を値まで突き合わせる。

2026-09-10 の計測は、文ごとに Oracle 側を最後まで回してから ScalarDB 側を回し、Oracle 側の fetch size も既定値のままだった（#27 の 32g で直した）。そのため、当時の数値と下の数値はそのまま比べられない。傾向（3 つの層、表サイズに対する伸び）は変わっていない。

主キー指定の文には反復ごとに異なる主キー値を与え、1 行だけを繰り返し読む形にならないようにしている。

### データセット

| テーブル | 行数 (基本ケース) | 構造 |
|---|---|---|
| `emp` | 20,000 (5,000 / 40,000 でも測定) | 主キー `empno`、`deptno` にセカンダリインデックス、`sal` は無索引、`comm` は 1/3 が NULL |
| `dept` | 40 | 主キー `deptno` |
| `bonus` | `emp` の 1/5 | 主キー `empno` |

`deptno` は 40 種類なので、インデックス等値検索は表の 1/40 (40,000 行で 1,000 行) を返す。

### 実行環境

| 項目 | 値 |
|---|---|
| ホスト | Apple M3 Pro / 36 GiB / macOS 15 (Darwin 25.6.0) |
| Oracle | Oracle Database 23ai Free (`gvenzl/oracle-free:23-slim-faststart`)、Docker、統計収集済み |
| ScalarDB | ScalarDB Cluster 3.19.1 (standalone、トライアルライセンス)、Consensus Commit / SERIALIZABLE |
| ScalarDB バックエンド | PostgreSQL 16 (Docker) |
| Docker | 28.3.2 (Apple Silicon 上のLinux VM) |
| JVM | OpenJDK 17 |

すべて同一ホストのコンテナで動くため、ネットワーク遅延はほぼゼロである。実運用では Oracle 側にもネットワーク往復が乗るので、**下表の倍率は ScalarDB 側に最も不利な条件での値**と読むべきである。

## 2. 互換性の結果

**15 文すべてで結果集合が Oracle と一致した** (値まで比較。更新系は影響行数で比較)。ScalarDB SQL に変換できた文が 10、アプリ側計画になった文が 5 である。

比較の過程で 2 件の不一致を検出し、いずれも修正した。

| 検出した不一致 | 原因 | 対処 |
|---|---|---|
| `ORDER BY` で NULL の位置が違う (`GROUP BY TO_CHAR(hiredate,'YYYY')` の NULL グループが Oracle では末尾、残余処理では先頭) | Oracle は昇順で NULLS LAST、H2 は NULLS FIRST。H2 の Oracle 互換モードはこの規則を再現しない | 残余 SQL を生成するとき、ソース方言の NULL 順序を `NULLS FIRST` / `NULLS LAST` として明示する (`decomposer.py`)。Oracle と PostgreSQL は昇順 NULLS LAST、MySQL は逆 |
| — | ScalarDB SQL 側の NULL 順序は Oracle と一致することを別途確認した (昇順で末尾、降順で先頭) | 対処不要 |

なお、これは PoC 実装側の不具合であって ScalarDB の非互換ではない。ScalarDB SQL 経路には元から差異が無かった。

## 3. 性能の結果

### 3.1 応答時間 (emp 40,000 行、p50)

| # | 文 | ScalarDB 経路 | 結果行 | 一致 | Oracle (ms) | ScalarDB (ms) | 倍率 | 取得行 |
|---|---|---|---|---|---|---|---|---|
| Q5 | 主キー 1 件検索 | ScalarDB SQL | 1 | PASS | 1.53 | 4.81 | 3.1 | — |
| Q6 | インデックス等値 (表の 1/40) | ScalarDB SQL | 1000 | PASS | 2.41 | 33.69 | 14.0 | — |
| Q7 | インデックス等値 + 先頭 10 行 | ScalarDB SQL | 10 | PASS | 0.72 | 4.82 | 6.7 | — |
| Q8 | 無索引列の範囲条件 | ScalarDB SQL | 443 | PASS | 1.73 | 20.95 | 12.1 | — |
| Q9 | 主キーの範囲条件 | ScalarDB SQL | 100 | PASS | 0.82 | 6.93 | 8.5 | — |
| Q10 | 全表集約 (`GROUP BY deptno`) | ScalarDB SQL | 40 | PASS | 3.03 | 1002.35 | 331 | — |
| Q11 | 主キー駆動の JOIN | ScalarDB SQL | 1 | PASS | 1.78 | 6.16 | 3.5 | — |
| Q12 | インデックス駆動の JOIN | ScalarDB SQL | 1000 | PASS | 2.46 | 31.75 | 12.9 | — |
| Q13 | 式射影 (`NVL` / 四則) + インデックス条件 | plan / P1 | 1000 | PASS | 2.55 | 36.46 | 14.3 | 1,000 |
| Q14 | 全表 `DISTINCT` | plan / P3 | 40 | PASS | 3.15 | 1053.26 | 334 | 40,000 |
| Q15 | ウィンドウ関数 + インデックス条件 | plan / P1 | 1000 | PASS | 2.46 | 32.14 | 13.1 | 1,000 |
| Q16 | `IN (サブクエリ)` | plan / P5 | 796 | PASS | 2.13 | 1166.43 | 548 | 40,796 |
| Q17 | 日付式での `GROUP BY` | plan / P1 | 9 | PASS | 3.84 | 1016.65 | 265 | 40,000 |
| Q18 | 主キー 1 行 `UPDATE` | ScalarDB SQL | 1 | PASS | 1.85 | 5.13 | 2.8 | — |
| Q19 | 1 行 upsert (`MERGE` → `UPSERT`) | ScalarDB SQL | 1 | PASS | 2.36 | 3.95 | 1.7 | — |

### 3.2 表サイズに対する伸び (ScalarDB p50、ms)

| # | 文 | 経路 | 5,000 行 | 20,000 行 | 40,000 行 |
|---|---|---|---|---|---|
| Q5 | 主キー 1 件検索 | ScalarDB SQL | 7.56 | 6.04 | 4.81 |
| Q7 | インデックス等値 + 先頭 10 行 | ScalarDB SQL | 6.34 | 4.30 | 4.82 |
| Q9 | 主キーの範囲条件 (100 行) | ScalarDB SQL | 8.60 | 6.34 | 6.93 |
| Q18 | 主キー 1 行 `UPDATE` | ScalarDB SQL | 6.77 | 7.30 | 5.13 |
| Q19 | 1 行 upsert | ScalarDB SQL | 5.34 | 4.18 | 3.95 |
| Q6 | インデックス等値 (表の 1/40) | ScalarDB SQL | 11.39 | 17.66 | 33.69 |
| Q12 | インデックス駆動の JOIN | ScalarDB SQL | 12.26 | 20.19 | 31.75 |
| Q13 | 式射影 + インデックス条件 | plan / P1 | 13.52 | 20.86 | 36.46 |
| Q15 | ウィンドウ関数 + インデックス条件 | plan / P1 | 10.75 | 19.54 | 32.14 |
| Q8 | 無索引列の範囲条件 | ScalarDB SQL | 10.26 | 11.38 | 20.95 |
| Q10 | 全表集約 | ScalarDB SQL | 102.36 | 425.02 | 1002.35 |
| Q14 | 全表 `DISTINCT` | plan / P3 | 112.89 | 590.97 | 1053.26 |
| Q16 | `IN (サブクエリ)` | plan / P5 | 118.54 | 493.87 | 1166.43 |
| Q17 | 日付式での `GROUP BY` | plan / P1 | 123.46 | 522.52 | 1016.65 |

結果行数が表サイズに依らない文 (Q5、Q7、Q9、Q18、Q19) は**表が 8 倍になっても応答時間が変わらない**。取得行数に比例する文は**行数に線形**で伸びる。二次的に悪化する挙動は残っていない。

### 3.3 plan 経路の内訳 (emp 40,000 行、最終反復)

| # | 取得行 | ScalarDB からの fetch (ms) | H2 での残余処理 (ms) |
|---|---|---|---|
| Q13 | 1,000 | 43.1 | 0.9 |
| Q15 | 1,000 | 30.4 | 1.2 |
| Q17 | 40,000 | 974.2 | 27.6 |
| Q14 | 40,000 | 1122.9 | 13.4 |
| Q16 | 40,796 | 1203.5 | 25.1 |

**アプリ側処理のコストはほぼ全部が ScalarDB からの行取得**で、H2 での残余 SQL 実行は 40,000 行でも 13〜28 ms しかかからない。ScalarDB SQL でそのまま実行できる Q10 (1,002 ms / 40,000 行) と、全行を取得して H2 で処理する Q14・Q16・Q17 (1.0〜1.2 秒 / 40,000 行) がほぼ同じ時間なのは、どちらも同じ全表走査を ScalarDB に課しているからである。つまり **H2 を挟むこと自体の追加コストは小さく、決定的なのは走査行数**である。

### 3.4 計測中に見つけて直した性能不具合

残余処理ランタイムが取得行を H2 に投入する際、複数スコープの重複除去のために全列をキーにした `MERGE` を使っていた。H2 は全列に索引を持たないので 1 行ごとに全表走査が発生し、**行数の二乗**で悪化していた (5,000 行 415 ms → 20,000 行 4,610 ms → 40,000 行 16,600 ms)。1 回の fetch は重複行を返さないため初回投入を `INSERT` に変え、同じ表を 2 回以上取得する場合だけ `MERGE` を使うようにした。

| 取得行 | 修正前 (ms) | 修正後 (ms) |
|---|---|---|
| 5,000 | 415 | 116 |
| 20,000 | 4,610 | 536 |
| 40,000 | 16,600 | 1,024 |

## 4. 読み取り方と移行時の指針

### 4.1 3 つの層に分かれる

| 分類 | 該当 | ScalarDB の応答 | Oracle 比 | 移行時の扱い |
|---|---|---|---|---|
| キー指定の点アクセス | Q5、Q7、Q9、Q11、Q18、Q19 | 4〜7 ms、表サイズに依らず一定 | 2〜9 倍 | そのまま移行できる。絶対値が小さく、実運用ではネットワーク往復に埋もれる |
| 索引で絞れる範囲アクセス | Q6、Q8、Q12、Q13、Q15 | 21〜36 ms / 1,000 行 | 12〜14 倍 | 移行できる。取得行数を絞る設計 (インデックス、`LIMIT`) が効く |
| 全表走査 | Q10、Q14、Q16、Q17 | 1.0〜1.2 秒 / 40,000 行 | 260〜550 倍 | オンライン処理では避ける。設計変更かバッチ化が必要 |

倍率の大きさは「ScalarDB が遅い」というより **1 行あたりの固定コストの差**である。ScalarDB は Consensus Commit のトランザクション層を通して 1 行ずつ読むため、40,000 行の走査に約 25 µs/行 かかる。Oracle はブロック単位で読んで表内で集約するため、同じ走査が 3 ms で終わる。行数が小さいうちは差が出ず、走査行数に比例して開く。

### 4.2 具体的な指針

1. **全表走査を前提にした SQL は移行前に設計を変える。** `GROUP BY` の集計、`DISTINCT`、相関のないサブクエリなど、表全体を読む文は 1 万行を超えたあたりから秒のオーダーになる。集計値を別テーブルに持つ、パーティションキーで絞る、といった変更が要る。
2. **アプリ側処理 (plan) を使うかどうかは、ScalarDB SQL に収まるかどうかで決めてよい。** 取得行数が同じなら plan 経路と ScalarDB SQL 経路の速度差はほとんどない (全表走査で Q10 が 1,002 ms、Q14・Q17 が 1,017〜1,053 ms)。H2 を挟むコストは無視できる。
3. **plan 経路では取得行数の上限 (ガードレール) を必ず設定する。** 既定は 1 テーブルあたり 10,000 行。上限を超えると `RowLimitExceededException` で失敗する。全表を H2 に載せる計画は、行数が読めない本番表では危険である。
4. **plan 経路の fetch は、既定の設定のままだと ScalarDB SQL 経由 (`--fetcher jdbc`) の方が Core API 経由 (`--fetcher core`) より速い。取得単位を上げると逆転する。** 40,000 行の同一データで比較すると、全表取得で 993 ms 対 2,236 ms、索引で 1,000 行取得で 33 ms 対 65 ms と、Core API 経路は約 2 倍かかった。2026-09-10 の計測では差は 26〜38% だった。

**差の原因は走査の取得単位 (`scalar.db.scan_fetch_size`、既定 10) で、コードの退行ではない。** Core API 経路はホストの JVM から Docker のポート転送越しにバックエンドの PostgreSQL を読む。既定値のままだと 40,000 行の取得は 4,000 往復になり、その往復がコンテナの中どうしで話す Cluster ノードより遅い分だけ差が開く。Core API 経路のクライアント設定に `scalar.db.scan_fetch_size=1000` を足して同じデータで測ると、全表取得は 2,236 ms → 309 ms、索引で 1,000 行取得は 65 ms → 25 ms になり、既定値のままの ScalarDB SQL 経路 (993 ms、33 ms) より速くなった。アプリケーションとバックエンドの間に往復の遅延がある構成では、まず `scan_fetch_size` を上げる (Cluster ノード側も同じで、1000 にすると 2〜3 倍速くなる。`docs/cassandra-verification-report.md` 3.2)。下の表は両経路とも既定値 (10) での値。

| # | 取得行 | core、`scan_fetch_size` 10 (ms) | core、1000 (ms) |
|---|---|---|---|
| Q13 | 1,000 | 65.45 | 24.85 |
| Q15 | 1,000 | 61.15 | 13.85 |
| Q14 | 40,000 | 2235.92 | 309.32 |
| Q16 | 40,796 | 2256.65 | 292.53 |
| Q17 | 40,000 | 2211.52 | 292.26 |

Core API 経路はライセンス不要という利点があるので、その差を許容できるかで選ぶ。

| # | 取得行 | jdbc fetch 経路 (ms) | core fetch 経路 (ms) |
|---|---|---|---|
| Q13 | 1,000 | 32.93 | 65.45 |
| Q15 | 1,000 | 30.35 | 61.15 |
| Q14 | 40,000 | 992.56 | 2235.92 |
| Q16 | 40,796 | 1114.16 | 2256.65 |
| Q17 | 40,000 | 1074.12 | 2211.52 |

5. **書き込みは差が小さい。** 1 行 `UPDATE` が 5.1 ms (Oracle 1.9 ms)、1 行 upsert が 4.0 ms (Oracle 2.4 ms)。Consensus Commit のコミットコストを含んでこの水準である。

### 4.3 この測定で分からないこと

- **同時実行性能**: 単一クライアント・単一スレッドの応答時間だけを測った。スループット、競合時の再試行、Consensus Commit の衝突は未測定である。
- **本番規模**: 最大 40,000 行である。100 万行以上での挙動、特に全表走査の実用限界は別途測る必要がある。
- **ネットワーク**: すべて同一ホスト上のコンテナである。実運用の Oracle は多くの場合ネットワーク越しで、点アクセスの倍率は今回より小さくなる。
- **ストレージ**: ScalarDB バックエンドは PostgreSQL 16。Cassandra や DynamoDB では走査特性が変わる。
- **Oracle 側のキャッシュ**: バッファキャッシュが暖まった状態での値である。ScalarDB 側も同条件だが、キャッシュの効き方は両者で異なる。

### 4.4 再現しなかった事象 (記録)

一連の計測のあと、Oracle 方言の差分テスト (`difftest/run.py … --fetcher jdbc`) が 17 文すべて FAIL する実行が 1 回だけ発生した。同じコマンドを続けて 6 回 (ベンチ実行直後に走らせる順序を含む) 試したがすべて 17/17 PASS で、原因は特定できていない。ベンチが移行元 Oracle と ScalarDB の両方に大量データを投入した直後だったため、テーブル再作成と計測の間の状態が絡んだ可能性がある。単体テスト 68 件、Oracle 固有機能の網羅調査 (51 PASS / 9 FAIL、既存レポートと完全一致)、PostgreSQL 方言 15/15 はいずれも通っているため、変換ツール側の退行ではない。

## 5. 再現手順

```
# 前提: ScalarDB Cluster (ライセンス)、Oracle、バックエンド PostgreSQL が起動していること
cd difftest && ./make-cluster-conf.sh && docker compose --profile cluster --profile oracle up -d && cd ..
cd runtime-java && gradle installDist && cd ..

# 20,000 行、15 反復 (データ投入からやり直す)
.venv/bin/python difftest/bench.py --rows 20000 --iterations 15 --fetcher jdbc --out out/bench-jdbc

# 投入済みデータで経路だけ変える
.venv/bin/python difftest/bench.py --rows 20000 --iterations 15 --fetcher core --skip-setup --out out/bench-core

# 表サイズを変えて 3 通り測り、比較表を出す
for n in 5000 20000 40000; do .venv/bin/python difftest/bench.py --rows $n --iterations 15 --fetcher jdbc --out out/bench-$n; done
.venv/bin/python difftest/bench_report.py out/bench-5000 out/bench-20000 out/bench-40000
```

計測対象の SQL は `difftest/cases/bench.sql` にあり、`-- @bench:` 注釈を付けた文が測定される。出力は `out/<name>/bench.json` (全反復の生データを含む) と `out/<name>/bench.md`。

| ファイル | 役割 |
|---|---|
| `difftest/cases/bench.sql` | 計測対象の Oracle SQL |
| `difftest/bench.py` | データ生成、Oracle と ScalarDB への投入、変換、計測の起動、結果比較 |
| `runtime-java/.../Bench.java` | 1 JVM から Oracle と ScalarDB の両方を計測する実行部 |
| `difftest/bench_report.py` | 複数回の測定結果から比較表を生成 |
