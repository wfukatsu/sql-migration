---
name: plsql-migrate
description: >-
  Oracle の PL/SQL（package / procedure / function / trigger）を ScalarDB 向けの Java（Service /
  Repository / domain）に変換する。変換は plsql.generate が決定的に行い、コンパイルと行数上限の
  決定漏れまで確かめる。そのうえで、生成器が決めてはならない「生成コードの外で決めること」
  （docs/plsql-decisions-outside-generator.md の OPS / CALL / BIZ 項目）を生成物から拾い、
  利用者に確認して、決めた人と日付つきで記録する。移行で意味が変わるところ（トランザクションの
  分割、行ロックから楽観制御、trigger が掛かる経路、TIMESTAMPTZ、TRUNCATE など）が業務ロジックと
  整合するかを、routine ごとの具体的な問いにして確かめる。
when_to_use: >-
  "PL/SQL を変換して", "PL/SQL を Java にして", "stored procedure を ScalarDB に移行",
  "パッケージを移行", "trigger を移行", "生成コードの外で決めること を確認", "OPS-1 を記録",
  "業務ロジックとの整合を確認", "convert PL/SQL", "migrate Oracle packages to ScalarDB"。
  PL/SQL の変換結果や limits.yaml の決定について聞かれたときも使う。
  対象外: PL/SQL を含まない SQL 文だけの方言変換（sql-transpile スキル）、Oracle と ScalarDB の
  結果の突き合わせだけ（difftest/plsql_capture.py・plsql_compare.py）、性能測定。
# 利用者に確認して記録するスキルなので context: fork にはしない。業務ロジックとの整合は判断が要るため、
# モデルは固定しない（セッションのモデルで受ける）
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(.venv/bin/python -c *)
  - Bash(java -version*)
  - Bash(gradle --version*)
  - Bash(.venv/bin/python -m plsql.generate *)
  - Bash(.venv/bin/python -m plsql.cli *)
  - Bash(.venv/bin/python skills/plsql-migrate/scripts/decision_items.py *)
---

# plsql-migrate — PL/SQL の変換と、生成コードの外で決めることの確認

PL/SQL を読んで ScalarDB 向けの Java を生成し、生成器が決めずに残した問いを利用者と片付ける。
このスキルの成果は 3 つある: **生成された Java**、**確認一覧（どの問いが出て、どれが決まったか）**、
**業務ロジックとの整合の確認結果**。

## Important

- **作業ディレクトリは sql-migration リポジトリのルート**。コマンドはここから `.venv/bin/python` で実行する
- **変換は `plsql.generate` に任せ、Java を自分で書き起こさない。** 生成器は決めてよいことだけを決め、
  決められないところは REVIEW / REDESIGN と生成物の中の印で残す。手で埋めると、その判断が記録の外に出る。
  REVIEW / REDESIGN の routine の手直しは、利用者が求めたら次の依頼として受ける
- **決めたと書けるのは、決めた人がいるときだけ。** 記録の「決定」には、利用者が示した答えと、
  **決めた人（役割）と日付**が要る。スクリプトはそれが無い「決定」を拒否する。
  - あなたの推奨は推奨として示す。記録するのは利用者が選んでからにする
  - 利用者が「推奨で」と言ったら、それは利用者の決定である。決めた人はその利用者の役割で記録する
  - 業務文書から読み取った答えは「案」（出典つき）であって決定ではない
- **比較ハーネスの「一致」は業務ロジックとの整合の証明ではない。** BIZ 項目で変わるのは、シナリオが
  観ていない部分（途中で止まったとき、同時に書いたとき、PL/SQL の外から書いたとき）である
- **routine ごとの決定は `limits.yaml` に書き、生成し直す。** 行数の上限（`scanRows`）、楽観制御へ移す routine
  （`rowLocks.optimistic`）、トランザクションの分割（`transactions`）、動的 SQL の表名（`dynamicTables`）は
  生成器が読む。値には理由をコメントで添える（既存の書き方に合わせる）。書くのは利用者が答えてから
