# ルールの発火を確かめるための最小ケース（P2-3）

「AUTO 禁止条件の取りこぼし 0」を、その構文を含まない corpus で測っても何も証明できない。
実際、corpus には Package 変数も `AUTHID CURRENT_USER` も `DBMS_SQL` も GOTO も write-then-scan も無く、
それらのルールは一度も発火していなかった。

ここに置くのは、**`docs/plsql-kpi.md` §2 の禁止条件それぞれについて、ルールが実際に発火することを
確かめるための最小ファイル**である。`tests/test_plsql_safety.py` だけが読む。

## corpus に入れない理由

corpus（`../src/`）は KPI-1 / KPI-2 / KPI-3 の母数である。ルールを発火させるためだけのファイルを
そこへ混ぜると、分母が動き、PoC の数字が「ルールを試すために書いたコード」で薄まる。
役割が違うものは別の場所に置く。

## 対応表

| 禁止条件（KPI 文書 §2） | ファイル | ルール |
|---|---|---|
| 1 routine 内の COMMIT | `../src/prc_nightly_close.prc`（corpus にある） | TX-001 / TX-003 |
| 2 Autonomous Transaction | `../src/prc_audit_autonomous.prc`（corpus にある） | TX-002 |
| 3 Package 変数 | `package_state.sql` | STATE-001 |
| 4 動的 SQL（確定できないもの） | `../src/pkg_dynamic_search.pkb`（corpus にある） | DYN-001 |
| 4 `DBMS_SQL` | `dbms_sql.sql` | DYN-003 |
| 5 Trigger | `../src/trg_orders_audit.trg`（corpus にある） | TRG-001 |
| 6 `AUTHID CURRENT_USER` | `authid_current_user.sql` | AUTHID-001 |
| 7 DB Link | `../src/prc_remote_sync.prc`（corpus にある） | LINK-001 |
| 8 行ロック | `../src/pkg_stock_reserve.pkb`（corpus にある） | LOCK-001 / LOCK-002 |
| 9 変換不能な SQL | `unsupported_sql.sql` | SQL-001 |
| 10 実行計画に落ちる SQL | `planned_sql.sql` | SQL-002 |
| 11 write-then-scan | `write_then_scan.sql` | TX-004 |
| 12 未解決シンボル・未解決型 | `unresolved_type.sql` | 確信度（typeResolution = 0） |
| — 外部副作用 | `external_package.sql` | EXT-001 |
| — GOTO | `goto.sql` | LOWER-002 |
| — ROUND と集約が、互換ランタイムを通らずに移行先（実行計画の H2）で評価される SELECT INTO | `round_in_the_target.sql` | SEM-001、SEM-004 |
| — オーバーロードの呼び出しで、引数の数と名前からどの版かを決められないもの | `overload_unresolved.sql` | CALL-002 |
| — 移行先では自然には起こらない例外の handler | `unreachable_handler.sql` | EXC-001 |
| 5 trigger の掛かる表への書き込みで、trigger を呼び出しに置き換えられていないもの | `trigger_not_applied.sql` | TRG-002 |
| — 再帰 | `recursive.sql` | RECUR-001 |
| — `WHEN OTHERS THEN NULL` | `others_swallowed.sql` | EXC-002 |
| — PL/SQL 変数と同名の列（Oracle は列として読む） | `variable_named_like_column.sql` | SQL-003 |
| — 静的な DML 以外が決める `SQL%ROWCOUNT` | `rowcount_after_dynamic_sql.sql` | SQL-004 |
| — どの書き換えの形にも当たらない明示 cursor | `explicit_cursor_unshaped.sql` | CUR-001（読むだけのループは 2026-09-18 から cursor FOR ループへ書き換わり、CUR-003 が持つ） |
