---
name: plsql-migrate
description: >-
  Oracle の PL/SQL（package / procedure / function / trigger）を ScalarDB 向けの Java（Service /
  Repository / domain）に変換する。変換は plsql.generate が決定的に行い、コンパイルと行数上限の
  決定漏れまで確かめる。そのうえで、生成器が決めてはならない「生成コードの外で決めること」
  （docs/plsql-migration/plsql-decisions-outside-generator.md の OPS / CALL / BIZ 項目）を生成物から拾い、
  利用者に確認して、決めた人と日付つきで記録する。移行で意味が変わるところ（トランザクションの
  分割、行ロックから楽観制御、trigger が掛かる経路、TIMESTAMPTZ、TRUNCATE など）が業務ロジックと
  整合するかを、routine ごとの具体的な問いにして確かめる。最後に、変換後のコードの文書（アーキテクチャ・
  仕様・使い方・制限・どのように移行したか）を Markdown にまとめ、生成物と突き合わせる。
  使うとき: 段階を指定して頼まれたとき——PL/SQL を Java に変換するだけ、生成コードの外で決めることや業務ロジックとの整合を確認・記録するだけ、変換後のコードを文書にするだけ——と、変換結果や limits.yaml の決定について聞かれたとき。対象外: 範囲を決めずに「PL/SQL を移行して」と頼まれたときや、仕様の調査から承認・テストまでの一連（migrate-flow。その中でこのスキルの手順を使う）、現行の仕様の調査だけ（plsql-spec）、SQL 文だけの方言変換（sql-transpile）、実 DB の結果の突き合わせだけ、性能測定。
when_to_use: >-
  "PL/SQL を変換して", "PL/SQL を Java にして", "この package を Java に変換だけして",
  "生成コードの外で決めること を確認", "OPS-1 を記録",
  "業務ロジックとの整合を確認", "変換後のコードを文書にして", "移行の経緯をまとめて", "convert PL/SQL to Java"。
  PL/SQL の変換結果や limits.yaml の決定について聞かれたときも使う。
  「パッケージを移行して」「stored procedure を ScalarDB に移行」のように範囲の決まらない依頼と、仕様の調査から
  承認・テストまでの一連の流れは migrate-flow スキルが受け持ち、その中でこのスキルの手順を使う。
  対象外: PL/SQL を含まない SQL 文だけの方言変換（sql-transpile スキル）、Oracle と ScalarDB の
  結果の突き合わせだけ（difftest/plsql_capture.py・plsql_compare.py）、性能測定。
# 利用者に確認して記録するスキルなので context: fork にはしない。業務ロジックとの整合は判断が要るため、
# モデルは固定しない（セッションのモデルで受ける）
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
  - Edit
  - Bash(.venv/bin/python skills/plsql-migrate/scripts/check_env.py)
  - Bash(java -version*)
  - Bash(runtime-java/gradlew --version*)
  - Bash(gradle --version*)
  - Bash(.venv/bin/python -m plsql.generate *)
  - Bash(.venv/bin/python -m plsql.cli *)
  - Bash(.venv/bin/python skills/plsql-migrate/scripts/decision_items.py *)
  - Bash(.venv/bin/python skills/plsql-migrate/scripts/migration_doc.py *)
  # プラグインとして入れたとき: <root>/bin/python と <root>/skills/... の形（先頭の * は公式の権限ルールで使える）
  - Bash(*/bin/python */skills/plsql-migrate/scripts/check_env.py)
  - Bash(*/runtime-java/gradlew --version*)
  - Bash(*/bin/python -m plsql.generate *)
  - Bash(*/bin/python -m plsql.cli *)
  - Bash(*/bin/python */skills/plsql-migrate/scripts/decision_items.py *)
  - Bash(*/bin/python */skills/plsql-migrate/scripts/migration_doc.py *)
---

# plsql-migrate — PL/SQL の変換と、生成コードの外で決めることの確認

PL/SQL を読んで ScalarDB 向けの Java を生成し、生成器が決めずに残した問いを利用者と片付ける。
このスキルの成果は 4 つある: **生成された Java**、**確認一覧（どの問いが出て、どれが決まったか）**、
**業務ロジックとの整合の確認結果**、**変換後のコードの文書**（アーキテクチャ・仕様・使い方・制限・
どのように移行したか）。