- 参照資料は必要になったときだけ読む:

  | 資料 | 読むとき |
  |---|---|
  | `docs/plsql-decisions-outside-generator.md` | 項目を問うとき。**問う前に、その項目の節を必ず読む**（選択肢・推奨・代償が書いてある） |
  | `references/alignment.md` | Step 6 で業務ロジックとの整合を確かめるとき |
  | `references/operations.md` | Oracle との突き合わせ（difftest）、生成物の構成、記録ファイルの形を聞かれたとき |
  | `fixtures/plsql/limits.yaml` | routine ごとの決定の書き方の実例が要るとき |

## Workflow

### Step 0: 環境を確かめる

```bash
.venv/bin/python -c "import sqlglot, yaml; print('ok')"
java -version 2>&1 | head -1; gradle --version 2>/dev/null | grep '^Gradle'
```

JVM と Gradle が無いとき、または利用者が外すよう言ったときは `--verify-compile` を外し、「コンパイルは確かめていない」と報告で明記する。

### Step 1: 入力を確定する

| 入力 | 既定（corpus） | 無いとき |
|---|---|---|
| PL/SQL のディレクトリ | `fixtures/plsql/src` | 利用者に聞く。**下のディレクトリも再帰して読む**（corpus なら `holdout/` `holdout2/` も含む）。一部だけを変換したいなら、そのディレクトリを渡す |
| Oracle の DDL | `<src>/schema.sql` があれば自動 | `%TYPE` / `%ROWTYPE` が解けず精度が落ちる。持っていないか聞く |
| ScalarDB の Schema Loader JSON | `<src>/../scalardb-schema.json` | ScalarDB が受け付けない SQL を判定できない。聞く |
| routine ごとの決定（`limits.yaml`） | `fixtures/plsql/limits.yaml` | 無しで回し、Step 3 で出た決定から作る |
| 出力先 | `out/plsql` | — |
| 記録ファイル | `limits.yaml` と同じディレクトリの `decisions-outside-generator.yaml` | 新しく作る |

### Step 2: 変換する

```bash
.venv/bin/python -m plsql.generate <src> \
  --scalardb-schema <scalardb-schema.json> --limits <limits.yaml> \
  --out-dir <out> --verify-compile --limits-strict
```

標準出力の 3 行（`wrote N files` / `routines: … AUTO … REVIEW … REDESIGN …` /
`untranslated statements … SQL ScalarDB refuses … planned …`）と終了コードを読む。

| 終了コード・出力 | 意味 | 次の手 |
|---|---|---|
| 0 | 生成・コンパイル・行数上限の決定、すべて済み | Step 3 へ |
| `the rules ask for a row limit and nobody decided one: <routine>` | 行を先に全部読む routine の上限を、誰も決めていない | 利用者に聞く（→ 下の「行数の上限を聞く」）。`limits.yaml` に書いて生成し直す |
| `AUTO but not cleanly generated` | ルールと生成器が食い違っている。生成器の不具合 | 利用者に報告する。手で直さない |
| `compile check: N javac error(s)` | 生成物がコンパイルできない | routine 名と判定（`[REVIEW]` など）をそのまま報告する |
| `compile check did not run` | JVM / Gradle が無い | `--verify-compile` を外して回し直し、未確認と明記する |

**行数の上限を聞く。** その routine が何を読むかを PL/SQL で確かめてから、業務の数として問う
（例: 「`order_total` は 1 注文の明細を全部読みます。1 注文の明細は最大何行ありえますか。超えたら止めて
よい異常ですか」）。答えは 3 通りで、どれも決定である:
値を書く（`scanRows.routines`）/ 上限を置かないと決めて理由を書く（`scanRows.notLimited`）/
問い合わせ自身で件数を絞るよう直す。

### Step 3: 生成物の中身を報告する

`<out>/generation-report.json` を読んで伝える。

