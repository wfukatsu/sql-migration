# Oracle Database サンプル SQL / PL/SQL 集

Oracle 公式ドキュメント（SQL Language Reference / PL/SQL Language Reference / PL/SQL Packages and Types Reference）の構成に沿って、よく使う SQL と PL/SQL を**そのまま実行できるスクリプト**にまとめたものです。Oracle 固有の構文には `[ORA]` 印を付けており、他 DB への移行を検討するときの洗い出しにも使えます。

- 対象バージョン: **Oracle Database 19c 以降**（23ai / 26ai でも動作する想定）。23ai 以降専用の構文はコメントアウトし、その旨を記載
- サンプルスキーマ: Oracle 標準の HR スキーマを簡略化した表（`jobs` / `departments` / `employees` / `orders` / `order_items`）を `00_setup.sql` で作成

## ファイル構成

| ファイル | 内容 | 主な参照章 |
|---|---|---|
| `00_setup.sql` | サンプル表・シーケンス・データの作成 | SQL Ref: CREATE TABLE |
| `01_sql_ddl.sql` | IDENTITY / 仮想列 / ALTER / 制約 / 索引 / ビュー / シーケンス / シノニム / 一時表 / パーティション / FLASHBACK TABLE | SQL Ref: CREATE・ALTER 各文 |
| `02_sql_query.sql` | 基本関数・結合・副問合せ・集合演算・ROLLUP・PIVOT・分析関数・Top-N・WITH・CONNECT BY・フラッシュバック問合せ | SQL Ref: SELECT, Functions |
| `03_sql_dml.sql` | INSERT ALL/FIRST・結合更新・MERGE・DML エラーロギング・SAVEPOINT・FOR UPDATE SKIP LOCKED・JSON | SQL Ref: DML 各文, JSON Developer's Guide |
| `04_plsql_basics.sql` | 変数（%TYPE/%ROWTYPE）・制御構造・カーソル・REF CURSOR・レコード・コレクション・例外処理 | PL/SQL Ref: 3〜7, 12章 |
| `05_plsql_units.sql` | プロシージャ・ファンクション（DETERMINISTIC / RESULT_CACHE）・パッケージ・自律型トランザクション・トリガー（行/複合/INSTEAD OF） | PL/SQL Ref: 9〜11章 |
| `06_plsql_advanced.sql` | BULK COLLECT・FORALL SAVE EXCEPTIONS・動的SQL・DBMS_SQL・パイプライン表関数・組込みパッケージ・条件付きコンパイル | PL/SQL Ref: 8, 12章 / Packages Ref |
| `99_cleanup.sql` | 作成した全オブジェクトの削除 | — |
| `run_all.sql` | 00〜06 を順に実行してログを出力 | — |

## 実行方法

```sql
-- 空のスキーマ（既存 HR スキーマとは別）に接続して実行
sql user/password@//host:1521/FREEPDB1
SQL> @run_all.sql        -- 結果は run_all.log に出力
SQL> @99_cleanup.sql     -- 片付け（再実行前にも実行）
```

- 各ファイルは `WHENEVER SQLERROR CONTINUE` で、エラーが出ても最後まで進みます。
- 意図的にエラーを起こして例外処理を確認するサンプル（例: 04 の 6-2）が含まれます。
- ビットマップ索引・パーティションは Enterprise Edition（または Oracle Database Free）が必要です。
- `DBMS_SCHEDULER` には CREATE JOB 権限、`UTL_FILE`（コメントアウト済み）には DIRECTORY オブジェクトが必要です。

## サンプルの要点

### SQL
- **Top-N / ページング**: 12c 以降は `FETCH FIRST n ROWS [ONLY | WITH TIES]` / `OFFSET`。移行元の古いコードでは `ROWNUM` を使う 3 段ネストが多く見られる（02 F-2）。
- **階層問合せ**: `CONNECT BY PRIOR` と `LEVEL`、`SYS_CONNECT_BY_PATH` は Oracle 固有。標準 SQL の再帰 `WITH` に書き換えられる（02 G-2 と G-3 は同じ結果）。
- **MERGE**: `WHEN MATCHED THEN UPDATE ... DELETE WHERE` は Oracle 拡張。
- **DML エラーロギング**: `LOG ERRORS INTO` で制約違反の行だけをエラー表へ逃がし、一括処理を止めない。
- **JSON**: 19c は `CLOB` + `IS JSON` 制約、21c 以降はネイティブ `JSON` 型。`JSON_TABLE` で行と列に展開できる。

### PL/SQL
- **カーソル**: 通常はカーソル FOR ループ（OPEN/FETCH/CLOSE が不要）。大量件数のときは `BULK COLLECT ... LIMIT` を使う。
- **例外**: `PRAGMA EXCEPTION_INIT` で ORA 番号に名前を付け、`RAISE_APPLICATION_ERROR(-20000〜-20999)` で業務エラーを返す。`FORMAT_ERROR_BACKTRACE` でエラー発生行がわかる。
- **パッケージ**: 仕様部で公開するもの、本体で非公開にするものを分ける。パッケージ変数は**セッション単位**で状態を持つ（コネクションプール環境では要注意）。
- **トリガー**: 行トリガーから自分自身の表を参照すると ORA-04091（変更表エラー）になる。複合トリガーで回避するのが定番（05 5-3）。
- **FORALL SAVE EXCEPTIONS**: エラー行だけを `SQL%BULK_EXCEPTIONS` で回収し、正常な行は確定させる。
- **動的 SQL**: 値は必ず `USING` のバインド変数で渡す。表名などの識別子を連結するときは `DBMS_ASSERT` で検証して SQL インジェクションを防ぐ。

