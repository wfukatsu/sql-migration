# 移行の判断ポイント早見表 — 何を、いつ、どう選ぶか

Oracle の SQL / PL/SQL を ScalarDB へ移すとき、**生成器が決められないこと**が繰り返し出てくる。
この文書は、これまでの corpus・チュートリアル・構文カタログ（samples/oracle-samples）で実際に判断した
ポイントを 1 か所に集め、選択肢の Pros / Cons と「どういうときに何を選ぶか」をまとめたものである。

読み方の前提:

- **決めていないものは REDESIGN のまま止まる。** 生成器は推測しない。決めた人がいて、理由と日付が
  `limits.yaml` に書かれたときだけ先へ進む（[生成コードの外で決めること](plsql-decisions-outside-generator.md)）。
- **判断は「意味が変わるか」で分ける。** 形が変わるだけ（結果は同じ）の判断は生成器が黙って行う。意味が変わる
  判断は人が決め、その帰結（何が Oracle と違ってくるか）を承知のうえで記録する。
- **決める順番**: 仕様（何をしていたか）→ 判断 → 変換後の仕様 → 承認 → 実 DB で比べる。判断を求めるときは
  推奨・理由・影響を並べる（skills/migrate-flow）。

## 早見表

| # | 判断ポイント | 出る場面（ルール / 診断） | 選択肢 | まず選ぶもの | 記録先 |
|---|---|---|---|---|---|
| 1 | 走査する行数の上限 | `CUR-002` `SCAN-001` | 既定値 / routine ごと / 上限で守らない / 1 反復 = 1 トランザクションに割る | 割れる routine は割る。割れなければ routine ごとに上限 | `scanRows` / `transactions.perIteration` |
| 2 | パーティションをまたぐ走査 | `SCAN-002` `CROSS_PARTITION` | RDBMS バックエンド前提で通す / キーで取ってアプリで絞る | Cassandra が候補ならアプリで絞る | 設計書 |
| 3 | 行ロック（`FOR UPDATE` / `NOWAIT` / `SKIP LOCKED`） | `LOCK-001` `LOCK-002` | 楽観制御 + 呼び出し側の再試行 / 取り合いをデータで作り直す | 楽観制御。担当者列は足さない | `rowLocks.optimistic` |
| 4 | routine の中の COMMIT / ROLLBACK | `TX-001` `TX-003` `BULK-002` | 1 反復 = 1 トランザクション / 呼び出し側の境界 / 別トランザクション | 反復があれば perIteration、無ければ callerBoundary | `transactions.*` |
| 5 | 採番（sequence / IDENTITY / 採番 trigger） | `SEQ` `TRIGGER_INLINED` `IDENTITY_FILLED` | hi/lo / counters 表 + 再試行 | 代理キーは hi/lo、業務番号は counters | DDL の `CACHE` / `NOCACHE`、列の定義 |
| 6 | trigger の掛け方 | `TRG-001` `TRG-002` | 書く側が呼ぶ（生成コードの経路だけ）/ 手で Service へ移す / 捨てる | 書く側が呼び、網羅は照合で追う | trigger-patterns、TriggerChecks |
| 7 | package 変数（セッション状態） | `STATE-001` | 呼び出し側が運ぶ / トランザクション context / Singleton の field | 呼び出し側が運ぶ | `packageState.carried` |
| 8 | CHECK / FOREIGN KEY の代わり | `CONSTRAINT_UNDECIDED` | 表ごとに guard を生成 / 全表で生成 / アプリの検証に任せる | 表ごとに決めて guard | `constraints.enforce` |
| 9 | 動的 SQL の表名 | `DYN-001` | 受け付ける表名を列挙 / 断る | 列挙できるなら列挙 | `dynamicTables` |
| 10 | DB link 越しの操作 | `LINK-001` | 別 namespace として同じトランザクション / 分散トランザクションの設計 | 同じクラスタに載るなら namespace | `dbLinks` |
| 11 | `USER` / `SYSTIMESTAMP` / `SYSDATE` | `NOW` `AuditContext` | 呼び出し側が渡す / 実行環境から黙って取る | 呼び出し側が渡す | 呼び出し側の設計書 |
| 12 | `''` と NULL | `EMPTY_STRING` | Oracle と同じく同一視 / 移行後は区別 | 同一視のまま | 設計書 |
| 13 | キーで届かない `SELECT INTO` | `SELECT-001` `MULTI_ROW_INTO` | 2 行まで読んで Oracle と同じ例外 / 0 件・複数件の意味を決め直す | 2 行まで読む | 設計書 |
| 14 | DB が上げていた例外の handler | `EXC-001` | handler は出さない（衝突として扱う）/ 明示の RAISE だけ残す | 出さない | 判定表の注記 |
| 15 | 金額・日付の型の規約 | 型対応表 | `scaled`（10^s 倍の BIGINT）/ `double` | 四捨五入を保つなら scaled | `scalardb-schema.json`、`--variant` |
| 16 | SQL の実行計画（ScalarDB が受けない SQL） | `PLANNED` `RESIDUAL_H2` | 取得して H2 で実行 / アプリで書き直す | 読み取りだけなら計画、更新はアプリ | SQL の報告 |
| 17 | 合否と引き渡し | 計画 §9 | 証拠のある AUTO だけを AUTO / 引き渡し後は手編集 | 実 DB の一致が AUTO の条件 | `--evidence`、`--handover` |

