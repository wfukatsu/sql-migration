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
   **誰を記録するのかを決め直す**（多くの場合これは改善である）。

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

ScalarDB に順序オブジェクトは無い。採番方式は
`docs/plsql-transaction-patterns.md` の D で決める（番号に意味があるか／欠番を許すか／順序を保つか）。

trigger 固有の論点は **`WHEN (NEW.order_id IS NULL)`** で、「呼び出し側が指定したならそれを使う」
という意味である。Service でも同じにすること——**常に採番すると、指定した ID が黙って無視される**。

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
`docs/plsql-transaction-patterns.md` の A と同じ再試行の話になる。

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