1. routine 数と判定の内訳（AUTO / REVIEW / REDESIGN）。REDESIGN は `verdicts.<routine>.reasons` の理由を添える
2. 変換できなかった文（`untranslatedStatements`）と、ScalarDB が受け付けない SQL（`unsupportedSql`）、
   実行計画に回した文（`plannedSql`）の件数。report には件数しか無い。どの文かは生成物の
   `UnsupportedOperationException`（変換できなかった文）を Grep で探し、直前のコメントの原文の位置
   （`<file>:<line>`）を添える
3. 出力の場所: `src/main/java/.../application`（Service）、`infrastructure`（Repository）、`domain`、
   `db/*.sql`（移行で足す表・権限の雛形）、`src/main/resources/plans/`

### Step 4: 生成コードの外で決めることを拾う

```bash
.venv/bin/python skills/plsql-migrate/scripts/decision_items.py scan \
  --generated <out> --limits <limits.yaml> --scalardb-schema <scalardb-schema.json> \
  --record <record.yaml> --write --out <out>/decision-items.md
```

生成物から「出た」項目を拾い、記録に**未決**として足す（決定済みには触れない。出なくなった未決は対象外にする）。
標準エラーの最終行 `FIRED= UNDECIDED= PROBLEMS=` を読む。`PROBLEMS` が 0 でなければ記録が壊れている
（決めた人の無い「決定」など）。直すのは利用者に確かめてから。

### Step 5: 項目を確認して記録する

`<out>/decision-items.md` を利用者に見せ、**誰が答える項目か**を最初に伝える
（OPS = 運用担当、CALL = 呼び出し側の設計者、BIZ = 業務担当）。利用者が答えられない項目は未決のまま残し、
答える人に渡す問いの一覧にする。

答えてもらう項目は、1 項目ずつ次の順で進める:

1. 文書のその項目の節を読む
2. **出た根拠（routine 名・ファイル）で問いを具体的にする。** 「OPS-1 照合をどこで回すか」ではなく
   「`TriggerCheckJob.daily`（監査 2 本と採番 1 本の照合）と `hourly`（支払いと商品価格の検証）を
   どこから回しますか」と聞く
3. 選択肢と推奨を示す。AskUserQuestion を使い、推奨を先頭に置く（選択肢が文書に無い項目は自由回答で聞く）
4. 答えと、**決めた人（役割）** を記録する。役割が分からなければ聞く

```bash
.venv/bin/python skills/plsql-migrate/scripts/decision_items.py set <ID> --record <record.yaml> \
  --status 決定 --decision "<選んだ選択肢と理由 1〜2 行>" --by <役割> --date <YYYY-MM-DD> --where <記録先>
```

- 記録したら `scan … --write --out <out>/decision-items.md` を回し直して一覧を新しくする（`set` は一覧を書き換えない）
- **決定を書き換えるときは `--by` と `--date` を必ず付ける**（無いと終了コード 1）。前の決定は `履歴` に残る。
  `未決` / `対象外` に戻したときも、前の決定は `履歴` へ移る
- `scan` に `--limits` と `--scalardb-schema` を渡さないと、BIZ-7・8・11 のように**そのファイルから見分ける項目は
  「分からない」扱い**になり、記録はそのまま残る（標準エラーに注意が出る）。`--write` するときは両方渡す
- 決定には、決めたときの問い（`決定時の題`）と根拠（`決定時の根拠`）が一緒に残る。あとで文書の問いか、
  生成物から出る根拠が変わると、`scan` が **「要再確認」** を記録の問題として挙げ、終了コード 1 を返す。
  決定がまだ当てはまるかを利用者に確かめ、当てはまるなら同じ内容で `set` し直す（`--by` と `--date` が要る）
- 記録のファイルに手で書いたコメントは保存されない。残したいことは `--note` で `メモ` に書く
- 一部だけ決まった項目（例: 時刻は決めたが、間隔に収まるかの測定が残る）は、決まった部分を `--decision` に、
  残りを `--remaining` に書く
- 生成物に効く答え（BIZ-7 の上限、BIZ-8 の表名、CALL-5 の楽観制御の対象）は、`limits.yaml` にも書いて
  Step 2 から回し直す。記録の `--where` には `limits.yaml` を書く
