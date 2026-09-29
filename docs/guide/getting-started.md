# はじめに

[文書の入口](../README.md) ｜ [はじめに](getting-started.md) ｜ [チュートリアル](tutorial.md) ｜ [SQL の変換](sql-conversion.md) ｜ [PL/SQL の変換](plsql-conversion.md) ｜ [スキル](skills.md) ｜ [検証環境](verification.md)

準備から、DB を使わずに試せるところまでを順に進めます。ここまでは Docker も ScalarDB のライセンスも要りません。
入力には、リポジトリに入れてある小さな題材 [docs/quickstart/](../quickstart/README.md)（図書の貸出。合成の SQL 16 文と PL/SQL の package 1 つ）を使います。
公開の GitHub の版でも、手順 1〜5 はそのまま動きます。自分の SQL や PL/SQL で試すときは、パスを差し替えてください。

| 手順 | 要るもの |
|---|---|
| 1〜3. SQL を変換し、実行計画を確かめる | Python 3.10 以上、（実行計画の確認だけ）Java 17 |
| 4. PL/SQL を解析して判定を見る | Python 3.10 以上 |
| 5. 移行の前に、いまの姿を見る | Python 3.10 以上、ブラウザ |
| 6. テストを回す（開発側のリポジトリだけ） | Python 3.10 以上、Java 17 |
| その先: 実 DB での突き合わせ（開発側のリポジトリだけ） | Docker、ScalarDB Cluster のトライアルライセンス（[検証環境](verification.md)） |

## 1. 準備

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # 変換ツールの依存
.venv/bin/pip install -r requirements-dev.txt      # pytest / hypothesis。テストを回すときだけ（開発側のリポジトリ）
.venv/bin/pip install -r requirements-difftest.txt # DB ドライバと DuckDB。difftest/ のハーネスを動かすときだけ
(cd runtime-java && ./gradlew installDist)          # Java 17。実行計画を動かすときだけ
```

`.venv/bin/python` の代わりに `bin/python` も使えます（どの作業ディレクトリからでも動き、仮想環境が無ければ作ります。スキルはこちらを使います）。
実 DB のハーネス（`difftest/`）とその依存（`requirements-difftest.txt`）は、開発側のリポジトリにだけあります。

## 2. SQL を変換する

```bash
.venv/bin/python -m scalardb_migrate.cli docs/quickstart/library.sql --source oracle --out-dir out --plan-dir out/plans
```

```text
[  3] OK    CREATE       CREATE INDEX idx_loans_member ON loans (member_id)
        INFO  INDEX: index name 'idx_loans_member' dropped: ScalarDB identifies indexes by table + column
        => CREATE INDEX ON loans (member_id)
[  4] ERROR CREATE       CREATE SEQUENCE loan_seq START WITH 1
        ERROR DDL: CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)
[  5] OK    SELECT       SELECT book_id, title, status FROM books WHERE book_id = :book_id
        INFO  ACCESS: SELECT: full primary key specified -> GET (single record)
        => SELECT book_id, title, status FROM books WHERE book_id = :book_id
[  7] PLANNED SELECT       SELECT title, NVL(updated_at, DATE '2000-01-01') AS last_update FROM b
        ERROR PROJECTION: main query: expressions in the select list (NVL(updated_at, TO_DATE('2000-01-01', 'YYYY-MM-DD'))) -- compute them in the application
        INFO  PLAN_FETCH: CROSS_PARTITION: SELECT book_id, title, shelf, updated_at FROM books WHERE shelf = 'A1'
        INFO  PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)
        WARN  PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan
...
16 statements: OK=7 WARN=5 PLANNED=1 ERROR=3
```

文ごとに判定が付きます。`--out-dir` には変換後の SQL（`.scalardb.sql`）、レポート（`.report.md` / `.json`）、スキーマ（`.schema.json`）、
`--plan-dir` には実行計画（`.plan.json`）が出ます。ERROR の文が 1 つでもあると終了コードは 1 です（上の例は `CREATE SEQUENCE`、
`loan_seq.NEXTVAL`、`SET returned_at = SYSDATE` の 3 文が ERROR なので 1）。

| 判定 | 意味 |
|---|---|
| **OK** / **WARN** | ScalarDB SQL に変換できた（WARN は意味や性能に注意がある。上の題材では、パーティションをまたぐ走査や `ROWNUM` の書き換え） |
| **PLANNED** | ScalarDB SQL にはできないが、実行計画（ScalarDB から取得 → H2 で元の SQL）で動かせる |
| **ERROR** | 自動では移行できない。レポートに理由と対応案が出る |

オプションと出力の詳細は [SQL の変換と実行計画](sql-conversion.md)。

## 3. 実行計画をオフラインで確かめる

```bash
runtime-java/build/install/residual-runner/bin/residual-runner validate --plan out/plans/library.7.plan.json
```

H2 で元の SQL がコンパイルできることだけを確かめます（`{"problems":[],"ok":true,"unresolved":[]}` が出ます）。実際に ScalarDB から取得して動かすのは、
[検証環境](verification.md) を立ててからです。

## 4. PL/SQL を解析して判定を見る

```bash
.venv/bin/python -m plsql.cli docs/quickstart/plsql/src --scalardb-schema docs/quickstart/plsql/scalardb-schema.json \
    --out-dir out/plsql-first
