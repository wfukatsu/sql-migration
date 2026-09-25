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
| F. `BULK COLLECT LIMIT` | 明示的な分割読み | n 件ずつ配るループ | 走査行数の上限（LIMIT はもうメモリを守らない） |
| H. cursor 変数（`OPEN rc FOR q` … FETCH … CLOSE） | A〜C・F と同じ形に、OPEN の問合せで当てる。IF の分岐ごとに別の問合せで開く形は分岐ごとのループ | 生成する（上限つき） | routine ごとの上限値（#44） |
| I. cursor 変数を呼び出し側へ返す（`OPEN rc FOR q; RETURN rc;`） | 行を読んで `List<行の record>` を返す | 生成する（上限つき） | 呼び出し側の受け取り方が変わる（FETCH → List）。上限値 |

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

## D. 走査しながら同じ表を更新 — 先に読む形で移す（2026-09-19 / #20）

```sql
FOR r IN (SELECT order_id FROM orders WHERE status = p_status ORDER BY ordered_at) LOOP
  UPDATE orders SET note = 'reviewed' WHERE order_id = r.order_id;
END LOOP;
```

**ScalarDB は同じトランザクションが書いた物の走査を禁じている**（DB-CORE-10106、P2-4 の
`scan_after_write`）。以前はこれを理由に一律に拒否していた。「先に全行読めば規則には触れないが、Oracle と
同じ意味である保証は無い」と考えていたためである。

**保証はある。** Oracle の cursor は OPEN の時点で読み取りが一貫している（`FOR UPDATE` ならロックで、
無くても読み取り一貫性で）。だから先に全部読む形と、回す行が同じである。読むのは書くより前の 1 回だけ
なので、P2-4 にも当たらない。`claim_batch`（`FOR UPDATE`、2026-09-19）で先に外し、#20 の決定 A で
ロックの無い cursor（`mark_reviewed`）にも広げた。

残る違いは、**OPEN のあとで他が行を変えたとき**である。Oracle はそのまま上書きし、移行先は commit で
弾かれる（再試行は呼び出し側の責務）。弾かれる分だけ安全側である。

**拒否が残る条件**:

* **routine がループより前に同じ表を書いている。** そのとき走査は「同じトランザクションで書いた物の
  走査」になり、実行時に落ちる。生成の時点で拒む
* 行ロックを持つ cursor で、落とすと記録されていない（#9）

呼び出し側が同じトランザクションで先に同じ表を書いていた場合は、生成器からは見えない。どの走査にも
言えることで、呼び出し側の境界の設計である。

### 結果に効かない `ORDER BY` は落とす

`mark_reviewed` の `ORDER BY ordered_at` は、全行に同じ定数を書くだけなので結果に効かない。Oracle で
順序が効くのは**ロックを取る順**だけで、移行先に行ロックは無い。落とすと `status` の索引で読め、
パーティションをまたぐ並べ替えが要らなくなる。

落とすのは次が**全部**成り立つときだけである——1 つでも欠けたら順序は残す:

* 件数の上限（`FETCH FIRST` / `ROWNUM` / `LIMIT`）が無い——あれば、どの行が選ばれるかが順序で決まる
* 本体が、読んだ行を**主キーで指す** UPDATE / DELETE だけである——他の行に触れない
* 書く値が、定数・その行の値・routine の引数だけである——前の反復が残した値を読まない
* 途中で抜けない

**その後（2026-09-19 / #19）**: `mark_reviewed` はさらに **1 注文 = 1 トランザクション**へ割った。
`p_status = 'SHIPPED'` で呼ばれると、出荷済みの注文を過去の分まで全部 1 トランザクションで書き換えることに
なり、行数もトランザクションの大きさも業務では決められないからである。本体は同じ値を書くだけなので、
原子性を失っても実害が小さい。処理対象は別のトランザクションでキー順に件数つきで読む（下の「行数の上限」）。

**実測（2026-09-19、実 ScalarDB Cluster）**: `report_mark_reviewed` が Oracle と一致した。

## E. 走査しながら別の表を更新

規則には触れないので生成できる。ただし A と同じくメモリの上限が要り、加えて
**途中で失敗したとき何件まで確定していてよいか**が業務判断になる。囲むトランザクションが 1 つなら
全部戻るが、行数がその 1 トランザクションに収まるかは別問題である。

