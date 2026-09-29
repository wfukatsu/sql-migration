# REVIEW / REDESIGN のルール ID から、決めることへ

`generation-report.json` の `verdicts.<routine>.reasons` の先頭にあるルール ID（`LOCK-001: …`）から、
**利用者に何を聞き、答えをどこに書くか**を引く表。SKILL.md の「Step 3b: routine ごとの決定を聞く」で使う。

「決定」の列は `docs/guide/conversion-catalog.md` の「プロジェクトの決定（limits.yaml）と生成物」
「ルールが REVIEW / REDESIGN にするもの（ルール ID ごと）」と同じ内容である。食い違いに気づいたら catalog を正とし、
この表を直すよう報告する。

## 読み方

- **limits.yaml の決定**の行は、答えを `limits.yaml` に書いて Step 2 から生成し直すと、生成物が変わる
  （断っていたところが動くコードになる）。多くは判定が REDESIGN のまま「決定済み」として数えられる
  （`plsql.cli` の `redesign {'undecided': N, 'decided': M, …}`）。判定の文字が変わらないのは誤りではない
- **直し方**の行は、`limits.yaml` では外れない。元の PL/SQL か呼び出し側の設計を変えるか、差を受け入れるかを聞く。
  REVIEW / REDESIGN の routine の手直しは、利用者が求めたら次の依頼として受ける（Important）
- どの行も、聞くときは SKILL.md の「判断を求めるときの形」の 6 つを示す。書くのは利用者が答えてから
- 決める人: 運用 = 運用担当、呼び出し = 呼び出し側の設計者、業務 = 業務担当

## limits.yaml の決定で進めるもの

| ルール ID | 何が起きているか | 聞くこと | 答えを書く所（`limits.yaml`） | 決める人 |
|---|---|---|---|---|
| LOCK-001 / LOCK-002 | 行ロック（`FOR UPDATE`、`NOWAIT`、`SKIP LOCKED`）。ScalarDB に行ロックは無い | この routine を楽観制御（同時の書き込みは commit で弾かれ、呼び出し側が再試行する）へ移してよいか。再試行してよい操作か（冪等か）、業務例外と衝突を呼び出し側が区別できるか | `rowLocks.optimistic.<routine>: <理由>` | 呼び出し |
| SQL-001（`SET c = c + x` の RMW） / SEM-006（割っていない MERGE） | 列を読む SET 式・MERGE は ScalarDB SQL に渡せない | 同じトランザクションの中で読んでから書く 2 文に割ってよいか（衝突は commit で弾かれる） | `rowLocks.optimistic.<routine>` | 呼び出し |
| TX-001 | routine の中の COMMIT / ROLLBACK / SAVEPOINT | 境界をどこに引くか: 1 反復 = 1 トランザクション（perIteration）/ 呼び出し側の境界へ移す（callerBoundary。途中の ROLLBACK が戻していた分は残る）/ 自律（separate） | `transactions.perIteration` / `callerBoundary` / `separate` の 1 つ | 業務 + 呼び出し |
| TX-003 / BULK-002 | ループの中の COMMIT、`FORALL … SAVE EXCEPTIONS` | 1 反復（1 要素）ずつ確定してよいか。途中で止まったとき、確定した分が残ることを業務が受け入れるか。再実行したとき二重にならないか | `transactions.perIteration.<routine>` | 業務 |
| TX-002 | `PRAGMA AUTONOMOUS_TRANSACTION` | 呼び出し側とは別のトランザクションで回してよいか（親の rollback で消えない、が保たれる） | `transactions.separate.<routine>` | 呼び出し |
| CUR-002 / BULK-003 / BULK-001 | 行を先に全部読む（上限が要る） | 1 回に読む行数の上限（業務の数として）。上限を置かないならその理由 | `scanRows.routines.<routine>: <数>` / `scanRows.notLimited.<routine>: <理由>` | 業務 |
| STATE-001 | package 変数（セッション状態）を読み書きする | その値を呼び出し側が持ち回ってよいか（IN OUT 引数と結果で運ぶ） | `packageState.carried.<package>: <理由>` | 呼び出し |
| DYN-001 | 表名・列名・ORDER BY・WHERE の断片・routine 名を実行時に組む動的 SQL | 渡されうる名前の一覧（それ以外は実行時に拒否する）。連結する項が 1 つのときだけ書ける（表名でも列名でもよい）。2 つ以上（ORDER BY の列と方向など）や WHERE の断片そのものは、query builder への作り直しを決める | `dynamicTables.<routine>: [名前, …]` | 業務 |
| DYN-004（動的な DDL: `EXECUTE IMMEDIATE 'CREATE …'`） | ScalarDB はトランザクションの中で DDL を流さない。Oracle の DDL は前後で COMMIT する。決定が無ければ断る | その DDL がデータに何も残さない（作ってすぐ消す一時表など）ので、移行先で省いてよいか。TRUNCATE は省けない（行を消す）ので、下の「外れないもの」の DYN-004 を聞く | `ddl.omit.<routine>: <理由>` | 運用 |
| LINK-001 | DB link 越しの操作 | link の先の表を ScalarDB の管理下に置き、別の namespace として同じトランザクションで書くか。その namespace | `dbLinks.<link>: {namespace: …, reason: …}` | 運用 |
| CONS-001 | CHECK / 外部キー / UNIQUE が移行先に無い（子のある親の DELETE を含む） | 表ごとに、書く前に生成コードで検査するか（NOT NULL・CHECK・外部キー）、アプリに任せるか | `constraints.enforce.<表>: <理由>` | 業務 |
| 診断 `CONDITIONAL_COMPILATION`（ルールではない、INFO） | `$IF` を移行元の PLSQL_CCFLAGS と版を仮定して解いた。書いていないフラグは NULL | 移行元の `PLSQL_CCFLAGS` の値と Oracle の版 | `conditionalCompilation: {flags: {…}, dbVersion: "19.0"}` | 運用 |

