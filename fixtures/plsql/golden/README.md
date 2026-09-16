# golden capture（P0-5）

`difftest/plsql_run.py run` が採取した、Oracle 上での観測結果。Phase 3 の差分比較はこれを正解とする。

- capture: **59 本**（`fixtures/plsql/scenarios/*.yaml` と 1 対 1）
- 対象 routine: **48 中 43**（下の「capture が無い routine」を参照）
- **2 回実行してバイト一致**することを確認済み

## 採取手順

```bash
(cd difftest && docker compose --profile oracle up -d source-oracle)
.venv/bin/python difftest/plsql_run.py deploy
.venv/bin/python difftest/plsql_run.py run --out fixtures/plsql/golden
```

形式と正規化規則は P0-4 で決めたもの（`difftest/plsql_run.py` の docstring と `../scenarios/README.md`）。

## 固定したもの

| 非決定要素 | 固定方法 |
|---|---|
| `SYSDATE` | `ALTER SYSTEM SET FIXED_DATE='2026-01-15-09:30:00'`。全シナリオで同じ時刻 |
| Sequence | 実行前に `START WITH` で作り直す。シナリオが `pinned.sequences` で開始値を指定する |
| 表の状態 | 各シナリオの前に全表を空にし、`setup` を流す |

`SYSTIMESTAMP` / `CURRENT_DATE` / `LOCALTIMESTAMP` は固定できない（P0-4 で実測）。これらから書かれる列は
シナリオの `mask` で宣言し、`{"$masked": ...}` に置き換える。マスクした列は capture の `masked` に残る。
該当するのは `audit_log.changed_at` と `payments.paid_at`。

## 「DB 差分」を差分ではなく実行後の状態として持つ理由

計画の成果物欄は「戻り値・OUT・例外・DB 差分」だが、capture が持つのは**実行後の全行**である。
実行前の状態はシナリオの `setup` が決定的に定めているので、実行後の状態を突き合わせれば差分を
突き合わせたことになる。差分だけを持つと、差分の計算方法が比較器と採取側の両方に必要になり、
ずれる余地が増える。

## capture が無い routine（48 中 5）

| routine | 理由 |
|---|---|
| `pkg_order_pricing.tier_discount` | package 本体だけに定義された private 関数。外から呼べない |
| `pkg_order_pricing.line_amount` | 同上 |
| `pkg_order_pricing.customer_tier` | 同上 |
| `pkg_order_lock.is_cancellable` | 同上 |
| `prc_remote_sync` | DB Link が存在せずコンパイルできない（INVALID） |

private routine は**公開 routine 経由でしか観測できない**。`order_total` と `reprice_order` の capture が
`tier_discount` / `line_amount` / `customer_tier` の振る舞いを間接的に覆っている。

これは KPI-5（意味的同等性テスト合格率）と確信度の `testEvidence` に直接効く。
`testEvidence` の定義から **capture が 1 つも無い routine は AUTO にならない**（`docs/plsql-kpi.md` §3）。
上の 5 つは、期待判定が AUTO であっても AUTO には昇格しない。
private routine を AUTO にしたければ、呼び出し元の capture を根拠にできるよう
`testEvidence` の算出を「自分の capture ∪ 自分を呼ぶ公開 routine の capture」へ広げる必要がある。
これは P2-2 でルールを実装するときに決める。

## 採取中に見つけた corpus の不具合

Oracle で実行して初めて分かったものが 1 件あった。

`pkg_order_pricing.reprice_order` が `UPDATE orders SET total_amount = order_total(...)` と書いており、
`order_total` は `orders` を読むため **ORA-04091 (mutating table)** で必ず失敗していた。
値を先に変数へ確定させてから UPDATE するように直した。

parse（P0-1）もコンパイル（P0-4）も通ったうえで、**実行して初めて落ちる**種類の不具合である。
Phase 1 の parse 率にも Phase 2 の compile 率にも表れない。
