# Oracle サンプル SQL / PL/SQL 集を ScalarDB に移せるかの検証（2026-09-24）

Cowork のセッション「Oracle Database サンプルコード」（`cse_01Qxb85VaSWEDcRN9pWtb9vM`、`~/Downloads/oracle-samples.zip`）が書いた、
Oracle 公式ドキュメントの構成に沿った **構文カタログ**（SQL 4 本 + PL/SQL 3 本、約 1,700 行）を、このリポジトリの変換ツールに通し、
実 DB（移行元 Oracle 26ai Free と ScalarDB Cluster 3.19、バックエンド PostgreSQL）で同じ結果になるかを比べた記録。

題材は業務ロジックではなく「Oracle の構文を一つずつ見せる」サンプルなので、**変換率は低く出て当然**である。読む価値があるのは、
構文ごとに「そのまま移る / 実行計画で動く / アプリに移す / 人が決めないと進まない」のどれになるかと、実 DB で本当にそうなったか、である。

| | 数 | 結果 |
|---|---|---|
| SQL 文（6 ファイル） | 177 文 | OK 27 / WARN 54 / PLANNED 27 / ERROR 69。ScalarDB SQL になったのは 81 文（45.8%）、実行計画で動くものが 27 文 |
| SQL の実 DB 比較（02 の読み取り文 + JSON 3 文 + 修正版 1 文） | 40 文 | **PASS 19** / FAIL 10 / SKIP 9（変換 ERROR）/ CASE_ERROR 2（Oracle 自身が拒否） |
| PL/SQL routine（05・06 の全ユニット + 04 の無名ブロック 10 個） | 41 routine | AUTO 候補 7 / REVIEW 10 / REDESIGN 24（証拠なしの時点） |
| PL/SQL の Java 生成 | 41 routine、109 ファイル | javac エラー 15 件（8 routine）。collection・package 変数・trigger・FORALL RETURNING |
| PL/SQL の実 DB 比較（コンパイルできる 24 routine、23 シナリオ） | 23 | **一致 10** / 相違 13。証拠を渡すと **AUTO 6** |

## フォルダ

```
samples/oracle-samples/
  src/                  Cowork が書いた原文 10 ファイル（手を入れない）
  split_sources.py      原文を変換ツールが読める形に分ける（下記）
  sql/                  SQL 文だけにした 6 ファイル + 実 DB 比較のケース（queries-check.sql / .data.json）
  plsql/src/            PL/SQL のユニット（.prc / .fnc / .pks / .pkb / .trg）と blocks/（無名ブロックを procedure に包んだもの）、schema.sql
  plsql/oracle-user.sh  Oracle にユーザ hrs を作る（SYSTEM で 1 度）
  scalardb-schema.json  ScalarDB のキー設計（namespace hr）
  make_runnable.py      コンパイルできるユニットだけの部分集合 plsql-run/ と、23 シナリオを作る
  plsql-run/            実 DB 比較用（src / scenarios / golden。work/ は git に入れない）
  make_tables.py        結果ファイルから下の付録の表を作る
  result/sql/           変換後 SQL（*.scalardb.sql）、文ごとのレポート（*.report.md / .json）、実行計画（plans/）、実 DB 比較（queries-check.json）
  result/plsql/         解析（analysis/）、証拠つきの判定（decisions-with-evidence.json）、実 DB 比較（plsql-diff.json）
  result/tables.md      付録の表（make_tables.py の出力）
```

### 原文をどう分けたか（`split_sources.py`）

原文は SQL*Plus 用のスクリプトで、`SET ECHO ON` / `WHENEVER SQLERROR` / `SHOW ERRORS` / `/` と、SQL 文と PL/SQL ブロックが 1 ファイルに混ざっている。
変換ツールは SQL 文（sql-transpile）と PL/SQL のユニット（`plsql.cli` / `plsql.generate`）を別に読むので、機械的に分けた。

- SQL*Plus の指示行を落とす（`SET` は SQL*Plus のオプション名が続く行だけ。`UPDATE … SET` と `SET TRANSACTION` は SQL なので残す）
- `CREATE OR REPLACE PROCEDURE / FUNCTION / PACKAGE [BODY] / TRIGGER` を 1 ユニット 1 ファイルに
- **無名ブロック**（`DECLARE … BEGIN … END; /`）は呼び出し単位を持たないので、引数なしの `PROCEDURE b04_1_variables AS …` に包んだ（本文は原文のまま）。
  04 の 10 個、05 の呼び出し例 3 個、06 の 9 個、00 の 2 個、03 の 1 個
- `CREATE TYPE`（06 のオブジェクト型）は変換ツールの対象外なので `plsql/src/schema.sql` に置き、Oracle への配備で表と一緒に作る
- 02 の H-4（`WITH FUNCTION … SELECT … /`）は SQL 文として残したが、分割器が内側の `;` で切ってしまうので変換は ERROR になる（後述）

### 決めたこと（人の決定として記録。移行責任者の承認は取っていない）

| 決定 | 理由 |
|---|---|
| 型の無い `NUMBER` の id 列（`orders.order_id` など）は `NUMBER(12)` → BIGINT | ツールは型の無い NUMBER を DOUBLE に写す。キーに DOUBLE は使えない |
| Oracle `DATE` の列（`hire_date` / `order_date`）は ScalarDB では TIMESTAMP | Oracle の DATE は時刻を持つ。DATE にすると比較で `2013-06-17T00:00:00` と `2013-06-17` が別の値になる（最初の比較で全シナリオが落ちて分かった） |
| `NUMBER(8,2)` の金額列は DOUBLE の規約（`--variant double`） | corpus の外のプロジェクト `create_order` と同じ |
| 索引: employees の department_id / manager_id / job_id / email、orders の status / employee_id、departments の department_name | 原文の索引 2 本と、PL/SQL が WHERE に使う列 |
| 行ロック・トランザクション境界・自律型・package 変数・trigger は **決めていない** | `limits.yaml` は置かない。決めていない routine の書き込みを生成器が拒否するのが正しい動きで、その拒否がそのまま比較の結果に出ている |

---

## 第 1 部: SQL 文

### 1.1 変換（`sql-transpile`）

```bash
S=samples/oracle-samples
for f in 00_setup 01_sql_ddl 02_sql_query 03_sql_dml 05_plsql_units 06_plsql_advanced; do
  .venv/bin/python skills/sql-transpile/scripts/transpile.py $S/sql/$f.sql --source oracle --target scalardb \
    --schema $S/scalardb-schema.json --out-dir $S/result/sql --plan-dir $S/result/sql/plans
done
```

| ファイル | 文 | OK | WARN | PLANNED | ERROR | 変換率 |
|---|---|---|---|---|---|---|
| `00_setup.sql`（表・データ） | 50 | 5 | 37 | 0 | 8 | 84.0% |
| `01_sql_ddl.sql`（DDL） | 39 | 9 | 3 | 0 | 27 | 30.8% |
| `02_sql_query.sql`（問合せ） | 39 | 0 | 5 | 23 | 11 | 12.8%（実行計画を足すと 71.8%） |
| `03_sql_dml.sql`（DML・JSON） | 37 | 9 | 6 | 2 | 20 | 40.5% |
| `05_plsql_units.sql`（SQL 部分） | 9 | 3 | 2 | 1 | 3 | 55.6% |
| `06_plsql_advanced.sql`（SQL 部分） | 3 | 1 | 1 | 1 | 0 | 66.7% |

文ごとの判定と指摘は付録 A（この文書の末尾）と `result/sql/*.report.md` にある。構文の種類ごとにまとめると次のとおり。

**そのまま ScalarDB SQL になったもの（OK / WARN）**

| 構文 | どう移ったか | 注意（WARN） |
|---|---|---|
| `CREATE TABLE`（5 表 + emp_audit / bulk_target / products_json） | `CREATE TABLE … PRIMARY KEY` に。複合主キーは先頭がパーティションキー、残りがクラスタリングキー | NOT NULL / CHECK / FK / UNIQUE / DEFAULT は落ちる（アプリで守る）。`NUMBER(8,2)` は DOUBLE（精度）、`DATE` は時刻を持つ |
| `CREATE INDEX`（単一列） | `CREATE INDEX ON t (col)` | 索引名は落ちる |
| `INSERT … VALUES`（33 文） | そのまま | 列並びを省いた INSERT は表定義順に依存する（列名を書くよう促す）。`DATE '…'` は素の文字列に |
| 主キー・索引列の `SELECT`、`JOIN`（B-1 / B-2 / B-4）、`ORDER BY … NULLS LAST` | そのまま | 索引で届かない表は cross-partition scan（`scalar.db.cross_partition_scan.enabled`）が要る。`(+)` は ANSI JOIN に書き換え |
| `UPDATE … SET col = リテラル WHERE key`、`DELETE`、`COMMIT` / `ROLLBACK` | そのまま | — |
| `SELECT … FOR UPDATE NOWAIT` / `SKIP LOCKED` | `FOR UPDATE` を落として変換 | WARN `LOCK`: 行ロックは無い。楽観制御と再試行に設計し直す |

**実行計画で動くもの（PLANNED、27 文）**: ScalarDB から行を取得し、メモリ上の H2 で元の SQL を実行する。`residual-runner` が計画（`result/sql/plans/*.plan.json`）を読んで動かす。

