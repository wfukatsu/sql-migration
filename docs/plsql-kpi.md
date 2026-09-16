# PL/SQL 移行基盤の KPI・AUTO 禁止条件・確信度（P0-7）

実装計画 [plsql-conversion-implementation-plan.md](plsql-conversion-implementation-plan.md) の P0-7 の成果物。
各フェーズの合否を人の判断に委ねず、機械的に判定できるようにするための定義をここに置く。

設計書 §8 の確信度式と §16 の PoC 合否項目を、実際に計算できる形まで落とす。

---

## 0. 前提: この corpus の数値が何を示さないか

corpus は全件が合成である（計画 §9 の決定、2026-09-17）。したがって **ここで定義する KPI はすべて
合成 corpus 上の値であり、実案件の PL/SQL に対する耐性を示すものではない**。

- レポートには必ずその旨を併記する。
- 判定適合率（KPI-3）の最終的な根拠には、ルール・grammar の作成時に参照しない
  **holdout**（`fixtures/plsql/src/holdout/`、全 24 ユニット中 9 ユニット = 37.5%）上の値を用いる。
  ただし **2026-09-17 時点で holdout の独立性は失われている**。P2-2 のルール開発中に期待値との差分一覧を
  繰り返し出力し、そこに holdout の routine 名と期待判定が含まれていたためである
  （`fixtures/plsql/README.md` に経緯を記録）。
  回復のため `src/holdout2/`（4 ユニット / 8 routine）を、ルールを凍結した後に書き起こし、期待判定を
  測定前に確定させた。**独立した証拠として読めるのは現時点で `holdout2/` の値だけ**である。
- 実案件由来のコードが入手できた時点で corpus に追加し、出自別（`real-anonymized` / `synthetic`）に
  集計し直す。

---

## 1. KPI の定義

計測はすべて `fixtures/plsql/manifest.yaml` を正解として行う。粒度が routine 単位なのは、
package の中で判定が割れるためである。

### KPI-1 parse 率

```text
parse 率 = 構文エラーなく parse できたファイル数 / corpus の全ファイル数
```

- 分母は `fixtures/plsql/src/` 以下の `*.pks` `*.pkb` `*.prc` `*.trg`（現在 38）。
- 構文エラーは例外ではなく診断として数える。1 ファイルの失敗が他を止めてはならない。
- **目標: Phase 1 で 90% 以上**。
- 注意: parse できることは**コンパイルできることを意味しない**。P0-4 で、ANTLR が通した 32 ファイルのうち
  2 件が Oracle のコンパイルで落ちた。parse 率は parser coverage の指標であって、移行可能性の指標ではない。

### KPI-2 symbol / type 解決率

```text
解決率 = 解決できた識別子参照の数 / 全識別子参照の数
```

- 分母は IR 上の識別子参照（変数・定数・引数・戻り値・package 公開要素・`%TYPE` / `%ROWTYPE` の参照先）。
- 未解決は理由付きで一覧できること（`unresolved.md`）。数だけ合っていても理由が出ないなら未達とする。
- **目標: Phase 1 で 95% 以上**。

### KPI-3 判定適合率

```text
適合率 = manifest の expected と一致した routine 数 / 判定対象の routine 数
```

- 分母は現在 56 routine（うち holdout 18）。
- **AUTO 禁止条件（§2）に該当する routine を AUTO と判定したら、その時点で不合格**とする。
  適合率が何 % でも、この false negative は許さない。
- **目標: Phase 2 で、AUTO 禁止条件の取りこぼし 0、全体一致 90% 以上**。
比較するのは**ルール判定**（`Decision.rule_verdict`）である。証拠まで含めた最終判定（`Decision.verdict`）は
Phase 3 が capture の合否を出すまで AUTO になり得ないため、それを manifest と比べるとルールの誤りに見えてしまう。
- 最終的な根拠は holdout 上の値とする（§0）。

補助指標として混同行列を出す。どちらへ間違えたかで意味が違うためである。

| 実際 \ 判定 | AUTO | REVIEW | REDESIGN |
|---|---|---|---|
| AUTO | 正解 | 安全側の誤り（工数が増えるだけ） | 安全側の誤り |
| REVIEW | **危険な誤り** | 正解 | 安全側の誤り |
| REDESIGN | **危険な誤り** | **危険な誤り** | 正解 |

### KPI-4 AUTO 生成コードの compile 率

