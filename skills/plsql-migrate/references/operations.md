# 生成物・limits.yaml・記録ファイルの形

生成物の中身、`limits.yaml` の書き方、記録ファイル（`decision_items.py` の YAML）の形を聞かれたとき、または自分で
書く前に確かめるときに読む。手順（いつ生成し直すか、いつ記録するか）は SKILL.md の Workflow にある。最後の節の
Oracle との突き合わせは、sql-migration のリポジトリで作業するときだけのものである。

## 生成物の構成

`python -m plsql.generate … --out-dir <out>` が書くもの。`<package>` は `--package`（既定 `com.example.migrated`）。

| 場所 | 中身 |
|---|---|
| `src/main/java/<package>/application/<Module>Service.java` | routine の本体。トランザクションは開始も commit もしない（境界は呼び出し側） |
| 同じ Service の `<routine>Start` / `Targets` / `One` / `Failed` / `Done` / `FailedBatch` と `<routine>After` | `transactions.perIteration` で割った routine の部品の method。推奨の回し方がコメントにある（出発点であって決定ではない）。`After` は次のページの起点のキーを返す |
| `…/application/TriggerChecks.java` / `TriggerCheckJob.java` | PL/SQL の外からの書き込みを見つける照合と、その回し方の雛形（`daily` / `hourly`） |
| `…/infrastructure/<Module>Repository.java` | SQL。1 文 = 1 method |
| `src/main/resources/plans/*.plan.json` | ScalarDB SQL が受け付けない読み取りの実行計画（ScalarDB から取得して H2 で元の SQL を実行） |
| `…/domain/` | 行と戻り値の record、エラーコードごとの例外（`RAISE_APPLICATION_ERROR` のコードは `<Module>Error<code>Exception`） |
| `db/trigger-check-baseline.sql` | 照合の控えの表（移行で足す表） |
| `db/restrict-direct-writes.sql` | 直接の書き込みを禁じる `REVOKE` の雛形（`<other_user>` は OPS-6 で決める） |
| `generation-report.json` | `summary`（件数）/ `errorCodes` / `verdicts`（routine ごとの `verdict`・`ruleVerdict`・`reasons` など）/ `refused`（routine → 生成コードが断る文の id）/ `diagnostics`（routine ごとの診断コード）/ `unconvertedFiles`（解析できなかったファイル。あるときだけ） |

## limits.yaml の書き方

routine ごとの決定を書くファイルで、`plsql.generate` と `plsql.cli` の `--limits` に渡す。どの節も、書いていない
routine は「決めていない」扱いで、生成器は既定の答えを当てない。理由は YAML のコメントではなく**値として**書く
（値は変換後の文書の事実の欄に引かれ、決定によっては生成コードのコメントにも入る。YAML のコメントはどこにも引かれない）。値が数である
`scanRows.default` / `scanRows.routines` だけは、理由をコメントで添える。`<routine>` は `generation-report.json` の `verdicts` のキーと
同じ id（package の routine は `<package>.<routine>`）。

```yaml
scanRows:
  default: 10000                  # 書かなければ 10000。これに落ちた routine は「決めていない」（--limits-strict で失敗）
  routines:
    pkg_order.order_total: 200    # 行数の上限（正の整数）。理由はこのようにコメントで: 1 注文の明細は最大 40 行
  notLimited:
    prc_nightly_close: <上限では守らないと決めた理由>   # routines と両方には書けない
rowLocks:
  optimistic:
    pkg_stock.reserve: <行ロックを落として楽観制御へ移す理由>
transactions:                     # 1 つの routine は次の 3 つのどれか 1 つにだけ書ける
  perIteration:
    prc_nightly_close: <1 反復 = 1 トランザクションに割る理由>
  separate:
    prc_audit_autonomous: <別のトランザクションで呼ぶ理由（自律トランザクション）>
  callerBoundary:
    pkg_x.y: <routine の中の COMMIT / ROLLBACK / SAVEPOINT を呼び出し側へ移す理由>
dynamicTables:
  pkg_util.truncate_staging: [staging_orders, staging_lines]   # 動的 SQL が受け付ける表名
ddl:
  omit:
    pkg_x.y: <routine の中の DDL を移行先で実行しない理由>      # 理由が空なら読み込みで失敗する
packageState:
  carried:
    pkg_x: <package 変数を呼び出し側が運ぶ理由>                  # package ごと
constraints:
  enforce:
    orders: <CHECK / FOREIGN KEY を書く側で評価する理由>         # 表ごと
dbLinks:
  warehouse_link:
    namespace: warehouse          # 必須: その link の表を置く ScalarDB の namespace
    reason: <理由>
conditionalCompilation:           # ソースに $IF があるとき。移行元の PLSQL_CCFLAGS と版（利用者に聞く）
  flags: {shop_debug: false, trace_level: 2}   # 書いていないフラグは NULL として解かれる（Oracle と同じ）
  dbVersion: "19.0"               # 書かなければ 19.0。$$PLSQL_VERSION / DBMS_DB_VERSION の比較に効く
```

