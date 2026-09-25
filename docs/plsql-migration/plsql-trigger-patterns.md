# Trigger と外部副作用の移行パターン

2026-09-17 / P4-8

Trigger は**隠れた副作用**である。表を書いた誰もが、書いたつもりのないことを起こす。移行先に
trigger は無いので、その副作用は Service 側の明示的な処理になる。

そこで**この移行の中心的な危険**が生まれる:

> Trigger は「この表へのすべての書き込み」に掛かっていた。Service へ移すと、**その Service を通らない
> 書き込みには掛からない**。網羅性が担保できなければ、移行後は trigger があったときより悪くなる。

だから各型の前に、**書込経路の網羅性をどう担保するか**を決める。

---

## 0. 書込経路の網羅性（先に決めること）

移行前に、対象表へ書く経路を**数え上げる**。corpus の範囲では PL/SQL だけだが、実案件では
たいてい他にもある:

| 経路 | どうするか |
|---|---|
| 移行対象の PL/SQL | Service に集約される。`traceability.csv` で辿れる |
| 他システムからの直接 DML | **ここが穴になる。** 経路を塞ぐか、同じ Service を通す |
| 手動の保守 SQL | 運用手順を変える。「直接書いてよい」を残すなら trigger の保証は失われる |
| バッチ／ETL | 同上 |

**担保の方法**は 3 つあり、強い順に:

1. **書き込み口を 1 つにする。** Repository を通らない書き込みを禁じ、権限で強制する。最も強く、
   最も影響が大きい。
2. **検証で追う。** 定期的に「trigger が書いたはずの記録」と実データを突き合わせ、ずれを検出する。
   穴を塞がないが、**穴に気づける**。
3. **諦めて記録する。** 網羅できないと分かったうえで、どの経路が外れているかを文書に残す。
   **黙って移行するよりはるかによい。**

3 を選ぶなら、それは「trigger と同等ではない」という**申し送り**である。

### 決定（2026-09-18 / #12）: 2. 検証で追う

**穴は塞がない。気づけるようにする。**

corpus の書き込み経路を数えた（実測）:

| 表 | 書く routine | 状態 |
|---|---|---|
| `orders`（`trg_orders_audit` / `trg_orders_seq`） | **6 本** | すべて移行対象 |
| `products`（`trg_products_audit`） | **5 本** | すべて移行対象 |

corpus の中では全経路が Service を通る。**実案件で穴になるのは PL/SQL の外**である——他システム
からの直接 DML、手動の保守 SQL、バッチ / ETL。そこまで権限で塞ぐ（方法 1）のは最も強いが影響が
大きく、諦める（方法 3）のは trigger と同等でないという申し送りになる。**その中間を採る。**

検証で追うとき、決めることが 3 つある:

* **何と何を突き合わせるか。** 監査 trigger なら「`orders.status` が変わった回数」と「`audit_log`
  の行数」である。**`traceability.csv` が Service 側の書き込み経路を持っている**ので、
  照合対象はそこから作れる
* **どれくらいの間隔で回すか。** ずれに気づくまでの時間が、そのまま「trigger が掛からなかった
  書き込み」が見えない時間になる
* **ずれを見つけたら何をするか。** 記録を後から補うのか、経路を塞ぐのか。**補えない種類の
  trigger（検証して拒否する B 型）では、ずれは既に入ってしまった不正なデータである**

B 型（検証して拒否する）の網羅性要求は A 型より高い——A なら記録が無いだけだが、B は不正な
データが入る。**同じ「検証で追う」でも、B 型では検出が遅れた分だけ被害が残る。**

### 決定（2026-09-19 / #12）: 「検証で追う」の中身

§0 で残していた 3 つの問いに答えた。

| 問い | 決定 |
|---|---|
| 何と何を突き合わせるか | **データだけで照合する（1a）**。列も変更ログも足さない。trigger の中身から、型ごとの照合を生成器が作る |
| どれくらいの間隔で回すか | **型ごとに変える（2b）**。A（監査）・C（採番）は日次、B（拒否）・D（別表を読む検証）は短い間隔（既定は 1 時間） |
| ずれを見つけたら何をするか | **A は補う**（補った監査行に印を付ける）。**B / D は人が判断する**。そのうえで **B / D の表は直接の書き込みを権限で禁じる**（§0 の方法 1 を、被害の残る表に限って併用する） |