```text
compile 率 = compile が通った AUTO 対象 routine 数 / AUTO 判定された routine 数
```

- 計測は `gradle compileJava`。warning policy を満たすこと（警告を成功として隠さない）。
- **目標: Phase 2 で 100%**。1 件でも落ちたら未達。

### KPI-5 意味的同等性テスト合格率

```text
合格率 = Oracle と結果が一致した capture 数 / 実行した capture 数
```

- 比較対象は設計書 §10.2 の全項目（戻り値、OUT / IN OUT、例外型と業務エラーコード、更新前後の行集合、
  影響行数、NULL・空文字・末尾空白、数値精度と丸め、日付・timezone・NLS、監査などの副作用）。
- 固定できない時計から書かれる列は、シナリオが `mask` で宣言した分だけ比較対象から外す。
  マスクは capture の `masked` に残るので、黙って落ちることはない（P0-4）。
- **目標: Phase 3 で AUTO 対象 100%**。REVIEW 対象は 100% でなくてよいが、**差分の理由を説明できること**を
  合格条件とする。

### KPI-6 人手修正時間

```text
中央値 = 生成物を受け入れ可能にするまでの実作業時間の中央値（判定区分ごと）
```

- 記録先は P3-5 が出す `decisions.json`。routine 単位で、AUTO / REVIEW / REDESIGN 別に記録する。
- **目標: Phase 3 で REVIEW 1 routine あたりの中央値をベースラインとして確定する**。以降はトレンドを見る。
- この KPI だけが移行工数そのものを測る。他の 5 つはツール内部の健全性指標であり、
  AUTO 率が上がっても REVIEW の手戻りが重ければ移行は速くならない。

### KPI-7 1,000 行当たりの未解決重大リスク数

```text
密度 = unresolved.md の ERROR 件数 / (corpus の総行数 / 1000)
```

- 継続計測し、削減トレンドを見る。絶対値の目標は置かない。

### 変換率を KPI にしない

行数ベースの変換率は測らない。corpus には routine 内 COMMIT・動的 SQL・Trigger・行ロックが
意図的に多く含まれており、その多くは REDESIGN に落ちる。**REDESIGN は失敗ではなく検出できたことの成果**
である。変換率を指標にすると、危険なものを AUTO に寄せる圧力が生まれる。

---

## 2. AUTO 禁止条件

次のいずれかを含む routine は、確信度がいくつであっても **AUTO にしない**。ルールエンジンはこれを
最優先で評価する。

| # | 条件 | 検出方法 | 最低判定 |
|---|---|---|---|
| 1 | routine 内の `COMMIT` / `ROLLBACK` / `SAVEPOINT` / `ROLLBACK TO` | IR の文種別 | REDESIGN |
| 2 | `PRAGMA AUTONOMOUS_TRANSACTION` | IR の routine 属性 | REDESIGN |
| 3 | Package 変数（package spec / body の状態） | Symbol Table | REDESIGN |
| 4 | `EXECUTE IMMEDIATE` / `DBMS_SQL` のうち、定数畳込みで SQL を確定できないもの | IR の DynamicSql | REDESIGN |
| 5 | Trigger | IR の module 種別 | REDESIGN |
| 6 | `AUTHID CURRENT_USER` | IR の routine 属性 | REDESIGN |
| 7 | DB Link を介した参照 | 識別子の `@link` | REDESIGN |
| 8 | 行ロック（`FOR UPDATE` とその変種） | SQL AST | REDESIGN |
| 9 | 変換不能な SQL（converter が `ERROR` を返す）を含む | SQL bridge の結果 | REVIEW 以上 |
| 10 | 実行計画に落ちる SQL（`PLANNED`）を含む | SQL bridge の結果 | REVIEW 以上 |
| 11 | write set と `PLANNED` の fetch 対象表が交差し、その fetch が走査 | P2-1 の解析 + 実行計画 | REDESIGN |
| 12 | 未解決シンボル・未解決型を含む | Symbol Table | REVIEW 以上 |

条件 11 は ScalarDB の制約による。同一トランザクション内で自分が書いた行を**走査**することはできない
（P2-9 で実測。キーアクセスなら見える）。実行時には `ScanAfterWriteException` になる。

---

## 3. 確信度

設計書 §8 の式を使う。

```text
confidence = ruleCoverage × symbolResolution × typeResolution × targetCapability × testEvidence
```

**LLM の自己申告値は使わない。** すべて規則と検証結果から計算する。