以下、1 つずつ。

## 1. 走査する行数の上限

**何が起きるか。** 生成コードは cursor の行を先に全部読んでから回す（ScalarDB はトランザクションをまたぐ
cursor を持たない）。動く行数はメモリで決まり、Oracle では止まらなかった処理が止まりうる。

| 選択肢 | Pros | Cons |
|---|---|---|
| 既定値（10000 行）のまま | 何もしなくてよい | 業務の数と無関係。超えたときに初めて分かる |
| routine ごとに上限を書く（`scanRows.routines`） | 「1 注文あたりの明細数」のように業務の数で守れる | routine ごとに数を知る人が要る |
| 上限で守らない（`notLimited`） | 大きな走査をそのまま通せる | メモリを使い切る道を開く。理由が要る |
| 1 反復 = 1 トランザクションに割る（`transactions.perIteration`） | 処理対象をキー順に件数つきで読むので上限が要らない。失敗した 1 件だけやり直せる | 全体の原子性が無くなる（BIZ-1）。回すのは呼び出し側 |

**選び方。** ループの中で書いている routine は、まず「割れる形か」を見る（ループが 1 つ、その中が 1 反復）。
割れるなら perIteration が一番よい。割れない・読むだけの routine は、業務の数（1 注文の明細数など）で上限を
書く。`notLimited` は「集計は全件」のような理由がはっきりしているときだけ。

## 2. パーティションをまたぐ走査

**何が起きるか。** フィルタも順序もパーティションキーを使わない走査（`WHERE status = ? ORDER BY ordered_at`）は、
ScalarDB では JDBC バックエンドのときだけ通る。Cassandra では同じ問い合わせが通らない。

| 選択肢 | Pros | Cons |
|---|---|---|
| RDBMS バックエンド前提でそのまま通す | 変換が要らない | バックエンドを変えると動かない。件数に比例して遅い |
| キーで取ってアプリで絞る・並べる | どのバックエンドでも同じ | 読む行が増える。アプリの設計が要る |

**選び方。** バックエンドが決まっていないか Cassandra が候補なら、キーで取ってアプリで処理する
（[ルール](../../README.md) の「クロスパーティション走査は RDBMS のときだけ」）。RDBMS に固定できるなら通す。
どちらでも `CROSS_PARTITION` は「意味が変わる」として記録に残す。

## 3. 行ロック

**何が起きるか。** ScalarDB に行ロックは無い。`FOR UPDATE` で待つ・`NOWAIT` で即座に断る・`SKIP LOCKED` で取り合う
という意味が、そのままでは無くなる。