## Important

- **作業ディレクトリは sql-migration リポジトリのルート**。コマンドはここから `.venv/bin/python` で実行する
- **プラグインとして入れたとき（作業ディレクトリが sql-migration のチェックアウトでないとき）**: このスキルの場所は `${CLAUDE_SKILL_DIR}`（置き換わらない環境では、この SKILL.md のあるディレクトリ）で、その 2 つ上が `<root>`。下のコマンドは `.venv/bin/python` を `<root>/bin/python` に、`skills/…` で始まるスクリプトのパスを `<root>/` からのパスに読み替え、**利用者のプロジェクトを作業ディレクトリにしたまま**動かす（`-m plsql.cli` などはそのままでよい。`bin/python` が `<root>` を import の経路に入れる）。入力と `<out>` は利用者のプロジェクトの側に置き、`<root>` の中には書かない（プラグインの更新で消える）。初回は `bin/python` が仮想環境を作るので 1 分ほどかかる。**`runtime-java/`（Step 0 の `runtime-java/gradlew`）と `docs/`（`docs/plsql-migration/…` など、下で読めと書いた文書）で始まるパスも `<root>/` からのパスに読み替える**（どちらもプラグインに入っている）。`fixtures/…`・`samples/…`・`difftest/…` は sql-migration のリポジトリにだけあり、プラグインには含まれない。それを前提にした手順は、リポジトリで作業するときにだけ使う。`allowed-tools` は、リポジトリのルートから動かす形（`.venv/bin/python …`）とプラグインの形（`*/bin/python */skills/…`）の両方を持つ
- **migrate-flow で続ける予定なら**、生成の `--out-dir` を `out/migrate/<名前>/generated`、文書を `out/migrate/<名前>/docs` にしておくと写し直しが要らない。別の場所に作ったものは `flow.py adopt --generated … --docs …` で取り込める
- **Claude Code 以外（Codex など）で動かすとき**: `allowed-tools` と `when_to_use`（sql-transpile では `model` / `effort` も）は Claude Code 用で、ほかでは無視される。Read / Grep / Bash などの道具の名前は、その環境の同じ働きの道具に読み替える。AskUserQuestion が無ければ、同じ内容（推奨を先頭に、選択肢ごとの影響つき）を本文で聞き、答えを待つ
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
  （`rowLocks.optimistic`）、トランザクションの境界（`transactions` の perIteration / separate / callerBoundary）、動的 SQL の表名（`dynamicTables`）、routine の中の DDL を省くか（`ddl.omit`）、package 変数の置き場（`packageState.carried`）、
  CHECK / 外部キーを書く側で検査する表（`constraints.enforce`）、DB link の行き先（`dbLinks`）、`$IF` を解く移行元の
  PLSQL_CCFLAGS と版（`conditionalCompilation`）は生成器が読む。理由は値として書く（書き方は `references/operations.md` の
  「limits.yaml の書き方」）。書くのは利用者が答えてから。知らない節・空の理由・ソースに無い routine id は、生成器が
  理由つきの終了 2 で止める（→ Error Handling）。どのルール ID をどの決定で進めるかは `references/decisions-by-rule.md`
- 参照資料は必要になったときだけ読む:

  | 資料 | 読むとき |
  |---|---|
  | `docs/plsql-migration/plsql-decisions-outside-generator.md` | 項目を問うとき。**問う前に、その項目の節を必ず読む**（選択肢・推奨・代償が書いてある） |
  | `references/decisions-by-rule.md` | Step 3b で REVIEW / REDESIGN の routine をどう進めるかを聞くとき（ルール ID → 聞くこと・`limits.yaml` の節・答える人） |
  | `references/alignment.md` | Step 6 で業務ロジックとの整合を確かめるとき（項目ごとの観点、問いの例、答えの記録の形） |
  | `references/documenting.md` | Step 7 で変換後のコードの文書を書くとき。**書く前に必ず読む** |
  | `references/operations.md` | 生成物の構成、`limits.yaml` の書き方、記録ファイルの形を聞かれたとき・書く前。Oracle との突き合わせ（difftest）は、その最後の節（sql-migration のリポジトリで作業するときだけ） |
  | `examples/create_order/` | 書き上がった文書の実例が要るとき（sql-migration のリポジトリにだけある） |

