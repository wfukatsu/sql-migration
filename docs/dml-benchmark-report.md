# DML テスト SQL の ScalarDB 変換とベンチマーク

作成日: 2026-09-15
関連文書: `skills/sql-transpile/examples/dml/`（テスト用 SQL）、`docs/bench-report.md`（性能測定の基準）、`docs/app-side-benchmark-comparison.md`（変換ツールの新旧比較）
説明資料: [Google スライド 26 枚](https://docs.google.com/presentation/d/1DQJZKhjteIoAxuSTsPqmW4cF15VovnDhT2nL-2TVn-c/edit)（生成元 `docs/slides/dml-benchmark-deck.py`）

## 結論

- **ScalarDB で実行できるのは 51 文中 31〜32 文。** 書き込み 33 文のうち 17〜18 文は ScalarDB SQL にできず、アプリ側で「読む → 計算する → キーを指定して書く」実装が要る。SELECT 17 文はすべて実行でき、うち 10〜11 文は実行計画（ScalarDB から取得して H2 で処理）になる
- **変換できた書き込みは速い。** ScalarDB 側の p50 の中央値は 3.3〜6.4 ms（COMMIT 込み、変換元 DB の 7〜20 倍）。キーで絞る読み取りも 4.5〜5.6 ms（6〜13 倍）
- **実行計画の読み取りは、読む行数で決まる。** 中央値 0.6 秒前後。3 表結合（S04）と反結合（S05）は当初 26〜28 秒かかり、その 9 割以上は H2 の処理だった（取得した表に索引が無く、結合が総当たりになる）。H2 に主キーと結合列の索引を作る改善で 1.5〜1.9 秒（14〜17 倍）になり、残りのほとんどは ScalarDB からの取得
- **変換ツールが OK / WARN と判定したのに、ScalarDB で失敗するか結果が変わる文があった。** Oracle 1 文、PostgreSQL 3 文、MySQL 5 文（ほかに実行計画の S11 が 3 方言とも失敗）。原因は PostgreSQL の型付き日付リテラル、MySQL の真偽値リテラルと照合順序、落ちる NULLS LAST、H2 の予約語で、いずれも変換ツールで直せる

---

## 1. 何を測ったか

`skills/sql-transpile/examples/dml/` の Oracle・PostgreSQL・MySQL 用テスト SQL（各 51 文、注文管理の 8 表）を ScalarDB SQL に変換し、変換元のデータベースに直接実行した場合と、ScalarDB Cluster で実行した場合の結果と応答時間を比べた。

| 項目 | 内容 |
|---|---|
| 比べた経路 | 変換元 DB に JDBC で直接実行（基準） / 変換後の ScalarDB SQL を ScalarDB JDBC で実行 / ScalarDB SQL に収まらない SELECT は実行計画（ScalarDB から取得 → H2 で元の SQL） |
| 測る文 | 変換結果が OK・WARN・PLANNED の文（各方言 31〜32 文）。ERROR の文は時間を測らず一覧にする |
| 書き込み（INSERT / UPDATE / DELETE / MERGE） | ファイルの準備データ（8 表・42 行）の上で実行。毎回の実行前に両方を準備データに戻す（時間に含めない）。時間は COMMIT を含む |
| 読み取り（SELECT） | 準備データに生成した行を足して実行（顧客 2,006、商品 206、注文 20,008、明細 約 50,000、在庫 2,008、監査ログ 10,002 行） |
| 回数 | ウォームアップ 3 回 + 15 回の p50。毎回、結果（書き込みは件数、読み取りは行の値）を変換元と突き合わせる |
| 環境 | Apple M3 Pro、Docker（VM 8 GiB）。Oracle Database 23ai Free、PostgreSQL 16、MySQL 8.4、ScalarDB Cluster 3.19.1（SERIALIZABLE、scan_fetch_size 10）+ PostgreSQL 16 バックエンド。単一クライアント |

ScalarDB 側だけ、`audit_log` の採番（IDENTITY / AUTO_INCREMENT）を外して変換した。ScalarDB に採番が無く、そのままでは表を作れないため。この結果、主キーを省く I07 は ScalarDB への変換で ERROR になる（テスト用 SQL の期待値では、表定義が分からず OK / WARN になる）。

---

## 2. 変換結果

テスト文 51 文の変換結果（`@expect-scalardb`、`tests/test_dml_examples.py` で確認）:

| 方言 | OK | WARN | PLANNED | ERROR |
|---|---|---|---|---|
| Oracle | 8 | 14 | 10 | 19 |
| PostgreSQL | 10 | 11 | 11 | 19 |
| MySQL | 11 | 11 | 11 | 18 |

| 種別 | Oracle | PostgreSQL | MySQL |
|---|---|---|---|
| INSERT（12 文） | OK 1・WARN 3・ERROR 8 | OK 3・WARN 2・ERROR 7 | OK 3・WARN 2・ERROR 7 |
| UPDATE（12 文） | OK 2・WARN 4・ERROR 6 | OK 3・WARN 3・ERROR 6 | OK 3・WARN 3・ERROR 6 |
| DELETE（9 文） | OK 3・WARN 2・ERROR 4 | OK 2・WARN 2・ERROR 5 | OK 3・WARN 2・ERROR 4 |
| SELECT（17 文） | OK 2・WARN 5・PLANNED 10 | OK 2・WARN 4・PLANNED 11 | OK 2・WARN 4・PLANNED 11 |
| SAVEPOINT（1 文） | ERROR | ERROR | ERROR |

- **書き込みの半分以上は ScalarDB SQL にできない。** 現在の値を使う更新（`qty = qty - 5`）、副問合せ・結合を使う更新と削除、`INSERT ... SELECT`、現在時刻、採番、列を参照する upsert が ERROR になる。いずれもアプリ側で「読んで計算して書く」処理に直す必要がある
- **SELECT はすべて実行できる。** 17 文中 10〜11 文は実行計画（ScalarDB から取得して H2 で実行）になる
- **方言で判定が分かれた文（8 文）:** Oracle の `INSERT ALL`（I03、Oracle だけ ERROR）、PostgreSQL の `RETURNING`（D09、PostgreSQL だけ ERROR）、行値の比較（S02）、`ILIKE`（S10）、`NULLS LAST`（S12）、BOOLEAN 列だけの条件（S16）など、方言固有の書き方による

---

## 3. ベンチマーク結果

### 3.1 方言ごとの集計

読み取りは H2 に索引を作った後の計測（3.4）。書き込みは変わらない。

| dialect | statements | timed | PASS | FAIL | not convertible | writes p50 ratio (median) | reads p50 ratio (median) |
|---|---|---|---|---|---|---|---|
| Oracle | 51 | 31 | 29 | 2 | 20 | 7.5x | 51.0x |
| PostgreSQL | 51 | 31 | 27 | 4 | 20 | 19.6x | 68.5x |
| MySQL | 51 | 32 | 26 | 6 | 19 | 6.7x | 50.0x |

### 3.2 文ごとの結果（変換元 p50 / ScalarDB p50 ms、倍率）

| id | statement | Oracle conversion | Oracle | PostgreSQL conversion | PostgreSQL | MySQL conversion | MySQL |
|---|---|---|---|---|---|---|---|
| I01 | 列リスト付きの 1 行 INSERT（主キーを含む） | OK | 0.6 / 5.4 (9.2x) | OK | FAIL | OK | FAIL |
| I02 | 列リストなしの INSERT（表定義の列順に依存する） | WARN | 0.5 / 3.7 (7.9x) | WARN | 0.3 / 10.3 (30.3x) | WARN | FAIL |
| I03 | 複数行の INSERT（Oracle は INSERT ALL、PostgreSQL・MySQL は VALUES を並べる） | ERROR | — | OK | 0.3 / 7.1 (21.0x) | OK | 0.9 / 11.5 (13.0x) |
| I04 | INSERT ... SELECT（別の表から行を写す） | ERROR | — | ERROR | — | ERROR | — |
| I05 | VALUES の中の DEFAULT（列の既定値を使う。ScalarDB には既定値が無い） | ERROR | — | ERROR | — | ERROR | — |
| I06 | 現在時刻を入れる（SYSTIMESTAMP / CURRENT_TIMESTAMP / NOW()） | ERROR | — | ERROR | — | ERROR | — |
| I07 | 採番列（IDENTITY / AUTO_INCREMENT）に任せて主キーを省く。表定義の変換が AUTO_INC で失敗するため、変換ツールは主キーの欠落を検出できない | ERROR | — | ERROR | — | ERROR | — |
| I08 | シーケンスで主キーを採番（MySQL はシーケンスが無いので採番表を更新する） | ERROR | — | ERROR | — | ERROR | — |
| I09 | あれば加算、無ければ挿入する upsert（列を参照する更新） | ERROR | — | ERROR | — | ERROR | — |
| I10 | あれば一部の列を上書き、無ければ挿入する upsert（UPSERT は全列を上書きする） | WARN | 0.5 / 5.0 (9.6x) | WARN | 0.4 / 7.2 (19.9x) | WARN | FAIL |
| I11 | 無いときだけ挿入する（Oracle は NOT EXISTS、PostgreSQL は ON CONFLICT DO NOTHING、MySQL は INSERT IGNORE） | ERROR | — | ERROR | — | ERROR | — |
| I12 | VALUES の中のスカラー副問合せ（別の表から単価を引く） | ERROR | — | ERROR | — | ERROR | — |
| U01 | 主キーを指定した 1 行の UPDATE | OK | 0.5 / 3.9 (8.2x) | OK | 0.3 / 8.4 (24.8x) | OK | 0.9 / 7.7 (8.4x) |
| U02 | 複合主キーを指定した UPDATE（タイムスタンプのリテラル） | WARN | 0.5 / 5.1 (10.8x) | OK | FAIL | OK | 0.9 / 8.4 (9.1x) |
| U03 | キー以外の条件で複数行を UPDATE（クロスパーティション走査になる） | WARN | 0.5 / 4.5 (9.9x) | WARN | 0.3 / 6.4 (19.3x) | WARN | 0.8 / 7.5 (9.1x) |
| U04 | 現在の値を使う UPDATE（在庫を減らす。読んで計算して書く必要がある） | ERROR | — | ERROR | — | ERROR | — |
| U05 | CASE で複数の列を条件付きで更新 | ERROR | — | ERROR | — | ERROR | — |
| U06 | SET の中の相関副問合せ（明細の合計で注文の合計を直す） | ERROR | — | ERROR | — | ERROR | — |
| U07 | 別の表を条件にした UPDATE（Oracle は EXISTS、PostgreSQL は UPDATE ... FROM、MySQL は UPDATE ... JOIN） | ERROR | — | ERROR | — | ERROR | — |
| U08 | NULL を設定し、IS NULL で絞る | WARN | 0.4 / 3.4 (7.7x) | WARN | 0.3 / 6.3 (19.8x) | WARN | 0.8 / 5.1 (6.4x) |
| U09 | 主キーの IN と BETWEEN を組み合わせた UPDATE | WARN | 0.4 / 3.2 (7.2x) | WARN | 0.3 / 7.0 (21.1x) | WARN | 0.8 / 7.2 (8.8x) |
| U10 | 日付の加算（Oracle は日数の足し算、PostgreSQL は INTERVAL、MySQL は DATE_ADD） | ERROR | — | ERROR | — | ERROR | — |
| U11 | 並べた先頭 1 行だけを UPDATE（Oracle は ROWNUM、PostgreSQL は副問合せの LIMIT、MySQL は ORDER BY ... LIMIT） | ERROR | — | ERROR | — | ERROR | — |
| U12 | セカンダリインデックスの列で絞る UPDATE | OK | 0.6 / 3.3 (5.3x) | OK | 0.3 / 5.4 (17.0x) | OK | 0.9 / 5.9 (6.9x) |
| D01 | 複合主キーを指定した 1 行の DELETE | OK | 0.4 / 2.8 (7.1x) | OK | 0.3 / 4.2 (12.8x) | OK | 0.8 / 4.3 (5.1x) |
| D02 | パーティションキーとクラスタリングキーの範囲で DELETE | OK | 0.4 / 3.1 (7.2x) | OK | 0.3 / 4.7 (14.5x) | OK | 0.8 / 4.6 (6.0x) |
| D03 | キー以外の列の IN で DELETE（クロスパーティション走査になる） | WARN | 0.4 / 2.7 (6.8x) | WARN | 0.3 / 4.5 (13.9x) | WARN | 0.8 / 4.5 (6.0x) |
| D04 | WHERE の無い DELETE（全行） | WARN | 0.5 / 2.4 (5.0x) | WARN | 0.4 / 3.6 (9.6x) | WARN | 0.9 / 4.3 (4.6x) |
| D05 | EXISTS 副問合せで絞る DELETE（取り消された注文の明細） | ERROR | — | ERROR | — | ERROR | — |
| D06 | NOT EXISTS で明細の無い注文を消す | ERROR | — | ERROR | — | ERROR | — |
| D07 | 別の表を条件にした DELETE（Oracle は IN 副問合せ、PostgreSQL は USING、MySQL は複数表の DELETE） | ERROR | — | ERROR | — | ERROR | — |
| D08 | 並べた先頭 1 行だけを DELETE（Oracle は ROWNUM、PostgreSQL は副問合せの LIMIT、MySQL は ORDER BY ... LIMIT） | ERROR | — | ERROR | — | ERROR | — |
| D09 | 消した行を返す DELETE（PostgreSQL は RETURNING。Oracle と MySQL の SQL には無いので主キーで消すだけ） | OK | 0.4 / 2.5 (6.2x) | ERROR | — | OK | 0.9 / 3.0 (3.3x) |
| S01 | パーティションキーの等値とクラスタリングキーの範囲 | OK | 0.5 / 23.8 (50.6x) | OK | 0.4 / 25.4 (60.5x) | OK | 0.7 / 22.6 (30.6x) |
| S02 | キーセットページング（行値の比較。Oracle は OR で書く） | WARN | 0.9 / 41.2 (45.8x) | PLANNED | 0.7 / 1314.8 (1933.5x) | PLANNED | 0.6 / 1247.3 (2188.3x) |
| S03 | OFFSET ページング | PLANNED | 2.4 / 639.0 (268.5x) | PLANNED | 1.7 / 529.1 (304.1x) | PLANNED | 2.8 / 572.9 (208.3x) |
| S04 | 3 表の結合と集約・HAVING | PLANNED | 8.1 / 1857.9 (230.5x) | PLANNED | 26.9 / 1725.0 (64.1x) | PLANNED | 31.6 / 1820.6 (57.5x) |
| S05 | LEFT JOIN と IS NULL による反結合（明細の無い注文） | PLANNED | 3.1 / 1517.3 (495.9x) | PLANNED | 5.0 / 1727.4 (346.2x) | PLANNED | 10.7 / 1643.6 (153.3x) |
| S06 | SELECT 句の相関スカラー副問合せ | PLANNED | 4.9 / 613.6 (125.5x) | PLANNED | 8.4 / 575.1 (68.5x) | PLANNED | 3.5 / 622.0 (178.7x) |
| S07 | グループごとの上位 1 件（ROW_NUMBER） | PLANNED | 8.3 / 575.7 (69.3x) | PLANNED | 6.2 / 613.9 (99.8x) | PLANNED | 12.4 / 585.0 (47.0x) |
| S08 | CASE を使った条件付き集約 | PLANNED | 6.4 / 618.0 (96.3x) | PLANNED | 4.5 / 590.9 (130.4x) | PLANNED | 12.1 / 651.8 (54.0x) |
| S09 | LIKE の ESCAPE（% を含むメールアドレス） | WARN | 0.3 / 6.5 (18.7x) | WARN | 0.5 / 5.7 (12.1x) | WARN | 0.7 / 5.8 (8.4x) |
| S10 | 大文字小文字を区別しない検索（Oracle は UPPER、PostgreSQL は ILIKE、MySQL は既定の照合順序） | PLANNED | 0.4 / 58.2 (145.5x) | WARN | 0.8 / 6.2 (7.5x) | WARN | FAIL |
| S11 | 月ごとに丸めて集計（Oracle は TRUNC、PostgreSQL は DATE_TRUNC、MySQL は DATE_FORMAT） | PLANNED | FAIL | PLANNED | FAIL | PLANNED | FAIL |
| S12 | NULL の並び位置を指定（MySQL には NULLS LAST が無いので IS NULL で並べる） | WARN | FAIL | WARN | FAIL | PLANNED | 0.6 / 10.0 (18.1x) |
| S13 | UNION ALL で 2 つの表の行を並べる | PLANNED | 1.2 / 18.4 (15.4x) | PLANNED | 0.6 / 21.9 (36.5x) | PLANNED | 0.8 / 21.8 (28.3x) |
| S14 | EXISTS による半結合（支払い済みの注文がある顧客） | PLANNED | 3.3 / 170.4 (51.0x) | PLANNED | 2.0 / 277.4 (138.7x) | PLANNED | 3.4 / 182.7 (53.1x) |
| S15 | FOR UPDATE で行をロックして読む | WARN | 0.6 / 5.0 (9.0x) | WARN | 0.4 / 5.0 (12.6x) | WARN | 0.5 / 4.0 (8.6x) |
| S16 | 真偽値の列で絞る（Oracle は NUMBER(1)、PostgreSQL は BOOLEAN、MySQL は TINYINT(1)） | WARN | 1.0 / 12.4 (12.2x) | PLANNED | 0.6 / 60.7 (94.8x) | WARN | FAIL |
| S17 | セカンダリインデックスで絞った集約 | OK | 0.3 / 5.3 (16.2x) | OK | 0.4 / 4.2 (10.7x) | OK | 0.4 / 7.3 (17.4x) |
| T01 | SAVEPOINT（ScalarDB にはセーブポイントが無い） | ERROR | — | ERROR | — | ERROR | — |

### 3.3 失敗した文

| dialect | id | conversion | detail |
|---|---|---|---|
| Oracle | S11 | PLANNED | JdbcSQLSyntaxErrorException: Syntax error in SQL statement "SELECT DATE_TRUNC('MONTH', order_date) AS [*]month, COUNT(*) AS n, SUM(total) AS amount FROM orders GROUP BY DATE_TRUNC('MONTH', order_date) |
| Oracle | S12 | WARN | row 0: Oracle (1081, 1502.99) vs ScalarDB (106, None) |
| PostgreSQL | I01 | OK | SQLSyntaxErrorException: Invalid query (INVALID_ARGUMENT: DB-SQL-10026: Syntax error. Line 1:135 no viable alternative at input 'INSERT INTO customers (customer_id, name, email, region, vip, created_a |
| PostgreSQL | U02 | OK | SQLSyntaxErrorException: Invalid query (INVALID_ARGUMENT: DB-SQL-10026: Syntax error. Line 1:40 no viable alternative at input 'UPDATE stock SET qty = 45, updated_at = TIMESTAMP') |
| PostgreSQL | S11 | PLANNED | JdbcSQLSyntaxErrorException: Syntax error in SQL statement "SELECT DATE_TRUNC('MONTH', order_date) AS [*]month, COUNT(*) AS n, SUM(total) AS amount FROM orders GROUP BY DATE_TRUNC('MONTH', order_date) |
| PostgreSQL | S12 | WARN | row 0: Oracle (1081, 1502.99) vs ScalarDB (106, None) |
| MySQL | I01 | OK | SQLSyntaxErrorException: Invalid query (INVALID_ARGUMENT: DB-SQL-10052: Unmatched column type. The type of the column vip should be INT, but a boolean value (BOOLEAN) is specified) |
| MySQL | I02 | WARN | SQLSyntaxErrorException: Invalid query (INVALID_ARGUMENT: DB-SQL-10052: Unmatched column type. The type of the column active should be INT, but a boolean value (BOOLEAN) is specified) |
| MySQL | I10 | WARN | SQLSyntaxErrorException: Invalid query (INVALID_ARGUMENT: DB-SQL-10052: Unmatched column type. The type of the column active should be INT, but a boolean value (BOOLEAN) is specified) |
| MySQL | S10 | WARN | result row count differs: Oracle 1 vs ScalarDB 0 |
| MySQL | S11 | PLANNED | JdbcSQLSyntaxErrorException: Syntax error in SQL statement "SELECT FORMATDATETIME(order_date, 'yyyy-MM-01') AS [*]month, COUNT(*) AS n, SUM(total) AS amount FROM orders GROUP BY FORMATDATETIME(order_d |
| MySQL | S16 | WARN | SQLSyntaxErrorException: Invalid query (INVALID_ARGUMENT: DB-SQL-10052: Unmatched column type. The type of the column vip should be INT, but a boolean value (BOOLEAN) is specified) |

### 3.4 H2 の索引による改善（実行計画の読み取り）

実行計画に、取得する表ごとの索引の列（主キーと、結合・相関・IN 副問合せの列）を `index_columns` として出力し、`runtime-java` の `Residual` が行を入れた後、問い合わせの前に一度だけ索引を作るようにした。索引を作るかはオプションで、既定はオフ（変換時の `--h2-indexes` で計画に `build_indexes: true` を入れるか、実行時に `residual-runner run --h2-indexes` / `bench_dml.py --h2-indexes` を指定する）。1 表だけの計画や小さな要求では構築の分だけ遅くなるため（`docs/dml-followup-research.md` 3 章）、大きな表を結合するバッチ処理などで使う。本節の「索引あり」はこのオプションを有効にした計測。索引を作る時間は H2 の処理に含む。同じ条件（注文 20,008 行、ウォームアップ 3 回 + 15 回）で読み取りを測り直した。

| dialect | id | statement | before p50 (ms) | after p50 (ms) | speed-up | before fetch + H2 (ms) | after fetch + H2 (ms) |
|---|---|---|---|---|---|---|---|
| Oracle | S04 | 3 表の結合と集約・HAVING | 26,300.0 | 1,857.9 | 14.2x | 2,545 + 26,522 | 1,520 + 153 |
| Oracle | S05 | LEFT JOIN と IS NULL による反結合（明細の | 26,163.4 | 1,517.3 | 17.2x | 1,994 + 22,938 | 1,596 + 96 |
| Oracle | S06 | SELECT 句の相関スカラー副問合せ | 1,854.6 | 613.6 | 3.0x | 591 + 1,142 | 478 + 66 |
| Oracle | S14 | EXISTS による半結合（支払い済みの注文がある顧客） | 338.6 | 170.4 | 2.0x | 253 + 173 | 421 + 30 |
| PostgreSQL | S04 | 3 表の結合と集約・HAVING | 26,090.9 | 1,725.0 | 15.1x | 1,914 + 25,205 | 1,514 + 181 |
| PostgreSQL | S05 | LEFT JOIN と IS NULL による反結合（明細の | 28,194.3 | 1,727.4 | 16.3x | 1,888 + 25,967 | 1,748 + 93 |
| PostgreSQL | S06 | SELECT 句の相関スカラー副問合せ | 1,946.3 | 575.1 | 3.4x | 517 + 1,409 | 626 + 62 |
| PostgreSQL | S14 | EXISTS による半結合（支払い済みの注文がある顧客） | 401.3 | 277.4 | 1.4x | 322 + 196 | 353 + 11 |
| MySQL | S04 | 3 表の結合と集約・HAVING | 27,210.8 | 1,820.6 | 14.9x | 1,828 + 27,709 | 1,558 + 239 |
| MySQL | S05 | LEFT JOIN と IS NULL による反結合（明細の | 27,335.1 | 1,643.6 | 16.6x | 1,867 + 25,468 | 1,543 + 94 |
| MySQL | S06 | SELECT 句の相関スカラー副問合せ | 1,888.8 | 622.0 | 3.0x | 706 + 1,274 | 614 + 38 |
| MySQL | S14 | EXISTS による半結合（支払い済みの注文がある顧客） | 345.1 | 182.7 | 1.9x | 178 + 189 | 163 + 26 |

- **3 表結合（S04）と反結合（S05）は 14〜17 倍速くなった。** 26〜28 秒が 1.5〜1.9 秒になり、H2 の処理は 23〜28 秒から 0.1〜0.2 秒に縮んだ
- **相関スカラー副問合せ（S06）は約 3 倍、半結合（S14）は 1.4〜2 倍。** 1.9 秒前後が 0.6 秒前後、0.3〜0.4 秒が 0.2〜0.3 秒になった
- **1 つの表を読む文（S03・S07・S08 など）は変わらない。** 索引を作る分（数十 ms）だけわずかに増えることがある
- **残る時間のほとんどは ScalarDB からの取得。** S04・S05 は約 67,000〜70,000 行を 1.5 秒前後で取得している。これ以上縮めるには、読む行数を減らす設計（集計表、キーで絞る取得）が要る

全文の比較は `.venv/bin/python difftest/bench_dml_compare.py`（改善前の結果は `out/dml-bench-before-h2index/`）。

---

## 4. 見つかった問題

### 4.1 実行計画の H2 が表の結合で遅い（S04・S05）→ 改善済み

3 表の結合（S04）と、LEFT JOIN による反結合（S05）は、ScalarDB 側が 26〜28 秒かかった。内訳は、ScalarDB からの取得（約 67,000〜70,000 行）が約 2 秒、H2 での処理が 23〜26 秒。

`runtime-java` の `Residual.load` は、取得した行を主キーもインデックスも無い H2 の表に入れている（`CREATE TABLE` だけ）。このため H2 の結合が入れ子ループになり、注文 2 万行 × 明細 5 万行を総当たりする。対策として、実行計画に索引の列を出力し、`Residual` が問い合わせの前に索引を作れるようにした（オプション、既定はオフ）。S04・S05 の H2 の処理は 0.1〜0.2 秒になり、全体で 14〜17 倍速くなった（3.4）。

### 4.2 H2 の予約語を列の別名に使うと実行計画が失敗する（S11）

`AS month` の `month` は H2 の予約語で、実行計画の H2 で構文エラーになった（3 方言とも）。変換ツールは H2 の予約語を検出していない。実行計画に回す文では、H2 の予約語の別名を引用符で囲む書き換えが要る。

### 4.3 NULLS LAST が落ちて並び順が変わる（S12）

`ORDER BY price DESC NULLS LAST` は、変換で `NULLS LAST` が落ち（WARN `NULLS`）、ScalarDB では NULL が先頭に来て結果が変わった。警告は出ているが、結果が変わる WARN なので、アプリ側で並べ替えるか実行計画に回す必要がある。

### 4.4 計測環境: 列の型を変えて表を作り直すと ScalarDB Cluster が失敗する

PostgreSQL と MySQL の書き込みは、1 回目の計測で ScalarDB 側がすべて `DB-CORE-30040 ... cached plan must not change result type` で失敗した。直前の方言の計測で同じ名前の表を別の列の型（INT と BOOLEAN）で作っており、ScalarDB Cluster がバックエンドの PostgreSQL に張ったままの接続に、古い表定義で準備した実行計画が残っていたため。

変換や ScalarDB SQL の問題ではない。ScalarDB Cluster を再起動してから書き込みを計測し直した（`difftest/bench_dml.py --restart-cluster`）。本番でも、表を削除して別の型で作り直したときは、ScalarDB Cluster のノードを再起動する必要がある。

### 4.5 PostgreSQL の DATE / TIMESTAMP リテラルがそのまま残る（I01・U02）

PostgreSQL の `DATE '2024-09-01'`・`TIMESTAMP '2024-09-01 10:00:00'` は、変換ツールがそのまま ScalarDB SQL に出力し（判定は OK）、ScalarDB で構文エラー（DB-SQL-10026）になった。Oracle の同じ書き方は文字列リテラルに書き換えているので動く。PostgreSQL の型付きリテラル（sqlglot では CAST）も、文字列リテラルに書き換える必要がある。

### 4.6 MySQL の真偽値リテラルと照合順序（I01・I02・I10・S16、S10）

MySQL の `TINYINT(1)` は ScalarDB では INT になるが、`TRUE` / `FALSE` はそのまま出力され、ScalarDB で型の不一致（DB-SQL-10052）になった（書き込み 3 文と S16）。列の型が INT のときは `1` / `0` に書き換える必要がある。

S10 の `name LIKE 'FRANK%'` は、MySQL では既定の照合順序が大文字小文字を区別しないので 1 行返るが、ScalarDB（PostgreSQL バックエンド）では区別するので 0 行になった。変換ツールは照合順序の差を警告していない。

---

## 5. 考察

1. **時間を決めるのは経路で、方言ではない。** ScalarDB 側の p50 は、書き込み 3〜6 ms、キーで絞る読み取り 4〜6 ms、実行計画 0.6 秒前後で、3 方言でほぼ同じだった。倍率が方言で違って見えるのは、主に変換元 DB の速さの違い（書き込みの中央値は PostgreSQL 0.33 ms、Oracle 0.46 ms、MySQL 0.85 ms）による
2. **移行の見積もりは、書き込みは実装の量、読み取りは読む行数で考える。** 書き込みは変換できない文が多いが、変換できれば数 ms で動く。読み取りは全部動くが、表を読む文は行数に比例して遅く、数万行を読む文は索引を作っても 1〜2 秒かかる
3. **H2 の索引で、結合の時間は取得の時間まで縮んだ。** S04・S05 は 26〜28 秒から 1.5〜1.9 秒になり、H2 の処理は 0.2 秒以下になった。残る時間は ScalarDB からの取得（数万行で 1.5 秒前後）で、これを減らすには集計表などで読む行数を減らす設計が要る
4. **変換結果の判定だけでは不十分で、実データでの実行確認が要る。** 型付きの日付リテラル（PostgreSQL）や真偽値リテラル（MySQL）は、変換ツールが OK と判定したのに ScalarDB で構文エラーや型エラーになった。これまでの SELECT 中心の検証では見えなかった、書き込み側の問題である
5. **計測の限界。** 単一クライアント、書き込みは 42 行の小さいデータ、各条件 1 回、バックエンドは PostgreSQL のみ。同時実行時のスループットと競合時の再試行は評価していない

---

## 6. 再現手順

```bash
cd difftest && ./make-cluster-conf.sh \
  && docker compose --profile cluster --profile oracle up -d source-oracle source-postgres backend-postgres scalardb-cluster && cd ..
docker run -d --name transpile-verify-mysql -e MYSQL_ROOT_PASSWORD=verify -e MYSQL_DATABASE=verify -p 13306:3306 mysql:8.4
(cd runtime-java && gradle installDist)
for d in oracle postgres mysql; do
  .venv/bin/python difftest/bench_dml.py --dialect $d --restart-cluster --orders 20000 --h2-indexes --out out/dml-bench
done
.venv/bin/python difftest/bench_dml_report.py out/dml-bench      # 方言をまたいだ集計表
.venv/bin/python difftest/bench_dml_compare.py out/dml-bench-before-h2index out/dml-bench  # 索引の前後の比較
```

変換結果だけなら DB は要らない:

```bash
.venv/bin/python -m scalardb_migrate.cli skills/sql-transpile/examples/dml/oracle.sql --source oracle \
  --out-dir out/dml-bench/convert --plan-dir out/dml-bench/convert/plans
```
