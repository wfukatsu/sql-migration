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
| — 移行先では自然には起こらない例外の handler | `unreachable_handler.sql` | EXC-001 |
| — 再帰 | `recursive.sql` | RECUR-001 |
| — どの書き換えの形にも当たらない明示 cursor | `explicit_cursor_unshaped.sql` | CUR-001（読むだけのループは 2026-09-18 から cursor FOR ループへ書き換わり、CUR-003 が持つ） |