## 判断を求めるときの形

利用者に判断を求めるときは、**どの問いでも**（行数の上限、OPS / CALL / BIZ 項目、REVIEW / REDESIGN の
直し方、Oracle との差を受け入れるか、入力が足りないまま進めるか）、次の 6 つを示してから聞く。
選択肢だけを並べて選ばせない。

| 示すこと | 中身 |
|---|---|
| 1. 何を決めるか | 出た根拠（routine 名・ファイル・行）つきで、業務の言葉で言う。項目の一般論にしない |
| 2. 推奨 | どの選択肢を勧めるか。**勧められないときは「推奨なし」と言い**、何が分かれば勧められるかを添える |
| 3. 理由 | なぜそれを勧めるか。根拠の出所を添える（文書の節、実 DB で確かめた結果、PL/SQL の原文の位置） |
| 4. 影響 | **選択肢ごとに**: 生成コードが変わるか（`limits.yaml` に書いて生成し直すか）、Oracle と振る舞いがどう変わるか、運用・呼び出し側に何が増えるか、性能や費用の代償、**あとから変えられるか** |
| 5. 決めないとどうなるか | 未決のまま何が止まるか（`--limits-strict` の失敗、`--handover` の拒否、REVIEW のまま残る routine）。止まらないなら、そう言う |
| 6. 誰が答える問いか | OPS = 運用担当、CALL = 呼び出し側の設計者、BIZ = 業務担当。利用者がその担当でなければ、決定にせず担当への問いにする |

- **推奨は根拠があるときだけ作る。** 文書（`docs/plsql-migration/plsql-decisions-outside-generator.md`）に推奨があれば
  それを使う。その案件の事情で自分の推奨が文書と違うときは、両方と、違う理由を示す
- **影響は推奨しない選択肢にも書く。** 推奨の代償（文書の「代償」の列）を省かない。利用者が推奨を
  退ける理由は、たいていそこにある
- **AskUserQuestion の前に、本文で 1〜5 を説明する。** 選択肢の `description` は短いので、そこには
  その選択肢の影響を 1 文で書き、理由は本文に置く。推奨は先頭に置き、ラベルの末尾に「（推奨）」を付ける
- 推奨を示しても、記録するのは利用者が選んでからである（Important の「決めたと書けるのは…」）

**例（行数の上限）**——routine 名と数値は形を示すためのもので、実際の問いでは生成物と原文から取る

> 決めること: `pkg_order.order_total`（`pkg_order.sql:42`）は 1 注文の明細を全部読んでから合計します。
> 1 回に読む行数の上限が要ります。
> 推奨: 上限 1,000 行。
> 理由: 明細は注文ごとに分かれていて、渡された DDL とテストデータでは最大 40 行です。業務上の上限を
> 超える入力は異常なので、止めるほうが安全です。
> 影響: 上限を置くと、超えた注文は例外で止まります（Oracle では止まりませんでした）。小さすぎると正当な
> 注文が止まり、大きすぎると異常を見逃してメモリを使います。「上限を置かない」も選べますが、理由を
> `scanRows.notLimited` に書くことになります。どれも `limits.yaml` の 1 行で、あとから変えられます
> （生成し直しが要ります）。
> 決めないと: `--limits-strict` が失敗し、生成が完了しません。
> 答える人: 業務担当（1 注文あたりの明細数を知っている人）。

## Workflow

### Step 0: 環境を確かめる

```bash
.venv/bin/python skills/plsql-migrate/scripts/check_env.py
java -version
runtime-java/gradlew --version
```

1 行に 1 コマンドで、パイプもリダイレクトも付けない（`allowed-tools` は単独のコマンドにしか合わないので、`| head -1` を付けると毎回許可を求められる）。出力が長くても、見るのはバージョンの行だけでよい。

プラグインとして入れたときは、1 行目を `<root>/bin/python <root>/skills/plsql-migrate/scripts/check_env.py`、3 行目を
`<root>/runtime-java/gradlew --version` に読み替える（作業ディレクトリは利用者のプロジェクトのまま）。