上の「`orders.status` が変わった回数と `audit_log` の行数」は、**あとからは観測できない**——表は今の値しか
持たないからである。実際に照合するのは、**今の値と、監査が最後に記録した値**である。

生成器が作るもの（`TriggerChecks` と `db/restrict-direct-writes.sql`）:

| 型 | trigger | 照合 | ずれたら |
|---|---|---|---|
| A | `trg_orders_audit` / `trg_products_audit` | 今の値 ≠ 最後に監査した値 | **補う**。書いた主体は `BACKFILL`、時刻は補った時刻（元の「誰が」「いつ」は分からない） |
| B | `trg_products_audit` | trigger 自身の拒否条件を、前 = 最後に監査した値・後 = 今の値に当てる | 人が判断する。**A の補完はこの行を飛ばす**——補うと拒否の照合から消え、入ってはいけなかった値を監査済みに見せてしまう |
| C | `trg_orders_seq` | 使われているキーの最大値 | 採番の次の値がこれ以下なら、先へ進める |
| D | `trg_payments_guard` | 生成した本体（読むだけ）を、今ある行ごとに呼ぶ | 人が判断する。**当時は正しかった行も含む**（支払いのあとで取消された注文など） |

`db/restrict-direct-writes.sql` は、B / D の表（`payments` / `products`）に対する `REVOKE INSERT, UPDATE, DELETE`
の雛形である。誰に禁じるか（`<other_user>`）は運用が決める。ScalarDB Cluster の認証・認可が有効である必要がある。

**回すのは呼び出し側である**——間隔は `TriggerChecks.INTERVALS` に書いてあるが、スケジューラは持たない
（生成コードがトランザクションを開かないのと同じ理由）。照合は表を全件読むので、JDBC バックエンド前提で
ある（#20）。

**照合の限界**: 値を変えて同じ値に戻した書き込みは見えない。監査行が 1 行も無い行は、比べる相手が無いので
見ない。

**実測（2026-09-19、実 ScalarDB Cluster / `TriggerChecksIT`）**: 生成したコードを通さずに直接書いた変更を、
4 つの照合がすべて見つけた。A の補完は 10% 下げの行だけを補い、90% 下げ（B が拒否するはずの行）は補わずに
残した。

### 照合の回し方（2026-09-19 / A-2・A-3）

**1 回の照合は、1 つの読み取り専用トランザクションで読む（A-2）。** `orders` と `audit_log` を別々のトランザ
クションで読むと、その間の書き込み（status が変わって監査行が書かれる）が「監査が合わない」に見える。移行後の
trigger は書き込みと同じトランザクションで呼ばれるので、同じ時点を読めば誤検知は出ない。`Connection.setReadOnly(true)`
が ScalarDB の読み取り専用トランザクションになることは実クラスタで確かめた（書き込みは DB-CORE-10211 で拒否される）。

生成されるジョブの雛形（`TriggerCheckJob`）:

| | 中身 | トランザクション |
|---|---|---|
| `daily(audit)` | A / C の照合 -> A の補完 -> 控えの更新 | 照合は読み取り専用、補完と控えの更新は**別々の**書き込み |
| `hourly()` | B / D の照合 | 読み取り専用 |

補完と控えの更新を同じトランザクションにしないのは、控えの更新が監査を全件読むからである——同じ
トランザクションで書いた表の走査は拒否される（DB-CORE-10106）。読み取り専用の照合が衝突で弾かれたとき
（SERIALIZABLE では起こりうる）は、読み直す。**スケジューラは持たない**——いつ回すかは運用が決める（A-1）。

**監査の削除と照合のぶつかりは、控えの表で解く（A-3）。** `prc_purge_audit` は古い監査行を一律に消す。A 型の
照合は「最後に監査した値」を基準にし、監査行の無いキーは比べずに飛ばすので、しばらく変わっていない行が削除
のあとで照合から外れていた——**B 型の拒否条件も、その値を「前の値」として使っているので同時に外れる**。

照合の控え（`trigger_check_baseline`、`db/trigger-check-baseline.sql`）に、キーごとの最後に監査した値を持つ:

* 照合は控えから始め、それより新しい監査行で上書きする
* 控えを更新するのは**最後に監査した値**であって、**今の値ではない**——今の値を控えると、ずれを監査済みと
  して洗い流してしまう（B が拒否する行を補わないのと同じ理由）
* **日次の照合は、監査の削除より先に回す**——削除のあとに控えを更新すると、消えた監査行の値が控えに残らない

`prc_purge_audit` 自体は変えていない。Oracle 版から外れる変更（キーごとの最新行を残す）ではなく、照合の側で
基準を持つ形にした。

**実測（2026-09-19、実 ScalarDB Cluster / `TriggerChecksIT` 7 本）**: 控えを取らずに監査行を消すと、ずれも拒否も
照合から消えた（反対側のテスト）。控えを取ってから消すと、両方とも見えたままだった。ジョブは読み取り専用で照合し、
orders 1001 と products 20 を補い、products 10（B が拒否するはずの行）は補わなかった。

### 実装（2026-09-18 / #12）: 見えている経路には掛ける

生成器は、**書き込む文のところで trigger を呼ぶ**。掛けるのは自分が見えている経路だけで、
それ以外は検証の仕事として残る——上の決定のとおりである。

```java
// pkg_shipment（orders を書くので trg_orders_audit を受け取る）
public PkgShipmentService(PkgShipmentRepository repository, TrgOrdersAuditService trgOrdersAudit)
...
    Object[] old = repository.markShippedStmt1trg1(pOrderId);   // :OLD（行が無ければ null）
    rowCount = repository.markShippedStmt1(pWhen, pOrderId);
    if (Plsql.gt(rowCount, 0)) {                                 // 0 行の更新では発火しない
        trgOrdersAudit.body(vTrg1, "SHIPPED", vTrg2, audit);
    }
```

**誰が呼ぶかが constructor に出る。** これは配線の都合ではなく、「掛かるのはこの経路だけ」という
事実がそこに見えるということである。

掛けるときに守っていること:

| 守ること | どうやって |
|---|---|
| `:OLD` は更新の**前に**読む | 書き込みの手前に読みを挟む。後では元の値が無い |
| 発火条件（`WHEN`） | **複製しない。** trigger 本体の先頭に番人として出ている（2 か所に書くと片方が古くなる） |
| 掛かる列（`UPDATE OF status`） | SET にその列が無ければ掛けない。掛けると**記録される量が変わる** |
| BEFORE と AFTER の順序 | BEFORE は書く前（値を拒否する trigger がある）、AFTER は書いた後 |
| **0 行の更新では発火しない** | AFTER は `SQL%ROWCOUNT`、BEFORE は `:OLD` が見つかったかで判断する |

最後の 1 つは**実際に壊した**。`:OLD` を無条件に `SELECT INTO` として読んだために、更新する行が
無いときの `SQL%ROWCOUNT = 0`（`mark_shipped` の「注文が無い」-20071）が NO_DATA_FOUND に化けた。
Oracle との比較が捕まえた——**trigger を掛ける側のバグは、掛けた表ではなく元の分岐に出る**。

掛けないと決めているもの:

* **1 行に絞れない更新**（主キーを等値で押さえていない WHERE）。Oracle なら行ごとに発火するので、
  1 回の呼び出しでは同じにならない。`TRIGGER_NOT_APPLIED` を残して見えるようにする
* **値そのものを書き換える trigger**のうち、採番だけの形に当てはまらないもの。呼び出しでは置き換えられない
  （下の C）。**採番だけの trigger は織り込む**（2026-09-20）: `BEFORE INSERT` で本体が `:NEW.<列> := <seq>.NEXTVAL` の
  1 文だけ、`WHEN` は無いか `WHEN (NEW.<列> IS NULL)` のとき、書き込む側の INSERT にその列と `seq.NEXTVAL` を足す
  （診断 `TRIGGER_INLINED`、注記のルール TRG-OPT-003）。番号は移行先の採番方式（計画 §9）で取る。静的に決まる場合
  だけ扱う: キーを書いていない・`NULL` を書いている → 織り込む、リテラルのキーを書いている（WHEN つき）→ 発火しない。
  変数のキーは実行時まで NULL かどうか分からず、`NVL(p_id, seq.NEXTVAL)` は使わなくても番号を消費するので、
  `TRIGGER_REDESIGN` のまま残す。ほかの trigger と違って、この経路を通らない書き込みは黙って通らない——キーを書かない
  INSERT は ScalarDB が拒否する