## F. `BULK COLLECT LIMIT`

```sql
FETCH c BULK COLLECT INTO v_ids LIMIT p_limit;
```

これを 1 行の `SELECT INTO` として扱うと **0 件と複数件が元に無い例外になり、複数行のときは
2 行目以降を黙って捨てる**（P3-2 で直した）。

### 実装（2026-09-18 / #14）: n 件ずつ**配る**ループにする

```java
// 行は先にまとめて読む。p_limit は 1 回に配る件数である
for (List<CollectOpenOrdersLoop3Row> vIds : Plsql.chunks(repository.collectOpenOrdersLoop3(), pLimit)) {
    if (Plsql.eq(vIds.size(), 0)) break;              // EXIT WHEN v_ids.COUNT = 0
    pCount = Plsql.dec(Plsql.add(pCount, vIds.size()));
}
```

**`p_limit` の意味が変わる。これがこの書き換えの代償である。**

| | Oracle | 移行先 |
|---|---|---|
| `p_limit` が決めるもの | 1 回に**読み込む**件数 | 1 回に**配る**件数 |
| メモリを守るもの | `p_limit` | **走査行数の上限**（`--limits` / #19） |

跨トランザクションの cursor が無い以上、行は先に読むしかない。だから `p_limit` はメモリを守らなく
なる——**それを黙って「ただの走査」に潰さない**ために、ループは `BULK_CHUNKED` を持ち、規則
`BULK-003` はその形に対して `row_limit` を要求し続ける。`collect_open_orders` の上限はまだ誰も
決めていないので、`--limits-strict` は今もその名前を挙げる。

書き換えない条件が 1 つある: **ループの後で配列を読んでいるとき**。Oracle は最後に取った（空の）
塊を残すので、そこまで同じにはできない。`EXIT WHEN v_ids.COUNT = 0` は残してある——配る側は
空の塊を渡さないので発火しないが、元に書いてあるものを落とす理由が無い。

**実測（2026-09-18、実 ScalarDB Cluster / `bulk_collect_open_orders`）**: Oracle と一致した
（`p_count` = 2）。書き換える前は `OpenCursor is not translated` で止まっていた。

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

### 実装（2026-09-18 / #14 / #24）

`transactions.perIteration` に `pkg_bulk_load.restock` を記録して、§E と同じ仕組みで割っている。

```java
void restockOne(BigDecimal pProductIdsItem, Long pDeltasItem)
void restockFailed(int i, Exception failed, AuditContext audit)
```

* **要素の並びは元の routine の引数順**にしてある。本体が読んだ順にすると、SQL の書き方が
  変わっただけで引数が入れ替わる——呼び出し側は PL/SQL の signature しか見ていない
* `SQL%BULK_EXCEPTIONS(i).ERROR_INDEX` は**失敗した要素の位置**そのものになった。ただし
  **Oracle は 1 から、生成したループは 0 から数える**ので、記録に残る値が 1 ずれる。1 から
  数えた値を残すなら呼び出し側が `i + 1` を渡す——生成コードにそう書いてある
* `IF SQLCODE = -24381` の分岐は消えた。あの番号は**まとめて投げていたから**付いていたもので、
  要素ごとに失敗が来るなら、それ以外の誤りはそのまま呼び出し側へ出る

**実測（2026-09-18、実 ScalarDB Cluster / `bulk_restock` シナリオ）**: 割った形は動いた——
2 要素とも 1 要素 = 1 トランザクションで実行され、失敗が要素ごとに `BULKERR` 行として
**別トランザクションで**記録された。ただし**その 2 要素は両方とも失敗した**:

```
SET stock_qty = stock_qty + :p_deltas_i: expressions referencing columns are not allowed
```

`UPDATE products SET stock_qty = stock_qty + <delta>` は**読んで計算して書く**形であり、
ScalarDB SQL は列を読む式を受け付けない。#9 の RMW 書き換え（同じトランザクションの中で
読んでから書く）が要るが、それは `rowLocks.optimistic` に**記録された routine だけ**に掛かる。

#### 決定（2026-09-18 / #9）: `restock` も読んでから書く 2 文に割る

記録した理由はこうである——**1 要素 = 1 トランザクションの中で読んで書く**ので、衝突は commit で
弾かれる（P3-4 で実測）。**弾かれた要素は何も書いていない**ので、呼び出し側はその要素だけを
安全に再試行できる: 差分の再適用にならず、二重加算にならない。失敗した要素を記録して続けるのは
`SAVE EXCEPTIONS` の要件そのもので、それは上で決めた形が持っている。