| 選択肢 | Pros | Cons |
|---|---|---|
| 楽観制御へ移す（`rowLocks.optimistic`） | 同じトランザクションで読んで書くので結果は同じ。衝突は commit で弾かれる | 再試行は呼び出し側の責務。「待つ」意味は再現しない。冪等でない副作用があると再試行は二重実行 |
| 取り合い（`SKIP LOCKED`）を担当者列や期限で作り直す | 誰がいつ取ったかが残る | Oracle 版に無い挙動（期限切れの再取得）が増える。2026-09-19 に「列は足さない」と改めた |
| 決めない（REDESIGN のまま） | 黙って進めない | いつまでも止まる |

**選び方。** A（読んで判断して書く）・B（NOWAIT）・C（SKIP LOCKED）はすべて楽観制御に移し、routine ごとに
「呼び出し側が何を再試行するか」を理由に書く。業務例外（在庫不足 -20030）と衝突を分けられるかを確かめる。
`NOWAIT` の ORA-54 を捕まえていた handler は出さない（起こりえない）。

## 4. トランザクション境界（routine の中の COMMIT / ROLLBACK）

**何が起きるか。** 生成コードはトランザクションを開始も commit もしない。routine の中の COMMIT は
「ここが 1 つの単位の終わり」と言っていたので、置き換えは分け方を決めることになる。

| 選択肢 | Pros | Cons | 向く形 |
|---|---|---|---|
| 1 反復 = 1 トランザクション（`perIteration`） | 失敗した 1 件だけやり直せる。100 件ごとの中間 COMMIT が 1 件ごとに細かくなるだけで保証は下がらない。`SAVE EXCEPTIONS` の「失敗を飛ばして続ける」もこの形 | 全体の原子性が無い。読んだ時点と処理する時点がずれる（途中で増えた行も処理される）。回すのは呼び出し側 | ループの中で書く batch、FORALL … SAVE EXCEPTIONS |
| 呼び出し側の境界（`callerBoundary`） | routine は 1 つの method のまま。呼び出し側が commit / rollback する | 途中の ROLLBACK が戻していた分は、呼び出し側が戻さないかぎり残る（意味が変わる）。guard の例外で途中で止まると、SAVE EXCEPTIONS とは合わない | ループの無い routine、「試したあと戻す」デモ |
| 別トランザクション（`separate`） | 親が rollback しても残る（自律トランザクションの意味を保つ） | 呼ぶ側が別の境界を開く口（`SeparateTransactions`）を持つ。OUT 引数を境界の外へ持ち出せない | `PRAGMA AUTONOMOUS_TRANSACTION`、失敗の記録 |

**選び方。** ループがあって中で書くなら perIteration。ループが無い（または割れない形）なら callerBoundary。
自律トランザクションは separate。**b06_2 の教訓**: FORALL … SAVE EXCEPTIONS を callerBoundary にすると、
CHECK の guard（判断 8）が最初の違反行で全体を止め、Oracle と合わなくなる。SAVE EXCEPTIONS は perIteration。
`SAVEPOINT` / `ROLLBACK TO` のあとに書くエラー行は別トランザクション（実測どおり）。

呼び出し側で決めること（CALL-1〜5）: 再試行の回数、並列度、中断と再開、1 回に取る件数 `pBatch`、全体の失敗の扱い。

## 5. 採番

**何が起きるか。** ScalarDB に順序オブジェクトは無い。`seq.NEXTVAL`、IDENTITY 列、`:NEW.id := seq.NEXTVAL` の
採番 trigger はすべて「番号をどこから取るか」の決定になる。

| 方式 | Pros | Cons | 向く列 |
|---|---|---|---|
| hi/lo（範囲を先に確保して配る） | 速い。高衝突点にならない | 欠番が出る（停止時に未使用分が捨てられる） | 監査 ID など代理キー（DDL の `CACHE`） |
| counters 表 + 再試行 | 連番。欠番なし | 更新するたびに全員とぶつかる高衝突点。他の更新と同じトランザクションに入れない | 注文番号・支払番号など業務上意味のある番号（`NOCACHE`） |
| UUID | 衝突点が無い | 列型が BIGINT から TEXT へ変わり、外部連携や帳票の番号体系が壊れる。採らない | — |

