# 変換後のコードの文書の書き方（Step 7）

SKILL.md の Step 7 で、`migration_doc.py facts` が作った文書の `（未記入: …）` を書く前に読む。章ごと・routine ごとに
何を書くか、事実の欄に出る診断コードと決定をどう言い換えるかがある。コマンド、書く順序、`check` の問題への
対処は SKILL.md の Step 7 にある。

読むのは、生成された Java を**呼び出す側の開発者**と、移行を**引き継ぐ人**である。PL/SQL も生成器も知らない
前提で書く。事実の欄（`<!-- facts:begin … -->` 〜 `<!-- facts:end … -->`）は `migration_doc.py` が出すので、
文章はその表を言い直さず、「だから呼び出し側は何をするのか」「だから何が Oracle と違うのか」を書く。

## 全体の決まり

- **生成された Java を開いて読んでから書く。** constructor の引数、method の形、投げている例外の class は、
  事実の欄と Java の両方で確かめる。コード例は、その形のままコンパイルできるものにする
- **Java のファイル名は実在するものだけを書く。** Repository の method 名は事実の欄の「文の対応」から引く
- **確かめたことと、確かめていないことを分ける。** 「一致」と書けるのは、実 DB の比較（`--evidence`）に
  そのシナリオがあるときだけである。比較が観ていない振る舞い（同時実行、途中で止まったとき、PL/SQL の外からの
  書き込み）は、観ていないと書く。比較が無ければ、「制限」に Oracle と同じ結果を返すかは確かめていないと書く
- **決定には、決めた人と日付を添える。** `limits.yaml` の理由、記録（`--record`）の「決めた人」「日付」、事実の欄の
  「受け入れた差」の「決めた日」から引く。分からなければ、分からないと書く——文書のために決定を作らない
- 元の PL/SQL の仕様書（plsql-spec の成果）があれば、動作はそこへの参照にして、**違うところだけ**を書く

## README.md の章

| 章 | 書くこと | 落としやすいこと |
|---|---|---|
| アーキテクチャ | 層の依存の向き。Service は PL/SQL の本体を文の順のまま写したもので、原文の位置がコメントに残ること。比較・算術が `Plsql`（runtime-java）を通る理由（Oracle と同じ比較・丸め・NULL の扱い）。Repository は SQL 1 文 = 1 method。**トランザクションの境界が生成コードの外にあること** | 分割した routine の部品（`Targets` / `One` / `Failed` …）と `TriggerChecks` があるなら、その役割。`resources/plans/` があるなら、実行計画（ScalarDB から取得 → H2 で元の SQL）で動く文があること |
| 使い方 | 呼び出し側が用意するもの（`Connection`、`Sequences`、`AuditContext`）。1 回の呼び出しのコード例（開始 → 呼ぶ → commit → 業務の失敗と衝突の分け方）。依存ライブラリ | **衝突の再試行は呼び出し側の仕事であること。** 業務の失敗（エラーコード）は再試行しないこと。`db/*.sql` があるなら、配備のときに流すもの |
| 制限 | AUTO でない routine と、その理由が呼び出し側に何を求めるか。Oracle と違うシナリオ（受け入れた差・受け入れていない差）。比較が観ていない振る舞い。未決の項目（誰の答えを待っているか） | 時刻の出所（DB サーバからアプリへ）。金額の型（DOUBLE と表記）。行数の上限で止まる routine。DDL を推し量ったなら、そのこと |
| どのように移行したか | 解析 → 判定 → 決定 → 生成 → 比較の順に、**この案件で実際に起きたこと**を書く。どのルールが当たり、誰が何をいつ決め、何本のシナリオがどうなったか。`limits.yaml` の決定の種類（`optimistic`、`perIteration` など）は、どれも名前を出す | 途中で見つけて直した生成器の不具合。採らなかった案と、その理由（決定の理由に書いてある） |

## routine ごとの節（`<module>.md`）

- **仕様**: Java の入口、引数と戻り値の意味（OUT 引数は record に入る）、どのコードの例外がいつ出るか、書く表
- **移行で変わったこと**: 事実の欄の「何がどう変わったか」と診断を、業務の言葉に直す（下の表）。変わらなければ「なし」
- **制限と注意**: 判定の理由のルール ID（`LOCK-001` など、REVIEW / REDESIGN のもの）を 1 つずつ挙げ、呼び出し側が
  気をつけることを書く。受け入れた差のシナリオ名と、その routine の `limits.yaml` の決定の名前も出す

## 診断・決定の言い換え

事実の欄の診断コードと `limits.yaml` の決定を、routine の言葉に直すときの手がかり。