| 構文 | 計画 | 実 DB での結果 |
|---|---|---|
| 関数を使う SELECT（`CASE` / `DECODE` / `NVL` / `NVL2` / 文字列・日付関数 / `REGEXP_*` / スカラー副問合せ） | P1（全行取得 → H2） | 一致（A-4 は `ROWNUM <= 5` が任意の 5 行なので不一致。後述） |
| `FULL OUTER JOIN`、`CROSS APPLY`、`JSON_TABLE` | P8（両表を取得 → H2 で結合） | **H2 が FULL OUTER JOIN / LATERAL を解釈できず失敗**。JSON_TABLE は取得 SQL が壊れる |
| 相関副問合せ、`NOT EXISTS`、多列 `IN`、ROWNUM の 3 段ネスト | P5 | 一致 |
| `MINUS` / `INTERSECT`、`WITH`（非再帰）、再帰 `WITH` | P6 / P1+P6 | MINUS / INTERSECT / WITH は一致。再帰 WITH は `CAST(… AS VARCHAR2(4000))` と `SEARCH DEPTH FIRST` を H2 が読めず失敗 |
| `GROUP BY / HAVING`、`LISTAGG` | P1 / P1+P7 | 一致 |
| ウィンドウ関数（`ROW_NUMBER` / `RANK` / `LAG` / `LEAD` / `NTILE` / `RATIO_TO_REPORT`、累計・移動平均） | P1 | 一致（同順位の行の並びだけ違う。後述） |
| `FETCH FIRST … WITH TIES`、`OFFSET … FETCH NEXT` | P1 / P4 | 一致（WITH TIES は同順位の並びだけ違う） |
| `SAMPLE (50)` | P1 | H2 に SAMPLE が無く失敗。そもそも結果が非決定的 |
| 利用者定義関数を呼ぶ SELECT（`annual_comp(…)`、`dept_name_of(…)`、`TABLE(emp_grades(80))`） | P1 | 関数は H2 に無いので、PL/SQL 側の Java を呼ぶ形にアプリで書く |

**自動では移せないもの（ERROR、69 文）**

| 構文 | 件数 | 理由と、どうするか |
|---|---|---|
| `CREATE SEQUENCE` / `.NEXTVAL` / `.CURRVAL`、`DEFAULT seq.NEXTVAL`、IDENTITY 列 | 6 | ScalarDB に sequence が無い。採番はアプリで行う（`Sequences` / `CountersSequences` の補助クラス）。`orders` への INSERT 5 文は主キー無しで ERROR `PK` |
| `CREATE VIEW` / `SYNONYM` / `GLOBAL TEMPORARY TABLE` / パーティション表 / ビットマップ索引 / 複合索引・関数索引 / `COMMENT ON` / `ALTER TABLE`（列追加・制約・SET UNUSED）/ `ALTER INDEX` / `FLASHBACK TABLE` / CTAS | 30 | ScalarDB の DDL に無い。view はアプリの問合せに、CTAS は SELECT + INSERT に。複合索引は 1 列の secondary index に設計し直す |
| `UPDATE … SET col = col * 1.05`（RMW、7 文） | 7 | SET に列を含む式は書けない。同じトランザクションで 読む → 計算 → リテラルで書く |
| `UPDATE (SELECT … JOIN …) SET`、相関副問合せ付き UPDATE、`SET (a) = (SELECT …)`、`MERGE … DELETE WHERE`、`INSERT … SELECT`、`INSERT ALL / FIRST`、`LOG ERRORS INTO`、`ROWID` による重複削除 | 10 | 読み取り + 書き込みに分ける。MERGE の `DELETE WHERE` と `INSERT FIRST` は sqlglot が構文を読めない（PARSE） |
| `SAVEPOINT` / `ROLLBACK TO` | 2 | ScalarDB に無い。ロールバック後に書く行は別トランザクション |
| `ROLLUP` / `GROUPING SETS`、`PIVOT` / `UNPIVOT`、`KEEP (DENSE_RANK FIRST)`、`CONNECT BY`（2 文） | 7 | H2 でも実行できない（`RESIDUAL_H2`）。アプリで実装する（`Windows` / 階層は再帰で） |
| `AS OF TIMESTAMP`（フラッシュバック）、`ROWID`、`WITH FUNCTION`、`JSON_VALUE … RETURNING NUMBER`、`JSON_MERGEPATCH … RETURNING CLOB` | 6 | 対応物が無い / sqlglot が読めない。フラッシュバックは履歴表の設計、JSON は文字列として持ち、アプリで解釈する |

### 1.2 実 DB で確かめる（`difftest/run.py`）

02 の読み取り文 36 文（H-4 を除く）+ 03 の JSON 問合せ 3 文 + E-1 の修正版 1 文を、Oracle と ScalarDB Cluster に同じデータ（`00_setup.sql` の投入データ）で流して比べた。

```bash
.venv/bin/python difftest/run.py samples/oracle-samples/sql/queries-check.sql --dialect oracle --fetcher jdbc --restart-cluster \
  --json-out samples/oracle-samples/result/sql/queries-check.json
```

```text
PASS=19 FAIL=10 SKIP=9 CASE_ERROR=2 (of PASS, EMPTY=0)
```

| 結果 | 文 | 中身 |
|---|---|---|
| PASS 19 | A-2, A-3, A-5, B-1, B-2 ×2, B-4, C-1〜C-5（6 文）, D-1, D-3, E-2, F-1 の OFFSET, F-2, G-1 | ScalarDB SQL 6 文と実行計画 13 文。**変換ツールの判定どおりに動いた** |
| FAIL 4（同順位・非決定） | A-1（`SYSDATE` / `USER`）, A-4（`ROWNUM <= 5`）, E-1'（`RANK` の同順位）, F-1 の `WITH TIES` | 値の差ではない。A-1 は時刻と接続ユーザ、A-4 は ORDER BY の無い ROWNUM が任意の 5 行を返す、E-1' / F-1 は `salary = 17000` の 2 人の並びが違う（集合としては同じ） |
| FAIL 4（実行基盤の穴） | B-3 `FULL OUTER JOIN`, B-5 `CROSS APPLY`, G-3 再帰 `WITH`, H-2 `SAMPLE` | 計画は立つが H2 が実行できない。FULL OUTER JOIN と LATERAL は H2 が非対応、再帰 WITH は `VARCHAR2(4000)` と `SEARCH DEPTH FIRST` が Oracle 方言のまま H2 に渡る、SAMPLE は H2 に無い。**ツールは PLANNED と言ったが動かない**。residual SQL を H2 方言に直す処理が要る |
| FAIL 2（JSON） | F-3 `JSON_TABLE`, F-4 `JSON_OBJECT` | JSON_TABLE は擬似表を取得 SQL に入れて `SELECT * FROM "` と壊れる。JSON_OBJECT は H2 が JSON 型を返し、runner がそれを base64 で返す |
| SKIP 9 | D-2 ×2, D-4 ×2, E-3, G-2, G-4, H-3, F-2（JSON_VALUE） | 変換 ERROR なので流していない（上の表のとおり） |
| CASE_ERROR 2 | E-1, H-1 | **Oracle 自身が拒否した**。E-1 は `AS share` が 26ai で予約語（ORA-00923）。H-1 は表を作った直後のフラッシュバック（ORA-01466）。どちらもサンプルの側の事情 |

FAIL の 10 件のうち、値が本当に違うものは無い。4 件は同順位・非決定、6 件は実行基盤（H2 / runner）が構文を受け付けない。

---

## 第 2 部: PL/SQL

### 2.1 解析と判定（`plsql.cli`）

```bash
P=samples/oracle-samples/plsql
.venv/bin/python -m plsql.cli $P/src --schema $P/src/schema.sql --scalardb-schema samples/oracle-samples/scalardb-schema.json \
  --out-dir samples/oracle-samples/result/plsql/analysis
```

```text
modules=36 routines=41 statements=242
parse rate      100.0%  (37/37 files)
type resolution 99.0%  (102/103 typed symbols)
scalardb        59.1% runnable  {'OK': 22, 'ERROR': 14, 'PLANNED': 4, 'WARN': 4}
verdicts        {'REDESIGN': 24, 'REVIEW': 17}  (no --evidence: nothing can be AUTO)
```

すべてパースでき、`%TYPE` / `%ROWTYPE` はほぼ解決した（未解決の 1 つは `c_emp%ROWTYPE`、cursor の行型）。routine ごとの判定は付録 C。理由ごとにまとめると:

| 判定 | routine | 当たったルール | 人が決めること |
|---|---|---|---|
| ルール上 AUTO（証拠待ちで REVIEW）7 | `annual_comp`, `dept_name_of`, `normalize_name`, `b04_1_variables`, `b04_2_control_flow`, `b04_6_1_predefined_exceptions`, `b06_6_conditional_compilation` | なし | 実 DB の証拠だけ |
| REDESIGN: routine 内の `COMMIT` / `ROLLBACK`（TX-001）9 | `b04_3`, `b04_6_2`, `b05_1`, `b05_3`, `b05_4`, `b06_2`, `b06_2_2`, `b06_3`, `log_msg` | TX-001（+ SQL-001 RMW、TRG-002） | トランザクション境界（`limits.yaml` の `transactions`）。サンプルでは「試したあと戻す」ための ROLLBACK なので、本番なら呼び出し側の境界に置く |
| REDESIGN: package 変数（STATE-001）6 | `emp_api.*`（hire / give_raise ×2 / validate_pct / call_count / get_by_dept） | STATE-001（+ EXC-001、SQL-001、TRG-002） | `g_calls` はセッション単位の状態。Singleton の field に置くと意味が変わる。呼び出し回数を持つ場所（呼び出し側か、持たない）を決める |
| REDESIGN: trigger（TRG-001）4 | `emp_biu_trg`, `emp_salary_audit_trg`, `emp_dept_cap_trg`, `emp_dept_upd_v_trg` | TRG-001 | 隠れた副作用。employees への全書き込み経路を Service に通す設計。INSTEAD OF trigger は view ごとアプリの操作に |
| REDESIGN: 行ロック（LOCK-001 / LOCK-002）1 | `b04_4_3_for_update_current_of` | LOCK-001, LOCK-002, CUR-002 | 楽観制御 + 再試行に移すか（`rowLocks.optimistic`） |
| REDESIGN: 動的 SQL（DYN-001 / DYN-003）3 | `b06_3_native_dynamic_sql`, `b06_3_6_dbms_sql`, `setup_drop_objects` | DYN-001, DYN-002, DYN-003 | 表名が実行時に決まる SQL は allowlist（`dynamicSql.allowed`）か専用 Repository。DBMS_SQL は実行ログから query family を洗う |
| REDESIGN: 外部副作用（EXT-001）1 | `b06_5_2_scheduler_job` | EXT-001, CALL-001 | DBMS_SCHEDULER はジョブ基盤へ |
| REVIEW: cursor（CUR-001 / CUR-002）4 | `b04_4_1`, `b04_4_2`, `b06_1`, `emp_grades` | CUR-001（明示 cursor の寿命）, CUR-002（走査行数の上限）, SCAN-002 | 走査行数の上限（`scanRows`）。`emp_grades` は PIPELINED（LOWER-001） |
| REVIEW: 模していない構文（LOWER-001）2 | `b04_4_4_ref_cursor`, `b04_5_records_collections` | LOWER-001 | SYS_REFCURSOR、連想配列 / ネスト表 / VARRAY は生成器がまだ扱えない |
| REVIEW: 解析範囲外の呼び出し（CALL-001）3 | `b06_5_builtin_packages`, `dml_d_create_error_log`, `setup_gather_stats` | CALL-001 | DBMS_APPLICATION_INFO / DBMS_ERRLOG / DBMS_STATS は移行先に対応物が無い |
| REVIEW: SELECT INTO がキーで届かない 1 | `b06_4_collection_in_sql` | SELECT-001, SEM-004, BULK-001 | オブジェクト型のコンストラクタと `TABLE()` はアプリで |
| REDESIGN: RETURNING + trigger 1 | `raise_salary` | SQL-004（RETURNING）, SQL-001（RMW）, TRG-002 | `UPDATE … RETURNING` は 読む → 書く に。employees に trigger が掛かるので、その分の処理を Service に |

