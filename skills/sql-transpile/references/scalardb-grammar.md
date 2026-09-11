# ScalarDB SQL の文法と変換ルール

Target に `scalardb` を指定したときに適用されるルールの要点。
公式の文法は https://scalardb.scalar-labs.com/docs/latest/scalardb-sql/grammar/ を参照。

---

## ScalarDB SQL の文法が絞られている理由

ScalarDB は複数のストレージを仮想的に統合し、それらをまたいだトランザクションを実現する。任意のサブクエリや任意の結合を許すと、どのストレージにどの処理を振るかが決まらなくなる。そのため文法は、確実に振り分けられる範囲に意図的に絞られている。

主な制約:

- 射影に書けるのは列と集約関数（`COUNT` / `SUM` / `AVG` / `MIN` / `MAX`）だけ。式や関数は書けない
- WHERE の右辺はリテラルだけ。列どうしの比較は JOIN の ON でしか書けない
- WHERE は DNF（OR の AND）か CNF（AND の OR）で書く必要がある
- JOIN の結合条件は、相手テーブルの主キーかセカンダリインデックスを覆う必要がある
- サブクエリ、CTE、UNION、ウィンドウ関数、DISTINCT、OFFSET、CASE は書けない
- `UPDATE SET col = col + 1` のような、列を参照する式での更新はできない

---

## 自動で書き換えるもの

| ソース構文 | 変換後 |
|---|---|
| `IN (a, b, c)` / `NOT IN` | `(col = a OR col = b OR col = c)` / `col <> a AND col <> b` |
| `NOT (...)` | 比較演算子を反転して押し下げ。`IS NOT NULL` / `NOT LIKE` はそのまま |
| 任意の AND / OR のネスト | DNF または CNF の短いほうに正規化し、括弧を付与 |
| `10 < col` | `col > 10`（右辺をリテラルに揃える） |
| `ROWNUM <= n` / `FETCH FIRST n ROWS ONLY` | `LIMIT n` |
| `FROM a, b WHERE a.x = b.y` | `INNER JOIN b ON a.x = b.y` |
| Oracle 外部結合 `a.x = b.y(+)` | `LEFT JOIN b ON a.x = b.y` |
| `JOIN ... USING (c)` | `JOIN ... ON a.c = b.c` |
| `ON CONFLICT DO UPDATE` / `ON DUPLICATE KEY UPDATE` / `REPLACE INTO` / 定数ソースの `MERGE` | `UPSERT INTO`（意味の差分を WARN） |

---

## 型の対応

ScalarDB の型は 11 種（`BOOLEAN` / `INT` / `BIGINT` / `FLOAT` / `DOUBLE` / `TEXT` / `BLOB` / `DATE` / `TIME` / `TIMESTAMP` / `TIMESTAMPTZ`）。

| ソース型 | ScalarDB | 重要度 |
|---|---|---|
| `NUMBER(p)` / `DECIMAL(p, 0)`、p ≤ 9 | `INT` | INFO |
| 同上、p ≤ 18 | `BIGINT` | INFO |
| `NUMBER(p, s)` / `DECIMAL(p, s)`、s > 0 | `DOUBLE` | WARN（精度損失） |
| `VARCHAR2(n)` / `VARCHAR(n)` / `CLOB` / `TEXT` | `TEXT` | INFO（長さ制約は消える） |
| Oracle `DATE` | `DATE` | WARN（Oracle の DATE は時刻を持つ。時刻を使うなら `TIMESTAMP`） |
| `JSON` / `UUID` / `ENUM` | `TEXT` | WARN |
| `ARRAY` / `INTERVAL` / `GEOMETRY` | なし | ERROR |

**金額の列は注意**。ScalarDB に `DECIMAL` が無いため `DOUBLE` になり、誤差が出る。スケール済みの整数（例: 円なら 1 倍、ドルなら 100 倍）として `BIGINT` に持たせる設計を推奨する。

---

## 制約と主キー

| ソース | 扱い |
|---|---|
| `NOT NULL` / `DEFAULT` / `UNIQUE` / `FOREIGN KEY` / `CHECK` | 削除して INFO または WARN。アプリケーション側で担保する |
| 単一列の主キー | パーティションキー |
| 複合主キー | 先頭列をパーティションキー、残りをクラスタリングキー |
| MySQL のインライン `INDEX (col)` | 別文の `CREATE INDEX ON t (col)` に分離 |
| 複合列インデックス | ERROR（ScalarDB のセカンダリインデックスは単一列） |

キーの分け方は `--keys テーブル=パーティションキー/クラスタリングキー` で上書きできる。

```bash
--keys orders=customer_id/order_no     # customer_id でパーティション、order_no で並べる
```

キー設計は移行後の性能を最も大きく左右する。パーティションキーで絞れない問合せは、表全体を走査するクロスパーティション SCAN になる。

---

## アクセスパスの判定

スキーマ（DDL か `--schema` の JSON）が分かると、各文のアクセスパスを判定して報告する。

| アクセスパス | 条件 | 判定 |
|---|---|---|
| GET | 主キーを完全に指定 | 問題なし |
| パーティション SCAN | パーティションキーを指定 | 問題なし |
| インデックス SCAN | セカンダリインデックスで絞れる | 問題なし |
| クロスパーティション SCAN | キーを覆う述語が無い | WARN（行数に比例して重くなる） |

---

## 変換できないもの（ERROR）

| 構文 | アプリケーション側の対応 |
|---|---|
| 射影の式・関数（`NVL`・`UPPER`・`col * 2`・`CASE`） | 取得後にアプリケーションで計算する |
| サブクエリ・CTE・UNION・ウィンドウ関数・DISTINCT・OFFSET | 取得後にアプリケーションで処理する |
| WHERE の列どうしの比較・関数適用 | JOIN の ON に移すか、取得後に絞る |
| `UPDATE SET col = col + 1` | 読み取り → 計算 → リテラルで UPDATE を 1 トランザクション内で行う |
| `UPDATE ... JOIN` / `DELETE ... USING` / `INSERT ... SELECT` / `RETURNING` | 対象行を読んでからアプリケーションで書き込む |
| `AUTO_INCREMENT` / `SERIAL` / `IDENTITY` / シーケンス | アプリケーションで採番する |
| `ON CONFLICT DO NOTHING` / `INSERT IGNORE` / 表ソースの `MERGE` | 存在確認と書き込みを 1 トランザクションにまとめる |
| ビュー・トリガー・ストアドプロシージャ・複合列インデックス | 設計を変える |

読み取り系の ERROR は、ScalarDB から行を取得してインメモリの H2 で元の SQL を実行する「実行計画」に分解できることが多い。このスキルは分解まではしない。必要なら本体の CLI を使う。

```bash
.venv/bin/python -m scalardb_migrate.cli <入力.sql> --dialect oracle --out-dir out --plan-dir out/plans
```

---

## 前提

ScalarDB SQL は Enterprise Premium の機能で、実行には ScalarDB Cluster とライセンスが要る。変換そのものはライセンス無しで行える。
