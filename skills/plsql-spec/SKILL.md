---
name: plsql-spec
description: >-
  既存の Oracle PL/SQL（package / procedure / function / trigger）を調べ、いま何をしているかを
  Markdown の仕様書にまとめる。処理の流れ・routine と表・呼び出しと trigger は Mermaid の図にする。引数・読み書きする表・SQL・エラーコード・例外ハンドラ・トランザクション
  制御・呼び出しの関係・発火する trigger は plsql.cli の IR から機械的に出し（事実の欄）、動作・業務ルール・
  エラー時の振る舞いは原文を読んで、原文の位置（ファイル:行）つきで書く。書き上がった仕様書は check で
  IR と突き合わせる（未記入、古い事実、文章に出てこないエラーコードと表、原文に無い位置の引用）。
  移行の前に「いまの仕様」を固めるための調査であり、変換はしない。
  使うとき: 既存の PL/SQL の仕様の調査、動作の文書化、移行前の現行の動作の整理だけを頼まれたとき
  （"document this PL/SQL", "reverse-engineer the spec of these packages", "PL/SQL の仕様書を作って"）。
  使い分け: 仕様の調査から承認・変換・テストまでの一連の移行（migrate-flow。その段階 1 でこのスキルの手順を使う）、
  Java への変換と移行で決めることの確認（plsql-migrate）、SQL 文だけの方言変換（sql-transpile）。
  対象外: 実 DB の結果の突き合わせ、性能測定。
when_to_use: >-
  "PL/SQL の仕様を調査して", "PL/SQL の動作をまとめて", "このパッケージが何をしているか文書にして",
  "stored procedure の仕様書を作って", "現行仕様を Markdown に", "移行前に現行の動作を整理",
  "document this PL/SQL", "reverse-engineer the spec of these packages"。
  対象外: 仕様の調査から承認・テストまでの移行（migrate-flow スキル。「移行を最初から最後まで」「承認をはさんで」）、
  Java への変換と、移行で決めることの確認（plsql-migrate スキル）、SQL 文だけの方言変換
  （sql-transpile スキル）、Oracle と ScalarDB の結果の突き合わせ（difftest）。
# 原文を読んで書く仕事で、分からないところは利用者に聞く。context: fork にはしない
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
  - Edit
  - Bash(.venv/bin/python -c *)
  - Bash(.venv/bin/python -m plsql.cli *)
  - Bash(.venv/bin/python skills/plsql-spec/scripts/spec_facts.py *)
  - Bash(*/bin/python -c *)
  - Bash(*/bin/python -m plsql.cli *)
  - Bash(*/bin/python */skills/plsql-spec/scripts/spec_facts.py *)
  - Bash(mmdc *)
---

# plsql-spec — 既存の PL/SQL の仕様を調べて Markdown にまとめる

PL/SQL を解析して事実を出し、原文を読んで動作を書き、書いたものを事実と突き合わせる。
成果は **仕様書（module ごとの Markdown と索引）** と、**原文からは決められなかったことの一覧**である。

## Important

- **作業ディレクトリは sql-migration リポジトリのルート**。コマンドはここから `.venv/bin/python` で実行する
- **プラグインとして入れたとき（作業ディレクトリが sql-migration のチェックアウトでないとき）**: このスキルの場所は `${CLAUDE_SKILL_DIR}`（置き換わらない環境では、この SKILL.md のあるディレクトリ）で、その 2 つ上が `<root>`。下のコマンドは `.venv/bin/python` を `<root>/bin/python` に、`skills/…` で始まるスクリプトのパスを `<root>/` からのパスに読み替え、**利用者のプロジェクトを作業ディレクトリにしたまま**動かす（`-m plsql.cli` などはそのままでよい。`bin/python` が `<root>` を import の経路に入れる）。入力と `<out>` は利用者のプロジェクトの側に置き、`<root>` の中には書かない（プラグインの更新で消える）。初回は `bin/python` が仮想環境を作るので 1 分ほどかかる。`fixtures/…`・`samples/…`・`difftest/…` は sql-migration のリポジトリにだけあり、プラグインには含まれない。それを前提にした手順は、リポジトリで作業するときにだけ使う
- **migrate-flow で続ける予定なら**、`<out>` を migrate-flow の置き場所（`out/migrate/<名前>`）にしておくと、仕様（`<out>/spec`）と解析（`<out>/spec-analysis` にするなら `--out-dir <out>/spec-analysis`）がそのまま使える。別の場所に作ったものは `flow.py adopt` で取り込める
- **Claude Code 以外（Codex など）で動かすとき**: `allowed-tools` と `when_to_use`（sql-transpile では `model` / `effort` も）は Claude Code 用で、ほかでは無視される。Read / Grep / Bash などの道具の名前は、その環境の同じ働きの道具に読み替える。AskUserQuestion が無ければ、同じ内容（推奨を先頭に、選択肢ごとの影響つき）を本文で聞き、答えを待つ
- **書くのは「いま何をしているか」であって、「どうあるべきか」でも「移行後にどうなるか」でもない。**
  おかしく見える処理（使われない引数、コメントと食い違う条件、握りつぶされる例外）も、そのまま仕様として
  書き、「確かめたいこと」に挙げる。直さない。移行先の都合（ScalarDB で動くか）は plsql-migrate の仕事である