ScalarDB が断る SQL 文 14 は、RMW（`SET salary = salary * …`）7、`RETURNING` 2、`ORDER BY 1`、`IS JSON` 相当の `IS` 述語、trigger 本体の `:NEW` / `CASE WHEN DELETING`、IDENTITY 列（`audit_id`）無しの INSERT、`TABLE()` である。

### 2.2 Java の生成とコンパイル（`plsql.generate --verify-compile`）

```text
wrote 109 files to out/oracle-samples/generated/
routines: 41  AUTO 7  REVIEW 10  REDESIGN 24
untranslated statements 36  SQL ScalarDB refuses 14  planned 4
compile check: 15 javac error(s)
```

javac エラーは 8 routine に集中し、いずれも判定が REVIEW / REDESIGN のもの（生成器がまだ模していない構文）:

| routine | 構文 | エラー |
|---|---|---|
| `b04_5_records_collections`（6 件） | 連想配列 `INDEX BY VARCHAR2`、ネスト表のコンストラクタ、`.FIRST` / `.NEXT` / `.EXTEND` / `.DELETE` / `.EXISTS` / `.LIMIT` | `List<BigDecimal>` に `first()` が無い、`tNames(...)` が無い |
| `emp_api.validate_pct` / `call_count` | package 変数 `g_calls` | 変数 `gCalls` が無い（STATE-001 で拒否したまま参照だけ残る） |
| `emp_dept_cap_trg.body`（2 件） | 複合 trigger の `INDEX BY PLS_INTEGER` 表 | `first()`、`gDepts(Object)` |
| `emp_biu_trg.body`（TriggerChecks） | `:NEW.email := UPPER(:NEW.email)` の trigger 本体 | 引数の型（String, BigDecimal）が合わない |
| `b06_2_2_forall_returning` | `FORALL … RETURNING salary BULK COLLECT INTO` | sqlglot が読めない（SQL_PARSE）+ `tNum(int,int,int)` が無い |
| `emp_grades` | `PIPELINED` / `PIPE ROW` / `RETURN;` | 戻り値が無い |
| `b05_1_call_raise_salary` / `b05_4_call_log_msg` | OUT 引数つき / 自律型の兄弟 routine を呼ぶ | `raiseSalary(int,int,BigDecimal)` / `logMsg(String)` が無い（呼び先は Result 型を返す形で生成されている） |

### 2.3 実 DB で比べる（Oracle → ScalarDB Cluster）

Gradle は木ごとコンパイルするので、上の 8 routine を含むユニットを外した **部分集合 `plsql-run/`**（24 routine）で比べた。
シナリオは 23 本（`make_runnable.py` が作る。表の初期データは `00_setup.sql` と同じ）。`SYSDATE` は両側で `2026-09-24 09:30:00` に固定。

```bash
samples/oracle-samples/plsql/oracle-user.sh                              # Oracle にユーザ hrs を作る（1 度だけ）
export SRC_ORACLE_USER=hrs SRC_ORACLE_PASSWORD=hrs
P=samples/oracle-samples/plsql-run
.venv/bin/python samples/oracle-samples/make_runnable.py
.venv/bin/python difftest/plsql_run.py deploy --project $P                # 表 7・sequence 2・型 2・ユニット 24 を配備
.venv/bin/python difftest/plsql_run.py run --project $P                   # Oracle で 23 シナリオ -> golden/
cp samples/oracle-samples/scalardb-schema.json difftest/work/hr-schema.json
(cd difftest && docker compose --profile tools --profile oracle --profile cassandra --profile cluster run --rm schema-loader \
    --config /conf/scalardb-in-docker.properties --schema-file /work/hr-schema.json --coordinator)
.venv/bin/python difftest/plsql_capture.py --project $P --namespace hr --variant double
.venv/bin/python difftest/plsql_compare.py --project $P --variant double --json $P/work/plsql-diff.json
.venv/bin/python -m plsql.cli $P/src --schema $P/src/schema.sql --scalardb-schema $P/scalardb-schema.json \
    --evidence $P/work/plsql-diff.json --variant double --generated $P/work/generated --out-dir $P/work/analysis-evidence
```

```text
23 compared: 10 identical, 13 differing; 0 not compared
verdicts        {'AUTO': 6, 'REDESIGN': 9, 'REVIEW': 9}
```

**一致した 10 本**（戻り値・OUT 引数・例外・表の状態がすべて同じ）:

| シナリオ | 確かめたこと |
|---|---|
| `annual_comp_with_comm` / `_null_comm` | `p_salary * 12 * (1 + NVL(p_comm, 0))`: 156000 / 72000。`DEFAULT NULL` の引数は NULL を明示して渡す |
| `dept_name_of_ok` / `_missing` | 主キーの SELECT INTO → 'IT'。無い部門は `NO_DATA_FOUND` handler で NULL |
| `normalize_name_ok` | IN OUT 引数: `'  john SMITH '` → `'John Smith'`（`INITCAP(TRIM(x))`） |
| `b04_1_variables` | `%TYPE` / `%ROWTYPE` の SELECT INTO と `DBMS_OUTPUT` |
| `b04_4_2_cursor_for_loop` | パラメータ付き cursor FOR ループ、暗黙 cursor FOR ループ |
| `b04_6_1_predefined_exceptions` | `NO_DATA_FOUND` / `TOO_MANY_ROWS` / `ZERO_DIVIDE` をそれぞれ捕捉して正常終了 |
| `b06_1_bulk_collect_limit` | `FETCH … BULK COLLECT INTO … LIMIT 5` の分割読み |
| `b06_6_conditional_compilation` | `$IF DBMS_DB_VERSION.VERSION >= 23 $THEN` |

証拠を渡すと、`annual_comp`, `dept_name_of`, `normalize_name`, `b04_1_variables`, `b04_6_1_predefined_exceptions`, `b06_6_conditional_compilation` の 6 routine が **AUTO** になった。
`b04_4_2_cursor_for_loop` と `b06_1_bulk_collect_limit` は一致したが、走査行数の上限（CUR-002 / SCAN-002）を誰も決めていないので REVIEW のまま。

**相違した 13 本**は、すべて「Oracle では正常に終わり、ScalarDB 側の Java が `UnsupportedOperationException` を投げた」形で、値の違いは無い。
つまり **ツールが解析で断ったとおりの場所で止まっている**（付録 D に 1 本ずつ）:

| 止まった理由 | シナリオ | 何が要るか |
|---|---|---|
| RMW（`SET salary = salary + 100`） | `b04_3_implicit_cursor_attrs` | 読む → 計算 → 書く への書き換え |
| `RETURNING` | `raise_salary_ok` / `_missing` | UPDATE の後に読む。`-20010`（0 件）は Java でも `rowCount == 0` で出せる |
| IDENTITY 列（`audit_id`）の採番 | `log_msg_ok` | 採番の設計（`Sequences` 補助クラス）+ 自律型トランザクションの境界（`transactions.separate`） |
| 行ロック（`FOR UPDATE … WHERE CURRENT OF`） | `b04_4_3_for_update_current_of` | `rowLocks.optimistic` の決定 |
| 明示 cursor の `OPEN` / `FETCH` / `CLOSE` | `b04_4_1_explicit_cursor` | 生成器の対応（cursor FOR ループは動くので、書き換えでも可） |
| `FOR j IN REVERSE 1 .. 6` | `b04_2_control_flow` | 生成器の対応（REVERSE の範囲ループ） |
| `SQLERRM` / `DBMS_UTILITY.FORMAT_ERROR_BACKTRACE` | `b04_6_2_user_exceptions` | 生成器の対応（例外メッセージの取得） |
| `EXECUTE IMMEDIATE`（表名を連結）/ `DBMS_SQL` | `b06_3_native_dynamic_sql`, `b06_3_6_dbms_sql` | allowlist の決定、または専用 Repository |
| `BULK COLLECT` した `NUMBER(8,2)` を DOUBLE 列に bind | `b06_2_forall_save_exceptions` | **生成器の不具合**: collection 要素（BigDecimal）を DOUBLE 列に `setObject` して `DB-SQL-10016`。列の型で double に直す必要がある |
| オブジェクト型のコンストラクタ + `TABLE()` | `b06_4_collection_in_sql` | アプリ側の実装 |
| `DBMS_APPLICATION_INFO.SET_MODULE(module_name => …)` | `b06_5_builtin_packages` | 対応物が無い（名前付き引数の外部呼び出し） |

---

## サンプル自体について分かったこと

Cowork は「実 Oracle で流していない」と断っていた。実際に流して見つかったのは 2 点。

