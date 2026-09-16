# シナリオ（P0-4 が決めた形式）

1 シナリオ = 「固定した状態で 1 routine を呼び、その観測可能な結果をすべて記録する」単位。
`difftest/plsql_run.py run` がこれを読み、`fixtures/plsql/golden/<name>.json` を書く。

```yaml
name: order_status_missing      # 出力ファイル名にもなる。一意
unit: pkg_order_status          # manifest.yaml のユニット名
routine: status_of              # manifest.yaml の routine 名
pinned:                         # 非決定要素の固定
  sysdate: "2026-01-15 09:30:00"   # ALTER SYSTEM SET FIXED_DATE。SYSDATE のみ効く
  sequences: {seq_audit_id: 1}     # 実行前に START WITH で作り直す
setup:                          # 実行前に流す SQL。表は毎回空にしてから流す
  - "INSERT INTO orders (...) VALUES (...)"
call:
  kind: function                # function | procedure
  name: pkg_order_status.status_of
  args: {p_order_id: 9999}      # IN 引数（名前つき）
  out: {}                       # OUT / IN OUT: 名前 -> Oracle 型
  returns: VARCHAR2             # kind: function のときだけ
capture_tables: [orders, audit_log]
mask:                           # 固定できない時計から書かれる列
  audit_log: [changed_at]
```

## mask が要る理由

`ALTER SYSTEM SET FIXED_DATE` は **SYSDATE しか固定しない**（Oracle 26ai Free で実測）。
`SYSTIMESTAMP` / `CURRENT_DATE` / `LOCALTIMESTAMP` は実時計のままなので、それらから書かれる列は
`mask` に挙げて `{"$masked": ...}` に置き換える。マスクした列は capture の `masked` に必ず残るので、
黙って落ちることはない。

corpus でこれに当たるのは `prc_audit_autonomous`、`trg_orders_audit`、`trg_products_audit`、
`pkg_payment.record_payment`（いずれも `SYSTIMESTAMP` を書く）。