- **事実の欄は手で書き換えない。** `<!-- facts:begin … -->` から `<!-- facts:end … -->` までは
  `spec_facts.py facts` が書く。間違って見えたら、原文と IR のどちらが正しいかを確かめ、IR の誤りなら
  利用者に報告する（解析器の不具合である）。文章のほうで黙って辻褄を合わせない
- **文章は原文を読んで書く。事実の欄を言い換えただけのものにしない。** 事実の欄に無いもの——処理の順序、
  計算式、条件の意味、既定値、エラーのときに何が残るか——を書くのが文章の役目である。
  文ごとに原文の位置（`` `ファイル:行` ``）を添える。位置を添えられない文は、原文に根拠が無い
- **原文から読み取れないことを、もっともらしく埋めない。** 業務上の意図、呼び出し元の事情、実データの
  件数は原文に書いていない。分からないことは「確かめたいこと」に問いとして書く
- **解析できなかったファイルと、読まなかったファイルは仕様書に入らない。** どちらも索引の先頭に出る。黙って進めず、利用者に伝える
- 参照資料は必要になったときだけ読む:

  | 資料 | 読むとき |
  |---|---|
  | `references/writing.md` | Step 3 で文章を書くとき。**書く前に必ず読む**（節ごとの書き方、図、Oracle の振る舞いの落とし穴） |
  | `examples/create_order/` | 書き上がった仕様書の実例が要るとき（sql-migration のリポジトリにだけある） |
  | `fixtures/plsql-external/README.md` | sql-migration のリポジトリで、利用者が貼った PL/SQL をどこに置くかを決めるとき（リポジトリにだけある） |

## 判断を求めるときの形

利用者に判断を求めるとき（DDL が無いまま進めるか、解析できないファイルをどうするか、調べる範囲を
絞るか、「確かめたいこと」のどれを誰に聞くか）は、選択肢だけを並べず、次を示してから聞く。

| 示すこと | 中身 |
|---|---|
| 1. 何を決めるか | 根拠（ファイル・routine・行）つきで具体的に言う |
| 2. 推奨 | どれを勧めるか。勧められないときは「推奨なし」と言い、何が分かれば勧められるかを添える |
| 3. 理由 | なぜそれを勧めるか |
| 4. 影響 | **選択肢ごとに**、仕様書の何が変わるか（精度が落ちる欄、入らなくなる routine、あとからやり直せるか） |
| 5. 決めないとどうなるか | 何が止まり、何が不確かなまま残るか |

AskUserQuestion を使うときは、先に本文で 1〜5 を説明し、推奨を先頭に置いてラベルの末尾に「（推奨）」を
付け、各選択肢の `description` にその選択肢の影響を書く。

**例（DDL が無い）**