1. **02 E-1 の `AS share` は Oracle 26ai Free で ORA-00923 になる**（`SHARE` が予約語）。`share_pct` に変えた文（E-1'）は Oracle でも ScalarDB でも動く
2. 02 H-1 のフラッシュバック問合せは、表を作った直後には ORA-01466 になる（サンプルの手順どおり `00_setup.sql` から続けて流すと必ずこうなる）

それ以外の SQL 文と、配備した 24 ユニット + 型 2 つは Oracle でそのまま通った（`compiled 24/24 units`）。

## 今回見つけて直したツールの不具合（7 件）

| 見つかったもの | 直した所 |
|---|---|
| 文の分割で、SQL*Plus の定番のヘッダ（`-----…` 80 文字の行）に当たると正規表現が指数的にバックトラックし、02 の変換が終わらなかった（30 文字で 0.45 秒、80 文字で事実上停止） | `scalardb_migrate/converter.py`（と skill 同梱コピー）: 先頭コメントと「コメントだけの断片」の正規表現を所有量指定子に。回帰テスト `test_split_statements_survives_dashed_banner_lines` |
| 宣言部の初期化式（`c INTEGER := DBMS_SQL.OPEN_CURSOR`）が翻訳できないと、生成器が例外で落ちて全 routine の生成が止まった | `plsql/gen_java/service.py` `_declaration`: 文と同じく「翻訳していない」コメント + throw にして先へ進む |
| IN OUT 引数を `String pName = pName;` と再宣言し、javac が落ちた（`normalize_name`） | 同 `_emit_method`: IN OUT は引数そのものを local にする |
| `PLS_INTEGER` の `i := i + 1` が `Integer i = Plsql.add(i, 1)`（Object）になりコンパイルできなかった | 同 `_coerce` + `Plsql.toInt` / `toLong` |
| `DBMS_OUTPUT.PUT_LINE` を「外部呼び出し」として throw していて、印字する block がすべて動かなかった（解析は harmless と判定している） | `Plsql.putLine` / `put` / `newLine` / `output()`（スレッドごとのバッファ） |
| `INITCAP` / `TRIM` が翻訳できなかった | `plsql/gen_java/expr.py` + `Plsql.initcap` / `trim` |
| Oracle 側のハーネスが IN OUT 引数の入力値を捨てていた（`out` の bind が `args` を上書き） | `difftest/plsql_run.py` `call_routine`: 同じ名前が両方にあれば入力値を入れる |

直していない穴（上の各節に出たもの）: H2 の FULL OUTER JOIN / LATERAL / SAMPLE、residual SQL の `VARCHAR2` / `SEARCH DEPTH FIRST`、JSON_TABLE の取得 SQL、
H2 の JSON 型が base64 で返る、collection 要素の DOUBLE 列への bind、IF の両分岐が throw で終わると後続が javac の unreachable になる（`b04_4_4_ref_cursor` を部分集合から外した）、
collection / package 変数 / trigger / PIPELINED / FORALL RETURNING / REVERSE ループ / SQLERRM の生成。

## 再現

```bash
python3 samples/oracle-samples/split_sources.py           # src/ -> sql/, plsql/src/
# 第 1 部 1.1 / 1.2、第 2 部 2.1 / 2.3 の各コマンド（上のとおり）
.venv/bin/python samples/oracle-samples/make_tables.py > samples/oracle-samples/result/tables.md
```

Docker の検証環境（`difftest/`）と ScalarDB Cluster のライセンスが要る（[検証環境](../../docs/guide/verification.md)）。表の型を変えて作り直したあとは Cluster を再起動する（`docker compose restart scalardb-cluster`。gRPC の `SqlException` で capture が落ちる）。

---

## 付録（`result/tables.md` と同じ。`make_tables.py` が結果ファイルから作る）

### 付録 A: SQL 変換（文ごと）

#### `00_setup.sql` — 50 文 / OK 5 / WARN 37 / PLANNED 0 / ERROR 8（変換率 84.0%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 |  | `CREATE TABLE jobs ( job_id VARCHAR2(10) CONSTRAINT jobs_pk PRIMARY KE…` | WARN | TYPE |  |
| 2 |  | `CREATE TABLE departments ( department_id NUMBER(4) CONSTRAINT dept_pk…` | OK |  |  |
| 3 |  | `CREATE TABLE employees ( employee_id NUMBER(6) CONSTRAINT emp_pk PRIM…` | WARN | CHECK, FK, TYPE, UNIQUE |  |
| 4 |  | `CREATE INDEX emp_dept_ix ON employees (department_id)` | OK |  |  |
| 5 |  | `CREATE INDEX emp_mgr_ix ON employees (manager_id)` | OK |  |  |
| 6 |  | `CREATE SEQUENCE emp_seq START WITH 300 INCREMENT BY 1 NOCACHE` | ERROR | DDL |  |
| 7 |  | `CREATE SEQUENCE order_seq START WITH 1 INCREMENT BY 1` | ERROR | DDL |  |
| 8 |  | `CREATE TABLE orders ( order_id NUMBER DEFAULT order_seq.NEXTVAL CONST…` | WARN | CHECK, DEFAULT, FK, TYPE |  |
| 9 |  | `CREATE TABLE order_items ( order_id NUMBER CONSTRAINT oi_order_fk REF…` | WARN | FK, TYPE |  |
| 10 |  | `INSERT INTO jobs VALUES ('AD_PRES', 'President', 20000, 40000)` | WARN | INSERT_COLS |  |
| 11 |  | `INSERT INTO jobs VALUES ('AD_VP', 'Vice President', 15000, 30000)` | WARN | INSERT_COLS |  |
| 12 |  | `INSERT INTO jobs VALUES ('IT_PROG', 'Programmer', 4000, 10000)` | WARN | INSERT_COLS |  |
| 13 |  | `INSERT INTO jobs VALUES ('SA_MAN', 'Sales Manager', 10000, 20000)` | WARN | INSERT_COLS |  |
| 14 |  | `INSERT INTO jobs VALUES ('SA_REP', 'Sales Representative', 6000, 1200…` | WARN | INSERT_COLS |  |
| 15 |  | `INSERT INTO jobs VALUES ('ST_CLERK','Stock Clerk', 2000, 5000)` | WARN | INSERT_COLS |  |
| 16 |  | `INSERT INTO departments VALUES (10, 'Administration', NULL, 'Tokyo')` | WARN | INSERT_COLS |  |
| 17 |  | `INSERT INTO departments VALUES (60, 'IT', NULL, 'Osaka')` | WARN | INSERT_COLS |  |
| 18 |  | `INSERT INTO departments VALUES (80, 'Sales', NULL, 'Tokyo')` | WARN | INSERT_COLS |  |
| 19 |  | `INSERT INTO departments VALUES (50, 'Shipping', NULL, 'Nagoya')` | WARN | INSERT_COLS |  |
| 20 |  | `INSERT INTO departments VALUES (90, 'Executive', NULL, 'Tokyo')` | WARN | INSERT_COLS |  |
| 21 |  | `INSERT INTO departments VALUES (99, 'Research', NULL, 'Fukuoka')` | WARN | INSERT_COLS |  |
| 22 |  | `INSERT INTO employees VALUES (100,'Steven','King', 'SKING', DATE '201…` | WARN | INSERT_COLS |  |
| 23 |  | `INSERT INTO employees VALUES (101,'Neena','Kochhar', 'NKOCHHAR',DATE …` | WARN | INSERT_COLS |  |
| 24 |  | `INSERT INTO employees VALUES (102,'Lex','De Haan', 'LDEHAAN', DATE '2…` | WARN | INSERT_COLS |  |
| 25 |  | `INSERT INTO employees VALUES (103,'Alexander','Hunold','AHUNOLD',DATE…` | WARN | INSERT_COLS |  |
| 26 |  | `INSERT INTO employees VALUES (104,'Bruce','Ernst', 'BERNST', DATE '20…` | WARN | INSERT_COLS |  |
| 27 |  | `INSERT INTO employees VALUES (107,'Diana','Lorentz', 'DLORENTZ',DATE …` | WARN | INSERT_COLS |  |
| 28 |  | `INSERT INTO employees VALUES (145,'John','Russell', 'JRUSSEL', DATE '…` | WARN | INSERT_COLS |  |
| 29 |  | `INSERT INTO employees VALUES (146,'Karen','Partners', 'KPARTNER',DATE…` | WARN | INSERT_COLS |  |
| 30 |  | `INSERT INTO employees VALUES (150,'Peter','Tucker', 'PTUCKER', DATE '…` | WARN | INSERT_COLS |  |
| 31 |  | `INSERT INTO employees VALUES (151,'David','Bernstein','DBERNSTE',DATE…` | WARN | INSERT_COLS |  |
| 32 |  | `INSERT INTO employees VALUES (155,'Oliver','Tuvault', 'OTUVAULT',DATE…` | WARN | INSERT_COLS |  |
| 33 |  | `INSERT INTO employees VALUES (120,'Matthew','Weiss', 'MWEISS', DATE '…` | WARN | INSERT_COLS |  |
| 34 |  | `INSERT INTO employees VALUES (125,'Julia','Nayer', 'JNAYER', DATE '20…` | WARN | INSERT_COLS |  |
| 35 |  | `INSERT INTO employees VALUES (178,'Kimberely','Grant','KGRANT', DATE …` | WARN | INSERT_COLS |  |
| 36 |  | `INSERT INTO employees VALUES (200,'Jennifer','Whalen','JWHALEN', DATE…` | WARN | INSERT_COLS |  |
| 37 |  | `UPDATE departments d SET manager_id = CASE d.department_id WHEN 10 TH…` | ERROR | RMW |  |
| 38 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 39 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 40 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 41 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 42 |  | `INSERT INTO orders (order_date, employee_id, customer, status, total)…` | ERROR | PK |  |
| 43 |  | `INSERT INTO order_items VALUES (1, 1, 'ScalarDB License', 1, 1000)` | WARN | INSERT_COLS |  |
| 44 |  | `INSERT INTO order_items VALUES (1, 2, 'Support', 1, 200)` | WARN | INSERT_COLS |  |
| 45 |  | `INSERT INTO order_items VALUES (2, 1, 'Training', 3, 150)` | WARN | INSERT_COLS |  |
| 46 |  | `INSERT INTO order_items VALUES (3, 1, 'ScalarDL License', 2, 1500)` | WARN | INSERT_COLS |  |
| 47 |  | `INSERT INTO order_items VALUES (4, 1, 'Consulting', 4, 200)` | WARN | INSERT_COLS |  |
| 48 |  | `INSERT INTO order_items VALUES (5, 1, 'ScalarDB License', 2, 1100)` | WARN | INSERT_COLS |  |
| 49 |  | `COMMIT` | OK |  |  |
| 50 |  | `SELECT table_name, num_rows FROM user_tables ORDER BY table_name` | OK |  |  |