JVM が無いとき（Gradle は `runtime-java/gradlew` が取ってくる。ネットワークに出られず `gradle` も無いときも同じ）、または利用者が外すよう言ったときは `--verify-compile` を外し、「コンパイルは確かめていない」と報告で明記する。

### Step 1: 入力を確定する

| 入力 | 既定 | 無いとき |
|---|---|---|
| PL/SQL のディレクトリ | —（利用者のもの） | 利用者に聞く。**下のディレクトリも再帰して読む**。一部だけを変換したいなら、そのディレクトリを渡す |
| Oracle の DDL | `<src>/schema.sql` があれば自動 | `%TYPE` / `%ROWTYPE` が解けず精度が落ちる。持っていないか聞く |
| ScalarDB の Schema Loader JSON | `<src>/../scalardb-schema.json` があれば自動 | ScalarDB が受け付けない SQL を判定できない。聞く |
| routine ごとの決定（`limits.yaml`） | —（利用者の案件のもの） | **`--limits` を外して回し**、Step 3b で決まったことから作る。別の案件（corpus など）の `limits.yaml` を当てない |
| 移行元の `PLSQL_CCFLAGS` と Oracle の版 | — | ソースに `$IF` があるときだけ要る。書かないとフラグは NULL、版は 19.0 として解かれる。「判断を求めるときの形」で聞き、`conditionalCompilation` に書く |
| 出力先 | `out/plsql` | — |
| 記録ファイル | `limits.yaml` と同じディレクトリの `decisions-outside-generator.yaml` | 新しく作る |

sql-migration のリポジトリで corpus を試すときだけ、`<src>` = `fixtures/plsql/src`（`holdout/` `holdout2/` も含む）、
`limits.yaml` = `fixtures/plsql/limits.yaml` を使う。

### Step 2: 変換する

```bash
.venv/bin/python -m plsql.generate <src> \
  --scalardb-schema <scalardb-schema.json> --limits <limits.yaml> \
  --out-dir <out> --verify-compile --limits-strict
```

`limits.yaml` がまだ無ければ（Step 1）、`--limits <limits.yaml>` を外して回す。無いパスを渡すと終了 2 で止まる。

標準出力の 3 行と、その下の行、終了コードを読む。

```text
wrote 13 files to out/plsql/
routines: 8  rules: AUTO 2  REVIEW 3  REDESIGN 3  |  verdict: AUTO 0  REVIEW 5  REDESIGN 3
untranslated statements 1  SQL ScalarDB refuses 1  planned 0
```

- 2 行目は 2 組ある。**`rules:`** はルールだけの判定（`generation-report.json` の `ruleVerdict`）、**`verdict:`** は
  それに証拠（実 DB での比較）の確信度を掛けた最終の判定（同じく `verdict`）。生成器は証拠を読まないので、
  **`verdict:` の AUTO は必ず 0** で、ルールでは AUTO の routine は `verdict:` では REVIEW に数えられる（理由は
  `confidence factor testEvidence is 0`）。これは失敗ではなく「実 DB でまだ比べていない」という意味である
- 3 行目は、変換できなかった文・ScalarDB が受け付けない SQL・実行計画（取得して H2 で実行）に回した文の数