## 移行検討時のチェックリスト（Oracle 固有機能）

既存システムの棚卸しでは、以下のキーワードでソースと `USER_SOURCE` / `USER_TRIGGERS` / `USER_SEQUENCES` を検索すると、移行の難しい箇所を早めに洗い出せます。

| 機能 | 検出キーワード | 標準 SQL / 他 DB での代替 | 移行難易度 |
|---|---|---|---|
| 外部結合 `(+)` | `(+)` | ANSI `LEFT/RIGHT JOIN` | 低 |
| `NVL` / `NVL2` / `DECODE` | 関数名 | `COALESCE` / `CASE` | 低 |
| `MINUS` | `MINUS` | `EXCEPT` | 低 |
| `DUAL` | `FROM dual` | FROM 句なし（DB により異なる） | 低 |
| `ROWNUM` による Top-N | `ROWNUM` | `FETCH FIRST` / `LIMIT` | 低〜中（並び順に注意） |
| 日付関数 | `ADD_MONTHS`, `MONTHS_BETWEEN`, `TRUNC(date)` | DB ごとの日付関数 | 中（端数の扱いに差がある） |
| 空文字と NULL の同一視 | `= ''`, `IS NULL` | 明示的な変換 | 中（**Oracle だけ `''` を NULL として扱う**。見落としやすい） |
| 階層問合せ | `CONNECT BY`, `LEVEL`, `PRIOR` | 再帰 `WITH` | 中 |
| シーケンス | `.NEXTVAL`, `.CURRVAL` | IDENTITY / アプリ側で採番 | 中（分散環境では採番方式の再設計が必要） |
| `MERGE ... DELETE WHERE` | `MERGE` | UPSERT + DELETE に分解 | 中 |
| `ROWID` | `ROWID` | 主キーで代替 | 中 |
| フラッシュバック | `AS OF TIMESTAMP`, `FLASHBACK` | 履歴表・監査設計 | 高 |
| ストアドプロシージャ / パッケージ | `USER_SOURCE`（`TYPE` 列） | アプリ層（Java 等）のサービスへ移す | 高 |
| トリガー | `USER_TRIGGERS` | アプリ層・CDC・イベント | 高（処理が見えにくい） |
| 自律型トランザクション | `AUTONOMOUS_TRANSACTION` | 別トランザクションまたは非同期ログ | 高 |
| パッケージ変数（セッション状態） | パッケージ本体のグローバル変数 | ステートレスな設計へ | 高 |
| `DBMS_*` / `UTL_*` パッケージ | `DBMS_`, `UTL_` | ジョブ基盤・アプリのライブラリ | 中〜高 |

> ScalarDB などの分散トランザクション基盤へ移行する場合、ストアド・トリガー・シーケンスは基本的に**アプリ層へ移す**前提になります。ScalarDB SQL で使える構文（結合や集約など）はバージョンによって違うため、使っているバージョンの ScalarDB ドキュメントで確認してください。

## 注意事項

- このサンプルは Oracle ドキュメントの章構成と構文仕様を参考に**新しく作成したもの**です。ドキュメントの例文をそのまま転載したものではありません。
- 作成した環境では Oracle Database のコンテナを取得できなかったため、**実 DB での実行確認はしていません**。構文は静的に確認していますが、最初は検証用のスキーマで実行してください。

## 参考ドキュメント

- [Oracle Database PL/SQL Language Reference (23ai)](https://docs.oracle.com/en/database/oracle/oracle-database/23/lnpls/toc.htm)
- [Oracle AI Database SQL Language Reference (26ai)](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/)
- [SQL Language Reference – SELECT](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/SELECT.html)
- [PL/SQL Static SQL](https://docs.oracle.com/en/database/oracle/oracle-database/26/lnpls/static-sql.html) / [Dynamic SQL](https://docs.oracle.com/en/database/oracle/oracle-database/26/lnpls/dynamic-sql.html) / [Subprograms](https://docs.oracle.com/en/database/oracle/oracle-database/26/lnpls/plsql-subprograms.html) / [Triggers](https://docs.oracle.com/en/database/oracle/oracle-database/26/lnpls/plsql-triggers.html)
- [PL/SQL Collections and Records](https://docs.oracle.com/en/database/oracle/oracle-database/26/lnpls/plsql-collections-and-records.html)
- [PL/SQL Packages and Types Reference (26ai)](https://docs.oracle.com/en/database/oracle/oracle-database/26/arpls/index.html)
- [Explore SQL Features in Oracle Database 23ai](https://docs.oracle.com/en/learn/db23ai-sql-features/index.html)