**選び方。** DDL の `CACHE` / `NOCACHE` から方式を導き、列ごとに選んだ理由を列の定義に残す。採番 trigger は
書く側の INSERT に織り込む（`WHEN (NEW.id IS NULL)` は「指定があればそれを使う」の意味なので保つ）。
IDENTITY 列と DDL の `DEFAULT` 句は生成器が INSERT に足す（省くと移行先では NULL になる）。

## 6. trigger の掛け方

**何が起きるか。** 移行先に trigger は無い。「その表へのすべての書き込み」に掛かっていたものは、書く側が呼ぶ
形でしか掛けられず、掛かるのは生成コードが書く経路だけになる。

| 選択肢 | Pros | Cons |
|---|---|---|
| 書く側が trigger を呼ぶ（既定。#12 §0） | 機械的に掛かる。BEFORE / AFTER、`WHEN`、`UPDATE OF`、`UPDATING('列')` を保つ。`:NEW.x := 式` は書く値に畳み込める | 他システムの直接 DML や手作業の SQL には掛からない。網羅は照合（TriggerChecks）と権限で追う |
| 手で Service へ移す | 意味を設計し直せる（validation は検証へ、audit は interceptor へ） | 工数。生成物との対応が切れる |
| trigger を捨てる（業務判断） | 単純 | 監査・検証が無くなる。業務の承認が要る |

**選び方。** まず生成器に掛けさせ、`TRIGGER_NOT_APPLIED` / `TRIGGER_REDESIGN` が残る所（1 行に絞れない更新、
MERGE、条件つきの `:NEW` の代入）だけを手で設計する。直接の書き込みを権限で禁じ、照合ジョブで差を追う。
view への INSTEAD OF trigger は view ごと設計し直す（#56）。

## 7. package 変数（セッション状態）

**何が起きるか。** package 変数は Oracle ではセッション単位。生成される Service は Singleton なので、field に置くと
プロセス単位になり意味が変わる。

| 選択肢 | Pros | Cons |
|---|---|---|
| 呼び出し側が引数と戻り値で運ぶ（`packageState.carried`） | セッション単位の意味をそのまま保てる。機械的に持ち上げられる（呼び先経由も含む） | 呼び出し側の signature が変わる。式の中の呼び出しは文に出す必要がある |
| トランザクションごとの context に置く | signature が変わらない | 「セッション」と「トランザクション」は違う。跨る状態は消える |
| Singleton の field | 何もしなくてよい | 全呼び出し元で共有される。意味が変わる |

**選び方。** 状態が呼び出しをまたいで意味を持つ（呼び出し回数、前回の値）なら運ぶ。定数は static field。
運ぶと決めたら、package の外の呼び出し側にも同じ引数が伝わる。

## 8. CHECK / FOREIGN KEY の代わり

**何が起きるか。** ScalarDB に CHECK と外部キーは無い。Oracle が弾いた行がそのまま入る。

| 選択肢 | Pros | Cons |
|---|---|---|
| 表ごとに決めて guard を生成（`constraints.enforce`） | 意味を保つ表と、アプリに任せる表を分けられる。違反は Oracle と同じ番号（-2290 / -2291）で handler が受ける | FK の親の読みが読み集合に入り、親の同時削除は commit の衝突になる。UPDATE が書かない列の CHECK は評価できない（`CONSTRAINT_NOT_GUARDED`） |
| すべての表で guard | 決定が要らない | 全書き込みに親の読みが付く。アプリで既に検証している表でも二重になる |
| 生成しない（アプリの検証に任せる） | 生成器は今のまま | 違反が黙って通る。移行前後で挙動が変わる箇所が相違として残り続ける |