> 決めること: `pkg_order.pkb` は `orders.order_id%TYPE` など表の列の型を 14 か所で借りていますが、
> 表の DDL がありません。
> 推奨: DDL（`CREATE TABLE` のスクリプトか、`DBMS_METADATA.GET_DDL` の出力）をもらってから進める。
> 理由: 引数と変数の型が解けないと、桁あふれや丸めの仕様が書けません。制約（一意・外部キー・NOT NULL）が
> 分からないと、どの INSERT がどの例外を上げうるかも書けません。
> 影響: DDL なしで進めると、型の欄は `%TYPE` のままになり、「エラーと例外」に制約違反を書けません。
> 手続きから DDL を推し量って進めることもできますが、推し量った型と制約は仕様書の中で事実と
> 区別がつかなくなりやすいので、`schema.inferred.sql` という名前で渡し、索引の事実の欄に「推し量った DDL」と出します。どちらも、あとから DDL が来たら解析からやり直せます
> （文章は残り、事実の欄だけが書き直されます）。
> 決めないと: 型と制約に頼る記述がすべて「確かめたいこと」に残ります。

## Workflow

### Step 0: 環境を確かめる

```bash
.venv/bin/python -c "import sqlglot, yaml; print('ok')"
```

### Step 1: 入力を確定する

| 入力 | 既定 | 無いとき |
|---|---|---|
| PL/SQL のディレクトリ | 利用者に聞く | 会話に貼られたものは、利用者のプロジェクトの側のディレクトリに置く。sql-migration のリポジトリで作業するときは `fixtures/plsql-external/<名前>/src/`（corpus には足さない） |
| Oracle の DDL | `<src>/schema.sql` があれば自動 | 「判断を求めるときの形」で聞く（上の例） |
| 条件付きコンパイル（原文に `$IF` があるとき）の本番の設定 | 未設定のフラグは NULL、DB の版は 19.0 と仮定する | 本番の `PLSQL_CCFLAGS` と DB の版を「判断を求めるときの形」で聞く（推奨: 聞いてから解析する。仮定のまま進めると、落ちた側の分岐は事実の欄にも図にも出ない）。答えは `conditionalCompilation` の節**だけ**の YAML（`<out>/ccflags.yaml`）に書き、Step 2 の `plsql.cli` に `--limits <out>/ccflags.yaml` を足す |
| 業務文書（仕様書・運用手順） | — | 無くてもよい。あれば Step 3 で用語を合わせ、食い違いを「確かめたいこと」に書く |
| 出力先 | `out/plsql-spec/<名前>/`（`analysis/` と `spec/`） | — |

すでに仕様書があるディレクトリを出力先にしてよい。事実の欄だけが書き直され、文章は残る。

### Step 2: 解析して、事実の欄を作る

```bash
.venv/bin/python -m plsql.cli <src> --out-dir <out>/analysis --quiet
.venv/bin/python skills/plsql-spec/scripts/spec_facts.py facts --analysis <out>/analysis --out-dir <out>/spec
```

`--schema <DDL>` は、DDL が `<src>/schema.sql` 以外にあるときに渡す。手続きから DDL を推し量ったときは、
`<src>` の外に `schema.inferred.sql` という名前で置いて `--schema` で渡す（名前に `inferred` があれば、索引に
「推し量った DDL」と出る）。ScalarDB の schema と、移行の決定を書いた `limits.yaml` は渡さない（移行先の話であって、
現行の仕様ではない。行数上限などが IR に織り込まれる）。渡してよいのは、`conditionalCompilation` の節だけの YAML
（Step 1）である。

`plsql.cli` の終了コード: 0 = すべて解析できた、1 = 解析できなかったファイルがある（コマンドの失敗ではない。下の表の
`failedFiles` を見る）、2 = 解析するものが無い（ディレクトリが無い、PL/SQL のファイルが 1 つも無い。標準エラーに理由）。
読む拡張子は `.pks` `.spc`（仕様）、`.pkb` `.bdy` `.pck` `.plb` `.prc` `.fnc` `.trg` `.pls`、PL/SQL を作る `.sql`。
object type（`.tps` `.tpb` `.typ`）と、知らない拡張子で `CREATE PACKAGE` などを含むファイルは読まず、標準エラーの
`warning: not analysed:` と `inventory.json` の `kpi.skippedFiles` に理由つきで出る。

`spec_facts.py facts` の標準エラーの最終行 `MODULES= ROUTINES= STALE= FAILED= SKIPPED=` を読む。

