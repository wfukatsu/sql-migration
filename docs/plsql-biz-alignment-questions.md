# 業務ロジックとの整合 — 業務担当への問い（2026-09-19）

PL/SQL の corpus（`fixtures/plsql/src`）を移行したとき、移行先の都合で**振る舞いが変わる**ところを routine ごとに
並べた。比較ハーネスは 1 回の呼び出しの結果しか観ないので、下の変化は「一致」の結果に表れない。
業務文書は渡されていないため、案も食い違いも記録していない。答えは業務担当に聞く（記録: 
`fixtures/plsql/decisions-outside-generator.yaml`、ID ごと）。

作り方: `skills/plsql-migrate` の Step 6。項目の定義は [生成コードの外で決めること](plsql-decisions-outside-generator.md) §3。
運用（OPS）と呼び出し側（CALL）の 16 項目は 2026-09-19 に移行担当（PoC 実施者）が決定し、記録ファイルにある。

## BIZ-1 途中で止まったときの確定の単位

| routine | 移行前（Oracle） | 移行後 | 聞くこと |
|---|---|---|---|
| `prc_nightly_close` | 出荷済みの注文を 100 件ごとに中間コミット | 1 件ずつ確定。`batch_control` の FAILED は呼び出し側が `FailedBatch` で書く（CALL-2） | 境目が 100 件単位であることに頼る帳票・後続処理はあるか |
| `prc_reprice_all` | 指定ランクの NEW の注文を再計算、50 件ごとに中間コミット | 1 件ずつ確定 | 途中で止まったとき、再計算済みと未済が混ざってよいか |
| `prc_purge_audit` | 古い監査行を 200 件ごとに中間コミットで削除 | 1 件ずつ削除 | （影響は小さい見込み）途中で止まったときの残りを翌日に回してよいか |
| `pkg_order_report.mark_reviewed` | **中間コミットなし**。全件が呼び出し側の 1 トランザクション | 1 件ずつ確定。**移行で新たに生じる差** | 一部の注文だけ `note = 'reviewed'` になった状態が起こりうる。それでよいか |
| `pkg_bulk_load.restock` | FORALL SAVE EXCEPTIONS: 失敗した行を飛ばして残りを反映（呼び出し側のトランザクション内） | 1 行ずつ確定し、失敗は `restockFailed` で監査に記録 | 呼び出し側が後で rollback しても、反映済みの在庫は戻らない。それでよいか |

## BIZ-2 対象を読んだ時点と処理する時点のずれ

- `prc_nightly_close`: 対象（`status = 'SHIPPED' AND ordered_at < p_batch_date`）を 100 件ずつ読むので、締めの実行中に SHIPPED になった注文も締まる。**締めの対象は開始時点で確定している必要があるか**
- `prc_reprice_all` / `mark_reviewed` / `prc_purge_audit`: 同様に、実行中に条件に入った行も処理される。`mark_reviewed` は元の `ORDER BY ordered_at` の順ではなく注文番号の順に処理する（結果は同じ）

## BIZ-3 自律トランザクションの監査記録

- `prc_audit_autonomous` は corpus の中に呼び出し元が無い。**どの業務から呼ばれているか**（CALL-4 で「常に別の境界で呼ぶ」と決定済み。呼び出し元の洗い出しが残り）

## BIZ-4 行ロックが無くなり、衝突が commit で分かる

| routine | 移行前 | 移行後 | 聞くこと |
|---|---|---|---|
| `reserve_nowait` | 他が処理中なら即座に -20031 | 待たずに進み、commit で弾かれて再試行（CALL-5） | 「即座に断る」ことに業務上の意味（画面で待たせない、別の倉庫へ回す）はあったか |
| `claim_batch` | NEW の注文を最大 N 件、他が取っている行は**飛ばして**取る | 取り合うと片方が弾かれて再試行 | 複数の担当・ジョブが同時に取りに行くか。飛ばす代わりに再試行でよいか |
| `reserve` / `next_payment_id` / `promote` / `cancel` | 行ロックで待つ | commit で弾かれて再試行 | 同じ商品・顧客・注文を同時に操作することはどのくらいあるか |

## BIZ-5 MERGE を読んでから選ぶ

- `pkg_customer_import.import`: 同じ顧客番号の取り込みが同時に走ると片方が弾かれる。**同じ顧客の取り込みが並行することはあるか**

## BIZ-6 監査に残る「誰が」

- `changed_by` は Oracle の DB 利用者名から、アプリのログイン利用者（バッチはジョブ名）に変わる（CALL-6）
- PL/SQL の外から書かれて照合で補った監査行（OPS-3 で自動補完と決定）は、書いた主体が `BACKFILL`、時刻が補った時刻になり、元の実施者・時刻は分からない。**監査要件（内部統制・法令）として許されるか**。許されないなら、`orders` への直接の書き込みも権限で禁じる必要がある（今は `payments` / `products` だけ）

## BIZ-7 行数の上限

- `order_total` / `cancel` / `archive_lines` は 1 注文の明細を全部読み、200 行を超えたら止まる。**1 注文の明細は業務上最大何行か。200 を超える注文は止めてよい異常か**

## BIZ-8 動的 SQL が受け付ける表名

- `pkg_dynamic_search.purge` と `pkg_customer_import.truncate_staging` は `inventory_tx` だけを受け付け、他の表名は実行時に拒否する（Oracle ではどの表でも走った）。**呼び出し側が渡す表名はどれか**。`truncate_staging` は名前に反して staging 表が schema に無い。移行先で staging 表を作るか

## BIZ-9 trigger が掛かるのは生成コードの経路だけ

- `orders`（監査・採番）、`payments`（検証）、`products`（監査・値下げ幅）に**PL/SQL 以外から書いている業務・システムはあるか**。あれば、その書き込みには trigger が掛からない（照合で検出されるだけ）

## BIZ-10 採番 trigger

- `trg_orders_seq` は `order_id` を渡さない INSERT に番号を振る。移行先では採番 Service の呼び出しに置き換わり、キーを渡さない INSERT は動かない。**キーを渡さずに注文を登録している業務はどれか。注文番号の欠番・順序に業務上の意味はあるか**

## BIZ-11 TIMESTAMPTZ の offset

- `payments.paid_at` はタイムゾーン付きで記録していたが、移行先では瞬間だけを持ち UTC で読み戻る（`last_paid_at` の返り値も同じ）。**支払い時刻の offset から拠点・地域を読んでいる帳票や業務はあるか**

## BIZ-12 TRUNCATE が直前の作業を確定しない

- `truncate_staging` の前に同じトランザクションで書いた内容は、Oracle では TRUNCATE の時点で確定したが、移行先では確定しない。**TRUNCATE の前の書き込みが確定している前提の処理はあるか**