```

```text
parse rate      100.0%  (2/2 files)
type resolution 100.0%  (13/13 typed symbols)
scalardb        85.7% runnable  {'OK': 5, 'WARN': 1, 'ERROR': 1}
verdicts        {'REDESIGN': 1, 'REVIEW': 2}  (no --evidence: nothing can be AUTO)
```

routine ごとに **AUTO**（無人で生成してよい）/ **REVIEW**（人が確認する）/ **REDESIGN**（設計を決め直す）が付きます。
AUTO は実 Oracle と実 ScalarDB で結果が一致した証拠（`--evidence`）が無ければ付かないので、DB なしの解析では AUTO は出ません。
`out/plsql-first/unresolved.md` に、REVIEW / REDESIGN の理由と、受け入れに要るテストが出ます。この題材では、本の行を
`FOR UPDATE` でロックする `lend_book` が REDESIGN（`LOCK-001`。代わりの設計、たとえば楽観制御と再試行を人が決める）、
残りの 2 つは証拠が無いので REVIEW です。

サンプルをスキルで最後まで通した記録は [チュートリアル](tutorial.md)（開発側のリポジトリで通した記録）。Java の生成、証拠の取り方、判定の読み方は
[PL/SQL → Java 変換](plsql-conversion.md)。Claude Code や Codex から、仕様の調査 → 承認 → 変換 → 承認 → テストの順に
進めるなら [スキル](skills.md) の migrate-flow を使います。

## 5. 移行の前に、いまの姿を見る

どの PL/SQL と SQL がどのテーブルを触るのか、その行はどこか、判定はなぜそうなったのかを、1 つの HTML で見られます。上の 4 の解析結果をそのまま使います（DB は要りません）。

```bash
.venv/bin/python -m plsql.explorer out/plsql-first --src docs/quickstart/plsql/src --sql docs/quickstart/library.sql \
    --out out/plsql-first/explorer.html
open out/plsql-first/explorer.html      # macOS。ほかの OS では、このファイルをブラウザで開く
```

DB にしかない情報（制約、外部キー、索引、行数、統計、DB で動いているコード）は、実 DB で 1 回だけ流す SELECT だけの収集スクリプトの snapshot から読みます。
渡さなければ、その欄は「未取得」と出ます。snapshot の中身と取り方は [Migration Explorer](explorer.md)（収集スクリプトは開発側のリポジトリにだけあります）。

## 6. テスト（開発側のリポジトリだけ）

テスト（`tests/`）、corpus（`fixtures/`）、実 DB のハーネス（`difftest/`）は、開発側のリポジトリにだけあります。公開の GitHub の版には入っていないので、
この節のコマンドは開発側のリポジトリで流します。

DB の要らないテスト（pytest、Java の単体テスト）は、merge の前に手元で
回します。main へ merge したあとは、開発側の CI も同じものを回します。DB の要る検証（`difftest/`）は手で回します。

```bash
.venv/bin/python -m pytest -q          # 変換ツール・PL/SQL 変換・スキル（図の描画のテストは mmdc が無ければ skip）
# 実行基盤。Java のテストは生成した Java を一緒にコンパイルするので、generated/（git 管理外）が先に要る
.venv/bin/python -m plsql.generate fixtures/plsql/src --scalardb-schema fixtures/plsql/scalardb-schema.json \
    --limits fixtures/plsql/limits.yaml --out-dir generated
(cd runtime-java && ./gradlew test)
```

`runtime-java/` の依存は `gradle.lockfile` で固定しています（更新は `./gradlew dependencies --write-locks`）。ScalarDB SQL の JDBC ドライバと Cluster のクライアント SDK（商用ライセンス）、MySQL のドライバ（GPL）、Oracle のドライバ（OTN）は実行時にだけ要るので、持ち込めない環境では `./gradlew -PcoreOnly test installDist` で外せます（`gradle-core.lockfile`。Core API の取得・ローダ・残りの SQL の実行は動き、`--fetcher jdbc` と Bench は動きません）。ScalarDB Core 自身が推移的に持つドライバ（ojdbc8 など）は残ります。
