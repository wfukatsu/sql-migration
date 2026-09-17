# Cursor の移行パターン

2026-09-17 / P4-5

Oracle の cursor は**トランザクションをまたいで保持される位置**である。ScalarDB に同じものは無い。
だから cursor の移行は書き換えではなく、**「その cursor は何をしていたのか」を決め直すこと**になる。
使われ方によって答えが違うので、corpus に現れた形で分類する。

`CUR-001`（明示 cursor の寿命）と `CUR-002`（Cursor FOR LOOP の N+1 とメモリ）が REVIEW にしている
21 routine は、下のいずれかである。

## 判定の早見表

| 形 | ScalarDB での書き方 | 生成器 | 人が決めること |
|---|---|---|---|
| A. 読むだけの走査 | 全行を読んでから Java で回す | **生成する** | **行数の上限** |
| B. 先頭 1 件だけ取る | 順序付き問い合わせの先頭行 | 未対応（P4 残） | 0 件のときの値 |
| C. 走査して件数を数える | 集約に置き換える | 未対応（P4 残） | — |
| D. 走査しながら**同じ表**を更新 | **拒否する** | 拒否 | 再設計（下記） |
| E. 走査しながら**別の表**を更新 | 全行を読んでから Java で回す | 生成する | 行数の上限、部分失敗 |
| F. `BULK COLLECT LIMIT` | 明示的な分割読み | 拒否 | 1 回の件数とメモリ上限 |

---

## A. 読むだけの走査

```sql
FOR r IN (SELECT qty, unit_price FROM order_lines WHERE order_id = p_order_id) LOOP
  v_gross := v_gross + line_amount(r.qty, r.unit_price);
END LOOP;
```

生成されるのは、**行を先に全部読んでから回す** Java である。

```java
// the rows are read before the loop runs: ScalarDB has no cursor held across a transaction
for (OrderTotalLoop1Row r : repository.orderTotalLoop1(pOrderId)) {
    vGross = Plsql.dec(Plsql.add(vGross, lineAmount(r.qty(), r.unitPrice())));
}
```

Oracle と違うところが 2 つあり、**どちらも意図的に見えるようにしてある**:

1. **行数がメモリで決まる。** Oracle の cursor は 1 行ずつ取るので、1000 万行でも動いた。ここでは動かない。
   だから `CUR-002` は REVIEW のままである——**走査行数の上限は業務の判断**であり、生成器が決めてよい
   ことではない。上限を決めたら `WHERE` か `FETCH FIRST` に書く。
2. **ループ中の書き込みが、回っている行集合を変えない。** Oracle では cursor の一貫性モデル次第で変わる。
   同じ表を書く場合は D へ。

ループ行の record は**列の型ではなく PL/SQL のループ変数**を写す。`r.qty` は PL/SQL では NUMBER であり、
NUMBER を引数に取る routine へ渡される。列の幅（`NUMBER(10)` → `Long`）を持ち込むと、呼び出し先の
引数型と合わない。

## B. 先頭 1 件だけ取る

```sql
OPEN c_amounts;            -- SELECT total_amount ... ORDER BY total_amount DESC
FETCH c_amounts INTO v_amount;
IF c_amounts%NOTFOUND THEN v_amount := 0; END IF;
CLOSE c_amounts;
```

これは走査ではなく「順序付き問い合わせの先頭行、無ければ既定値」である。ScalarDB では
clustering key の順序か、アプリ側で最大を取る。**0 件のときの値は元のコードに書いてある**ので、
そこだけは失わないこと（`%NOTFOUND` の分岐）。

## C. 走査して件数を数える

```sql
OPEN c_open_orders(p_status);
LOOP FETCH ... EXIT WHEN %NOTFOUND; p_count := p_count + 1; END LOOP;
CLOSE c_open_orders;
```

集約 1 本に置き換わる。ただし **`COUNT` は 0 件でも 1 行返る**（`SEM-004`）ので、
`NO_DATA_FOUND` にならず NULL になる差を持ち込まないこと。

## D. 走査しながら同じ表を更新 — 拒否する

```sql
FOR r IN (SELECT order_id FROM orders WHERE status = p_status ORDER BY ordered_at) LOOP
  UPDATE orders SET note = 'reviewed' WHERE order_id = r.order_id;
END LOOP;
```

生成器はこれを拒否し、理由を残す:

```
cursor FOR loop whose body writes ['orders'], which its own query reads
```

**ScalarDB は同じトランザクションが書いた物の走査を禁じている**（DB-CORE-10106、P2-4 の
`scan_after_write`）。先に全行読んでから回せば規則には触れないが、それは Oracle と同じ意味ではない——
Oracle でこの形が何を見るかは cursor の一貫性モデルの話であり、**先に読む実装がたまたま一致する保証は
無い**。だから書き換えずに、再設計として扱う。

再設計の選択肢:

- **キーを先に確定させ、別トランザクションで更新する。** 部分失敗と再試行の方針が要る。
- **更新対象を業務条件から直接特定する**（走査を介さない）。
- **1 件ずつ独立したトランザクションにする。** 全体の原子性を捨ててよいかを決める。

## E. 走査しながら別の表を更新

規則には触れないので生成できる。ただし A と同じくメモリの上限が要り、加えて
**途中で失敗したとき何件まで確定していてよいか**が業務判断になる。囲むトランザクションが 1 つなら
全部戻るが、行数がその 1 トランザクションに収まるかは別問題である。

## F. `BULK COLLECT LIMIT`

```sql
FETCH c BULK COLLECT INTO v_ids LIMIT p_limit;
```

拒否する。P3-2 で直したとおり、これを 1 行の `SELECT INTO` として扱うと **0 件と複数件が元に無い例外に
なり、複数行のときは 2 行目以降を黙って捨てる**。分割読みとして書き直すこと自体は素直だが、
1 回の件数とメモリ上限は元のコードが `p_limit` で外に出しているので、移行先でも外に出す。

---

## 共通して決めておくこと

1. **行数の上限。** どの形でも、これを決めずに移行すると本番で初めて分かる。
2. **順序。** `ORDER BY` が clustering key に乗らなければ、アプリ側で並べ替えることになる。
   その時点で全行がメモリに乗る。
3. **失敗したときどこまで確定してよいか。** Oracle の cursor ループは routine 内 COMMIT と
   組み合わさっていることが多い（`prc_nightly_close`）。その場合は `TX-001` の再設計が先である。
