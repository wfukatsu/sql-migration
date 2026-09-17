# Phase 3 完了報告 — PL/SQL → ScalarDB 変換 PoC

2026-09-17 / 対象コミット: `main`

## 1. 判定

**Phase 3 の完了条件を満たした。** 条件は「AUTO 対象の意味的同等性テストが 100%、REVIEW 対象は差分理由を
説明できる」（計画 §5）。実 ScalarDB Cluster と実 Oracle に対して測定し、AUTO 14 本は両方の金額規約で
Oracle と完全一致、残る差異はすべて REVIEW / REDESIGN で理由が付いている。

**ただしこの数値は合成 corpus 上のものである。** 実案件の PL/SQL での達成を示す証拠ではない（計画 §9 の
決定）。実案件コードが入手できた時点で corpus に追加し、KPI を出自別に出し直す必要がある。

## 2. KPI

`python -m plsql.kpi --evidence difftest/work/plsql-diff.json --generated generated` の出力。

| KPI | 目標 | 実測 | 判定 |
|---|---|---|---|
| KPI-1 parse 率 | 90% 以上 | **100.0%**（38/38 files） | 合格 |
| KPI-2 型解決率 | 95% 以上 | **100.0%**（167/167 typed symbols） | 合格 |
| KPI-3 判定一致 | 90% 以上・AUTO 禁止条件の取りこぼし 0 | **100.0%**（56/56）・取りこぼし **0** | 合格 |
| KPI-4 compile 率 | 100% | **100.0%**（AUTO 15/15 が生成完結） | 合格 |
| KPI-5 意味的同等性 | AUTO 100% | **100.0%**（scaled / double とも AUTO 14/14） | 合格 |
| KPI-6 人手修正時間 | ベースライン確定 | **未計測**（0/56 routine） | **未達** |
| KPI-7 未解決リスク密度 | 目標なし（トレンド） | 25.8 件/1000 行（24 ERROR / 931 行） | — |

**KPI-6 は未達である。** 誰も実作業時間を測っていないので `decisions.json` は `null` のままであり、
「REVIEW 1 routine あたりの中央値をベースラインとして確定する」という Phase 3 の目標は達成していない。
枠組み（`--fix-times` で人が測った値を取り込み、`measured` 件数つきで中央値を出す）は用意したが、
値そのものは人が REVIEW を実際に消化しないと出ない。**数字を作って埋めることはしていない。**

## 3. 何を作ったか

| タスク | 成果物 | 結果 |
|---|---|---|
| P3-1 | `runtime-java` の `ScalarDbCaptureIT` / `ScalarDbRunner` / `Scenario`、`difftest/plsql_capture.py` | 両金額規約で 52/59 採取、想定外の失敗 0 |
| P3-2 | `difftest/plsql_compare.py`、`difftest/plsql_diff.py` | AUTO 14/14 一致 |
| P3-3 | `difftest/plsql_semantics.py`、`fixtures/plsql/semantics.json`、`PlsqlPropertyTest`、`tests/test_plsql_property.py` | Oracle の実答 2015 件を両側で再生 |
| P3-4 | `runtime-java` の `TransactionIT` | 7 本。同時更新で更新消失なし |
| P3-5 | `plsql/review.py`、`plsql/kpi.py` | `decisions.json` / `unresolved.md` / `traceability.csv` / KPI 計測 |

### 採取できなかった 7 シナリオ（両規約共通）

| 件数 | 理由 | 扱い |
|---|---|---|
| 5 | trigger 4 本と `pkg_customer_import.import` を生成器が意図的に拒否 | 仕様どおり |
| 2 | setup の `SYSTIMESTAMP` が ScalarDB SQL で書けず、変換器が正しく拒否 | fixture 側の課題 |

黙って飛ばしたものは無い。各シナリオは「capture ファイルがあるか、名前つきの理由があるか」のどちらかで
あることをテストが検査する。

## 4. Phase 3 が見つけた不具合

**8 件。いずれも compile が通り、多くは H2 でも通っていた。** 「compile が通ることは意味が保存されている
証拠にならない」の次の段として、**「H2 で通ることは ScalarDB で動く証拠にならない」**が要ることを示した。