| 終了コード・出力 | 意味 | 次の手 |
|---|---|---|
| 0 | 生成・コンパイル・行数上限の決定、すべて済み。解析できなかったファイルも無い | Step 3 へ |
| 1 と `the rules ask for a row limit and nobody decided one: <routine>` | 行を先に全部読む routine の上限を、誰も決めていない | 利用者に聞く（→ 下の「行数の上限を聞く」）。`limits.yaml` に書いて生成し直す |
| 1 と `AUTO but not cleanly generated` | ルールと生成器が食い違っている。生成器の不具合 | 利用者に報告する。手で直さない |
| 1 と `compile check: N javac error(s)` | 生成物がコンパイルできない | routine 名と判定（`[REVIEW]` など）をそのまま報告する |
| 1 と `compile check did not run` | JVM / Gradle が無い | `--verify-compile` を外して回し直し、未確認と明記する |
| 2 と `limits.yaml の誤り: …` | `limits.yaml` が無い・読めない・知らない節やキー・空の理由・ソースに無い routine id など。**何も書いていない** | 最後の行を利用者に見せ、直す値を聞いてから直す（→ Error Handling）。2 は引数の誤り（argparse）でも出る |
| 3 と `source file not parsed (<コード>), …: <file>` | ほかは通ったが、**解析できなかったファイルがある**。`PARSE` は構文の誤りで、解析器が読めた部分だけから生成した（その routine は AUTO にしない）。`ORA_04085` は `:OLD` に代入する trigger で、変換していない（Oracle でも作れない元のソースの誤り） | 次の段へ進む前に、ファイル名と最初の誤りを利用者に見せ、ソースを直して回し直すか、そのファイルを除いて進むかを「判断を求めるときの形」で聞く。進むなら報告と文書の「制限」に必ず書く（`generation-report.json` の `unconvertedFiles` に残る） |

1 と 3 が重なると 1 になる。解析できなかったファイルの行はどちらでも出る（`--quiet` のときは標準エラーに）。

**行数の上限を聞く。** その routine が何を読むかを PL/SQL で確かめてから、「判断を求めるときの形」で
業務の数として問う
（例: 「`order_total` は 1 注文の明細を全部読みます。1 注文の明細は最大何行ありえますか。超えたら止めて
よい異常ですか」）。答えは 3 通りで、どれも決定である:
値を書く（`scanRows.routines`）/ 上限を置かないと決めて理由を書く（`scanRows.notLimited`）/
問い合わせ自身で件数を絞るよう直す。

### Step 3: 生成物の中身を報告する

`<out>/generation-report.json` を読んで伝える。

1. routine 数と判定の内訳を、**`ruleVerdict`（ルール）と `verdict`（最終）の 2 組で**。REVIEW は次の 2 つに分けて報告する:
   - **利用者に問うもの**: `ruleVerdict` が REVIEW / REDESIGN の routine。`verdicts.<routine>.reasons` の先頭のルール ID
     （`CALL-001: …`）ごとに理由を添える。Step 3b で聞く
   - **実 DB で比べていないだけのもの**: `ruleVerdict` が AUTO で、`verdict` が REVIEW、`reasons` が
     `confidence factor testEvidence is 0` だけの routine。「ルールでは AUTO、実 DB で比べていない」と 1 行にまとめる。
     決めることは無い
2. 変換できなかった文（`untranslatedStatements`）と、ScalarDB が受け付けない SQL（`unsupportedSql`）、
   実行計画に回した文（`plannedSql`）の件数。どの routine のどの文かは `refused`（routine → 文の id。routine ごと断ったものは
   routine の id そのもの）にある。生成物の `UnsupportedOperationException` を Grep すると、Service では直前のコメントに
   原文の位置（`<file>:<line>`）がある
3. 解析できなかったファイル（`unconvertedFiles`。Step 2 の終了 3）があれば、ファイル・理由・生成物にどこまであるか
4. 出力の場所（中身は `references/operations.md` の「生成物の構成」）

### Step 3b: routine ごとの決定を聞く

Step 3 で「利用者に問うもの」に分けた routine を、ルール ID ごとに進める。**Step 4 の確認一覧は、ここで決めたあとに
生成物に出るもの**（楽観制御へ移したあとの再試行の設計など）を拾うので、先にこちらを片付ける。

1. `references/decisions-by-rule.md` で、そのルール ID が **`limits.yaml` の決定で進めるもの**か、**直し方か受け入れるかを
   聞くもの**かを引く
2. 原文と生成物を開き、その routine で何が起きているかを業務の言葉で言い、「判断を求めるときの形」の 6 つを示して聞く
   （例: 「`pkg_shop.reserve`（`pkg_shop.pkb:12`）は `FOR UPDATE NOWAIT` で在庫行をロックしています。楽観制御へ移すと…」）
3. `limits.yaml` の決定なら、答えを理由つきで書いて Step 2 から生成し直す。直し方なら、元の PL/SQL を直すか差を受け入れるかの
   答えを報告に残す（手直しは利用者が求めたら次の依頼として受ける）
