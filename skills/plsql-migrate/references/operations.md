# 運用の手順

## Oracle との突き合わせ（difftest）

生成コードが Oracle と同じ結果を返すかは、シナリオ（`fixtures/plsql/scenarios/*.yaml`）を両側で実行して
比べる。コンテナ（Oracle 23ai と ScalarDB Cluster）が動いている必要がある（`difftest/docker-compose.yml`）。

```bash
# Oracle 側: PL/SQL を配備して、シナリオの結果を golden に取る（Oracle が変わったときだけ）
.venv/bin/python difftest/plsql_run.py deploy
.venv/bin/python difftest/plsql_run.py run --out fixtures/plsql/golden        # 1 本だけなら --scenario <name>

# ScalarDB 側: 生成し直して、同じシナリオを Java で実行する。namespace が無ければ --print-schema-command
.venv/bin/python difftest/plsql_capture.py --variant scaled

# 比べる
.venv/bin/python difftest/plsql_compare.py --variant scaled
```

一致しないシナリオは、差が**生成物の誤り**か**移行で意味が変わる既知の差**（BIZ 項目、たとえば trigger を
通らない直接 DML）かを分けて報告する。後者は業務ロジックとの整合の確認（`alignment.md`）へ回す。

一致しても、BIZ 項目の振る舞い（途中で止まったとき・同時に書いたとき・PL/SQL の外から書いたとき）は
観ていない。

## 生成物の構成

| 場所 | 中身 |
|---|---|
| `src/main/java/<package>/application/<Module>Service.java` | routine の本体。トランザクションは開始も commit もしない（境界は呼び出し側） |
| `…/application/<routine>Targets` / `One` / `Failed` / `Start` / `Done` / `FailedBatch` / `After` | `transactions.perIteration` で割った routine の部品。回し方の出発点がコメントにある |
| `…/application/TriggerChecks.java` / `TriggerCheckJob.java` | PL/SQL の外からの書き込みを見つける照合と、その回し方の雛形（`daily` / `hourly`） |
| `…/infrastructure/<Module>Repository.java` | SQL。ScalarDB が受け付けない読み取りは `resources/plans/*.plan.json` の実行計画へ |
| `…/domain/` | 行の record、`Error<code>Exception`（`RAISE_APPLICATION_ERROR` のコード） |
| `db/trigger-check-baseline.sql` | 照合の控えの表（移行で足す表） |
| `db/restrict-direct-writes.sql` | 直接の書き込みを禁じる `REVOKE` の雛形（`<other_user>` は OPS-6 で決める） |
| `generation-report.json` | `summary` / `errorCodes` / `verdicts`（routine ごとの判定と理由）/ `diagnostics`（routine ごとの診断コード） |

## 記録ファイル

`decision_items.py` が読み書きする YAML。項目 ID ごとに次を持つ（`docs/plsql-decisions-outside-generator.md` §0.2）。

```yaml
items:
  OPS-1:
    状態: 決定            # 未決 / 決定 / 対象外
    決定: a. CronJob から TriggerCheckJob を回す。業務と同じプロセスで全件を読ませない
    決めた人: 運用担当
    日付: '2026-09-20'
    記録先: 運用設計書
    出た:                 # スクリプトが毎回書き直す
    - '`application/TriggerChecks.java` / `TriggerCheckJob.java` — TriggerCheckJob.java; TriggerChecks.java'
  BIZ-7:
    状態: 未決
    案: 1 注文の明細は最大 100 行（出典: 受注業務仕様書 3.2）
  BIZ-1:
    状態: 未決
    食い違い: 仕様 3.2 は全件一括の確定。移行後は 1 件ずつ（移行前も 100 件ごとの中間コミット）
```

`OPS-2` のように一部だけ決まった項目は、`決定` と並べて `残り`（まだ決まっていない部分）を持つ。

- `scan --strict` は、出た項目に未決が残っていれば 1 で終わる。引き渡しの前に CI で使える
- 「決定」なのに 決定・決めた人・日付 のどれかが無いと、`scan` も `set` も 1 で終わる
- 決定した項目が次の生成で出なくなっても、決定は消さない（`出た` だけが消える）