| 出力 | 意味 | 次の手 |
|---|---|---|
| `STALE=0 FAILED=0 SKIPPED=0` | — | `<src>` にあるファイルの数と `inventory.json` の `kpi.totalFiles` が合うかを見て、Step 3 へ |
| `SKIPPED=` が 0 でない（`kpi.skippedFiles`） | 読まなかったファイルがある。その routine は仕様書に入らない | ファイル名と理由を利用者に伝える。拡張子の付け違いなら、名前を変えてよいかを聞く（原文は利用者のもの） |
| `IR に無くなった節が残っている: <file>: <id>` | 原文から消えた（か、名前が変わった）routine の節が仕様書に残っている | 消すか、名前の変わった routine へ文章を移すかを利用者に確かめる。自分で消さない |
| `FAILED=` が 0 でない（`<out>/analysis/inventory.json` の `kpi.failedFiles`） | 解析できなかったファイルがある | ファイル名を利用者に伝える。その routine は仕様書に入らない。原文を直せるか（SQL*Plus の指示の混入、別言語の混在）を一緒に見る |
| `kpi.typeResolutionRate` が 1 未満 | `%TYPE` / `%ROWTYPE` が解けていない | DDL が足りない。Step 1 に戻って聞く |

### Step 3: 原文を読んで、文章を書く

`references/writing.md` を読んでから始める。`<out>/spec/README.md`（索引）で全体をつかみ、module ごとに:

1. 原文（body と、あれば spec）を**全部**読む。事実の欄だけを見て書かない
2. 呼ばれる側から書く（索引の「呼び出しの関係」の右側から）。呼ぶ側を書くときに、呼び先の動作を引ける
3. routine ごとに `（未記入: …）` を置き換える: **動作 → 業務ルール → エラーと例外 → 確かめたいこと**。
   事実の欄の**処理の流れの図**（Mermaid）を見ながら原文を読むと、分岐と例外の道を落としにくい
4. trigger は、発火させる側の routine の「動作」にも 1 行書く（`references/writing.md` の「動作」）
5. 最後に module の「概要」と、索引の「全体の概要」を書く。個々の routine を読み終えてからでないと書けない
6. 状態の遷移や、複数の routine にまたがる業務の流れは、文章の側に Mermaid で描く（`references/writing.md` の「図」）。
   描いた図は `mmdc -i <file.md> -o <scratch>/check.md -q` で確かめる。`mmdc` が無ければ、確かめていないと報告する

module が多いとき（目安 10 以上）は、Agent で手分けしてよい。決まりは 4 つ:

- 分ける単位は、**呼び出しでも trigger の発火でもつながっていない** module の組（索引の「呼び出しと trigger」の図で
  線がつながらないもの）。trigger の結果は発火させる側の routine の「動作」にも書くので、trigger と書く側は同じ Agent にする
- 各 Agent には `references/writing.md` を読ませ（無ければ、この SKILL.md の Step 3 と Step 4 の表で書かせる）、
  **担当の Markdown だけを書かせる。`facts` は回させない**（すべての頁を書き直すので、ほかの Agent が書いている頁を
  上書きしうる）。`check` は回してよいが、自分の頁の問題だけを直させる
- 事実の欄を作り直す（`facts`）のは、全部が戻ってから自分で 1 回
- 索引の「全体の概要」は、全部が戻ってから自分で書く

Agent が無い環境（Codex など）では、呼ばれる側の module から順に 1 つずつ書く。

### Step 4: 確かめる

```bash
.venv/bin/python skills/plsql-spec/scripts/spec_facts.py check --analysis <out>/analysis --out-dir <out>/spec --src <src>
```

`--src` を渡すと、引用 `ファイル:行` を原文の行数で確かめる（仕様の `.pks` の引用も）。
標準エラーの最終行 `ROUTINES= UNWRITTEN= PROBLEMS=` を読む。0 で終わるまで直す。