## limits.yaml では外れないもの（直し方か、受け入れるかを聞く）

| ルール ID | 何が起きているか | 聞くこと | 決める人 |
|---|---|---|---|
| CALL-001 | 解析した範囲に無い routine の呼び出し。生成コードは断る | 呼び先のソースを足して変換し直すか、呼び先の代わり（adapter、外部 Service）を決めるか。呼び先が COMMIT するか、外へ送るか | 呼び出し |
| CALL-002 | どの版か決まらないオーバーロードの呼び出し | 名前付き引数で呼ぶ形に元の PL/SQL を直してよいか | 呼び出し |
| CALL-003 | OUT 引数のある関数を、評価されるか条件で決まる位置で呼ぶ | 条件を IF に分ける形に直してよいか | 呼び出し |
| SEM-010 | `SYSTIMESTAMP` を列へ書く | 時刻はアプリ（呼び出し側の `AuditContext`）で求める。業務がそれで足りるか（ミリ秒の違い、時計の出所） | 業務 |
| SEM-002 | タイムゾーンに依る関数 | 移行先でどのタイムゾーンで評価するか | 運用 |
| SEM-013 | `SYS_CONTEXT` | 要る値（ユーザ、クライアント情報など）を呼び出し側が渡す形にしてよいか | 呼び出し |
| SEM-014 | `DBMS_RANDOM`、`SYS_GUID` | 乱数・一意値の出所（Oracle と同じ値にはならない）を業務が受け入れるか | 業務 |
| SEM-007 | 時計を 2 回以上読む | 1 回読んで使い回す形に直してよいか | 業務 |
| SEM-001 / 003 / 009 / 012、SEM-008 | 移行先 DB が評価する ROUND・空文字・CAST、言語で変わる書式 | 式を SQL の外へ出すか、書式を明示するか | 業務 |
| AUTHID-001 | `AUTHID CURRENT_USER`（package の仕様に書いたものは本体の routine すべて） | 呼び出した人の権限で動いていたことを、移行先の認証・認可でどう保つか | 運用 |
| TX-004 / SCAN-001 | 同じトランザクションで書いた表を走査する（呼び先を含む）。ScalarDB は拒む | routine を境界で割るか、読み取りをキーにするか | 業務 + 呼び出し |
| LOWER-002 | 組み直せない `GOTO` | 制御構造を元の PL/SQL で組み直すか | 呼び出し |
| LOWER-001 | 下ろせない構文（構文エラーから回復した unit を含む） | 元の PL/SQL を直すか（構文エラーなら、Step 2 の「解析できなかったファイル」と同じ） | 呼び出し |
| STATE-002 | package 本体の初期化部 | 初期化をどこで（誰が、いつ）行うか | 呼び出し |
| DYN-002（DDL 以外） | とりうる文を展開して確かめられなかった動的 SQL | 流れうる文（表名・列名なら DYN-001 と同じく `dynamicTables`）と、`USING` の bind の対応・権限を確かめるか | 呼び出し |
| DYN-003 | `DBMS_SQL`（定数の問合せでないもの） | 実行ログから流れる文を洗い出すか | 運用 |
| DICT-001 | データ辞書を読む | 移行先のメタデータか設定値のどちらに置き換えるか | 運用 |
| TRG-001 | trigger そのもの | 生成コードの外からの書き込みを、照合（OPS-1）と権限（直接の書き込みを禁じる）で追うか | 運用 |
| TRG-002 | 畳み込めない `:NEW` の代入 | 書く側が行ごとに渡すものを設計するか | 呼び出し |
| EXT-001 / EXT-002 | `UTL_*` などの外部 package、呼び出し仕様（`LANGUAGE JAVA` など） | adapter 経由の外部 Service にするか、本体をアプリに移すか | 呼び出し |
| EXC-001 / EXC-002 / EXC-003 | 例外に頼る分岐（`PRAGMA EXCEPTION_INIT` で制約の番号に結んだものを含む）、`WHEN OTHERS THEN NULL`、書き込みを囲む `WHEN OTHERS`（移行先では DB の誤りが抜ける） | 読んでから選ぶ形・事前の検査に書き直すか、捕まえたい例外を名前で書くか | 業務 |
| CONS-002 | 守ると決めた表でも書く前に検査しない制約（UNIQUE、子のある親の DELETE、書く値が文から読めない、動的 SQL の文） | 先に読む形にするか、呼び出し側で保証するか、差を受け入れるか | 業務 |
| DYN-004（TRUNCATE） | routine の中の TRUNCATE。Oracle では前後で COMMIT し、ScalarDB では直前の作業を確定しない | トランザクションの外の運用の処理へ移すか、キーで DELETE する形に直すか（BIZ-12） | 業務 + 運用 |
| SQL-002 / SEM-004 / SEM-005 / SELECT-001 | 実行計画（取得して H2 で実行）に回る文 | 結果は同じ。取得のコスト（全パーティションの走査など）を受け入れるか、キーで届く形に直すか | 運用 |
| SQL-003 / SQL-004 / RECUR-001 / CUR-001 / CUR-003 | 名前の衝突、`SQL%ROWCOUNT`、再帰、書き換えられなかった cursor | 生成物と原文を並べて、同じ振る舞いかを確かめる（多くは直し方を示して聞く） | 呼び出し |

## ルールではなく、証拠が無いだけの REVIEW

`verdicts.<routine>` が `ruleVerdict: AUTO`、`verdict: REVIEW`、`reasons: ['confidence factor testEvidence is 0']` の
routine は、**ルールは何も問うていない**。実 DB で Oracle と比べていないので、確信度が 0 になっているだけである。
利用者に決めることは無い。報告では「ルールでは AUTO、実 DB で比べていない」と 1 行にまとめ、比べる手順
（`operations.md` の最後の節）を案内する。
