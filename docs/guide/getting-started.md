# はじめに

[文書の入口](../README.md) ｜ [はじめに](getting-started.md) ｜ [チュートリアル](tutorial.md) ｜ [SQL の変換](sql-conversion.md) ｜ [PL/SQL の変換](plsql-conversion.md) ｜ [スキル](skills.md) ｜ [検証環境](verification.md)

準備から、DB を使わずに試せるところまでを順に進めます。ここまでは Docker も ScalarDB のライセンスも要りません。

| 手順 | 要るもの |
|---|---|
| 1〜3. SQL を変換し、実行計画を確かめる | Python 3、（実行計画の確認だけ）Java 17 |
| 4. PL/SQL を解析して判定を見る | Python 3 |
| 5. テストを回す | Python 3、Java 17 |
| その先: 実 DB での突き合わせ | Docker、ScalarDB Cluster のトライアルライセンス（[検証環境](verification.md)） |

## 1. 準備

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # sqlglot / pytest / duckdb
.venv/bin/pip install -r requirements-difftest.txt # DB ドライバ。difftest/ のハーネスを動かすときだけ
(cd runtime-java && ./gradlew installDist)          # Java 17。実行計画を動かすときだけ
```

## 2. SQL を変換する

```bash
.venv/bin/python -m scalardb_migrate.cli samples/oracle.sql --source oracle --out-dir out --plan-dir out/plans
```

```text
[  3] OK    CREATE       CREATE INDEX idx_emp_deptno ON emp (deptno)
        INFO  INDEX: index name 'idx_emp_deptno' dropped: ScalarDB identifies indexes by table + column
        => CREATE INDEX ON emp (deptno)
[  4] ERROR CREATE       CREATE SEQUENCE emp_seq START WITH 1
        ERROR DDL: CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)
[  8] PLANNED SELECT       SELECT e.ename, NVL(e.sal, 0) AS sal FROM emp e WHERE e.deptno IN (10,
        ERROR PROJECTION: main query: expressions in the select list (NVL(e.sal, 0)) -- compute them in the application
        INFO  PLAN_FETCH: CROSS_PARTITION: SELECT empno, ename, sal, deptno FROM emp WHERE (deptno = 10 OR deptno = 20 OR deptno = 30)
        INFO  PLAN_RESIDUAL: H2 Oracle mode runs the original SQL (pattern P1, H2 indexes off)
        WARN  PLAN_CROSS_PARTITION: a fetch needs a cross-partition scan
```

文ごとに判定が付きます。`--out-dir` には変換後の SQL（`.scalardb.sql`）、レポート（`.report.md` / `.json`）、スキーマ（`.schema.json`）、
`--plan-dir` には実行計画（`.plan.json`）が出ます。ERROR の文が 1 つでもあると終了コードは 1 です（上の例は `CREATE SEQUENCE` があるので 1）。

| 判定 | 意味 |
|---|---|
| **OK** / **WARN** | ScalarDB SQL に変換できた（WARN は意味や性能に注意がある） |
| **PLANNED** | ScalarDB SQL にはできないが、実行計画（ScalarDB から取得 → H2 で元の SQL）で動かせる |
| **ERROR** | 自動では移行できない。レポートに理由と対応案が出る |

オプションと出力の詳細は [SQL の変換と実行計画](sql-conversion.md)。

## 3. 実行計画をオフラインで確かめる

```bash
runtime-java/build/install/residual-runner/bin/residual-runner validate --plan out/plans/oracle.8.plan.json
```

H2 で元の SQL がコンパイルできることだけを確かめます。実際に ScalarDB から取得して動かすのは [検証環境](verification.md) を立ててからです。

## 4. PL/SQL を解析して判定を見る

```bash
.venv/bin/python -m plsql.cli fixtures/plsql-external/create_order/src --out-dir out/plsql-first
```

```text
parse rate      100.0%  (1/1 files)
type resolution 100.0%  (7/7 typed symbols)
scalardb        50.0% runnable  {'WARN': 1, 'ERROR': 2, 'OK': 1}
verdicts        {'REDESIGN': 1}  (no --evidence: nothing can be AUTO)
```

routine ごとに **AUTO**（無人で生成してよい）/ **REVIEW**（人が確認する）/ **REDESIGN**（設計を決め直す）が付きます。
AUTO は実 Oracle と実 ScalarDB で結果が一致した証拠（`--evidence`）が無ければ付かないので、DB なしの解析では AUTO は出ません。
`out/plsql-first/unresolved.md` に、REVIEW / REDESIGN の理由と、受け入れに要るテストが出ます。

サンプルをスキルで最後まで通した記録は [チュートリアル](tutorial.md)。Java の生成、証拠の取り方、判定の読み方は [PL/SQL → Java 変換](plsql-conversion.md)。Claude Code や Codex から、仕様の調査 → 承認 → 変換 → 承認 → テストの順に
進めるなら [スキル](skills.md) の migrate-flow を使います。

## 5. テスト

DB の要らないテストは CI でも回ります（`.github/workflows/ci.yml` と `.gitlab-ci.yml`、同じ内容）: pytest、同梱コピーの
一致（`vendor_sync.py --check`）、Java の単体テスト。DB の要る検証（`difftest/`）は手で回します。

```bash
.venv/bin/python -m pytest -q          # 変換ツール・PL/SQL 変換・スキル（図の描画のテストは mmdc が無ければ skip）
# 実行基盤。Java のテストは生成した Java を一緒にコンパイルするので、generated/（git 管理外）が先に要る
.venv/bin/python -m plsql.generate fixtures/plsql/src --scalardb-schema fixtures/plsql/scalardb-schema.json \
    --limits fixtures/plsql/limits.yaml --out-dir generated
(cd runtime-java && ./gradlew test)
```

`runtime-java/` の依存は `gradle.lockfile` で固定しています（更新は `./gradlew dependencies --write-locks`）。ScalarDB SQL の JDBC ドライバと Cluster のクライアント SDK（商用ライセンス）、MySQL のドライバ（GPL）、Oracle のドライバ（OTN）は実行時にだけ要るので、持ち込めない環境では `./gradlew -PcoreOnly test installDist` で外せます（`gradle-core.lockfile`。Core API の取得・ローダ・残りの SQL の実行は動き、`--fetcher jdbc` と Bench は動きません）。ScalarDB Core 自身が推移的に持つドライバ（ojdbc8 など）は残ります。