書ける節とキーは上のものだけで、`plsql.generate` と `plsql.cli` は読む前に確かめる（#145）。次のどれかがあると、
理由を標準エラーに出して**終了 2** で止まり、何も書かない:

- 知らない節・キー（`rowlocks:`、`optimistc:` など。近い名前があれば「〜のこと？」と添える）
- 理由の要る所（`notLimited`、`rowLocks.optimistic`、`transactions.*`、`ddl.omit`、`packageState.carried`、
  `constraints.enforce`）の空の値
- ソースに無い routine id（打ち間違い、名前の変わった routine。近い id を添える）
- 正の整数でない行数、`routines` と `notLimited` の重なり、2 つ以上の `transactions.*` に書いた routine、
  `namespace` の無い `dbLinks`、`"19.0"` の形でない `dbVersion`、YAML として読めないファイル、無いファイル

止まったら、最後の行を利用者に見せ、直す値を聞いてから直す（決定の中身を推し量って書き換えない）。

routine 名・表名・理由は形を示すためのもので、実際には原文と利用者の答えから取る。各決定で生成物と振る舞いが
どう変わるかは `references/documenting.md` の「診断・決定の言い換え」にある。

## 記録ファイル

`decision_items.py` が読み書きする YAML で、最上位の `items:` の下に項目 ID ごとの内容を持つ。項目の定義は
`docs/plsql-migration/plsql-decisions-outside-generator.md`（§0.1 が「出た」の見分け方、§0.2 が記録の形）で、
スクリプトはこの文書を読む（`--doc` で別の文書を渡せる）。

```yaml
items:
  OPS-1:
    状態: 決定
    決定: a. CronJob から TriggerCheckJob を回す。業務と同じプロセスで全件を読ませない
    決めた人: 運用担当
    日付: '2026-09-20'
    記録先: 運用設計書
    決定時の題: 照合をどこで回すか
    決定時の根拠:
    - '`application/TriggerChecks.java` / `TriggerCheckJob.java` — TriggerCheckJob.java; TriggerChecks.java'
    出た:
    - '`application/TriggerChecks.java` / `TriggerCheckJob.java` — TriggerCheckJob.java; TriggerChecks.java'
  BIZ-7:
    状態: 未決
    案: 1 注文の明細は最大 100 行（出典: 受注業務仕様書 3.2）
```

| 欄 | 中身 | 書くもの |
|---|---|---|
| `状態` | 未決 / 決定 / 対象外（生成物に出ていない） | `set --status`、`scan --write` |
| `決定` / `決めた人` / `日付` / `記録先` | 選んだ選択肢と理由、決めた人の役割、YYYY-MM-DD、記録先 | `set --decision` / `--by` / `--date` / `--where` |
| `案` | 業務文書などから引いた答えの候補（出典つき）。決定ではない | `set --proposal` |
| `食い違い` | 業務文書と生成物（または元の PL/SQL）の食い違い。再設計の要否として残す | `set --conflict` |
| `残り` | 決定のうち、まだ決まっていない部分 | `set --remaining` |
| `メモ` | 自由記述 | `set --note` |
| `出た` | 生成物から拾った根拠 | `scan` が毎回書き直す |
| `決定時の題` / `決定時の根拠` | 決めたときの問いと、そのときの `出た` | `set --status 決定` が書く |
| `履歴` | 上書きされた、または取り消された以前の決定 | `set` が移す |

- ファイルは書き込みのたびに作り直すので、**YAML のコメントは残らない**
- `状態: 決定` なのに `決定` / `決めた人` / `日付` のどれかが無い、`日付` が日付でない、決定でないのに `決定` などが
  残っている、`決定時の題` か `決定時の根拠` がいまと違う（要再確認）——これらは記録の問題で、`scan` も `set` も
  終了コード 1 を返す
- `scan --strict` は、出た項目に未決が残っていれば 1 で終わる。引き渡しの前の CI で使える
- 決定した項目が次の生成で出なくなっても、決定は消さない（`出た` だけが消える）。出なくなった未決は対象外になる