4. **決定が効いたかを確かめる。** 生成し直したら、その routine の `generation-report.json` の `diagnostics` に決定の印が
   出ているかを見る（`rowLocks.optimistic` → `OPTIMISTIC`、`transactions.perIteration` → Service の部品の method
   （`<routine>Start` / `Targets` / `One` …）、`scanRows` → `ROW_LIMIT_DECIDED`、`constraints.enforce` → `CONSTRAINT_GUARD`、`packageState.carried` →
   `STATE_CARRIED`、`dbLinks` → `DBLINK_MAPPED`）。`python -m plsql.cli … --limits …` の `redesign {'undecided': N, …}` の
   N が減っていることでも分かる。出ていなければ、書いた場所（節・routine id）を見直す

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
3. 「判断を求めるときの形」の 6 つ（何を決めるか・推奨・理由・影響・決めないとどうなるか・誰が答えるか）を
   本文で示してから、AskUserQuestion で聞く。推奨を先頭に置き、各選択肢の `description` にその選択肢の
   影響を書く。文書の「代償」の列は影響としてそのまま伝える（選択肢が文書に無い項目は、推奨と理由・影響を
   示したうえで自由回答で聞く）
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
- 生成物に効く答え（BIZ-7 の上限、BIZ-8 の表名）は、`limits.yaml` にも書いて Step 2 から回し直す。記録の `--where` には
  `limits.yaml` を書く。LOCK-* の routine を楽観制御へ移すかどうかは Step 3b の決定（`rowLocks.optimistic`）で、CALL-5 は
  そのあとに出る「弾かれたときの再試行」の問いである
- Step 2 で答えてもらった行数の上限は、ここで BIZ-7 の決定として `set` する（同じことを 2 回聞かない）
- 問いが一度に多いと答えが雑になる。1 回の AskUserQuestion は 4 問まで、同じ担当の項目をまとめる

### Step 6: 業務ロジックとの整合を確かめる

出た BIZ 項目それぞれについて、**routine ごとに**「移行で何が変わったか」を具体的に言い、業務がそれを
受け入れられるかを確かめる。項目ごとの観点・問いの例・記録の形は `references/alignment.md` にある。

1. PL/SQL の原文と生成された Java を並べて読み、その routine で**実際に変わる振る舞い**を 1〜2 文で書く
2. 利用者が業務文書（仕様書・業務ルール・運用手順）を渡していれば、その振る舞いに触れる記述を探す。
   見つかったら出典つきで**案**として記録する
3. **業務文書と食い違う**なら、それは確認ではなく再設計の要否の問題である。案にせず、食い違いとして記録し、
   報告する。食い違いが元の PL/SQL にすでにあるなら、そう書き分ける
4. 利用者に問う。BIZ 項目の選択肢は「受け入れる / 受け入れない（どう直すか）/ 担当に確認する」で、
   ここでも推奨と理由、選択肢ごとの影響を示す（受け入れたら何が変わったままになるか、受け入れないなら
   どの直し方があり、それぞれ `limits.yaml` の変更で済むのか再設計になるのか）。答えを記録する。
   **利用者が BIZ 項目の担当でない**（たとえば運用担当）なら、BIZ 項目は決定にしない。案と食い違いを記録し、
   業務担当への問いとして報告に並べる

### Step 7: 変換後のコードを文書にまとめる

生成された Java を呼び出す人と、移行を引き継ぐ人のための文書を `<out>/docs/` に作る。
`README.md` に **アーキテクチャ / 使い方 / 制限 / どのように移行したか**、module ごとの Markdown に routine ごとの
**仕様 / 移行で変わったこと / 制限と注意** が入る。事実の欄には、**何がどう変わったか**の表（意味が変わる /
形が変わるが結果は同じ / 未分類）と、Mermaid の図（全体の形、1 回の呼び出し、変換前の文 → 変換後の method）が出る。
「未分類」が出たら、その診断を読んで文章で説明し、`migration_doc.py` の `CHANGES` に足すよう報告する。