| 診断・決定 | 変わること（routine の言葉に直して書く） |
|---|---|
| `ROW_LOCK` / `OPTIMISTIC`（`rowLocks.optimistic`） | 待たせる・即座に断る → commit で弾く。呼び出し側に再試行が要る |
| `RMW_SPLIT` | `SET col = col ± x` を、読んでからアプリで計算して書く 2 文に割った。同じトランザクションの中なので結果は同じ |
| `NOW` | `SYSDATE` はアプリの時計（`Plsql.sysdate()`）、`SYSTIMESTAMP` と `USER` は呼び出し側が渡す `AuditContext`。**`NOW` が出ないこともある**: PL/SQL の式として先に計算して bind する文（`INSERT … VALUES (SYSTIMESTAMP)` など）には付かない。事実の欄が「文に付いた診断は無い」でも、原文に `SYSDATE` / `SYSTIMESTAMP` / `USER` があれば、生成された Java（`audit.now()`、`Plsql.sysdate()`）で確かめて書く |
| 生成コードが断る文（事実の欄の「生成コードが断る文」。generation-report の `refused`） | 外部の package の呼び出し（`CALL-001`）、変換できなかった文、ScalarDB が受け付けない文。呼ぶとそこで `UnsupportedOperationException` になる。診断が付かないこともあるので、この一覧で書く |
| ScalarDB SQL が受け付けない形（`PROJECTION`、`RMW`、`EXPR` など） | 事実の欄の区分は文の行き先で決まる。実行計画に回った文は結果が同じ（性能だけが変わる）、断った文は移行先で動かない |
| `TRANSACTION_IN_ROUTINE`（`transactions.perIteration` / `separate`） | routine の中の COMMIT が消え、1 反復 = 1 トランザクションの部品に割れた。回すのは呼び出し側 |
| `transactions.callerBoundary` | routine の中の COMMIT / ROLLBACK / SAVEPOINT は出さず、呼び出し側が commit / rollback する。途中の ROLLBACK が戻していた分は、呼び出し側が戻さないかぎり残る（意味が変わる決定） |
| `scanRows` | 先に全部読む routine に行数の上限が付いた。超えると例外で止まる（Oracle では止まらなかった） |
| `TRIGGER_APPLIED` / `TRIGGER_CALL` | trigger は、生成コードが書く経路でだけ動く。PL/SQL の外からの書き込みには掛からない（照合で追う） |
| `TRIGGER_INLINED` | 採番 trigger を INSERT の値として織り込んだ。キーを渡さない INSERT は、生成コードの外の経路では採番されない |
| `dynamicTables` | 動的 SQL の表名は一覧にあるものだけ。それ以外は実行時に拒否する |
| `DYN_STATIC` / `DYN_INLINED` | 文字列が定数の動的 SQL を静的な文として下ろした（`RETURNING INTO` 付きの DML、`OPEN FOR '定数'`、動的 PL/SQL ブロック）。`EXECUTE IMMEDIATE` の実行者の権限で走っていたことは `DYN_PRIVILEGE` に残る |
| `OBJECT_BUILT` / `TABLE_COLLECTION` / `PIPELINED` | スキーマのオブジェクト型は Java の record。コンストラクタを選ぶ SELECT は列を読んでアプリで組み、`TABLE(コレクション)` を 1 つだけ読む問合せは List を回して絞り・並べ（#135）、PIPELINED 関数は List をまとめて返す（行が出るそばから読むのではない） |
| `DBMS_SQL_STATIC` | 定数の問合せを PARSE する DBMS_SQL の一連を、静的な cursor FOR ループにした |
| `SUBQUERY_READ_FIRST` | SET の相関の無いスカラ副問合せを UPDATE の前に読む。0 行は NULL、2 行以上は ORA-01427（Oracle と同じ） |
| `OPTIONAL_FILTER` | `WHERE p IS NULL OR col = p` を 2 つの問合せに分け、p の値で選ぶ |
| `ddl.omit` | routine の中の DDL（一時表の CREATE / DROP など）を移行先で実行しない。元の文はコメントに残る。書いていない routine の DDL は生成器が断る |
| Oracle 提供の package | `DBMS_APPLICATION_INFO.SET_MODULE` / `SET_ACTION` / `SET_CLIENT_INFO` と `DBMS_STATS.GATHER_SCHEMA_STATS` / `GATHER_TABLE_STATS` は何もしない（理由をコメントに残す）。`DBMS_SESSION.SLEEP` / `DBMS_LOCK.SLEEP` / `DBMS_OUTPUT` / `DBMS_RANDOM.VALUE` / `STRING` / `DBMS_UTILITY.GET_TIME` は `Plsql` の helper。乱数と時計は Oracle と同じ値にならない。これ以外の package の呼び出しは生成器が断る |
| `constraints.enforce.<table>` | 移行先に無い CHECK / FOREIGN KEY を書く側で評価する表。CHECK は書く値で式を評価し、FOREIGN KEY は親を先に読み、違反は Oracle と同じ番号（-2290 / -2291）の例外になる。書かない表は `CONSTRAINT_UNDECIDED` で、アプリ側の検証に任せたことが見える |
| `packageState.carried` | package 変数（セッション状態）は呼び出し側が運ぶ。その変数を読み書きする routine（呼び先経由も含む）は IN OUT 引数として受け取り、結果で返す |
| `dbLinks` | DB link の先の表は、別の namespace として同じトランザクションで書く |
| `EXC-001` | `DUP_VAL_ON_INDEX` / `INVALID_NUMBER` / `VALUE_ERROR` の handler は、移行先では走らない。重複 INSERT はその文では弾かれず、commit 時の衝突として返る |
| 例外で抜けたとき | Oracle の文単位のロールバックは無い。呼び出し側が rollback しなければ、途中までの書き込みが残る |

`transactions.separate` の routine を呼ぶ Service は、constructor で `SeparateTransactions`（runtime-java）を受け取り、
それが開いた別の connection の上で呼び先の Service を組み立てて呼ぶ（処理が返れば commit、例外なら rollback。
呼ぶ側が後で rollback しても残る）。`SeparateTransactions` はインターフェースで、実装は移行先の配線の側にある。
「使い方」には、この口にトランザクションマネージャから新しい transaction を取る実装を渡すことを書く。