### 各因子の算出

いずれも 0.0〜1.0。`0` になる条件を明示してあるのは、1 つでも 0 なら積が 0 になり AUTO にならないためである。

| 因子 | 算出 | 0 になる条件 |
|---|---|---|
| `ruleCoverage` | ルールが判定を出せた IR ノード数 / routine 内の全 IR ノード数 | 未知の構文を 1 つでも含む |
| `symbolResolution` | 解決できた識別子参照 / 全識別子参照（KPI-2 の routine 単位版） | 未解決シンボルが 1 つでもある |
| `typeResolution` | 型が確定した変数・引数・戻り値 / 全体 | 精度不明の `NUMBER` を Java 型へ確定できない、`%TYPE` の参照先 DDL が無い |
| `targetCapability` | ScalarDB SQL で実行できる SQL 文 / routine 内の全 SQL 文。`PLANNED` は 0.5 として数える。**まだ検査していない文は 0.5 ではなく 0** — 「未解析」は能力の半分ではない | converter が `ERROR` を返す SQL を含む、または P2-4 を通していない SQL を含む |
| `testEvidence` | 意味的同等性テストに合格した capture 数 / その routine に紐づく capture 数 | capture が 1 つも無い、または 1 つでも落ちている |

`testEvidence` の定義から、**capture が無い routine は AUTO にならない**。これは意図した性質である。
テストの裏付けがないコードを人のレビューなしに出さない。

### AUTO の下限しきい値

```text
AUTO とするのは confidence >= 0.95 かつ §2 の禁止条件に 1 つも該当しない場合に限る。
0.95 未満は REVIEW。REDESIGN は §2 の条件か、ルールが明示的に REDESIGN を出した場合。
```

0.95 という値の根拠は、5 因子の積であることによる。各因子が 0.99 なら積は 0.951 でちょうど境界に乗り、
1 つでも 0.95 に落ちれば積は 0.912 で届かない。
つまり **どの側面もほぼ完全なときだけ AUTO を許す**、という意味の閾値である。
この値は PoC の結果を見て見直す。見直したらこの文書と `plsql/rules/` の両方を同時に更新する。

### 確信度を上げてはいけない経路

- LLM の出力を根拠に因子を上げない。LLM の生成物は常に REVIEW 扱いで、規則化と差分テスト通過を経て
  初めて AUTO 相当になる（設計書 §17-7）。
- 人がレビューで承認したことを `testEvidence` に数えない。承認は決定であって証拠ではない。
  承認した内容はルールか mapping として戻し、その結果としてテストが通ることで初めて数える。

---

## 4. 計測の再現手順

```bash
# KPI-3 判定適合率（ルール判定 vs manifest）。ScalarDB の能力判定まで含めた値は capability 側で測る
.venv/bin/python -m pytest tests/test_plsql_rules.py tests/test_plsql_safety.py tests/test_plsql_capability.py -q

# ScalarDB で実行できる文の割合（P2-4）
.venv/bin/python -m plsql.cli fixtures/plsql/src --scalardb-schema fixtures/plsql/scalardb-schema.json

# KPI-2 symbol / type 解決率
.venv/bin/python -m pytest tests/test_plsql_symbols.py -q

# KPI-1 parse 率 / corpus の健全性
.venv/bin/python -m pytest tests/test_plsql_corpus.py tests/test_plsql_frontend.py -q
.venv/bin/python -c "from plsql.frontend import parse_directory, coverage; c = coverage(parse_directory('fixtures/plsql/src')); print(f'parse rate {c.rate:.1%}', c.failed)"

# KPI-3 の正解（manifest）と corpus のずれが無いこと
.venv/bin/python -m pytest tests/test_plsql_manifest.py -q

# KPI-5 の入力（Oracle 側の capture）      ※ Oracle コンテナが要る
.venv/bin/python difftest/plsql_run.py deploy
.venv/bin/python difftest/plsql_run.py run --out fixtures/plsql/golden
```

KPI-1 と KPI-2 はレポートが同時に出す（P1-7）。

```bash
.venv/bin/python -m plsql.cli fixtures/plsql/src --out-dir out/plsql
# parse rate / type resolution が標準出力に、詳細が out/plsql/inventory.json に出る
```

KPI-4 / KPI-5 / KPI-6 / KPI-7 の計測コマンドは、対応するフェーズの実装（P2-8 / P3-2 / P3-5）が
入った時点でここに追記する。
