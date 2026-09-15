# アプリ側で処理するときの注意と補助クラス

ScalarDB SQL でも実行計画（H2）でも動かない読み取り文を、アプリケーション（Java）で書き換えるときの注意。レポートの `APP_SEMANTICS` はこの表の該当行を指している。

Oracle の「エリア別・店舗別の月次売上分析」を書き換えたときに実際に問題になった点をもとにしている（`docs/area-sales-analysis-scalardb-conversion.md`）。

---

## 結果を変えないための注意

| 構文 | Oracle の動き | Java で起きがちな誤り | 補助クラス |
|---|---|---|---|
| `LAG` / `LEAD` | パーティション内の 1 つ前・後の**行**。売上の無い月は飛ばす | 暦の前月と比べてしまう | `Windows.lag` / `Windows.lead` |
| 除算 | 0 で割ると `ORA-01476` で文全体が失敗。PostgreSQL もエラー、MySQL は NULL | 黙って NULL や無限大にする | `OracleNumbers.divide` |
| `ROUND` | 0 から遠いほうへ丸める（-2.5 → -3） | `HALF_EVEN` や `double` で丸める | `OracleNumbers.round` |
| `SUM` / `AVG` / `MIN` / `MAX` | NULL を除く。全部 NULL なら NULL | 0 を返す、NULL を 0 として数える | `OracleNumbers.sum` / `avg`、`Windows.movingAverage` |
| `RANK` / `DENSE_RANK` | 同じ値は同じ順位。NULL どうしは同じ値として扱う | 同値でも順位を進める | `Windows.rank` / `Windows.denseRank` |
| `ORDER BY`（NULL） | ASC で NULL は最後、DESC で最初（MySQL は逆） | Java の比較器で NULL を扱わない | `OracleOrdering.asc` / `desc` |
| `ORDER BY`（文字列） | `NLS_SORT=BINARY` ならコードポイント順（AL32UTF8 のバイト順） | `String.compareTo`（UTF-16 順）で「𠮷」などのサロゲートペアの順が変わる | `OracleOrdering.BINARY` |
| `CONNECT BY` | `LEVEL` は START WITH の行が 1。循環すると `ORA-01436` | 深さの数え方を 0 から始める、循環で無限ループ | `Hierarchy.connectBy` |
| `SYS_CONNECT_BY_PATH` | 値の前に毎回区切り文字を付ける（先頭にも付く）。値が区切り文字を含むと `ORA-30004` | 先頭の区切り文字を落とす | `Hierarchy.connectBy`（path 付き） |
| `ADD_MONTHS` | 月末は月末に揃える（2025-02-28 の 12 か月前は 2024-02-29） | `LocalDate.plusMonths` のまま使う | `OracleDates.addMonths` |
| `TO_CHAR`（日付） | セッションのタイムゾーンと日付言語で書式化 | JVM のタイムゾーン・ロケールで書式化 | `OracleDates.yearMonth`（`YYYY-MM`） |
| `SYSDATE` | DB サーバーの時計とタイムゾーン | JVM の時刻を使う | — |
| 空文字列 | Oracle は `''` を NULL として扱う | `""` と NULL を区別する | — |

補助クラスはすべて `runtime-java` の `com.scalar.migrate.appside` パッケージにある。

---

## H2 で実行できない構文の書き換え

`RESIDUAL_H2` の文は、アプリで実装する代わりに、H2 が実行できる SQL に書き換えて実行計画にする方法もある（`docs/oracle-sql-report.md`）。

| 構文 | 書き換え |
|---|---|
| `CONNECT BY` / `SYS_CONNECT_BY_PATH` | 再帰 WITH。経路は再帰部分で文字列を連結する |
| `ROLLUP` / `CUBE` / `GROUPING SETS` | 集約レベルごとの SELECT を `UNION ALL` |
| `PIVOT` | `SUM(CASE WHEN job = 'A' THEN sal END)` のような条件付き集約 |
| `UNPIVOT` | 列ごとの SELECT を `UNION ALL` |
| `KEEP (DENSE_RANK FIRST ...)` | `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...) = 1` の行を選ぶ |

書き換えた SQL はもう一度 `transpile.py --target scalardb --plan-dir` に通し、PLANNED になることを確かめる。

---

## Oracle の結果との突き合わせ（golden）

アプリ側の実装は、Oracle で取った正解と比べる。正解の取得は 1 回だけ Oracle が要り、比較は DB なしで行える。

```bash
.venv/bin/python difftest/golden.py capture --setup <setup.sql> --query <query.sql> \
  --tables <表1>,<表2> --out difftest/golden/<名前>          # Oracle が要る
(cd runtime-java && gradle installDist)
.venv/bin/python difftest/golden.py check --golden difftest/golden/<名前> --impl <実装クラス名> [--cp <追加のクラスパス>]
```

- 実装クラスは `com.scalar.migrate.appside.AppSideQuery`（`tables` は小文字の表名 → 小文字の列名の行）を実装する
- `golden.json` の `ordered` は、最上位の問合せに `ORDER BY` があるかで決まる。`ORDER BY` の値が同じ行どうしの順序は Oracle でも決まっていないので、差分が出たらまずそこを疑う
- 数値は `BigDecimal.compareTo` で比べる（`2.50` と `2.5` は同じ）。Oracle の `DATE` は日時として返るので、`LocalDate` はその日の 0 時と同じとみなす
- 例: `difftest/golden/area-sales/`（setup と query のみ。正解はまだ取っていない）と `com.scalar.migrate.examples.AreaSalesReport`