* **MERGE での書き込み**（INSERT と UPDATE のどちらで発火するかが行ごとに決まる）と、本体が
  **`UPDATING('列')` の形で列ごとのイベントを見る trigger**。どちらも `TRIGGER_NOT_APPLIED` を残す

**DELETE と、複数のイベントで発火する trigger は掛ける**（2026-09-20 / #29 の 25。それまでは掛けずに
`TRIGGER_NOT_APPLIED` にしていた）。

| 形 | 掛け方 |
|---|---|
| DELETE | `:NEW` の行は無いので、相関行はすべて**消す前に読んだ `:OLD`**。`:NEW.x` は Oracle と同じく NULL を渡す。AFTER DELETE でも読むのは消す前。1 行に絞れる条件（主キーの等値）は UPDATE と同じ |
| 複数イベント（`INSERT OR UPDATE OR DELETE`） | 本体が読む `INSERTING` / `UPDATING` / `DELETING` を、**相関行と同じく引数で渡す**。どの文のところで呼んでいるかは呼ぶ側が静的に知っているので、定数（`true` / `false`）になる。そのイベントに無い側の行（INSERT の `:OLD`、DELETE の `:NEW`）は NULL |
| BEFORE で、本体が行を 1 つも読まない | 主キーだけ先に読む。BEFORE は書く前に呼ぶので `SQL%ROWCOUNT` で「当たる行があったか」を見られず、読まないと無い行の UPDATE / DELETE でも発火してしまう |

相関行の数値は、列の幅（`NUMBER(10)` → `Long`）ではなく PL/SQL の NUMBER（`BigDecimal`）として受け渡す。呼ぶ側の
引数も、本体の SQL の bind もそう型付けされているためである。

`UPDATE OF qty OR DELETE ON t` の列の並びを `qty or delete` と読んで、qty を SET する更新に trigger が**黙って
掛からない**不具合もこのとき直した（列の並びは次のイベントか ON で終わる）。

実 DB の evidence は `pkg_line_edit`（`trg_lines_audit`: 3 イベントの監査、`trg_inventory_tx_keep`: BEFORE DELETE の
検証）を **routine 経由で**通す 7 シナリオで、scaled・double とも Oracle と一致した。コーパスにこの形の trigger が
無かったので実装と一緒に書いたもので、holdout ではない。直接の DML の 3 シナリオ（`trigger_orders_seq_assigns_id`、
`trigger_products_audit_price`、`trigger_products_audit_rejects_big_drop`）は §0 の決定どおり相違のままで、これは
この実装では変わらない。

`trg_lines_audit` には照合（TriggerChecks の A）が組まれない: 監査行のキーが 2 列の主キーのうち 1 列だけで、
監査の読み取りが列の役割を推測しないためである。照合の側を広げるのは別の作業になる。

**掛けなかった書き込みも REDESIGN になる**（ルール TRG-002。`TRIGGER_NOT_APPLIED` / `TRIGGER_REDESIGN` の付いた
文を見る）。以前はこの診断を読むルールが無く、さらに trigger のイベントは最初の 1 つしか残さず、DELETE で書く
routine は黙って通り過ぎていた——**正しく掛けられた routine が REDESIGN で、掛けられなかった routine が AUTO に
なりうる**、という逆転があった（レビュー #27-25）。

**`UPDATING('列')` は書く側が渡す**（2026-09-25、samples/oracle-samples の `emp_biu_trg`）。本体が `IF UPDATING('SALARY') THEN` で列ごとのイベントを読むとき、この UPDATE が SET に salary を書いているかは呼ぶ側で静的に決まるので、`INSERTING` / `UPDATING` / `DELETING` と同じく BOOLEAN の引数（`UPDATING_SALARY`）で渡す。以前は「渡す形が無い」として掛けていなかった。TriggerChecks の D 型（今ある行を本体に通す）はイベントに false を渡す——今ある行は書かれている最中ではない。`:NEW.x := …` で値を書き換える本体は変わらず `TRIGGER_REDESIGN`（#47）。