| 問題 | 対処 |
|---|---|
| 未記入が N か所（`(未記入` も数える） | 書く。書けないなら、なぜ書けないかを「確かめたいこと」に書いたうえで、その節に分かる範囲を書く |
| 「業務ルール」などの節が無い / 空 | routine の節には 4 つの見出し（`### 動作` `### 業務ルール` `### エラーと例外` `### 確かめたいこと`）を残す。書くことが無ければ「なし」と書く |
| 文章にエラーコード -20xxx / 上げる例外が出てこない | 「エラーと例外」に、いつ起きるかと、そのときの書き込みの扱いを書く |
| 文章に、書き込む表が出てこない | 「動作」に、その表へ何を書くかを書く（表の名前は単語として探す。`shelf_log` は `shelf` に数えない） |
| 文章に原文の位置が 1 つも無い / 引用が routine の外 / 原文の範囲の外 / 原文に無いファイル | 原文を開いて位置を確かめる。**行番号を推し量って書かない** |
| 事実の欄が IR と違う | 原文が変わったか、欄を手で書き換えた。`facts` を回し直し、その routine の文章を原文と読み合わせる |

`check` が確かめるのは、文章が事実から**離れていない**ことまでである。文章が正しいことは確かめない
（「-20102 は起きない」と書いても、コードが出てくれば通る。範囲の中の別の文の行を引いても通る）。
通ったあとで、routine を 2〜3 本選んで原文と読み合わせ、順序と条件の向き（`<` と `<=`）が合っているかを見る。

### Step 5: 報告する

1. 調べた範囲: module 数、routine 数、解析できなかったファイル、DDL の有無（推し量ったなら、そう言う）
2. 全体の概要（索引に書いたものを 3〜5 文で）
3. **確かめたいこと**を、誰に聞くかで分けて並べる（業務担当 / 呼び出し側の開発者 / DBA）。
   全 module の「確かめたいこと」から集める。「なし」ばかりなら、読み方が浅くないかを疑う
4. 目についた危ういところ（握りつぶされる例外 `WHEN OTHERS THEN NULL`、routine の中の COMMIT、
   待ち時間の上限が無いロック、動的 SQL に入る引数）。評価はせず、場所と、何が起きるかだけを言う
5. ファイルの場所: `<out>/spec/README.md` と module ごとの Markdown
6. 次の手: 移行するなら migrate-flow（この仕様書の承認 → 変換 → 変換後の仕様 → テスト）。変換だけなら plsql-migrate。BIZ 項目（業務ロジックとの整合）を確かめるときに、この仕様書が
   「業務文書」の代わりにはならないことを添える——これは原文の写しであって、業務の意図ではない

## Error Handling

| 状況 | 対処 |
|---|---|
| `ModuleNotFoundError` | リポジトリルートで実行しているか確かめ、`.venv/bin/pip install -r requirements.txt`。プラグインとして入れたときは `<root>/bin/python` で動かしているかを確かめる（仮想環境は `bin/python` が作り直す） |
| `入力が読めない: … program.ir.json が無い` | Step 2 の 1 行目（`plsql.cli`）を先に回す |
| 事実の欄に、原文に無い SQL やエラーがある | lowering が足した文が混ざっている（`spec_facts.py` の `FROM_SOURCE` が見分ける）。利用者に報告する。文章で取り繕わない |
| 事実の欄に、原文にある文が無い | 解析器が読み飛ばした。`<out>/analysis/diagnostics.sarif` にその位置の診断が無いか見て、報告する。文章には原文のとおり書き、「確かめたいこと」ではなく報告に挙げる |
| 事実の欄の先頭に「**解析できなかった部分**」がある（`diagnostics.sarif` の `UNSUPPORTED_CONSTRUCT`） | その範囲の SQL・呼び出し・エラーは事実の欄と図に無い（例: package の routine と同じ名前の入れ子の手続き）。事実の欄は不完全なので、その範囲は原文を読んで文章に書く。欄に出た「原文にあるエラーコード」は文章に書く（`check` が見る）。利用者には解析器の限界として報告する |
| 原文が wrap されている（`CREATE … WRAPPED`） | 読めない。元のソースを求める |

## Output

| ファイル | 内容 |
|---|---|
| `<out>/analysis/` | `plsql.cli` の出力（`program.ir.json`、`inventory.json`、`diagnostics.sarif` など） |
| `<out>/spec/README.md` | 索引: module の一覧、表と routine の対応、エラーコードの一覧、トランザクションを制御する routine、呼び出しの関係、全体の概要 |
| `<out>/spec/<module>.md` | module ごとの仕様: 概要と、routine ごとの 事実 / 動作 / 業務ルール / エラーと例外 / 確かめたいこと |
