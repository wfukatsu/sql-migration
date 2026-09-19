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
| `JOIN ... USING (c)` | `JOIN ... ON a.c = b.c`。結合した列 `c` は、行が全部残る側で修飾する（内部結合・LEFT は FROM 表、RIGHT は結合先の表） |
| Oracle の `LIKE`（`ESCAPE` なし、パターンに `\` がある、または bind） | `LIKE ... ESCAPE ''`。Oracle には既定のエスケープ文字が無く、ScalarDB の既定は `\`。`ESCAPE ''` で無効にできる（公式の文法ページ） |
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
| 精度なしの Oracle `NUMBER` / PostgreSQL `NUMERIC` | `DOUBLE` | WARN（どちらも桁数無制限で小数を持てる。精度なしで `(10,0)` になるのは MySQL だけ） |
| Oracle `INTEGER` / `INT` / `SMALLINT` | `BIGINT` | WARN（Oracle の整数型はすべて `NUMBER(38)`。正確に対応させるなら `NUMBER(p)` と書く） |
| Oracle `FLOAT` | `DOUBLE` | WARN（2 進精度つきの `NUMBER` で最大 38 桁。IEEE の単精度ではない） |
| `CHAR(n)`、n > 1 | `TEXT` | WARN（空白で埋められ、埋めた分を無視して比較される。`TEXT` は厳密に比べるので、移行時に trim しないと同じ比較が当たらなくなる） |
| `TIMESTAMP` / `TIMESTAMPTZ`、精度が 4 以上（Oracle / PostgreSQL は**精度を書かなければ 6**、MySQL は 0） | 同名 | WARN（ミリ秒まで） |
| `TIMETZ` | `TIME` | WARN（オフセットは落ちる） |
| `ARRAY` / `INTERVAL` / `GEOMETRY` | なし | ERROR |

**金額の列は注意**。ScalarDB に `DECIMAL` が無いため `DOUBLE` になり、誤差が出る。スケール済みの整数（例: 円なら 1 倍、ドルなら 100 倍）として `BIGINT` に持たせる設計を推奨する。

---

## 識別子と文字列の意味論

- **引用符は外して出す。** ScalarDB SQL で識別子を引用する方法は、公式の文法ページからは確認できていない。
  そのため、引用符が意味を持っていた名前には WARN `IDENT` を出す: 文法が使う語（`"order"`、`"key"`、`"user"` など）、
  方言が畳む大文字小文字と違う綴り（PostgreSQL の `"UnitPrice"`、Oracle の `"orders"`）。
  すべての識別子を引用する ORM の `"status"` のような名前には出さない
- **大文字小文字**: 移行元は引用していない識別子を畳むが、ScalarDB は畳まない。同じ表が `customers` と
  `Customers` の 2 通りに書かれていたら WARN `IDENT`
- **Oracle の `''`**: 変換できた文（OK / WARN）にも WARN `SEMANTICS` を出す。Oracle は `''` を NULL として保存し、
  `= ''` は決して真にならない。ScalarDB は空文字のまま持つ
- **MySQL の照合順序**: 文字列リテラルとの `=` / `LIKE` / `IN` に INFO `SEMANTICS`（既定の照合順序は大文字小文字を
  区別しない。ScalarDB は厳密に比べる）。列の照合順序が分からないので、判定は変えない

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

読み取り系の ERROR は、ScalarDB から行を取得してインメモリの H2 で元の SQL を実行する「実行計画」に分解できることが多い。`--plan-dir` を付けると分解する。

```bash
.venv/bin/python skills/sql-transpile/scripts/transpile.py <入力.sql> --source oracle --target scalardb \
  --out-dir out --plan-dir out/plans
```

計画の各取得には、H2 に作ると結合が速くなる索引の列（`index_columns`: 主キーと、結合・相関・IN 副問合せの列）が付く。索引を作るかはオプションで、既定はオフ。`--h2-indexes` を付けると計画に `build_indexes: true` が入り、ランタイムが問い合わせの前に索引を作る（`residual-runner run --h2-indexes` でも有効にできる）。数万行以上の表を結合するバッチ処理では数十秒が数秒になる一方、1 表だけの計画や小さな要求では、索引を作る時間（投入と同程度）とメモリ（行の約 1.6 倍）の分だけ遅くなる（`docs/dml-followup-research.md`）。

---

## アプリ側に移す処理の指摘コード

ERROR の読み取り文には、文全体（CTE の本体、サブクエリを含む）を調べた結果が付く。

| コード | 重要度 | 意味 |
|---|---|---|
| `CTE` / `SUBQUERY` / `SET_OP` | ERROR | WITH、サブクエリ、UNION などをアプリで評価する |
| `HIERARCHICAL` | ERROR | `START WITH` / `CONNECT BY`。アプリで木をたどるか、階層を表に事前計算する |
| `WINDOW` / `KEEP` | ERROR | ウィンドウ関数、`KEEP (DENSE_RANK FIRST/LAST)` |
| `PROJECTION` / `GROUP` / `PRED` / `ORDER` | ERROR | 射影・GROUP BY・WHERE・ORDER BY の式や関数。どのスコープかと、射影と GROUP BY は式そのもの、WHERE と ORDER BY は関数名を列挙する |
| `AGG` | ERROR | 集約関数が対応外（COUNT / SUM / AVG / MIN / MAX 以外）か、対応する関数の引数が列でなく式（`SUM(qty * price)`） |
| `PIVOT` / `DISTINCT` / `OFFSET` / `NOW` | ERROR | それぞれの構文。`NOW` は時刻をアプリで計算してバインドする |
| `RESIDUAL_H2` | ERROR | 実行計画の H2 が実行できない構文（`CONNECT BY`、`ROLLUP` / `CUBE` / `GROUPING SETS`、`PIVOT` / `UNPIVOT`、`KEEP`）。計画を作らない |
| `FULL_SCAN` | ERROR | `--storage cassandra` で、キーでもインデックスでも読めない表。すべての表を列挙し、結合相手のキーで読む方法（CTE の列もたどる）を提案する |
| `APP_SEMANTICS` | WARN | アプリで書き換えるときに結果を変えないための注意。計画（H2 が元の SQL を実行する）には付かない |
| `DESIGN` | INFO | 集計表、階層の事前計算、結合列のキー・インデックス、ScalarDB Analytics の提案 |
| `COST` | INFO | 取得コストの見積もり。スキャン 1 行 約 25 µs、キー指定 約 5 ms（`docs/bench-report.md`）。`SERIALIZABLE` はスキャンを 2 倍にする |
| `ROW_LIMIT` | WARN | 計画の行数上限（既定 1 万行）を超える見込み |
| `COST_DEADLINE` | WARN | 見積もりが ScalarDB Cluster の gRPC 期限（既定 60 秒）を超える |
| `CONFIG` | INFO | 読み取り専用トランザクション、`scan_fetch_size`、クロスパーティションスキャン、分離レベルの影響 |

`--storage cassandra` での `FULL_SCAN` の提案は、結合相手がキーで読める場合だけ「read X first, then Y」になる。相手もキーで読めない（循環する）場合は、アプリが既に持っているキーから始める設計が要ると書く。

---

## 前提

ScalarDB SQL は Enterprise Premium の機能で、実行には ScalarDB Cluster とライセンスが要る。変換そのものはライセンス無しで行える。
