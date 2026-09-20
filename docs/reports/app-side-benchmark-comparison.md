# 変換ツールの新旧比較ベンチマーク（アプリ側分析の導入前後）

作成日: 2026-09-15
関連文書: `docs/reports/bench-report.md`（性能測定の方法と基準値）、`docs/examples/area-sales-analysis-scalardb-conversion.md`（エリア別月次売上分析の書き換え）
説明資料: [Google スライド 21 枚](https://docs.google.com/presentation/d/1mAlk25_U5rL5ql61EcUFMYEp_UXx5gOQPjIkpRyARKE/edit)（生成元 `docs/slides/app-side-benchmark-deck.py`）

## 結論

- **WITH の中の日付範囲を取得に押し下げたことで、該当する文が 8〜12 倍速くなった。** 表全体 20,018 行を読んでいたものが、範囲内の 1,409 行・2,430 行だけを読むようになった（p50 566 ms → 47 ms、567 ms → 69 ms）
- **旧方式で実行時に失敗していた文は、新方式では変換時点で「アプリ側で実装する」と判定されるようになった。** `ROLLUP` と、エリア別月次売上分析（`CONNECT BY`）は、旧方式では実行計画が作られ H2 でエラーになっていた
- **エリア別月次売上分析をアプリ側 Java（新しい補助クラス）で実行し、Oracle と 2,400 行すべて値まで一致した。** 実データの Oracle と突き合わせたのはこれが初めて。応答時間は p50 814 ms（Oracle 14 ms の 58 倍）で、ほぼすべてが 29,426 行の取得にかかっている
- **既存のベンチマーク 15 文は、変換結果が新旧で同じで、応答時間の差は測定のばらつきの範囲だった。** 読み取り専用トランザクション（Core API 経由の取得）の効果は、今回の 1 回ずつの測定では見分けられなかった（計画の文で新/旧 0.94〜0.99）

---

## 1. 何を比べたか

| 方式 | 内容 |
|---|---|
| 旧方式 | コミット `2825858`（アプリ側分析の導入前）の変換ツール・実行時ライブラリ・ベンチマークハーネス |
| 新方式 | `main`（`7f28da6`）に、ベンチマークハーネスへアプリ側 Java の実行経路を足したもの（`difftest/bench.py` の `@appside` / `@fetch`、`Bench.java` の `appside` 経路） |

同じ ScalarDB Cluster と Oracle、同じデータに対して、次の順に実行した。

1. **変換:** 3 つのケースファイルを新旧の変換ツールで変換し、結果を比べる
2. **ベンチマーク:** ケースごとに新方式でデータを 1 回投入し、旧方式 → 新方式の順に同じ条件で測る

### ケースファイル

| ケース | データ | 内容 |
|---|---|---|
| `difftest/cases/bench.sql` | emp 20,000 行、dept 40 行、bonus 4,000 行 | 既存の 15 文（`docs/reports/bench-report.md` と同じ） |
| `difftest/cases/bench-appside.sql`（新規） | 同上 | 新方式で扱いが変わる文: WITH の中の日付範囲 2 文、`GROUP BY ROLLUP` 1 文 |
| `difftest/cases/bench-area-sales.sql`（新規） | 組織 214 行（本部 2・エリア 10・店舗 200 ほか）、売上 40,000 行（2026 年分は 29,426 行） | エリア別月次売上分析。新方式ではアプリ側 Java（`com.scalar.migrate.examples.AreaSalesReport`）で実行する |

### 実行環境と測定条件

| 項目 | 値 |
|---|---|
| ホスト | Apple M3 Pro / 36 GiB / macOS（Darwin 25.6.0）、Docker 28.3.2（VM 8 GiB、11 CPU） |
| Oracle | Oracle Database 23ai Free（Docker） |
| ScalarDB | ScalarDB Cluster 3.19.1（standalone、トライアルライセンス）、Consensus Commit / SERIALIZABLE、`scan_fetch_size` は既定値 10 |
| バックエンド | PostgreSQL 16（Docker） |
| 測定 | 単一クライアント・単一スレッド、ウォームアップ 3 回のあとの 15 回の p50。各回で Oracle と結果を突き合わせる（先頭 5,000 行は値まで） |
| 実行計画の取得 | `--fetcher jdbc`（ScalarDB SQL）と `--fetcher core`（Core API）の両方を測った。エリア別月次売上分析は jdbc のみ |

---

## 2. 変換結果

| ケース | 旧方式 | 新方式 |
|---|---|---|
| bench.sql（15 文 + DDL 4 文） | OK 8 / WARN 6 / PLANNED 5 | 同じ |
| bench-appside.sql（3 文 + DDL 4 文） | OK 3 / WARN 1 / PLANNED 3 | OK 3 / WARN 1 / PLANNED 2 / ERROR 1 |
| bench-area-sales.sql（1 文 + DDL 2 文） | OK 2 / PLANNED 1 | OK 2 / ERROR 1 |

変わった文:

| 文 | 旧方式 | 新方式 |
|---|---|---|
| WITH の中の日付範囲 + ウィンドウ関数 | PLANNED。取得 `SELECT empno, sal, deptno, hiredate FROM emp`（条件なし） | PLANNED。取得 `... FROM emp WHERE hiredate >= '2020-06-01' AND hiredate < '2021-01-01'` |
| WITH の中の日付範囲 + 月次集計 | PLANNED。取得は条件なし | PLANNED。取得 `... WHERE hiredate >= '2020-01-01' AND hiredate < '2021-01-01'` |
| `GROUP BY ROLLUP` | PLANNED（H2 で実行する計画） | ERROR `RESIDUAL_H2`、`GROUP` |
| エリア別月次売上分析 | PLANNED（両表を全件取得して H2 で実行する計画） | ERROR `RESIDUAL_H2`、`CTE`、`HIERARCHICAL`、`WINDOW`、`PROJECTION`、`GROUP` |

旧方式は `DATE '...'` をリテラルとして扱えず、WITH の中の条件を取得に押し下げていなかった。

---

## 3. 性能の比較

### 3.1 WITH の中の日付範囲（押し下げの効果）

| 文 | 取得 | 旧: 取得行数 | 旧: ScalarDB p50 | 新: 取得行数 | 新: ScalarDB p50 | 新/旧 | Oracle p50 |
|---|---|---|---|---|---|---|---|
| 日付範囲 + ウィンドウ関数（emp の約 7 %） | jdbc | 20,018 | 566.4 ms | 1,409 | 46.9 ms | 0.08 | 4.8 ms |
| 日付範囲 + 月次集計（emp の約 12 %） | jdbc | 20,018 | 566.9 ms | 2,430 | 69.2 ms | 0.12 | 2.1 ms |
| 日付範囲 + ウィンドウ関数 | core | 20,018 | 641.9 ms | 1,409 | 62.4 ms | 0.10 | 4.4 ms |
| 日付範囲 + 月次集計 | core | 20,018 | 625.3 ms | 2,430 | 98.1 ms | 0.16 | 2.2 ms |

4 つとも Oracle と結果が一致した（PASS）。時間はほぼ取得の時間で、H2 での残りの処理は 2〜15 ms だった。取得する行数に比例して短くなっており、`docs/reports/bench-report.md` の「コストは読む行数で決まる」という結果と合う。

### 3.2 H2 で実行できない文

| 文 | 旧方式 | 新方式 |
|---|---|---|
| `GROUP BY ROLLUP`（jdbc・core とも） | FAIL: `Function "ROLLUP" not found` | 変換時に NOT_CONVERTIBLE。理由とアプリ側での対応（集約レベルごとの UNION ALL）を報告 |
| エリア別月次売上分析 | FAIL: `Function "SYS_CONNECT_BY_PATH" not found` | アプリ側 Java で PASS（下表） |

### 3.3 エリア別月次売上分析（アプリ側 Java）

| 項目 | 値 |
|---|---|
| 結果 | **PASS**。2,400 行すべてを Oracle と値まで比較して一致 |
| Oracle p50 | 14.0 ms |
| 新方式 p50 / p95 | 813.6 ms / 1,000.9 ms（Oracle の 58 倍） |
| 内訳（最終回） | 取得 637.1 ms（組織 214 行 + 売上 29,426 行、1 トランザクション）、Java での処理 36.5 ms |
| 取得 1 行あたり | 約 22 µs |

- 時間の 95 % 以上は ScalarDB からの取得で、階層の展開・月次集計・分析関数・並べ替えは 40 ms 未満だった
- 変換ツールのコスト見積もり（`appside.estimate_cost`: 1 行 25 µs、SERIALIZABLE で 2 倍）で計算すると 29,426 行で約 1.5 秒になる。実測は取得とコミットを合わせて約 0.8 秒で、見積もりは約 2 倍多めだった。見積もりは上限側の目安として扱う
- 性能を上げるには、`docs/examples/area-sales-analysis-performance.md` のとおり、読む行数を減らす設計（月次集計表など）が要る

### 3.4 既存の 15 文（変換結果が同じもの）

変換ツールが出す SQL と計画は新旧で同じで、変わったのは Core API 経由の取得が読み取り専用トランザクションになった点だけ。

| 経路 | 文 | 新/旧（p50 の比） |
|---|---|---|
| jdbc: ScalarDB SQL（10 文） | 主キー・インデックス・結合・更新 | 0.44〜1.17 |
| jdbc: 実行計画（5 文） | 式・DISTINCT・ウィンドウ関数・IN 副問合せ・日付の GROUP BY | 0.88〜1.06 |
| core: ScalarDB SQL（10 文） | 同上（この経路は取得方式の影響を受けない） | 0.78〜1.20 |
| core: 実行計画（5 文、読み取り専用トランザクション） | 同上 | 0.94〜0.99 |

- 30 組すべて Oracle と一致した（PASS）
- jdbc の ScalarDB SQL の文で新方式が速く見える（主キー検索 13.1 ms → 5.7 ms など）が、この経路のコードは変わっていない。旧方式をデータ投入の直後に測った順序の影響（キャッシュや JIT の暖まり方）と考えられる
- core の実行計画の 5 文は一貫して 1〜6 % 短かったが、同じコードの ScalarDB SQL の文でも ±20 % ばらついており、読み取り専用トランザクションの効果とは言い切れない。確かめるには、順序を入れ替えて複数回測る必要がある

全 30 組の数値は `out/bench-compare/` の `bench.json` と、下の再現手順で出る比較表にある。

---

## 4. 注意点

1. **各条件 1 回ずつの測定。** 旧方式 → 新方式の順で測ったため、順序の影響が残る。差が 20 % 以内の結果は、改善・悪化のどちらとも判断しない
2. **書き込みを含む。** bench.sql の UPDATE と MERGE がデータを変えるため、旧方式の 1 回目と後の回で行数がわずかに違う（取得 20,001 行と 20,018 行）
3. **1 台の PC 上のコンテナで、ネットワーク遅延はほぼゼロ。** 本番の値ではなく、新旧の比較として読む
4. **新方式のアプリ側経路は、ハーネスに足した部分を含む。** 旧方式にはこの経路が無いため、エリア別月次売上分析は「旧方式では失敗、新方式では動いた」という比較になる

---

## 5. 再現手順

```bash
cd difftest && ./make-cluster-conf.sh \
  && docker compose --profile cluster --profile oracle up -d source-oracle backend-postgres scalardb-cluster && cd ..
difftest/bench_compare_versions.sh 2825858 out/bench-compare     # 変換 + ベンチマーク（約 6 分）
.venv/bin/python difftest/bench_compare_report.py out/bench-compare  # 新旧の比較表
```

`bench_compare_versions.sh` は、旧コミットを `out/old-<commit>` に git worktree として展開してビルドし、変換結果を `out/bench-compare/convert-{old,new}/`、ベンチマーク結果を `out/bench-compare/{old,new}-<ケース>-<取得方式>/` に書く。