**判定は動かない。** trigger が REDESIGN なら、それを呼ぶ経路も REDESIGN になる（呼び出しグラフを
通って伝わる）。`mark_shipped` は AUTO から REDESIGN へ変わった——**簡単な routine だから安全、
ということではない**。網羅性はその経路の設計の問題であって、routine の複雑さの問題ではない。

**実測（2026-09-18、実 ScalarDB Cluster）**: `nightly_close` と `lock_cancel` が Oracle と一致した。
`audit_log` の行が揃ったのは、`trg_orders_audit` が書いていた行をこの経路が書くようになったからである。

---

## 判定の早見表

| 元の形 | 例 | 置き換え | 網羅性の要求 |
|---|---|---|---|
| A. 監査ログ | `trg_orders_audit` | Service の書き込み後に記録 | **高い**（漏れた書き込みは記録されない） |
| B. 検証／拒否 | `trg_products_audit` | Service の書き込み前に検査 | **最も高い**（漏れた書き込みは検証されない） |
| C. 採番 | `trg_orders_seq` | 採番 Service | 中（採番漏れは主キー違反で気づく） |
| D. 別表の参照検証 | `trg_payments_guard` | Service で先に読む | 高い |
| E. DB link 越しの副作用 | `prc_remote_sync` | 分散トランザクションの設計 | 別問題（下記） |

---

## A. 監査ログ

```sql
AFTER UPDATE OF status ON orders FOR EACH ROW WHEN (OLD.status <> NEW.status)
BEGIN INSERT INTO audit_log (... , :OLD.status, :NEW.status, SYSTIMESTAMP, USER); END;
```

**保つべきものが 4 つある**、どれも落としやすい:

1. **before と after の両方。** Service では更新前の値を**先に読んでおく**必要がある。読まずに
   更新すると `:OLD` に相当する値が無い。
2. **発火条件。** `WHEN (OLD.status <> NEW.status)` は「変わったときだけ」である。無条件に記録すると
   量が変わる。
3. **失敗時の挙動。** trigger の INSERT は同じトランザクションなので、**監査が失敗すれば更新も失敗
   した**。Service で別トランザクションにすると、その性質が変わる。
4. **`USER` と時刻の出所。** `USER` は DB セッションのユーザで、アプリの利用者ではない。移行時に
   **誰を記録するのかを決め直す**（多くの場合これは改善である）。**生成規約は決まっている**——
   `USER` と `SYSTIMESTAMP` は呼び出し側が `AuditContext` として渡す（#1・#8）。**何を渡すのが
   業務的に正しいかは、渡す側で決める**。

```java
// Service の中。順序が意味を持つ
OrdersRow before = repository.load(orderId);          // :OLD
repository.updateStatus(orderId, newStatus);          // 本体
if (!Plsql.eq(before.status(), newStatus)) {          // WHEN 句
    repository.insertAudit(orderId, before.status(), newStatus, actor, at);
}
```

**採番（`seq_audit_id.NEXTVAL`）は C の問題**であり、ここでも解かなければならない。

## B. 検証して拒否する

```sql
BEFORE UPDATE OF unit_price ON products FOR EACH ROW
BEGIN
  IF :NEW.unit_price < :OLD.unit_price * 0.5 THEN
    RAISE_APPLICATION_ERROR(-20050, 'price drop over 50% requires approval');
  END IF;
  ...
END;
```

これは**業務ルールがデータベースに置かれている**形である。Service へ移すと、**Service を通らない
更新はルールを免れる**。A より危険なのは、A なら記録が無いだけだが、B は**不正なデータが入る**からである。

移行の形は素直（更新前に検査する）だが、**網羅性の担保（§0）を決めずに移してはならない**。
決められないなら、検証を残せる場所——アプリの単一の書き込み口——を先に作る。

## C. 採番 trigger

```sql
BEFORE INSERT ON orders FOR EACH ROW WHEN (NEW.order_id IS NULL)
BEGIN :NEW.order_id := seq_order_id.NEXTVAL; END;
```