| # | 不具合 | 性質 |
|---|---|---|
| 1 | 生成 Repository が `BigDecimal` をそのまま束縛し、ScalarDB が拒否（DB-SQL-10016）。52 本中 33 本が失敗 | H2 は受け取る |
| 2 | `SELECT * INTO v_row`（`%ROWTYPE`）が**列 1 だけ**を読んでいた | 型が合えば黙って違う行を返す |
| 3 | package ローカル record 型への `SELECT INTO` が**一切代入されていなかった** | record が null のまま返る |
| 4 | `BULK COLLECT INTO` が 1 行の `SELECT INTO` として扱われ、2 行目以降を捨てていた | 元に無い例外も出す |
| 5 | `RTRIM('')` が `''` を返す（Oracle は NULL） | corpus では踏んでいない |
| 6 | `RTRIM(' ')` が `''` を返す（Oracle は NULL） | 同上 |
| 7 | `SYSDATE` が ScalarDB 側で固定されず、2 本が別の日付で比較されていた | `Plsql.setClock` はあったが未使用 |
| 8 | **`gradle test -D...` がテスト用 JVM に渡らず、P2-11 の差分比較 7 本が 1 件も実行されないまま BUILD SUCCESSFUL になっていた** | 緑のビルドが「何も走っていない」を意味しうる形だった |

8 番は報告の訂正でもある。Phase 2 完了時点で「P2-11 の 7 本が通っている」と報告したが、その時点では
走っていなかった。転送を明示したうえで再測定し、7 本が実際に通ることを確認した（結論は変わらない）。

変換器側でも 3 件直した。INSERT が対象表を記録しておらず `DATE` リテラルを TIMESTAMP 列へ日付だけで
書いていた件、予約列チェックがインライン `PRIMARY KEY` を見落としていた件、ScalarDB 予約列 `tx_id` を
検出できていなかった件である。

## 5. 決めたこと

### 金額はスケール済み整数（`BIGINT` × 10^scale）で持つ

根拠は 1 点、**Oracle の `NUMBER` が 10 進数であること**。実機で確認した:

```
Oracle : 0.1 + 0.2 = 0.3
double : 0.1 + 0.2 = 0.30000000000000004
```

比較で見つかった差（`money_rounded_total`: Oracle `1234.57` / double `1234.565`）はこの性質の 1 事例で
あって、丸め処理の詰めの問題ではない。DOUBLE を選ぶ限り、corpus に現れていない値でいつでも同じことが
起きる。

両規約を実際に走らせて比較した結果、**どのシナリオが走るかは規約で変わらず、値が違ったのはこの 1 本だけ**
だった。scaled は Oracle を再現し、double は再現しない。

### PoC の合否基準を先に固定し、判定者は後で決める

判定者は契約と体制の話なので open のままとし、基準（上の KPI 表）だけ計画 §9 に固定した。

### 再生成モデルは引き渡し時点で終了する

帰結として、(1) 生成物ヘッダの「編集するな」は引き渡し後は誤りになるので文言を変える必要がある（P4）、
(2) 引き渡し後に読むのは人間なので可読性とトレーサビリティが引き渡し時点で完結している必要がある、
(3) ルール改善が顧客コードへ還流しないので、**REVIEW を残したまま引き渡すとその負債は恒久的に顧客へ移る**。

## 6. 再現手順

```
# Oracle 側（P0-5）
python difftest/plsql_run.py deploy
python difftest/plsql_run.py run --out fixtures/plsql/golden

# ScalarDB 側（P3-1）。namespace は --print-schema-command が出す手順で作る
python difftest/plsql_capture.py --variant scaled
python difftest/plsql_capture.py --variant double

# 比較（P3-2）と KPI（P3-5）
python difftest/plsql_diff.py --full --json difftest/work/plsql-diff.json
python -m plsql.kpi --evidence difftest/work/plsql-diff.json --generated generated

# レビュー動線（P3-5）
python -m plsql.cli fixtures/plsql/src --out-dir out/plsql \
    --evidence difftest/work/plsql-diff.json --generated generated

# 意味論の再採取（Oracle のバージョンが変わったとき、P3-3）
python difftest/plsql_semantics.py
```

テスト: Python 1284 passed / 1 skipped、Java 全テスト通過（実 ScalarDB Cluster 含む）。

## 7. Phase 4 へ引き継ぐもの

- **KPI-6 の実測**。REVIEW を人が実際に消化してベースラインを出す。これが出るまで移行工数は見積もれない。
- **生成物ヘッダの引き渡し版**。再生成モデル終了の決定から出た未実装項目。
- **REVIEW 29 件の消化**。引き渡し後は還流しないので、引き渡し前に減らすことの価値が高い。
- **実案件 corpus**。現在の KPI は合成 corpus 上の値であり、実案件耐性の証拠ではない。
- 計画 §5 の Phase 4 項目（動的 SQL の部分評価、trigger / scheduler の設計テンプレート、LLM Remediator、
  承認された決定からのルール提案、Migration Workbench）。
