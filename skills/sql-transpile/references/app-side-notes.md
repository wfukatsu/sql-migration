# アプリ側で処理するときの注意と書き換え

ScalarDB SQL でも実行計画（H2）でも動かない読み取り文を、アプリケーション（Java）で書き換えるとき、または H2 で動く SQL に書き換えるときに読む。レポートの `APP_SEMANTICS` は、下の「結果を変えないための注意」の表の行を指している。指摘コードの意味は `scalardb-grammar.md` にある。

---

## 結果を変えないための注意

移行元のデータベースの動きを、Java でもそのとおりに再現する。補助クラスは `runtime-java` の `com.scalar.migrate.appside` パッケージにある（`Windows`・`OracleNumbers`・`OracleOrdering`・`OracleDates`・`Hierarchy`）。

| 構文 | 移行元の動き | Java で起きがちな誤り | 補助クラス |
|---|---|---|---|
| `LAG` / `LEAD` | パーティション内の 1 つ前・後の**行**。行の無い期間（売上の無い月）は飛ばす | 暦の前の期間と比べてしまう | `Windows.lag` / `Windows.lead` |
| 除算 | 0 で割ると Oracle は `ORA-01476` で文全体が失敗、PostgreSQL もエラー、MySQL は NULL（警告つき）。PostgreSQL の整数どうしは切り捨て | 黙って NULL や無限大にする | `OracleNumbers.divide`（Oracle と同じく例外を投げる） |
| `ROUND` | NUMBER / NUMERIC は 0 から遠いほうへ丸める（-2.5 → -3） | `HALF_EVEN` や `double` で丸める。金額を `double` で持つ | `OracleNumbers.round`（`RoundingMode.HALF_UP`、`BigDecimal`） |
| `SUM` / `AVG` / `MIN` / `MAX` | NULL を除く。全部 NULL なら NULL。`COUNT(col)` は NULL でない値だけを数える | 0 を返す、NULL を 0 として数える | `OracleNumbers.sum` / `avg`、`Windows.movingAverage` |
| `RANK` / `DENSE_RANK` / `ROW_NUMBER` | 同じ値は同じ順位。NULL どうしは同じ値として扱う。`ROW_NUMBER` の同順位の順は決まっていない | 同値でも順位を進める | `Windows.rank` / `Windows.denseRank` / `Windows.rowNumber` |
| `ORDER BY`（NULL） | Oracle と PostgreSQL は ASC で NULL が最後、DESC で最初。MySQL は逆。`OVER (ORDER BY ...)` の中も同じ | Java の比較器で NULL を扱わない | `OracleOrdering.asc` / `desc` |
| `ORDER BY`（文字列） | Oracle は `NLS_SORT` に従う。`BINARY` ならコードポイント順（AL32UTF8 のバイト順）。`JAPANESE_M` などの言語順は Collator が要る。PostgreSQL は列の照合順序、MySQL は照合順序（既定は大文字小文字を区別しない） | `String.compareTo`（UTF-16 順）で「𠮷」などのサロゲートペアの順が変わる | `OracleOrdering.BINARY` |
| `CONNECT BY` | `LEVEL` は START WITH の行が 1。循環すると `NOCYCLE` が無ければ `ORA-01436` | 深さを 0 から数える、循環で無限ループ | `Hierarchy.connectBy` |
| `SYS_CONNECT_BY_PATH` | 値の前に毎回区切り文字を付ける（先頭にも付く）。値が区切り文字を含むと `ORA-30004` | 先頭の区切り文字を落とす | `Hierarchy.connectBy`（経路つき） |
| `ADD_MONTHS` | 月末は月末にそろえる（2025-02-28 の 12 か月前は 2024-02-29） | `LocalDate.plusMonths` のまま使う | `OracleDates.addMonths` |
| `TO_CHAR` / `DATE_FORMAT`（日付） | セッションのタイムゾーンと日付言語で書式化 | JVM のタイムゾーン・ロケールで書式化 | `OracleDates.yearMonth`（`YYYY-MM`） |
| `SYSDATE` / `CURRENT_TIMESTAMP` | データベースサーバーの時計とタイムゾーン | JVM の時刻を使う | — |
| 空文字列 | Oracle は `''` を NULL として扱う | `""` と NULL を区別する | — |

---

## H2 で実行できない構文の書き換え

`RESIDUAL_H2` の文は、アプリで実装する代わりに、H2 が実行できる SQL に書き換えて実行計画にする方法もある。

| 構文 | 書き換え |
|---|---|
| `CONNECT BY` / `SYS_CONNECT_BY_PATH` | 再帰 WITH。経路は再帰部分で文字列を連結する（アプリで木をたどってもよい） |
| `ROLLUP` / `CUBE` / `GROUPING SETS` | 集約のレベルごとの SELECT を `UNION ALL` |
| `PIVOT` | `SUM(CASE WHEN job = 'A' THEN sal END)` のような条件つき集約 |
| `UNPIVOT` | 列ごとの SELECT を `UNION ALL` |
| `KEEP (DENSE_RANK FIRST ...)` | `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...) = 1` の行を選ぶ |
| `FULL OUTER JOIN` | LEFT JOIN と RIGHT JOIN の結果を UNION する。または 2 つの取得をアプリで突き合わせる |
| `LATERAL` / `CROSS APPLY` | 外側の行ごとに内側の問合せをアプリで実行する |
| `SAMPLE` | H2 に無い。Oracle でも結果は無作為なので、アプリで抜き出す |
| 再帰 WITH の `SEARCH DEPTH / BREADTH FIRST` | 再帰の順序をアプリで決める |
| `JSON_TABLE` | JSON の列を読み、アプリで展開する |

書き換えた SQL はもう一度 `transpile.py --target scalardb --plan-dir` に通し、PLANNED になることを確かめる。