## sql-migration のリポジトリで作業するとき: Oracle との突き合わせ

**この節は、sql-migration のチェックアウト（`difftest/` と `fixtures/` がある）で作業するときだけ当てはまる。**
プラグインとして入れた環境には比較のハーネスが無いので、比較の結果（`--evidence`）無しで文書を書き、「制限」に
確かめていないと書く。

生成コードが Oracle と同じ結果を返すかは、シナリオを Oracle と ScalarDB の両側で実行して比べる。Oracle と
ScalarDB Cluster のコンテナ（`difftest/docker-compose.yml`）が要り、どちらの DB にも書き込むので、始める前に
利用者に確かめる。

corpus（`fixtures/plsql/`）のとき:

```bash
# Oracle 側: PL/SQL を配備して、シナリオの結果を golden に取る（Oracle 側が変わったときだけ）
.venv/bin/python difftest/plsql_run.py deploy
.venv/bin/python difftest/plsql_run.py run --out fixtures/plsql/golden        # 1 本だけなら --scenario <name>

# ScalarDB 側: 生成し直して、同じシナリオを Java で実行する。namespace が無ければ --print-schema-command
.venv/bin/python difftest/plsql_capture.py --variant scaled

# 比べる。--json の出力が migration_doc.py と plsql.cli の --evidence になる
.venv/bin/python difftest/plsql_compare.py --variant scaled --json difftest/work/plsql-diff.json
```

corpus の外のプロジェクト（`fixtures/plsql-external/<名前>/`、migrate-flow のテスト）は、どのコマンドにも
`--project <dir>` を付け、capture に `--namespace <ns>` を渡す。プロジェクトの形、Oracle のユーザー、Schema Loader の
流し方は `fixtures/plsql-external/README.md` にある。

```bash
.venv/bin/python difftest/plsql_run.py deploy --project <dir>
.venv/bin/python difftest/plsql_run.py run --project <dir>
.venv/bin/python difftest/plsql_capture.py --project <dir> --namespace <ns> --variant double
.venv/bin/python difftest/plsql_compare.py --project <dir> --variant double --json <dir>/work/plsql-diff.json
```

- `--variant` は金額列の規約（`scaled` = 桁をずらした BIGINT、`double` = DOUBLE）で、ScalarDB の schema と生成コードが
  変わる。比較と capture で同じものを使う
- capture は `plsql_capture.py` で取る。Gradle を手で回しただけの capture には「何を測ったか」の指紋が無く、
  `plsql.cli --evidence` は古い証拠として数えない
- 一致しないシナリオは、差が**生成物の誤り**か**移行で意味が変わる既知の差**（BIZ 項目、たとえば trigger を
  通らない直接 DML）かを分けて報告する。後者は `references/alignment.md` の確かめ方へ回す
- 一致しても、BIZ 項目の振る舞い（途中で止まったとき・同時に書いたとき・PL/SQL の外から書いたとき）は観ていない

シナリオ（`<src>/../scenarios/*.yaml`。形は `fixtures/plsql/scenarios/README.md`）に書く宣言のうち、変換の決定と
関わるもの:

| 宣言 | 使うとき |
|---|---|
| `boundary: rollback` | `transactions.callerBoundary` の routine で、元の routine が自分で ROLLBACK していたとき。ScalarDB 側のハーネスが呼び出し側として呼んだあとに rollback する。Oracle 側は原文が自分で戻すので無視する。書かないと、Oracle が戻した行が ScalarDB 側に残って相違になる |
| `call` の `via: table` | PIPELINED 関数（PL/SQL から呼べない、PLS-00653）。Oracle 側は `SELECT * FROM TABLE(f(p => :p))` で読み、ScalarDB 側は生成した method が返す List を読む |
| `nondeterministic`（`reason` と、`ignore_columns: {表: [列, …]}` または `unordered: true`） | Oracle 自身が結果を決めていないとき。`ignore_columns` は `ORDER BY` の無い `ROWNUM <= n` の更新などで、その列を両側から外した行の集合で比べる。`unordered` は、routine が返す行（BULK COLLECT、PIPELINED）に並びを決める `ORDER BY` が無いときで、行の集まりとして比べる。書かなければ、返す行は順序どおりに比べる。理由の無い宣言は拒否される |
| `accepted_difference`（`exception: {oracle: …, target: …}`、`reason`、`decided`） | 移行先では同じ例外を上げられない差を、理由つきで受け入れたとき。その 2 つの例外コードの組だけを覆い、表や結果の差は相違のまま残る |
| `mask` | ハーネスが固定できない時計から書かれる列 |