- 問いが一度に多いと答えが雑になる。1 回の AskUserQuestion は 4 問まで、同じ担当の項目をまとめる

### Step 6: 業務ロジックとの整合を確かめる

出た BIZ 項目それぞれについて、**routine ごとに**「移行で何が変わったか」を具体的に言い、業務がそれを
受け入れられるかを確かめる。手順と観点は `references/alignment.md` にある。要点:

1. PL/SQL の原文と生成された Java を並べて読み、その routine で**実際に変わる振る舞い**を 1〜2 文で書く
   （例: 「`prc_nightly_close` は 1 注文ずつ確定する。途中で止まると、確定した注文と未処理の注文が混ざる」）
2. 利用者が業務文書（仕様書・業務ルール・運用手順）を渡していれば、その振る舞いに触れる記述を探す。
   見つかったら出典つきで**案**として記録する（`--status 未決 --proposal "<案>（出典: <文書> <節>）"`）
3. **業務文書と食い違う**（例: 仕様に「締めは全件一括で確定する」とある）なら、それは確認ではなく
   再設計の要否の問題である。案にせず、`--conflict "<食い違い>（出典: <文書> <節>）"` で記録し、報告する。
   食い違いが**元の PL/SQL にすでにある**（移行前から仕様に合っていない）なら、そう書き分ける——移行で
   生じた差ではないので、直すかどうかは移行とは別の判断になる
4. 利用者に問い、答えを Step 5 と同じ形で記録する。**利用者が BIZ 項目の担当でない**（たとえば運用担当）なら、
   BIZ 項目は決定にしない。案と食い違いを記録し、業務担当への問いとして報告に並べる

### Step 7: 報告する

次の順でまとめる。

1. 変換の結果（Step 3）
2. 確認の状況: 出た項目数、決定 / 未決の数、今回決めた項目（ID・決定・決めた人）
3. **未決の項目と、誰に聞くか**（担当ごとに問いを並べる）
4. 業務ロジックとの食い違い（あれば。再設計の候補として）
5. ファイルの場所: 生成物、`decision-items.md`、記録ファイル、書き換えた `limits.yaml`
6. 次の手: 未決が残っていれば担当への確認。Oracle との突き合わせをまだしていなければ
   `references/operations.md` の difftest を案内する

## Error Handling

| 状況 | 対処 |
|---|---|
| `ModuleNotFoundError` | リポジトリルートで実行しているか確かめ、`.venv/bin/pip install -r requirements.txt` |
| `decision_items.py` が「§0.1 の行 … の見分け方が 0 個」 | 文書に行が足されたのにスクリプトが追いついていない。`DETECTORS` に見分け方を足す（黙って飛ばさない） |
| 「generation-report.json に diagnostics が無い」 | 古い生成器の出力。Step 2 から生成し直す |
| 記録の問題「決定だが 決めた人 が無い」 | 誰が決めたかを利用者に聞いて `set` で埋める。分からなければ `--status 未決` に戻す |
| `--limits-strict` の失敗が大量 | `--limits` を渡していない（`--limits was not given` が出る）。渡して回し直す |
| REDESIGN が多い | 失敗ではない。生成器が移行先で同じ保証を作れないと判断した routine で、理由が `reasons` にある。理由をそのまま伝え、手直しの要否は利用者に委ねる |

## Output

| ファイル | 内容 |
|---|---|
| `<out>/src/main/java/...` | 生成された Java（application / infrastructure / domain） |
| `<out>/db/*.sql` | 移行で足す表（照合の控え）と、直接の書き込みを禁じる権限の雛形 |
| `<out>/generation-report.json` | 判定（`verdicts`）、例外コード（`errorCodes`）、routine ごとの診断コード（`diagnostics`） |
| `<out>/decision-items.md` | 生成コードの外で決めることの確認一覧（出た根拠・状態・決定） |
| `<record.yaml>` | 記録（ID ごとに 状態 / 決定 / 決めた人 / 日付 / 記録先 / 案 / 出た） |