```bash
.venv/bin/python -m plsql.cli <src> --scalardb-schema <scalardb-schema.json> --limits <limits.yaml> --evidence <plsql-diff.json> --out-dir <out>/analysis --quiet
.venv/bin/python skills/plsql-migrate/scripts/migration_doc.py facts --src <src> --generated <out> --analysis <out>/analysis --limits <limits.yaml> --evidence <plsql-diff.json> --record <record.yaml> --out-dir <out>/docs
```

1. 1 行目は、生成（Step 2）と**同じ** `--limits` と `--scalardb-schema` で回す（`limits.yaml` が無ければ、生成と同じく外す）。
   文ごとの診断と判定ルールはここから来る。`plsql.cli` の終了コードは、0 = すべてのファイルを解析できた、
   **1 = 解析できなかったファイルがある**（Step 2 の終了 3 と同じもの。`--quiet` では理由が出ないので、
   `<out>/analysis/summary.md` の「parse できなかったファイル」を読む。文書の「制限」に書くので、止まらずに次へ進む）、
   2 = `limits.yaml` の誤りか引数の誤り（Step 2 と同じ直し方）。
   `--evidence`（実 DB の比較の結果。取り方は `references/operations.md` の最後の節で、sql-migration のリポジトリでだけ
   取れる）と `--record` は、無ければ外す。**比較が無いなら、文書の「制限」に「Oracle と同じ結果を返すかは
   確かめていない」と書くことになる**（`check` が見る）
2. `references/documenting.md` を読み、生成された Java（Service と Repository）と原文を開いて、`（未記入: …）` を
   置き換える。事実の欄（`<!-- facts:begin … -->` 〜 `<!-- facts:end … -->`）は書き換えない
3. routine ごとの節を先に書き、`README.md` の 4 章は最後に書く
4. 確かめる。0 で終わるまで直す:

```bash
.venv/bin/python skills/plsql-migrate/scripts/migration_doc.py check --src <src> --generated <out> --analysis <out>/analysis --limits <limits.yaml> --evidence <plsql-diff.json> --record <record.yaml> --out-dir <out>/docs
```

| 問題 | 対処 |
|---|---|
| 未記入が N か所 | 書く |
| 「制限」の文章に、変換していないファイル `<file>` が出てこない | 解析できなかったファイル（Step 2 の終了 3）を `README.md` の「制限」に、理由（構文の誤り / ORA-04085）と、生成物に無い・一部だけあることと一緒に書く |
| 文章に、判定の理由 `LOCK-001` / 受け入れた差のシナリオ / 決定 `rowLocks.optimistic` が出てこない | その routine の「移行で変わったこと」「制限と注意」に、呼び出し側が何をすればよいかを書く |
| 「制限」に AUTO でない routine・Oracle と違うシナリオ・実 DB で比べていないことが出てこない | `README.md` の「制限」に挙げる。落とすと、読んだ人が踏む |
| 「どのように移行したか」に決定 `<種類>` が出てこない | `limits.yaml` にある決定の種類（`optimistic`、`perIteration` など）を、誰がなぜ決めたかと一緒に書く |
| 文章が引く `X.java` は生成物に無い | 生成物を開いて名前を確かめる。推し量って書かない |
| 事実の欄が生成物と違う / 節が無い | 生成し直したか、決定・比較が変わった。`facts` を回し直し、その節の文章を読み直す |
| `<id>` は生成物に無い | 消えた routine の節が残っている。消すか、名前の変わった routine へ文章を移すかを利用者に確かめる |

生成し直したら（Step 5 の決定で `limits.yaml` が変わったときなど）、`facts` を回し直す。文章は残り、事実の欄だけが
書き直される。`check` が通るのは文章が事実から離れていないことまでで、コード例が動くことは確かめない。

### Step 8: 報告する

次の順でまとめる。

1. 変換の結果（Step 3。`ruleVerdict` と `verdict` の 2 組、問うべき REVIEW と比べていないだけの REVIEW の区別、
   解析できなかったファイル）と、Step 3b で決めたこと・直し方として残ったこと
