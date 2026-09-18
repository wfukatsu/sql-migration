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
| A. 読むだけの走査 | 全行を読んでから Java で回す | **生成する**（上限つき） | **routine ごとの上限値** |
| B. 先頭 1 件だけ取る | 順序付き問い合わせの先頭行 | **生成する**（`LIMIT 1`） | 0 件のときの値、順序の載り先 |
| C. 走査して件数を数える | 集約に置き換える | **生成する**（`COUNT(*)`） | — |
| D. 走査しながら**同じ表**を更新 | **拒否する** | 拒否 | 再設計（下記） |
| E. 走査しながら**別の表**を更新 | 全行を読んでから Java で回す | 生成する（上限つき） | routine ごとの上限値、部分失敗 |
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
   **上限は `--limits` の YAML で routine ごとに指定する**（2026-09-17 の決定）。生成コードは読みながら
   数え、超えたら例外で止まる——全部読んでから数えると、止める前にメモリを使い切っている。

   ```yaml
   scanRows:
     default: 10000
     routines:
       pkg_order_pricing.order_total: 1000   # 1 注文あたりの明細数
   ```

   **`CUR-002` は REVIEW のままである。** 上限を指定できるようになったことと、**その routine の上限を
   決めたことは別**だからである。既定値のままの routine では、生成コードにもそう書いてある
   （`この routine 固有の上限は決められていない`）。
2. **ループ中の書き込みが、回っている行集合を変えない。** Oracle では cursor の一貫性モデル次第で変わる。
   同じ表を書く場合は D へ。

**`FOR r IN c LOOP`（宣言済みの cursor を回す形）も同じ経路に乗る。** query は宣言から解決され
（`plsql/cursors.py`）、以後はインライン版と区別されない。宣言に書かれた `FOR UPDATE` も一緒に付いて
くる——ロックはこれらの routine が再設計になる理由そのものなので、付いてこない query は安全に見えて
しまう。

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

これは走査ではなく「順序付き問い合わせの先頭行、無ければ既定値」である。生成器はこの並び
（`OPEN` → `FETCH` → 任意の `%NOTFOUND` 分岐 → `CLOSE`）を認識し、cursor の query に `LIMIT 1` を
付けた 1 文に置き換える（#11）。

```java
Object[] largestOrderStmt2Row = repository.largestOrderStmt2(pCustomerId);
cAmountsNotFound = largestOrderStmt2Row == null;
if (largestOrderStmt2Row != null) {
    vAmount = Plsql.dec(largestOrderStmt2Row[0]);
}
if (cAmountsNotFound) {          // 元のコードの %NOTFOUND 分岐、そのまま
    vAmount = Plsql.dec(0);
}
```

**保っているものが 3 つある。**

1. **0 件のときの値。** 元のコードの `%NOTFOUND` 分岐をそのまま残す。生成器はこれを翻訳せず、
   位置も変えない。
2. **`%NOTFOUND` は「行が無かった」であって「値が NULL だった」ではない。** repository は行の不在を
   `null` で返し（値ではなく配列で返すのはこのため）、分岐はそこから答える。
3. **見つからなかった `FETCH` は INTO 先を書き換えない。** Oracle がそうするからであり、
   `%NOTFOUND` 分岐を持たない並びはまさにそれに依存している。

残るのは**順序の載り先**である。`ORDER BY` が partition key に乗らなければ走査はパーティションを
またぎ、**フィルタも順序も JDBC バックエンドでしか通らない**（`SCAN-002`）。corpus の
`pkg_order_report.largest_order` はこれに当たるので REVIEW のままである。

## C. 走査して件数を数える

```sql
OPEN c_open_orders(p_status);
LOOP FETCH ... EXIT WHEN %NOTFOUND; p_count := p_count + 1; END LOOP;
CLOSE c_open_orders;
```

集約 1 本に置き換わる（#11）。

```java
pCount = Plsql.dec(0);                                        // ループ前の初期化、そのまま
pCount = Plsql.dec(repository.countByStatusStmt3(pStatus));   // SELECT COUNT(*) ... WHERE status = :p_status
```

**`COUNT` は 0 件でも 1 行返る**（`SEM-004`）。それは元のループの答えと同じである——1 回も回らず、
ループ前に置いた初期値が残る——ので、`NO_DATA_FOUND` は持ち込まれない。同じ理由で
`TOO_MANY_ROWS` も届かないため、キーで届かない `SELECT INTO` の警告（`MULTI_ROW_INTO`）も付かない。

置き換えるのは**ループが数える以外のことをしていないとき**だけである。fetch した値を body が
読んでいれば、それは COUNT がしないことをしている。cursor の `%ISOPEN` を見ていた handler は、
cursor ごと消える。

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

## G. `FORALL ... SAVE EXCEPTIONS`

```sql
FORALL i IN 1 .. p_product_ids.COUNT SAVE EXCEPTIONS
  UPDATE products SET stock_qty = stock_qty + p_deltas(i) WHERE product_id = p_product_ids(i);
EXCEPTION WHEN OTHERS THEN
  IF SQLCODE = -24381 THEN                      -- 一部が失敗した
    FOR i IN 1 .. SQL%BULK_EXCEPTIONS.COUNT LOOP
      INSERT INTO audit_log (... 'BULKERR' ...);   -- 失敗を記録して続行
```

**「失敗しても続け、記録する」**という要件である。一括の原子性ではない——`SAVE EXCEPTIONS` は
まさに原子性を捨てる指定である。

### 決定（2026-09-18 / #14）: #3 と同じ形に揃える

* **1 要素 = 1 トランザクション**（transaction-patterns §E の決定と同じ）
* **失敗した要素のエラー行は別トランザクション**（同 §F / §G の決定と同じ）——1 要素分を
  rollback すると、その中に書いたエラー行も消えるため

```java
for (int i = 0; i < pProductIds.size(); i++) {
    try {
        tx.run(() -> service.restockOne(pProductIds.get(i), pDeltas.get(i)));
    } catch (Exception failed) {
        tx.run(() -> service.restockFailed(pProductIds.get(i), failed));   // 別トランザクション
    }
}
```

**新しい判断ではない。** `SAVE EXCEPTIONS` が表している要件（続行して記録する）は、#3 で
`prc_nightly_close` について決めたものと同じである。決定を 2 つに分けると、片方だけ実装されて
食い違う。

`SQL%BULK_EXCEPTIONS` は移行先に無い。**どの要素が失敗したか**は、上の形では catch した側が
知っている（`i` を持っているのはループである）。

---

## 共通して決めておくこと

1. **行数の上限。** 仕組みは `--limits` にある（2026-09-17 の決定）。**値を決めるのは業務側である**——
   各 routine について「何行まで来うるか」を決めて `limits.yaml` に書き、根拠をコメントに残すまで、
   既定値は「決めていない」という意味である。
2. **順序。** `ORDER BY` が clustering key に乗らなければ、アプリ側で並べ替えることになる。
   その時点で全行がメモリに乗る。
3. **失敗したときどこまで確定してよいか。** Oracle の cursor ループは routine 内 COMMIT と
   組み合わさっていることが多い（`prc_nightly_close`）。その場合は `TX-001` の再設計が先である。
