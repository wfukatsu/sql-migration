# PL/SQL corpus（P0-1）

実装計画 [docs/plsql-conversion-implementation-plan.md](../../docs/plsql-conversion-implementation-plan.md) の
P0-1「PL/SQL corpus と元表 DDL の作成」の成果物。

## 出自: 合成（2026-09-17 の決定）

**この corpus は全件が合成である。実案件のコードもスキーマも実データも含まない。**

計画 §9 の「corpus の入手元」は合成で代替すると決めた。この決定には次の帰結がある。

- §8 の KPI（parse 率・判定適合率など）は**合成 corpus 上の値であり、実案件耐性の証拠にはならない**。
  レポートには必ずその旨を併記する。
- 期待判定を書く人間とルールを書く人間が同じになるため、数値は自己採点になりやすい。
  これを部分的に抑えるのが下記の holdout である。
- 実案件のコードが入手できた時点で、ここへ追加し、KPI を出自別に出し直す。

## holdout

`src/holdout/` の 5 ユニット（8 ファイル、全 20 ユニット中 25%）は、
**grammar のパッチ作成とルール作成の際に参照しない**。Phase 2 の判定適合率は、まず holdout 上の値を根拠とする。

holdout を使ってよいのは、受入判定の計測時だけである。実装中に holdout を開いて挙動を確認したら、
そのファイルは holdout ではなくなるので、`src/` 直下へ移してこの README を更新する。

## 構成

```text
fixtures/plsql/
  manifest.yaml        期待判定・出自・holdout フラグ（P0-2）
  scalardb-schema.json ScalarDB の Schema Loader JSON（P0-3）
  KEY-DESIGN.md        キー設計の根拠とアクセスパスの洗い出し（P0-3）
  scenarios/           Oracle 実行シナリオ（P0-4）。形式は scenarios/README.md
  golden/              シナリオごとの capture（P0-5）。59 本。形式は golden/README.md
  src/
    schema.sql      corpus が参照する表・順序・索引の Oracle DDL
    *.pks / *.pkb   package 仕様と本体
    *.prc           単独 procedure
    *.trg           trigger
    holdout/        上記と同じ形式。ルール作成時は参照しない
```

計画の P0-1 成果物欄には `*.pks,*.pkb,*.prc` と書いているが、カテゴリ 9（Trigger / DB Link）を満たすために
`*.trg` も置いている。

## カテゴリ網羅（設計書 §16 の 11 カテゴリ）

| # | カテゴリ | `src/` 直下 | `src/holdout/` |
|---|---|---|---|
| 1 | 単純 CRUD | `pkg_customer_crud`, `prc_add_product` | — |
| 2 | SELECT INTO と例外 | `pkg_order_status` | `pkg_payment` |
| 3 | Package 内 private call | `pkg_order_pricing` | `pkg_order_lock` |
| 4 | `%TYPE` / `%ROWTYPE` | `pkg_customer_view` | `pkg_order_lock` |
| 5 | Cursor loop | `pkg_order_report` | `prc_reprice_all` |
| 6 | BULK COLLECT / FORALL | `pkg_bulk_load` | `pkg_customer_import` |
| 7 | routine 内 COMMIT | `prc_nightly_close`, `prc_audit_autonomous` | `prc_reprice_all` |
| 8 | 動的 SQL | `pkg_dynamic_search` | `pkg_customer_import` |
| 9 | Trigger / DB Link | `trg_orders_audit`, `trg_orders_seq`, `prc_remote_sync` | `trg_products_audit` |
| 10 | 日付・NUMBER・NULL 依存 | `pkg_money_calc` | `pkg_payment` |
| 11 | 同時更新・lock 依存 | `pkg_stock_reserve` | `pkg_order_lock` |

11 カテゴリすべてに `src/` 直下のユニットが 1 つ以上ある。ユニット数は 20（ファイル数 32）。

## 判定の期待値

機械可読な形は `manifest.yaml`（P0-2）。粒度は **routine 単位**である。package の中で判定が割れるため
（`pkg_dynamic_search` が典型: 定数の動的 SQL は REVIEW、表名が動的な SQL は REDESIGN）、ファイル単位では測れない。
各ファイルの先頭コメントにも同じ内容を書いてあるが、判定適合率（§8）が突き合わせるのは manifest のほうである。

`tests/test_plsql_manifest.py` が manifest と corpus のずれを検査する。manifest に書いた routine が
ソースに存在すること、**ソースにある routine が manifest に漏れていないこと**の両方向を、
ANTLR パーサで取り出した定義名と突き合わせて確認する。

現在の分布は 48 routine 中 AUTO 14 / REVIEW 19 / REDESIGN 15。

意図した分布は次のとおり。計画どおり、REDESIGN が多いのは**欠陥ではなく検出できたことの成果**である（§8）。

| 期待判定 | 主なユニット |
|---|---|
| AUTO | 単純 CRUD、private call、`%TYPE`/`%ROWTYPE` |
| REVIEW | SELECT INTO の 0 件/複数件、Cursor、BULK COLLECT/FORALL、定数・有限 variant の動的 SQL、日付・数値・NULL 依存 |
| REDESIGN | routine 内 COMMIT、Autonomous Transaction、表名が動的な SQL、Trigger、DB Link、行ロック |

## 確認済み

- 32 ファイルすべてが P0-6 の parser で構文エラーなく parse できる（`tests/test_plsql_corpus.py`）。
  これは parser coverage の確認であって、意味が保存できるかの確認ではない。
- 合成であるため parse 率 100% は当然に近い。実案件コードでの parse 率はこの数値から予測できない。

## Oracle 上で動かす（P0-4）

```bash
(cd difftest && docker compose --profile oracle up -d source-oracle)
.venv/bin/pip install oracledb
.venv/bin/python difftest/plsql_run.py deploy                 # スキーマ作成 + corpus のコンパイル
.venv/bin/python difftest/plsql_run.py run --out fixtures/plsql/golden
```

`deploy` は 32 ユニットすべてをコンパイルする。`prc_remote_sync` だけは DB Link が存在しないため INVALID
のまま残るが、これは想定内としてランナーが許容している。

**parse できることとコンパイルできることは別である。** P0-1 の時点で 32 ファイルすべてが ANTLR で parse
できていたが、Oracle に流したところ 2 件がコンパイルに失敗した（`SQL%BULK_EXCEPTIONS` と `SQLERRM` を
SQL 文の中で参照していた）。corpus 側を直してある。Phase 1 の parse 率は、この意味でコンパイル可能性を
保証しない。
