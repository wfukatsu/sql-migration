# corpus の外から来た PL/SQL

`fixtures/plsql/` の corpus は合成で、ルールと一緒に育ってきた。ここには**外から受け取った routine** を、corpus とは
別のプロジェクトとして置く。KPI の分母には入れない（期待判定が無い）。実案件のコードを KPI の分母に入れるときは、ここではなく
corpus に `origin: real-anonymized` として入れる。匿名化の方針と手順は [plsql-corpus-anonymization.md](../../docs/design/plsql-corpus-anonymization.md)（#15）。

置く理由は 2 つある。

- corpus が一度も踏んでいない形を踏む。1 本目の `create_order` で、SQL の外で取る採番（`v_id := seq.NEXTVAL`）が
  compile できない不具合が見つかった（2026-09-20、修正済み）。
- 実 DB のハーネスを corpus 以外に向けられることを保つ（`--project`）。

## プロジェクトの形

```
<project>/
  src/schema.sql          Oracle の DDL（%TYPE の解決、deploy、金額列の判定に使う）
  src/*.pks,*.pkb,*.prc   PL/SQL
  scalardb-schema.json    Schema Loader の JSON。namespace は corpus と別にする
  limits.yaml             プロジェクトの決定（行ロック、境界、走査行数、DB link）
  scenarios/*.yaml        fixtures/plsql/scenarios/README.md と同じ形式
  golden/*.json           Oracle の capture
  work/                   生成物・setup・ScalarDB の capture・比較（git には入れない）
```

表・sequence・deploy の順序は DDL とファイル名から読む（`plsql_run.use_project`）。corpus のように手で並べた一覧は無い。

## Claude Code から進める

`migrate-flow` スキルが、現行の仕様 → 変換と人の判断 → 変換後の仕様 → 承認 → テストの順に進める。作業ディレクトリは
`out/migrate/<project>/`（git には入れない）で、`limits.yaml` と記録はこのプロジェクトのディレクトリに置く。
下の「流し方」は、その最後の段階（テスト）で回すコマンドである。

```sh
P=fixtures/plsql-external/create_order
python skills/migrate-flow/scripts/flow.py init --out out/migrate/create_order --kind plsql --src $P/src \
    --scalardb-schema $P/scalardb-schema.json --limits $P/limits.yaml
python skills/migrate-flow/scripts/flow.py status --out out/migrate/create_order
```

比較が済んだら `flow.py tested --out out/migrate/create_order --result pass --report difftest/work/plsql-diff.json` で
結果を控える。同じファイルを `migration_doc.py` の `--evidence` に渡して文書を作り直し、`converted` の承認を取り直す
（`converted` だけを取り直すかぎり、テストの結果は残る）。承認のあとで `src/` の原文を変えると、`spec` の承認は古くなる。

`create_order` で書き上げた現行の仕様は `skills/plsql-spec/examples/create_order/`、変換後の文書は
`skills/plsql-migrate/examples/create_order/` にある（後者の `evidence.json` は、下の比較の結果の写しである）。

## 流し方

Oracle 側はプロジェクト専用のユーザで繋ぐ。`deploy` は DDL にある表を drop して作り直す。

```sh
P=fixtures/plsql-external/create_order
export SRC_ORACLE_USER=shop SRC_ORACLE_PASSWORD=shop        # 先に SYSTEM でユーザを作っておく
python difftest/plsql_run.py deploy --project $P
python difftest/plsql_run.py run --project $P               # -> $P/golden

# ScalarDB 側: namespace を読み込み、生成して、setup を変換し、capture を取る
cp $P/scalardb-schema.json difftest/work/shop-schema.json   # loader のコンテナから見える場所
(cd difftest && docker compose --profile tools --profile oracle --profile cassandra --profile cluster run --rm schema-loader \
    --config /conf/scalardb-in-docker.properties --schema-file /work/shop-schema.json --coordinator)
python difftest/plsql_capture.py --project $P --namespace shop --variant double

python difftest/plsql_compare.py --project $P --variant double --json $P/work/plsql-diff.json
```

`plsql_capture.py --project` は、プロジェクトの `limits.yaml` で生成し（`work/generated`）、setup を変換し、生成した Java を Cluster で流し
（`SCALARDB_IT=1`、`-Dplsql.project`・`-Dplsql.namespace`・`-Pplsql.generatedDir`・`-Pplsql.buildDir` つきの `ScalarDbCaptureIT`）、最後に
**何を測ったかの指紋**（`work/plsql-scalardb-<variant>/fingerprint.json`）を書く。指紋が無い比較は `plsql.cli --evidence` が「古い証拠」として
数えないので、Gradle を手で回しただけでは routine は AUTO にならない（2026-09-20 に `samples/tutorial` で見つけて直した）。
Schema Loader は、compose のすべての backend の profile を付けないと `invalid compose project` で止まる。

## create_order（2026-09-20）

受注を 1 件作る procedure。**DDL は受け取っておらず、`src/schema.sql` は routine の使い方から置いた推定である**
（本物が来たら置き換えて取り直す）。金額列は DOUBLE の規約で流した。

- 判定: REDESIGN（`FOR UPDATE` の行ロック、LOCK-001）。決定は 1 つ: 同じトランザクションで読んで書く楽観制御にし、
  呼び出し側は衝突だけを再試行する（`limits.yaml`）。
- 実 DB: 7 シナリオすべてが Oracle と一致（表の状態・OUT 引数・例外コード）。うち 1 件は**受け入れた差** 1 点つき。
  - 一致したもの: 正常系、在庫ちょうど、数量 0（-20001）、在庫不足（-20002）、商品なし（NO_DATA_FOUND → -20003）、
    **小数の数量**（`p_quantity` は NUMBER。2.5 個 × 19.99 = 49.98、NUMBER(10) の列へは 3 と 8 に丸めて入る）。
  - 受け入れた差: `create_order_duplicate_id`。採番した番号の受注が既にあるとき、Oracle は `DUP_VAL_ON_INDEX` の
    handler が -20004 に変える。ScalarDB は重複 INSERT をその文では弾かず、commit 時の衝突（`DB-CORE-20013`）として
    返すので、handler は走らない。どちらも何も書かない。解析が EXC-001 で先に指摘していたとおりの差である。
    **受け入れる**（2026-09-20 の決定）: 受注番号は採番で取るので、採番が一意であるかぎり実運用では起きない。
    -20004 を再現するには INSERT の前に存在を読む必要があり、受注のたびに読みが 1 回増えるので採らない。
    シナリオの `accepted_difference` に理由つきで書いてあり、比較の報告には「受け入れた差」として出る。