2. 確認の状況: 出た項目数、決定 / 未決の数、今回決めた項目（ID・決定・決めた人）
3. **未決の項目と、誰に聞くか**（担当ごとに問いを並べる）
4. 業務ロジックとの食い違い（あれば。再設計の候補として）
5. ファイルの場所: 生成物、`decision-items.md`、記録ファイル、書き換えた `limits.yaml`、文書（`<out>/docs/README.md`）
6. 次の手: 未決が残っていれば担当への確認。Oracle との突き合わせをまだしていなければ、それが未確認であることを
   伝える（sql-migration のリポジトリで作業しているなら、`references/operations.md` の最後の節の difftest を案内する）

## Error Handling

| 状況 | 対処 |
|---|---|
| `ModuleNotFoundError` | リポジトリで作業しているなら、ルートで実行しているか確かめ、`.venv/bin/pip install -r requirements.txt`。プラグインのときは `<root>/bin/python` の仮想環境（`$CLAUDE_PLUGIN_DATA/venv`、無ければ `~/.cache/sql-migration/venv`）を消して `<root>/bin/python <root>/skills/plsql-migrate/scripts/check_env.py` で作り直す（消すのは利用者に確かめてから） |
| `limits.yaml の誤り: …`（`plsql.generate` / `plsql.cli` の終了 2） | 知らない節・キー（「〜のこと？」が付く）、空の理由、ソースに無い routine id、数でない・負の行数、`routines` と `notLimited` の重なり、YAML の構文の誤り、無いファイル。最後の行を利用者に見せ、「判断を求めるときの形」で直す値を聞いてから直す。打ち間違いは直してよいか確かめ、理由の中身は推し量って書かない。まだ決定が無いなら `--limits` を外す |
| `source file not parsed`（`plsql.generate` の終了 3、`plsql.cli` の終了 1） | 解析できなかったファイル。Step 2 の表のとおり、進むか直すかを聞き、進むなら報告と文書の「制限」に書く |
| `decision_items.py` が「§0.1 の行 … の見分け方が 0 個」 | 文書に行が足されたのにスクリプトが追いついていない。`DETECTORS` に見分け方を足す（黙って飛ばさない） |
| 「generation-report.json に diagnostics が無い」 | 古い生成器の出力。Step 2 から生成し直す |
| 記録の問題「決定だが 決めた人 が無い」 | 誰が決めたかを利用者に聞いて `set` で埋める。分からなければ `--status 未決` に戻す |
| `--limits-strict` の失敗が大量 | `--limits` を渡していない（`--limits was not given` が出る）。渡して回し直す |
| REDESIGN が多い | 失敗ではない。生成器が移行先で同じ保証を作れないと判断した routine で、理由が `reasons` にある。理由をそのまま伝え、Step 3b で `references/decisions-by-rule.md` を引き、`limits.yaml` の決定で進めるものと直し方を聞くものに分けて、「判断を求めるときの形」で利用者に問う（直し方ごとの影響と、直さないと何が残るかを示す） |
| `verdict:` の AUTO が 0 | 失敗ではない。生成器は実 DB の比較（証拠）を読まないので、ルールで AUTO の routine も最終の判定は REVIEW になる（Step 2）。報告では「ルールでは AUTO、実 DB で比べていない」とまとめる |

## Output

| ファイル | 内容 |
|---|---|
| `<out>/src/main/java/...` | 生成された Java（application / infrastructure / domain） |
| `<out>/db/*.sql` | 移行で足す表（照合の控え）と、直接の書き込みを禁じる権限の雛形 |
| `<out>/generation-report.json` | 判定（`verdicts` の `ruleVerdict` / `verdict` / `reasons`）、生成コードが断る文（`refused`）、例外コード（`errorCodes`）、routine ごとの診断コード（`diagnostics`）、解析できなかったファイル（`unconvertedFiles`、あるときだけ） |
| `<out>/decision-items.md` | 生成コードの外で決めることの確認一覧（出た根拠・状態・決定） |
| `<record.yaml>` | 記録（ID ごとに 状態 / 決定 / 決めた人 / 日付 / 記録先 / 案 / 出た） |
| `<out>/docs/README.md` | 変換後のコードの文書: アーキテクチャ / 使い方 / 制限 / どのように移行したか |
| `<out>/docs/<module>.md` | routine ごとの 事実（判定・引数の対応・例外・文の対応・決定・比較の結果）/ 仕様 / 移行で変わったこと / 制限と注意 |