**記録したあとの実測（同日、同じ実クラスタ）**: `bulk_restock` は Oracle と**一致**した
（`stock_qty` 105 / 47、`audit_log` は 0 行）。比較で一致するシナリオは 44 -> **45** になり、
ScalarDB が拒む SQL は 2 -> **1** になった。

---

## H / I. cursor 変数（SYS_REFCURSOR）— 2026-09-25 / #44

`OPEN rc FOR SELECT …` は cursor の宣言を持たないので、以前は下ろせなかった（Unsupported）。いまは OPEN 自身の問合せを cursor の問合せとして、
A〜C・F の形をそのまま当てる。`IF … THEN OPEN rc FOR q1; ELSE OPEN rc FOR q2; END IF; LOOP FETCH rc …; CLOSE rc;` のように**分岐が問合せを選ぶだけ**の形は、
分岐ごとにその問合せのループにする（本体は同じ、行だけが違う）。ELSE の無い IF は、開かれない経路（Oracle では FETCH で ORA-01001）を模していないので触らない。

`OPEN rc FOR q; RETURN rc;` は cursor を呼び出し側へ渡す形で、移行先に渡せる cursor は無い。行を読んで `List<行の record>` を返す method にし、
呼び出し側の受け取り方が変わることを signature で見せる（`public List<GetByDeptLoop1Row> getByDept(...)`）。
実 DB の証拠: samples/oracle-samples の `b04_4_4_ref_cursor` が一致（2026-09-25）。

## 共通して決めておくこと

1. **行数の上限。** 仕組みは `--limits` にある（2026-09-17 の決定）。**値を決めるのは業務側である**——
   各 routine について「何行まで来うるか」を決めて `limits.yaml` に書き、根拠をコメントに残すまで、
   既定値は「決めていない」という意味である。

   **2026-09-19（#19）に、人が値を決めなくて済む形を 3 つ入れた。** corpus では `--limits-strict` が
   通るようになった（値を決めたのは 1 注文の明細数の 200 だけ）:

   | 形 | 例 | 上限はどうなるか |
   |---|---|---|
   | 問い合わせ自身が件数を絞っている | `claim_batch` の `LIMIT p_limit` | 読む行数を決めているのは問い合わせで、件数を渡すのは呼び出し側。人に求めない |
   | 件数を数えるだけの分割読み | `collect_open_orders` | `COUNT(*)` にしたので行を持たない |
   | 1 反復 = 1 トランザクションに割った routine | `nightly_close` / `purge_audit` / `reprice_all` / `mark_reviewed` | 処理対象を**キー順に件数つきで繰り返し読む**。上限は「何行来うるか」ではなく「1 回に何行ずつ取るか」（運用の調整値） |

   繰り返し読む形は **keyset**（`WHERE key > :after ORDER BY key FETCH FIRST :batch`）に揃えた。処理すると
   対象から外れる routine（SHIPPED -> CLOSED、DELETE）なら同じ問い合わせを繰り返せば済むが、外れない routine
   （`reprice_all`）は同じ行が返って終わらない。keyset はどちらでも正しい。並べ替えはパーティションをまたぐが、
   移行先は JDBC に限定した（#20）。**変わること**: ページごとに読むので、途中で増えた行（キーが先のもの）も回る。

   `COUNT(*)` への書き換えでは、`LIMIT` が正でないときに Oracle が投げる誤りをそのまま投げる。
   **2026-09-19 に Oracle 23ai で実測した**: `LIMIT 0` と負の値は ORA-06502、`LIMIT NULL` は ORA-06500。
   分割読み（§F）にも同じガードを入れた——以前 `Plsql.chunks` に「`LIMIT 0` は 0 件で抜けるのと同じ」と
   書いていたのは、**実測せずに書いた誤り**だった。
2. **順序。** `ORDER BY` が clustering key に乗らなければ、アプリ側で並べ替えることになる。
   その時点で全行がメモリに乗る。
3. **失敗したときどこまで確定してよいか。** Oracle の cursor ループは routine 内 COMMIT と
   組み合わさっていることが多い（`prc_nightly_close`）。その場合は `TX-001` の再設計が先である。