#### `01_sql_ddl.sql` — 39 文 / OK 9 / WARN 3 / PLANNED 0 / ERROR 27（変換率 30.8%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 | 1 IDENTITY 列・仮想列・DEFAULT ON NU | `CREATE TABLE sample_ddl ( id NUMBER GENERATED ALWAYS AS IDENTITY (STA…` | ERROR | PARSE |  |
| 2 |  | `COMMENT ON TABLE sample_ddl IS 'DDLサンプル用テーブル'` | ERROR | STATEMENT |  |
| 3 |  | `COMMENT ON COLUMN sample_ddl.price IS '税抜価格'` | ERROR | STATEMENT |  |
| 4 |  | `INSERT INTO sample_ddl (name, price) VALUES ('Widget', 1000)` | ERROR | PK |  |
| 5 |  | `INSERT INTO sample_ddl (name, price) VALUES ('Gadget', NULL)` | ERROR | PK |  |
| 6 |  | `SELECT id, name, price, price_incl FROM sample_ddl` | WARN | CROSS_PARTITION |  |
| 7 | 2 ALTER TABLE：列の追加・変更・名前変更・削除 | `ALTER TABLE sample_ddl ADD (category VARCHAR2(20) DEFAULT 'GENERAL' N…` | ERROR | ALTER |  |
| 8 |  | `ALTER TABLE sample_ddl MODIFY (name VARCHAR2(100 CHAR))` | ERROR | UNPARSED |  |
| 9 |  | `ALTER TABLE sample_ddl RENAME COLUMN note TO description` | OK |  |  |
| 10 |  | `ALTER TABLE sample_ddl SET UNUSED (description)` | ERROR | UNPARSED |  |
| 11 |  | `ALTER TABLE sample_ddl DROP UNUSED COLUMNS` | ERROR | ALTER |  |
| 12 | 3 制約の追加・無効化・有効化 | `ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_name_uk UNIQUE (name)` | ERROR | ALTER |  |
| 13 |  | `ALTER TABLE sample_ddl ADD CONSTRAINT sample_ddl_price_ck CHECK (pric…` | ERROR | ALTER |  |
| 14 |  | `ALTER TABLE sample_ddl DISABLE CONSTRAINT sample_ddl_price_ck` | ERROR | UNPARSED |  |
| 15 |  | `ALTER TABLE sample_ddl ENABLE NOVALIDATE CONSTRAINT sample_ddl_price_…` | ERROR | UNPARSED |  |
| 16 | 4 CTAS（CREATE TABLE AS SELECT） | `CREATE TABLE emp_stage AS SELECT employee_id, last_name, salary, depa…` | ERROR | DDL |  |
| 17 | 5 インデックス | `CREATE INDEX emp_name_ix ON employees (last_name, first_name)` | ERROR | INDEX |  |
| 18 |  | `CREATE INDEX emp_upper_email_ix ON employees (UPPER(email))` | ERROR | INDEX |  |
| 19 |  | `CREATE BITMAP INDEX orders_status_bix ON orders (status)` | ERROR | UNPARSED |  |
| 20 |  | `ALTER INDEX emp_name_ix INVISIBLE` | ERROR | UNPARSED |  |
| 21 |  | `ALTER INDEX emp_name_ix VISIBLE` | ERROR | UNPARSED |  |
| 22 | 6 ビュー（WITH CHECK OPTION / READ | `CREATE OR REPLACE VIEW emp_it_v AS SELECT employee_id, last_name, sal…` | ERROR | DDL |  |
| 23 |  | `CREATE OR REPLACE VIEW emp_dept_v AS SELECT e.employee_id, e.last_nam…` | ERROR | DDL |  |
| 24 | 7 シーケンス [ORA] | `CREATE SEQUENCE audit_seq START WITH 1 INCREMENT BY 1 CACHE 20 NOCYCLE` | ERROR | DDL |  |
| 25 |  | `SELECT audit_seq.NEXTVAL, audit_seq.CURRVAL FROM dual` | OK |  |  |
| 26 |  | `ALTER SEQUENCE audit_seq INCREMENT BY 10` | ERROR | UNPARSED |  |
| 27 | 8 シノニム [ORA] | `CREATE OR REPLACE SYNONYM staff FOR employees` | ERROR | UNPARSED |  |
| 28 |  | `SELECT COUNT(*) FROM staff` | OK |  |  |
| 29 | 9 一時表 [ORA] | `CREATE GLOBAL TEMPORARY TABLE gtt_work ( id NUMBER, val VARCHAR2(100)…` | ERROR | TEMP |  |
| 30 | 10 パーティション表 [ORA]（EE の Partitio | `CREATE TABLE sales_part ( sale_id NUMBER, sale_date DATE, amount NUMB…` | ERROR | UNPARSED |  |
| 31 |  | `INSERT INTO sales_part VALUES (1, DATE '2025-12-31', 100)` | WARN | INSERT_COLS |  |
| 32 |  | `INSERT INTO sales_part VALUES (2, DATE '2026-03-15', 200)` | WARN | INSERT_COLS |  |
| 33 |  | `COMMIT` | OK |  |  |
| 34 |  | `SELECT partition_name, high_value FROM user_tab_partitions WHERE tabl…` | OK |  |  |
| 35 | 11 TRUNCATE / DROP / FLASHBACK  | `TRUNCATE TABLE emp_stage` | OK |  |  |
| 36 |  | `DROP TABLE gtt_work` | OK |  |  |
| 37 |  | `DROP TABLE sales_part` | OK |  |  |
| 38 |  | `FLASHBACK TABLE sales_part TO BEFORE DROP` | ERROR | PARSE |  |
| 39 |  | `DROP TABLE sales_part PURGE` | OK |  |  |

