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
  kind: function                # function | procedure | block
  name: pkg_order_status.status_of
  args: {p_order_id: 9999}      # IN 引数（名前つき）。値がリストなら PL/SQL の
                                #   索引付き表（TABLE OF ... INDEX BY PLS_INTEGER）として束縛する
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

## `kind: block`

PL/SQL 専用の戻り値（`%ROWTYPE`、`BOOLEAN`）を返す function や、routine を経由しない経路
（素の `INSERT` で trigger を踏むなど）は、function / procedure 呼び出しでは捕まえられない。
その場合は無名ブロックを書き、結果を OUT バインドで受け取る。

```yaml
call:
  kind: block
  body: |
    DECLARE
      v_row orders%ROWTYPE;
    BEGIN
      v_row := pkg_order_lock.snapshot(:p_order_id);
      :o_status := v_row.status;
    END;
  args: {p_order_id: 1001}
  out: {o_status: VARCHAR2}
```