**選び方。** 表ごとに決める。データの整合が DB 側の責務だった表（他システムも書く表）は guard、アプリが唯一の
書き手で検証を持つ表は任せる。決めていない表は `CONSTRAINT_UNDECIDED` で見える。

## 9. 動的 SQL の表名

| 選択肢 | Pros | Cons |
|---|---|---|
| 受け付ける表名を列挙（`dynamicTables`） | 有限の変種に畳めて静的な SQL と同じ経路に乗る。それ以外は実行時に拒否 | 列挙できない（表名が入力で決まる）なら使えない |
| 断る（REDESIGN） | 推測しない | 動かない |

**選び方。** 表名が定数の集合から選ばれているなら列挙する。`DBMS_SQL`、動的な RETURNING、動的 PL/SQL ブロックは
まだ模していない（#52 / #53）。

## 10. DB link

| 選択肢 | Pros | Cons |
|---|---|---|
| 別 namespace として同じトランザクションで書く（`dbLinks`） | ScalarDB の 1 トランザクションで両方を確定できる（Oracle より強い） | 同じクラスタに載ることが前提。失敗時の例外の種類が変わる（ORA-02067 と DB-CORE-20013） |
| 分散トランザクションの設計 | 別システムのまま | 設計そのもの。生成器の外 |

**選び方。** 相手の表を同じクラスタへ移せるなら namespace。移せないなら設計に回す。

## 11. `USER` / `SYSTIMESTAMP` / `SYSDATE`

**何が起きるか。** `USER` は DB セッションの利用者であってアプリの利用者ではない。`SYSTIMESTAMP` は固定できない
時計で、比較もできなかった。

| 選択肢 | Pros | Cons |
|---|---|---|
| 呼び出し側が `AuditContext`（誰が・いつ）を渡す | 検証できる（固定できる）。「誰を記録するか」を業務が決められる（多くは改善） | 引数が増える。`user()` に何を入れるかの決定が要る |
| 実行環境から黙って取る（ThreadLocal など） | signature が変わらない | 出所が見えず、検証も監査もできない |

**選び方。** 渡す。`SYSDATE` はアプリの時計（固定できる）なので引数にしない。DDL の `DEFAULT USER` /
`DEFAULT SYSTIMESTAMP` も同じ口から入る。

## 12. `''` と NULL

Oracle は `''` を NULL として保存するので、移行元のデータでは区別できない。

| 選択肢 | Pros | Cons |
|---|---|---|
| Oracle と同じく同一視（runtime の既定） | 既存データと条件式（`v = '' OR v IS NULL`）の意味が変わらない | 移行後も区別できない |
| 移行後は区別する | Java と同じ意味 | 既存データに `''` は無いので、区別する意味が生まれるのは新規データだけ。条件式を全部見直す |

**選び方。** 同一視のまま。区別したい要件があるなら列ごとに決め、条件式を洗う。

## 13. キーで届かない `SELECT INTO`

| 選択肢 | Pros | Cons |
|---|---|---|
| 2 行まで読んで Oracle と同じ例外（`MULTI_ROW_INTO`） | `NO_DATA_FOUND` / `TOO_MANY_ROWS` の意味を保つ | 走査になる（判断 1・2 と同じ話） |
| 0 件・複数件の意味を決め直す（既定値を返す、複数件は起きないと宣言） | 走査を減らせる | なぜ起きないかを条件として書ける必要がある |

**選び方。** まず 2 行まで読む形で意味を保ち、性能で困る所だけ決め直す。

## 14. DB が上げていた例外の handler

| 選択肢 | Pros | Cons |
|---|---|---|
| 出さない（`EXC-001`、衝突として扱う） | 移行先では起こらない誤り（ORA-54、一意制約違反の一部）を捕まえる dead code を作らない | 元の handler の意図（ログ、言い換え）が消える。判定表の注記で見せる |
| 明示の RAISE だけを残す | 業務例外（`RAISE e_x`、`RAISE_APPLICATION_ERROR`）は保たれる | DB 由来のものは別途、衝突の再試行として設計する |