#### `02_sql_query.sql` — 39 文 / OK 0 / WARN 5 / PLANNED 23 / ERROR 11（変換率 12.8%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 | A-1 DUAL 表 [ORA]（23ai 以降は FROM 句 | `SELECT SYSDATE, SYSTIMESTAMP, USER FROM dual` | PLANNED | PROJECTION | P1 |
| 2 | A-2 絞り込み・並べ替え（NULL の並び順指定） | `SELECT employee_id, last_name, salary, commission_pct FROM employees …` | WARN | CROSS_PARTITION, NULLS |  |
| 3 | A-3 CASE / DECODE [ORA] / NULL 処 | `SELECT last_name, salary, CASE WHEN salary >= 15000 THEN 'HIGH' WHEN …` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 4 | A-4 文字列・数値・日付関数 | `SELECT UPPER(last_name) AS upper_name, INITCAP(email) AS initcap_emai…` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 5 | A-5 正規表現 | `SELECT email, REGEXP_SUBSTR(email, '^[A-Z]') AS first_char, REGEXP_RE…` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 6 | B-1 内部結合（ANSI） | `SELECT e.last_name, d.department_name FROM employees e JOIN departmen…` | WARN | CROSS_PARTITION |  |
| 7 | B-2 外部結合（ANSI）と Oracle 独自の (+) 記 | `SELECT e.last_name, d.department_name FROM employees e LEFT JOIN depa…` | WARN | CROSS_PARTITION |  |
| 8 |  | `SELECT e.last_name, d.department_name FROM employees e, departments d…` | WARN | CROSS_PARTITION, ORACLE_JOIN_MARK |  |
| 9 | B-3 完全外部結合：部門なし社員・社員なし部門の両方を出す | `SELECT e.last_name, d.department_name FROM employees e FULL OUTER JOI…` | PLANNED | JOIN, PLAN_CROSS_PARTITION | P8 |
| 10 | B-4 自己結合（上司名の取得） | `SELECT w.last_name AS employee, m.last_name AS manager FROM employees…` | WARN | CROSS_PARTITION |  |
| 11 | B-5 LATERAL / CROSS APPLY（12c+）： | `SELECT d.department_name, t.last_name, t.salary FROM departments d CR…` | PLANNED | JOIN, PLAN_CROSS_PARTITION | P8 |
| 12 | C-1 スカラー副問合せ | `SELECT last_name, salary, (SELECT ROUND(AVG(salary)) FROM employees) …` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 13 | C-2 相関副問合せ：部門平均より高い社員 | `SELECT e.last_name, e.department_id, e.salary FROM employees e WHERE …` | PLANNED | PLAN_CROSS_PARTITION, SUBQUERY | P5 |
| 14 | C-3 EXISTS / NOT EXISTS | `SELECT d.department_name FROM departments d WHERE NOT EXISTS (SELECT …` | PLANNED | NOT, PLAN_CROSS_PARTITION, SUBQUERY | P5 |
| 15 | C-4 多列 IN | `SELECT last_name, department_id, salary FROM employees WHERE (departm…` | PLANNED | PLAN_CROSS_PARTITION, SUBQUERY | P5 |
| 16 | C-5 集合演算（MINUS は Oracle 固有 [ORA] | `SELECT department_id FROM departments MINUS SELECT department_id FROM…` | PLANNED | PLAN_CROSS_PARTITION, SET_OP | P6 |
| 17 |  | `SELECT job_id FROM employees WHERE department_id = 80 INTERSECT SELEC…` | PLANNED | PLAN_CROSS_PARTITION, SET_OP | P6 |
| 18 | D-1 GROUP BY / HAVING | `SELECT department_id, COUNT(*) AS cnt, SUM(salary) AS total, ROUND(AV…` | PLANNED | PLAN_CROSS_PARTITION, PLAN_UNRESOLVED, PROJECTION | P1 |
| 19 | D-2 ROLLUP / CUBE / GROUPING SET | `SELECT CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(de…` | ERROR | APP_SEMANTICS, GROUP, PROJECTION, RESIDUAL_H2 |  |
| 20 |  | `SELECT department_id, job_id, SUM(salary) FROM employees GROUP BY GRO…` | ERROR | APP_SEMANTICS, GROUP, RESIDUAL_H2 |  |
| 21 | D-3 LISTAGG（文字列集約） | `SELECT department_id, LISTAGG(last_name, ', ') WITHIN GROUP (ORDER BY…` | PLANNED | AGG, PLAN_CROSS_PARTITION, PROJECTION | P1+P7 |
| 22 | D-4 PIVOT / UNPIVOT（11g+） | `SELECT * FROM (SELECT department_id, job_id, salary FROM employees) P…` | ERROR | APP_SEMANTICS, FROM, RESIDUAL_H2, SUBQUERY |  |
| 23 |  | `SELECT employee_id, pay_type, amount FROM (SELECT employee_id, salary…` | ERROR | FROM, PROJECTION, RESIDUAL_H2, SUBQUERY |  |
| 24 |  | `SELECT department_id, last_name, salary, ROW_NUMBER() OVER (PARTITION…` | PLANNED | PLAN_CROSS_PARTITION, PLAN_UNRESOLVED, PROJECTION, WINDOW | P1 |
| 25 | E-2 累計・移動平均（ウィンドウ句） | `SELECT order_date, total, SUM(total) OVER (ORDER BY order_date ROWS B…` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION, WINDOW | P1 |
| 26 | E-3 KEEP (DENSE_RANK FIRST/LAST) | `SELECT department_id, MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY …` | ERROR | APP_SEMANTICS, KEEP, PROJECTION, RESIDUAL_H2 |  |
| 27 | F-1 12c+ 標準構文 | `SELECT last_name, salary FROM employees ORDER BY salary DESC FETCH FI…` | PLANNED | LIMIT, PLAN_CROSS_PARTITION | P1 |
| 28 |  | `SELECT last_name, salary FROM employees ORDER BY salary DESC OFFSET 5…` | PLANNED | OFFSET, PLAN_CROSS_PARTITION | P4 |
| 29 | F-2 11g 以前の ROWNUM 方式 [ORA]（移行元コ | `SELECT * FROM (SELECT a.*, ROWNUM rnum FROM (SELECT last_name, salary…` | PLANNED | FROM, PLAN_CROSS_PARTITION, PLAN_UNRESOLVED, SUBQUERY | P5 |
| 30 | G-1 副問合せのファクタリング | `WITH dept_stats AS ( SELECT department_id, AVG(salary) AS avg_sal FRO…` | PLANNED | CTE, PLAN_CROSS_PARTITION, PROJECTION | P1+P6 |
| 31 | G-2 CONNECT BY による階層問合せ [ORA] | `SELECT LEVEL, LPAD(' ', 2 * (LEVEL - 1)) || last_name AS org_chart, S…` | ERROR | APP_SEMANTICS, HIERARCHICAL, PROJECTION, RESIDUAL_H2 |  |
| 32 | G-3 再帰 WITH（標準SQL、他DBへ移行しやすい書き方） | `WITH org (employee_id, last_name, manager_id, lvl, path) AS ( SELECT …` | PLANNED | CTE, PLAN_CROSS_PARTITION, PROJECTION, SET_OP | P1+P6 |
| 33 | G-4 CONNECT BY LEVEL による連番生成 [OR | `SELECT DATE '2026-09-01' + LEVEL - 1 AS cal_date FROM dual CONNECT BY…` | ERROR | APP_SEMANTICS, HIERARCHICAL, PROJECTION, RESIDUAL_H2 |  |
| 34 | H-1 フラッシュバック問合せ [ORA]（UNDO 保持期間内 | `SELECT employee_id, salary FROM employees AS OF TIMESTAMP (SYSTIMESTA…` | ERROR | PARSE |  |
| 35 | H-2 サンプリング [ORA] | `SELECT COUNT(*) FROM employees SAMPLE (50)` | PLANNED | CLAUSE, PLAN_CROSS_PARTITION | P1 |
| 36 | H-3 ROWID [ORA]（重複行削除などで頻出） | `SELECT ROWID, employee_id FROM employees WHERE ROWNUM <= 3` | ERROR | ROWID |  |
| 37 | H-4 WITH 句内での PL/SQL 関数定義（12c+）[ | `WITH FUNCTION annual(p_sal NUMBER, p_comm NUMBER) RETURN NUMBER IS BE…` | ERROR | PARSE |  |
| 38 |  | `END` | ERROR | STATEMENT |  |
| 39 |  | `SELECT last_name, annual(salary, commission_pct) AS annual_comp FROM …` | PLANNED | PROJECTION | P1 |

#### `03_sql_dml.sql` — 37 文 / OK 9 / WARN 6 / PLANNED 2 / ERROR 20（変換率 40.5%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 | A-1 単一行（シーケンス使用） | `INSERT INTO employees (employee_id, first_name, last_name, email, hir…` | ERROR | SEQUENCE |  |
| 2 | A-2 INSERT ... SELECT | `INSERT INTO emp_stage (employee_id, last_name, salary, department_id)…` | ERROR | INSERT_SELECT |  |
| 3 | A-3 複数表への条件付き INSERT（INSERT ALL  | `CREATE TABLE emp_high AS SELECT employee_id, salary FROM employees WH…` | ERROR | DDL |  |
| 4 |  | `CREATE TABLE emp_low AS SELECT employee_id, salary FROM employees WHE…` | ERROR | DDL |  |
| 5 |  | `INSERT FIRST WHEN salary >= 10000 THEN INTO emp_high (employee_id, sa…` | ERROR | STATEMENT |  |
| 6 | A-4 INSERT ALL による複数行挿入（23ai 未満で | `INSERT ALL INTO jobs VALUES ('MK_MAN', 'Marketing Manager', 9000, 150…` | ERROR | STATEMENT |  |
| 7 | B-1 相関副問合せによる更新 | `UPDATE employees e SET salary = salary * 1.05 WHERE salary < (SELECT …` | ERROR | RMW |  |
| 8 | B-2 複数列を副問合せで一括更新 | `UPDATE departments d SET (location) = (SELECT 'Tokyo' FROM dual) WHER…` | ERROR | SET |  |
| 9 | B-3 結合更新（インラインビュー更新）[ORA] | `UPDATE (SELECT e.salary, j.min_salary FROM employees e JOIN jobs j ON…` | ERROR | PARSE |  |
| 10 | B-4 DELETE（ROWID による重複削除の定番パターン） | `DELETE FROM emp_stage a WHERE a.ROWID > (SELECT MIN(b.ROWID) FROM emp…` | ERROR | ROWID |  |
| 11 |  | `UPDATE emp_stage SET salary = salary + 500` | ERROR | RMW |  |
| 12 |  | `MERGE INTO employees t USING (SELECT employee_id, salary FROM emp_sta…` | ERROR | PARSE |  |
| 13 |  | `ALTER TABLE emp_stage ADD CONSTRAINT emp_stage_sal_ck CHECK (salary <…` | ERROR | UNPARSED |  |
| 14 |  | `INSERT INTO emp_stage (employee_id, last_name, salary, department_id)…` | ERROR | PARSE |  |
| 15 |  | `SELECT ora_err_number$, ora_err_tag$, employee_id, salary FROM emp_er…` | OK |  |  |
| 16 |  | `SAVEPOINT before_raise` | ERROR | STATEMENT |  |
| 17 |  | `UPDATE employees SET salary = salary * 2 WHERE department_id = 60` | ERROR | RMW |  |
| 18 |  | `ROLLBACK TO SAVEPOINT before_raise` | ERROR | SAVEPOINT |  |
| 19 |  | `COMMIT` | OK |  |  |
| 20 |  | `SELECT employee_id, salary FROM employees WHERE department_id = 80 FO…` | WARN | LOCK |  |
| 21 |  | `ROLLBACK` | OK |  |  |
| 22 |  | `SELECT order_id FROM orders WHERE status = 'NEW' FOR UPDATE SKIP LOCK…` | WARN | LOCK |  |
| 23 |  | `ROLLBACK` | OK |  |  |
| 24 |  | `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` | ERROR | STATEMENT |  |
| 25 |  | `SELECT COUNT(*) FROM employees` | WARN | CROSS_PARTITION |  |
| 26 |  | `COMMIT` | OK |  |  |
| 27 | F-1 JSON を格納する表（19c 互換: CLOB + I | `CREATE TABLE products_json ( id NUMBER PRIMARY KEY, doc CLOB CONSTRAI…` | WARN | CHECK, TYPE |  |
| 28 |  | `INSERT INTO products_json VALUES (1, '{"name":"ScalarDB","tier":"Ente…` | WARN | INSERT_COLS |  |
| 29 |  | `INSERT INTO products_json VALUES (2, '{"name":"ScalarDL","tier":"Stan…` | WARN | INSERT_COLS |  |
| 30 |  | `COMMIT` | OK |  |  |
| 31 | F-2 値の取り出し（JSON_VALUE / JSON_QUE | `SELECT JSON_VALUE(doc, '$.name') AS name, JSON_VALUE(doc, '$.price' R…` | ERROR | PARSE |  |
| 32 | F-3 JSON_TABLE：JSON を行列に展開 | `SELECT p.id, jt.name, jt.tag FROM products_json p, JSON_TABLE(p.doc, …` | PLANNED | JOIN, PLAN_CROSS_PARTITION | P8 |
| 33 | F-4 リレーショナル → JSON 生成 | `SELECT JSON_OBJECT('dept' VALUE d.department_name, 'members' VALUE JS…` | PLANNED | PLAN_CROSS_PARTITION, PROJECTION | P1 |
| 34 | F-5 部分更新（19c+） | `UPDATE products_json SET doc = JSON_MERGEPATCH(doc, '{"price":1200,"s…` | ERROR | PARSE |  |
| 35 |  | `COMMIT` | OK |  |  |
| 36 |  | `DROP TABLE emp_high PURGE` | OK |  |  |
| 37 |  | `DROP TABLE emp_low PURGE` | OK |  |  |

#### `05_plsql_units.sql` — 9 文 / OK 3 / WARN 2 / PLANNED 1 / ERROR 3（変換率 55.6%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 |  | `CREATE TABLE emp_audit ( audit_id NUMBER GENERATED BY DEFAULT AS IDEN…` | ERROR | AUTO_INC, TYPE |  |
| 2 | 1 プロシージャ（IN / OUT / IN OUT、既定値 | `SELECT last_name, dept_name_of(department_id) AS dept, annual_comp(sa…` | PLANNED | PLAN_CROSS_PARTITION, PLAN_UNRESOLVED, PROJECTION | P1 |
| 3 | 3 パッケージ（仕様部 / 本体、オーバーロード、プライベー | `SELECT action, message FROM emp_audit` | WARN | CROSS_PARTITION |  |
| 4 | 5 トリガー | `CREATE OR REPLACE VIEW emp_dept_upd_v AS SELECT e.employee_id, e.last…` | ERROR | DDL |  |
| 5 |  | `UPDATE employees SET salary = salary * 1.01 WHERE department_id = 60` | ERROR | RMW |  |
| 6 |  | `UPDATE emp_dept_upd_v SET department_name = 'Sales' WHERE employee_id…` | OK |  |  |
| 7 |  | `SELECT employee_id, action, old_salary, new_salary FROM emp_audit WHE…` | WARN | CROSS_PARTITION |  |
| 8 |  | `ROLLBACK` | OK |  |  |
| 9 |  | `SELECT name, type, line, position, text FROM user_errors ORDER BY nam…` | OK |  |  |

#### `06_plsql_advanced.sql` — 3 文 / OK 1 / WARN 1 / PLANNED 1 / ERROR 0（変換率 66.7%）

| # | 節 | 元の SQL | 判定 | 指摘 | 実行計画 |
|---|---|---|---|---|---|
| 1 |  | `CREATE TABLE bulk_target ( employee_id NUMBER PRIMARY KEY, last_name …` | WARN | CHECK, TYPE |  |
| 2 | 1 BULK COLLECT（LIMIT 付きで大量件数でも | `SELECT * FROM TABLE(emp_grades(80)) ORDER BY grade, last_name` | PLANNED | UNSUPPORTED | P1 |
| 3 | 5 よく使う組込みパッケージ | `SELECT job_name, enabled, repeat_interval FROM user_scheduler_jobs` | OK |  |  |

### 付録 B: 実 DB 比較（SQL）
| # | 元の SQL | 変換 | 実行 | 結果 | 差の内容 |
|---|---|---|---|---|---|
| 10 | `SELECT SYSDATE, SYSTIMESTAMP, USER FROM dual` | PLANNED | 実行計画 P1 | FAIL | result mismatch (no row of the actual result equals expected (datetime.datetime(2026, 9, 24, 7, 59, 16), datet |
| 11 | `SELECT employee_id, last_name, salary, commission_pct FROM …` | WARN | ScalarDB SQL | PASS |  |
| 12 | `SELECT last_name, salary, CASE WHEN salary >= 15000 THEN 'H…` | PLANNED | 実行計画 P1 | PASS |  |
| 13 | `SELECT UPPER(last_name) AS upper_name, INITCAP(email) AS in…` | PLANNED | 実行計画 P1 | FAIL | result mismatch (no row of the actual result equals expected ('DE HAAN', 'Ldehaan', 'De ', 2, '000102', 'Lex D |
| 14 | `SELECT email, REGEXP_SUBSTR(email, '^[A-Z]') AS first_char,…` | PLANNED | 実行計画 P1 | PASS |  |
| 15 | `SELECT e.last_name, d.department_name FROM employees e JOIN…` | WARN | ScalarDB SQL | PASS |  |
| 16 | `SELECT e.last_name, d.department_name FROM employees e LEFT…` | WARN | ScalarDB SQL | PASS |  |
| 17 | `SELECT e.last_name, d.department_name FROM employees e, dep…` | WARN | ScalarDB SQL | PASS |  |
| 18 | `SELECT e.last_name, d.department_name FROM employees e FULL…` | PLANNED | 実行計画 P8 | FAIL |  |
| 19 | `SELECT w.last_name AS employee, m.last_name AS manager FROM…` | WARN | ScalarDB SQL | PASS |  |
| 20 | `SELECT d.department_name, t.last_name, t.salary FROM depart…` | PLANNED | 実行計画 P8 | FAIL |  |
| 21 | `SELECT last_name, salary, (SELECT ROUND(AVG(salary)) FROM e…` | PLANNED | 実行計画 P1 | PASS |  |
| 22 | `SELECT e.last_name, e.department_id, e.salary FROM employee…` | PLANNED | 実行計画 P5 | PASS |  |
| 23 | `SELECT d.department_name FROM departments d WHERE NOT EXIST…` | PLANNED | 実行計画 P5 | PASS |  |
| 24 | `SELECT last_name, department_id, salary FROM employees WHER…` | PLANNED | 実行計画 P5 | PASS |  |
| 25 | `SELECT department_id FROM departments MINUS SELECT departme…` | PLANNED | 実行計画 P6 | PASS |  |
| 26 | `SELECT job_id FROM employees WHERE department_id = 80 INTER…` | PLANNED | 実行計画 P6 | PASS |  |
| 27 | `SELECT department_id, COUNT(*) AS cnt, SUM(salary) AS total…` | PLANNED | 実行計画 P1 | PASS |  |
| 28 | `SELECT CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE …` | ERROR | — | NOT_CONVERTIBLE | main query: expressions in the select list (CASE GROUPING(department_id) WHEN 1 THEN '全部門' ELSE TO_CHAR(depart |
| 29 | `SELECT department_id, job_id, SUM(salary) FROM employees GR…` | ERROR | — | NOT_CONVERTIBLE | main query: GROUP BY GROUPING SETS -- aggregate each level in the application; the H2 residual engine cannot r |
| 30 | `SELECT department_id, LISTAGG(last_name, ', ') WITHIN GROUP…` | PLANNED | 実行計画 P1+P7 | PASS |  |
| 31 | `SELECT * FROM (SELECT department_id, job_id, salary FROM em…` | ERROR | — | NOT_CONVERTIBLE | FROM must reference exactly one base table (no subqueries); main query: derived table in FROM -- evaluate it i |
| 32 | `SELECT employee_id, pay_type, amount FROM (SELECT employee_…` | ERROR | — | NOT_CONVERTIBLE | FROM must reference exactly one base table (no subqueries); main query: derived table in FROM -- evaluate it i |
| 33 | `SELECT department_id, last_name, salary, ROW_NUMBER() OVER …` | PLANNED | 実行計画 P1 | CASE_ERROR | source database rejected the statement: ORA-00923: FROM keyword not found where expected |
| 34 | `SELECT department_id, last_name, salary, ROW_NUMBER() OVER …` | PLANNED | 実行計画 P1 | FAIL | result mismatch (row 13: expected (90, 'Kochhar', 17000.0, 2, 2, 2, 58000, 0.293, 24000, 'Hunold', 4), actual  |
| 35 | `SELECT order_date, total, SUM(total) OVER (ORDER BY order_d…` | PLANNED | 実行計画 P1 | PASS |  |
| 36 | `SELECT department_id, MAX(last_name) KEEP (DENSE_RANK FIRST…` | ERROR | — | NOT_CONVERTIBLE | projection 'MAX(last_name) KEEP (DENSE_RANK FIRST ORDER BY salary DESC)' is an expression; ScalarDB SQL only s |
| 37 | `SELECT last_name, salary FROM employees ORDER BY salary DES…` | PLANNED | 実行計画 P1 | FAIL | result mismatch (row 2: expected ('Kochhar', 17000.0), actual ('De Haan', 17000)): expected [('King', 24000.0) |
| 38 | `SELECT last_name, salary FROM employees ORDER BY salary DES…` | PLANNED | 実行計画 P4 | PASS |  |
| 39 | `SELECT * FROM (SELECT a.*, ROWNUM rnum FROM (SELECT last_na…` | PLANNED | 実行計画 P5 | PASS |  |
| 40 | `WITH dept_stats AS ( SELECT department_id, AVG(salary) AS a…` | PLANNED | 実行計画 P1+P6 | PASS |  |
| 41 | `SELECT LEVEL, LPAD(' ', 2 * (LEVEL - 1)) || last_name AS or…` | ERROR | — | NOT_CONVERTIBLE | main query: START WITH / CONNECT BY with CONNECT_BY_ISLEAF, CONNECT_BY_ROOT, LEVEL, SYS_CONNECT_BY_PATH -- wal |
| 42 | `WITH org (employee_id, last_name, manager_id, lvl, path) AS…` | PLANNED | 実行計画 P1+P6 | FAIL |  |
| 43 | `SELECT DATE '2026-09-01' + LEVEL - 1 AS cal_date FROM dual …` | ERROR | — | NOT_CONVERTIBLE | main query: START WITH / CONNECT BY with LEVEL -- walk the tree in the application (appside.Hierarchy) or prec |
| 44 | `SELECT employee_id, salary FROM employees AS OF TIMESTAMP (…` | ERROR | — | CASE_ERROR | source database rejected the statement: ORA-01466: unable to read data - table definition has changed |
| 45 | `SELECT COUNT(*) FROM employees SAMPLE (50)` | PLANNED | 実行計画 P1 | FAIL |  |
| 46 | `SELECT ROWID, employee_id FROM employees WHERE ROWNUM <= 3` | ERROR | — | NOT_CONVERTIBLE | pseudo-column ROWID does not exist in ScalarDB; use the primary key |
| 47 | `SELECT JSON_VALUE(doc, '$.name') AS name, JSON_VALUE(doc, '…` | ERROR | — | NOT_CONVERTIBLE | Expecting ). Line 6, Col: 42. |
| 48 | `SELECT p.id, jt.name, jt.tag FROM products_json p, JSON_TAB…` | PLANNED | 実行計画 P8 | FAIL | DB-SQL-10026: Syntax error. Line 1:14 no viable alternative at input 'SELECT * FROM "') |
| 49 | `SELECT JSON_OBJECT('dept' VALUE d.department_name, 'members…` | PLANNED | 実行計画 P1 | FAIL | result mismatch (no row of the actual result equals expected ('{"dept":"Administration","members":[{"id":200," |

### 付録 C: PL/SQL 判定（routine ごと）
| routine | 判定 | ルール | 理由（先頭） |
|---|---|---|---|
| `annual_comp` | REVIEW |  | confidence factor testEvidence is 0 |
| `b04_1_variables` | REVIEW |  | confidence factor testEvidence is 0 |
| `b04_2_control_flow` | REVIEW |  | confidence factor testEvidence is 0 |
| `b04_3_implicit_cursor_attrs` | REDESIGN | SQL-004, SQL-001, TX-001, TX-004, TRG-002 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b04_4_1_explicit_cursor` | REVIEW | CUR-001 | CUR-001: 明示 cursor は寿命がトランザクション境界をまたぎます; CUR-001: 明示 cursor は寿命がトランザクション境界をまたぎます |
| `b04_4_2_cursor_for_loop` | REVIEW | CUR-002, SQL-002 | CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size  |
| `b04_4_3_for_update_current_of` | REDESIGN | CUR-002, SQL-004, LOCK-001, LOCK-002, SQL-001, TX-001, TRG-002 | LOCK-001: 行ロックです。ターゲットで同じ保証を別の方法で与える設計が要ります; LOCK-002: cursor の宣言で行ロックしています。文だけを |
| `b04_4_4_ref_cursor` | REVIEW | LOWER-001, CUR-001 | LOWER-001: lowering がまだ模していない構文です。意味が保てる保証がありません; LOWER-001: lowering がまだ模していない構 |
| `b04_5_records_collections` | REVIEW | LOWER-001, CUR-002 | LOWER-001: lowering がまだ模していない構文です。意味が保てる保証がありません; LOWER-001: lowering がまだ模していない構 |
| `b04_6_1_predefined_exceptions` | REVIEW | SELECT-OPT-001 | confidence factor testEvidence is 0 |
| `b04_6_2_user_exceptions` | REDESIGN | TX-001, TRG-002 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b05_1_call_raise_salary` | REDESIGN | TX-001 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b05_3_call_emp_api` | REDESIGN | TX-001 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b05_4_call_log_msg` | REDESIGN | SQL-004, SQL-001, TX-001, TRG-002 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b06_1_bulk_collect_limit` | REVIEW | SCAN-002, CUR-002, BULK-003 | CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size  |
| `b06_2_2_forall_returning` | REDESIGN | SQL-001, BULK-001, TX-001 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b06_2_forall_save_exceptions` | REDESIGN | SCAN-002, CUR-002, BULK-003, TX-001 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `b06_3_6_dbms_sql` | REDESIGN | DYN-003, CALL-001 | DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります; DYN-003: DB |
| `b06_3_native_dynamic_sql` | REDESIGN | DYN-001, DYN-002, DYN-OPT-002, LOWER-001, CUR-001, TX-001 | DYN-001: 表名など識別子が実行時に決まる SQL です。allowlist か専用 Repository への再設計が要ります; TX-001: rou |
| `b06_4_collection_in_sql` | REVIEW | SELECT-001, SEM-004, SQL-002, BULK-001 | SELECT-001: キーで届かない SELECT INTO で、ScalarDB がそのまま実行できる文ではありません。0 件と複数件の意味（NO_DATA |
| `b06_5_2_scheduler_job` | REDESIGN | CALL-001, EXT-001 | EXT-001: UTL_* / DBMS_SCHEDULER / AQ などの外部副作用があります |
| `b06_5_builtin_packages` | REVIEW | CALL-001 | CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません |
| `b06_6_conditional_compilation` | REVIEW |  | confidence factor testEvidence is 0 |
| `dept_name_of` | REVIEW |  | confidence factor testEvidence is 0 |
| `dml_d_create_error_log` | REVIEW | CALL-001 | CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません |
| `emp_api.call_count` | REDESIGN | STATE-001 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります |
| `emp_api.get_by_dept` | REDESIGN | LOWER-001, STATE-001 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります |
| `emp_api.give_raise~1` | REDESIGN | SQL-004, SQL-001, STATE-001, TRG-002 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります; TRG-002 |
| `emp_api.give_raise~2` | REDESIGN | SQL-001, STATE-001, TRG-002 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります; TRG-002 |
| `emp_api.hire` | REDESIGN | EXC-001, SQL-001, STATE-001, TRG-002 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります; TRG-002 |
| `emp_api.validate_pct` | REDESIGN | STATE-001 | STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります |
| `emp_biu_trg.body` | REDESIGN | TRG-001 | TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります |
| `emp_dept_cap_trg.body` | REDESIGN | TRG-001 | TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります |
| `emp_dept_upd_v_trg.body` | REDESIGN | SQL-001, TRG-001 | TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります |
| `emp_grades` | REVIEW | LOWER-001, CUR-002, SQL-002 | LOWER-001: lowering がまだ模していない構文です。意味が保てる保証がありません; CUR-002: Cursor FOR LOOP です。走査 |
| `emp_salary_audit_trg.body` | REDESIGN | SQL-001, TRG-001 | TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります |
| `log_msg` | REDESIGN | SQL-001, TX-001, TX-002 | TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません |
| `normalize_name` | REVIEW |  | confidence factor testEvidence is 0 |
| `raise_salary` | REDESIGN | SQL-004, SQL-001, TRG-002 | TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigg |
| `setup_drop_objects` | REDESIGN | DYN-001, DYN-002, CUR-002 | DYN-001: 表名など識別子が実行時に決まる SQL です。allowlist か専用 Repository への再設計が要ります |
| `setup_gather_stats` | REVIEW | CALL-001 | CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません |

### 付録 D: PL/SQL 実 DB 比較
| シナリオ | routine | 結果 | 差の内容 |
|---|---|---|---|
| `annual_comp_null_comm` | `annual_comp.annual_comp` | 一致 |  |
| `annual_comp_with_comm` | `annual_comp.annual_comp` | 一致 |  |
| `b04_1_variables` | `b04_1_variables.b04_1_variables` | 一致 |  |
| `b04_2_control_flow` | `b04_2_control_flow.b04_2_control_flow` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in Loop: for loop) |
| `b04_3_implicit_cursor_attrs` | `b04_3_implicit_cursor_attrs.b04_3_implicit_cursor_attrs` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (SET salary = salary + 100: expressions referencing columns are not allowed; do SELECT -> compute  |
| `b04_4_1_explicit_cursor` | `b04_4_1_explicit_cursor.b04_4_1_explicit_cursor` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (OpenCursor is not translated) |
| `b04_4_2_cursor_for_loop` | `b04_4_2_cursor_for_loop.b04_4_2_cursor_for_loop` | 一致 |  |
| `b04_4_3_for_update_current_of` | `b04_4_3_for_update_current_of.b04_4_3_for_update_current_of` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in Loop: cursor FOR loop whose body writes ['employees'], which its own query reads) |
| `b04_6_1_predefined_exceptions` | `b04_6_1_predefined_exceptions.b04_6_1_predefined_exceptions` | 一致 |  |
| `b04_6_2_user_exceptions` | `b04_6_2_user_exceptions.b04_6_2_user_exceptions` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in Call: SQLERRM) |
| `b06_1_bulk_collect_limit` | `b06_1_bulk_collect_limit.b06_1_bulk_collect_limit` | 一致 |  |
| `b06_2_forall_save_exceptions` | `b06_2_forall_save_exceptions.b06_2_forall_save_exceptions` | 相違 | exception: expected=none actual=java.sql.SQLDataException (Invalid data type (DB-SQL-10016: The type java.math.BigDecimal is not supported)); table bulk_target row count: |
| `b06_3_6_dbms_sql` | `b06_3_6_dbms_sql.b06_3_6_dbms_sql` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in declaration c: DBMS_SQL.OPEN_CURSOR) |
| `b06_3_native_dynamic_sql` | `b06_3_native_dynamic_sql.b06_3_native_dynamic_sql` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in DynamicSql: EXECUTE IMMEDIATE whose statement is not a knowable set) |
| `b06_4_collection_in_sql` | `b06_4_collection_in_sql.b06_4_collection_in_sql` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in SqlOperation: execution plan result) |
| `b06_5_builtin_packages` | `b06_5_builtin_packages.b06_5_builtin_packages` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (unresolved in Call: named arguments of a routine that is not in the program) |
| `b06_6_conditional_compilation` | `b06_6_conditional_compilation.b06_6_conditional_compilation` | 一致 |  |
| `dept_name_of_missing` | `dept_name_of.dept_name_of` | 一致 |  |
| `dept_name_of_ok` | `dept_name_of.dept_name_of` | 一致 |  |
| `log_msg_ok` | `log_msg.log_msg` | 相違 | exception: expected=none actual=java.lang.UnsupportedOperationException (INSERT must specify the full primary key; missing ['audit_id']); table emp_audit row count: expec |
| `normalize_name_ok` | `normalize_name.normalize_name` | 一致 |  |
| `raise_salary_missing` | `raise_salary.raise_salary` | 相違 | exception code: expected=-20010 actual=java.lang.UnsupportedOperationException (RETURNING is not supported) |
| `raise_salary_ok` | `raise_salary.raise_salary` | 相違 | out p_new_sal: missing (expected only) = 6600; exception: expected=none actual=java.lang.UnsupportedOperationException (RETURNING is not supported); table employees row e |