ScalarDB に順序オブジェクトは無い。**採番方式は決まっている**（2026-09-17、計画 §9）——代理キーは
hi/lo、業務上意味のある番号は counters 表 + 再試行。`docs/plsql-migration/plsql-transaction-patterns.md` の D を見ること。
`orders.order_id` は注文番号なので**後者**である。

trigger 固有の論点は **`WHEN (NEW.order_id IS NULL)`** で、「呼び出し側が指定したならそれを使う」
という意味である。Service でも同じにすること——**常に採番すると、指定した ID が黙って無視される**。

### C-2. 値を書き換える BEFORE trigger（畳み込み、#47）

```sql
BEFORE INSERT OR UPDATE OF salary, email ON employees FOR EACH ROW
BEGIN
  :NEW.email := UPPER(:NEW.email);
  IF UPDATING('SALARY') AND :NEW.salary < :OLD.salary * 0.5 THEN RAISE_APPLICATION_ERROR(-20030, '…'); END IF;
END;
```

採番以外の `:NEW.x := 式` も、**式が :NEW / :OLD の値・リテラル・runtime が評価できる関数だけを読み、本体の先頭で無条件**
なら、書く側が同じ式を書く値に畳み込める（`triggers._fold`、2026-09-25、samples/oracle-samples の `emp_biu_trg`）:

```sql
INSERT INTO employees (…, email, …) VALUES (…, UPPER(p_email), …);
-> emp_biu_trg.body(NEW.email => UPPER(p_email), …);            -- 検査はここで走る（BEFORE）
   INSERT INTO employees (…, email, …) VALUES (…, UPPER(UPPER(p_email)), …);
UPDATE employees SET salary = v WHERE employee_id = p_id;         -- email を SET していない
-> SELECT email, salary INTO v_trg_1, v_trg_2 …;                  -- :NEW.email は今の値
   emp_biu_trg.body(…);
   UPDATE employees SET salary = v, email = UPPER(v_trg_1) WHERE employee_id = p_id;
```

本体は自分の引数に代入する（`newEmail = Plsql.upper(newEmail)`）ので、あとの検査は書き換え後の値を読む。
`TRIGGER_FOLDED` が付き、TRG-001 は「決定済み」（FOLD_DECISION）になる。畳み込めない代入——条件つき、局所変数や
SQL を読む、`:OLD` への代入——は `TRIGGER_REDESIGN` のまま（掛けない、と言う）。

## D. 別表を読んで検証する

```sql
BEFORE INSERT ON payments FOR EACH ROW
DECLARE v_status orders.status%TYPE;
BEGIN
  SELECT status INTO v_status FROM orders WHERE order_id = :NEW.order_id;
  IF v_status = 'CANCELLED' THEN RAISE_APPLICATION_ERROR(...); END IF;
END;
```

Service で先に読むだけだが、**読んだ後に相手が変わりうる**。Oracle でも trigger は
その行をロックしていないので同じだが、ScalarDB では**衝突として現れる**——
`docs/plsql-migration/plsql-transaction-patterns.md` の A と同じ再試行の話になる。

`SELECT INTO` の 0 件（`NO_DATA_FOUND`）を落とさないこと。参照先が無い支払いは、
**検証を通ったのではなく検証できなかった**のであり、意味が違う。

## E. DB link 越しの副作用

```sql
INSERT INTO remote_orders@remote_db ...;
```

trigger とは別の問題だが、**「移行対象の外側に副作用がある」**点は同じである。分散トランザクション
の設計（参加 DB・timeout・再試行）が要り、**PoC の範囲では再設計として扱う**。

corpus の `prc_remote_sync` は Oracle 側でも DB link が無いためコンパイルできない
（`fixtures/plsql/golden/README.md` に記録済み）。**動かない物を移行の成功例にしない**ため、
比較対象からも外してある。

---

## 共通して決めること

1. **書込経路の網羅性（§0）。** これが決まらないうちは、他をどれだけ正しく移しても trigger の保証は
   再現できない。
2. **失敗の連動。** trigger の副作用は同じトランザクションにあった。Service で分けるなら、
   **分けてよい理由**を書くこと。
3. **発火条件（`WHEN` 句）。** 落とすと量が変わり、監査なら「変わっていないのに記録がある」になる。
4. **`USER` の置き換え。** DB セッションのユーザではなく、アプリの利用者を記録する機会である。