`PRAGMA EXCEPTION_INIT` の番号は例外クラスが持つので、生成した guard（判断 8）の -2291 は元の handler が受ける。

## 15. 金額・日付の型の規約

| 規約 | Pros | Cons |
|---|---|---|
| `scaled`（NUMBER(p, s) を 10^s 倍の BIGINT） | Oracle の四捨五入を保つ。比較で値が一致する | 生成コードが往復させる。手で読むとき桁が分かりにくい |
| `double`（DOUBLE） | そのまま読める | 丸めが変わる。`6000` と `6000.0` のような scale の差が出る（比較は scale だけの差を数えない） |

日付: Oracle の `DATE` は時刻を持つので `TIMESTAMP`。`DATE` 型にすると比較が全滅する。精度の無い `NUMBER` は
`NUMBER(12)` のように幅を決める（決めないと BigDecimal を TEXT に入れる）。

## 16. ScalarDB が受けない SQL（実行計画）

| 選択肢 | Pros | Cons |
|---|---|---|
| 取得して H2 で実行する計画（`PLANNED`） | 分析系の SELECT（結合、ウィンドウ関数、階層問い合わせ）がそのまま動く | 取得はキーかパーティション走査。H2 が受けない構文（FULL OUTER JOIN、LATERAL、SAMPLE、JSON_TABLE）は `RESIDUAL_H2` で ERROR |
| アプリで書き直す | 何でも書ける | 工数。生成物との対応が切れる |

**選び方。** 読み取りだけなら計画に回し、`ROWNUM` に `ORDER BY` が無い・同順位の並びが決まらない文は
「非決定」として比較から外す（#57）。更新を含む文はアプリの設計。

## 17. 合否と引き渡し

- **AUTO は証拠があるときだけ。** 実 DB（Oracle と ScalarDB Cluster）で同じシナリオを流し、一致した routine だけを
  AUTO にする（`--evidence`）。合成 corpus や構文カタログの数字は実案件耐性の証拠ではない。
- **判定者は契約と体制で決める**（#6、未定のまま）。基準は計画 §9 の KPI 表。
- **引き渡したら再生成しない**（`--handover`）。以後は通常の Java として手編集する。

## 記録の形（`limits.yaml`）

決定は routine（または表・package）ごとに、理由と日付と決めた人が分かる形で書く。書いていないものは決めていない。

```yaml
rowLocks:
  optimistic:
    raise_salary: 昇給の UPDATE … RETURNING。読んでから書き、衝突は呼び出し側が再試行（利用者の決定、2026-09-25）
transactions:
  perIteration:
    b06_2_forall_save_exceptions: FORALL … SAVE EXCEPTIONS は「失敗した要素を飛ばして続ける」（2026-09-25）
  callerBoundary:
    b04_3_implicit_cursor_attrs: 最後の ROLLBACK はデモを戻すため。呼び出し側が戻す（2026-09-25）
  separate:
    log_msg: PRAGMA AUTONOMOUS_TRANSACTION。親が rollback してもログは残る（2026-09-25）
packageState:
  carried:
    emp_api: g_calls はセッション単位の呼び出し回数。呼び出し側が運ぶ（2026-09-25）
constraints:
  enforce:
    employees: emp_job_fk / emp_dept_fk / emp_salary_ck。FK 違反（-2291）を handler が受ける（2026-09-25）
dynamicTables:
  b06_3_native_dynamic_sql: [employees]
```

## 関連

- [生成コードの外で決めること](plsql-decisions-outside-generator.md) — 運用（OPS）・呼び出し側（CALL）・業務（BIZ）の確認項目
- [トランザクションと行ロック](plsql-transaction-patterns.md)、[cursor](plsql-cursor-patterns.md)、[trigger](plsql-trigger-patterns.md) — 各判断の実測と実装
- [samples/oracle-samples](../../samples/oracle-samples/README.md) — 構文カタログで実際に判断した記録（2026-09-24〜26）
