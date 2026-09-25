# 人手が要る項目

41 件（REDESIGN 先頭）。AUTO は無人で生成してよいという判定なので、ここには出ない。

## REDESIGN: `b04_3_implicit_cursor_attrs` — `b04_3_implicit_cursor_attrs.prc:2`

**再設計の状態: 未決定**

- `calls emp_salary_audit_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `TX-004` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `TRG-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- TX-004: 同一トランザクションで書いた表を走査しています。ScalarDB はこれを拒否します（P2-9 で実測）
- TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**判定したルール**

- `SQL-004` (REVIEW, `semantics.yaml`): SQL%ROWCOUNT を読んでいますが、静的な DML 以外（FORALL・動的 SQL・MERGE・呼び出し先の SQL）が件数を決めうる routine です
- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- `TX-004` (REDESIGN, `transaction.yaml`): 同一トランザクションで書いた表を走査しています。ScalarDB はこれを拒否します（P2-9 で実測）
- `TRG-002` (REDESIGN, `trigger.yaml`): trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**代替案**

- 件数を返す形に呼び出し先を直すか、件数を読む位置を静的な DML の直後に寄せる
- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める
- 耐久境界で routine を分割する
- 読み取りをキーアクセスに変える
- trigger の処理を、この書き込みをする Service に持たせる（値を書き換える trigger は採番 Service へ）
- 複数行の更新・DELETE・MERGE・複数イベントの trigger は、行ごとに何を渡すかを設計する

**受け入れに必要なテスト**

- all_write_paths_covered
- equivalent_result
- partial_failure
- rollback_boundary
- row_count
- scan_after_write

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `b04_4_3_for_update_current_of` — `b04_4_3_for_update_current_of.prc:2`

**再設計の状態: 未決定**

- `calls emp_dept_cap_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `calls emp_salary_audit_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `LOCK-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `LOCK-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `TRG-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- LOCK-001: 行ロックです。ターゲットで同じ保証を別の方法で与える設計が要ります
- LOCK-002: cursor の宣言で行ロックしています。文だけを見ると見えない形です
- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**判定したルール**

- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `SQL-004` (REVIEW, `semantics.yaml`): SQL%ROWCOUNT を読んでいますが、静的な DML 以外（FORALL・動的 SQL・MERGE・呼び出し先の SQL）が件数を決めうる routine です
- `LOCK-001` (REDESIGN, `sql.yaml`): 行ロックです。ターゲットで同じ保証を別の方法で与える設計が要ります
- `LOCK-002` (REDESIGN, `sql.yaml`): cursor の宣言で行ロックしています。文だけを見ると見えない形です
- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- `TRG-002` (REDESIGN, `trigger.yaml`): trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**代替案**

- 件数を返す形に呼び出し先を直すか、件数を読む位置を静的な DML の直後に寄せる
- 楽観制御と再試行にする
- NOWAIT / SKIP LOCKED は待たない意味を個別に設計する
- 楽観制御と再試行にする
- SKIP LOCKED の「取り合い」の意味を業務要件として決める
- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める
- trigger の処理を、この書き込みをする Service に持たせる（値を書き換える trigger は採番 Service へ）
- 複数行の更新・DELETE・MERGE・複数イベントの trigger は、行ごとに何を渡すかを設計する

**受け入れに必要なテスト**

- all_write_paths_covered
- concurrent_claim
- concurrent_update
- equivalent_result
- partial_failure
- performance
- rollback_boundary
- row_count
- row_limit

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `b04_6_2_user_exceptions` — `b04_6_2_user_exceptions.prc:2`

**再設計の状態: 未決定**

- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `TRG-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**判定したルール**

- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- `TRG-002` (REDESIGN, `trigger.yaml`): trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**代替案**

- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める
- trigger の処理を、この書き込みをする Service に持たせる（値を書き換える trigger は採番 Service へ）
- 複数行の更新・DELETE・MERGE・複数イベントの trigger は、行ごとに何を渡すかを設計する

**受け入れに必要なテスト**

- all_write_paths_covered
- partial_failure
- rollback_boundary

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `b05_1_call_raise_salary` — `b05_1_call_raise_salary.prc:2`

**再設計の状態: 未決定**

- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `calls raise_salary` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**判定したルール**

- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**代替案**

- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める

**受け入れに必要なテスト**

- partial_failure
- rollback_boundary

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `b05_3_call_emp_api` — `b05_3_call_emp_api.prc:2`

**再設計の状態: 未決定**

- `calls emp_api.call_count` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `calls emp_api.give_raise~1` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `calls emp_api.give_raise~2` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `calls emp_api.hire` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**判定したルール**

- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**代替案**

- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める

**受け入れに必要なテスト**

- partial_failure
- rollback_boundary

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `b05_4_call_log_msg` — `b05_4_call_log_msg.prc:2`

**再設計の状態: 未決定**

- `calls emp_dept_cap_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `calls emp_salary_audit_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `TRG-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `calls log_msg` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**判定したルール**

- `SQL-004` (REVIEW, `semantics.yaml`): SQL%ROWCOUNT を読んでいますが、静的な DML 以外（FORALL・動的 SQL・MERGE・呼び出し先の SQL）が件数を決めうる routine です
- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- `TRG-002` (REDESIGN, `trigger.yaml`): trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**代替案**

- 件数を返す形に呼び出し先を直すか、件数を読む位置を静的な DML の直後に寄せる
- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める
- trigger の処理を、この書き込みをする Service に持たせる（値を書き換える trigger は採番 Service へ）
- 複数行の更新・DELETE・MERGE・複数イベントの trigger は、行ごとに何を渡すかを設計する

**受け入れに必要なテスト**

- all_write_paths_covered
- equivalent_result
- partial_failure
- rollback_boundary
- row_count

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `b06_2_2_forall_returning` — `b06_2_2_forall_returning.prc:2`

**再設計の状態: 未決定**

- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**判定したルール**

- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `BULK-001` (REVIEW, `sql.yaml`): BULK COLLECT は行数上限とメモリ上限が要ります
- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**代替案**

- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める

**受け入れに必要なテスト**

- equivalent_result
- partial_failure
- rollback_boundary
- row_limit

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `b06_2_forall_save_exceptions` — `b06_2_forall_save_exceptions.prc:2`

**再設計の状態: 未決定**

- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**判定したルール**

- `SCAN-002` (AUTO, `scalardb_capability.yaml`): パーティションキーで絞れない走査です。JDBC バックエンドでは実行できますが、フィルタも順序もパーティションをまたぐため、JDBC 以外（Cassandra など）では同じ問い合わせが通りません。そこへ移すときは、キーで届く読み取りに直す必要があります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `BULK-003` (REVIEW, `semantics.yaml`): 分割読みは行をまとめて読む形になりました。メモリを守るのは LIMIT ではなく走査行数の上限です
- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**代替案**

- キーで届く読み取りに変える
- 全行を取得してアプリ側で絞る・並べる（docs/design/app-side-processing-plan.md）
- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める

**受け入れに必要なテスト**

- cross_partition_scan
- partial_failure
- performance
- rollback_boundary
- row_limit

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `b06_3_6_dbms_sql` — `b06_3_6_dbms_sql.prc:2`

**再設計の状態: 未決定**

- `DYN-003` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- DYN-003: DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります

**判定したルール**

- `DYN-003` (REDESIGN, `dynamic_sql.yaml`): DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- `DYN-003` (REDESIGN, `dynamic_sql.yaml`): DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- `DYN-003` (REDESIGN, `dynamic_sql.yaml`): DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- `DYN-003` (REDESIGN, `dynamic_sql.yaml`): DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- `DYN-003` (REDESIGN, `dynamic_sql.yaml`): DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- `DYN-003` (REDESIGN, `dynamic_sql.yaml`): DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- `DYN-003` (REDESIGN, `dynamic_sql.yaml`): DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- `DYN-003` (REDESIGN, `dynamic_sql.yaml`): DBMS_SQL は静的解析だけでは追えません。実行ログも使って query family を洗い出す必要があります
- `CALL-001` (REVIEW, `lowering.yaml`): 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません

**代替案**

- 実行ログから実際に流れた SQL を集める
- allowlist 型の専用 Repository にする
- 実行ログから実際に流れた SQL を集める
- allowlist 型の専用 Repository にする
- 実行ログから実際に流れた SQL を集める
- allowlist 型の専用 Repository にする
- 実行ログから実際に流れた SQL を集める
- allowlist 型の専用 Repository にする
- 実行ログから実際に流れた SQL を集める
- allowlist 型の専用 Repository にする
- 実行ログから実際に流れた SQL を集める
- allowlist 型の専用 Repository にする
- 実行ログから実際に流れた SQL を集める
- allowlist 型の専用 Repository にする
- 実行ログから実際に流れた SQL を集める
- allowlist 型の専用 Repository にする
- 呼び先のソースを解析対象に加える
- 加えられない（Oracle 提供のパッケージなど）なら、移行先での代替を決める

**受け入れに必要なテスト**

- dynamic_sql_variants
- equivalent_result

**確信度が 0 になっている要因**: symbolResolution, testEvidence

## REDESIGN: `b06_3_native_dynamic_sql` — `b06_3_native_dynamic_sql.prc:2`

**再設計の状態: 未決定**

- `DYN-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- DYN-001: 表名など識別子が実行時に決まる SQL です。allowlist か専用 Repository への再設計が要ります
- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**判定したルール**

- `DYN-001` (REDESIGN, `dynamic_sql.yaml`): 表名など識別子が実行時に決まる SQL です。allowlist か専用 Repository への再設計が要ります
- `DYN-002` (REVIEW, `dynamic_sql.yaml`): 動的 SQL です。とりうる文をすべて展開して ScalarDB がそのまま実行できる、とは確かめられていません。bind の復元と権限の確認が要ります
- `DYN-002` (REVIEW, `dynamic_sql.yaml`): 動的 SQL です。とりうる文をすべて展開して ScalarDB がそのまま実行できる、とは確かめられていません。bind の復元と権限の確認が要ります
- `DYN-002` (REVIEW, `dynamic_sql.yaml`): 動的 SQL です。とりうる文をすべて展開して ScalarDB がそのまま実行できる、とは確かめられていません。bind の復元と権限の確認が要ります
- `DYN-002` (REVIEW, `dynamic_sql.yaml`): 動的 SQL です。とりうる文をすべて展開して ScalarDB がそのまま実行できる、とは確かめられていません。bind の復元と権限の確認が要ります
- `DYN-OPT-002` (AUTO, `dynamic_sql.yaml`): 動的 SQL は、とりうる文をすべて静的な文に展開して生成しました（どれも ScalarDB がそのまま実行できます）。Oracle で EXECUTE IMMEDIATE に与えていた権限に相当するものが移行先に要るかは、運用で確認することを推奨します
- `LOWER-001` (REVIEW, `lowering.yaml`): lowering がまだ模していない構文です。意味が保てる保証がありません
- `CUR-001` (REVIEW, `semantics.yaml`): 明示 cursor は寿命がトランザクション境界をまたぎます
- `CUR-001` (REVIEW, `semantics.yaml`): 明示 cursor は寿命がトランザクション境界をまたぎます
- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません

**代替案**

- allowlist 型の query builder にする
- 有限 variant なら variant ごとに静的な query にする
- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める

**受け入れに必要なテスト**

- bind_restoration
- cursor_lifetime
- dynamic_sql_variants
- equivalent_result
- partial_failure
- rollback_boundary

**確信度が 0 になっている要因**: ruleCoverage, testEvidence

## REDESIGN: `b06_5_2_scheduler_job` — `b06_5_2_scheduler_job.prc:2`

**再設計の状態: 未決定**

- `EXT-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- EXT-001: UTL_* / DBMS_SCHEDULER / AQ などの外部副作用があります

**判定したルール**

- `CALL-001` (REVIEW, `lowering.yaml`): 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません
- `EXT-001` (REDESIGN, `trigger.yaml`): UTL_* / DBMS_SCHEDULER / AQ などの外部副作用があります

**代替案**

- 呼び先のソースを解析対象に加える
- 加えられない（Oracle 提供のパッケージなど）なら、移行先での代替を決める
- adapter 経由の外部 Service にする
- timeout・再試行・冪等性を決める

**受け入れに必要なテスト**

- equivalent_result
- external_failure

**確信度が 0 になっている要因**: symbolResolution, testEvidence

## REDESIGN: `emp_api.call_count` — `emp_api.pkb:53`

**再設計の状態: 決定済み（実 DB では未検証、または相違あり）**

- `STATE-001` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- 実 DB の比較: まだ無い
- 判定は REDESIGN のまま（AUTO 禁止条件）。同時実行での衝突と再試行など、呼び出し側に残る責務は決定の理由に書いてある

**根拠**

- STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります

**判定したルール**

- `STATE-001` (REDESIGN, `state.yaml`): Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります

**代替案**

- 引数で渡す
- 明示的な SessionContext か永続化に移す

**受け入れに必要なテスト**

- state_isolation_between_calls

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `emp_api.get_by_dept` — `emp_api.pkb:44`

**再設計の状態: 決定済み（実 DB では未検証、または相違あり）**

- `STATE-001` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- 実 DB の比較: まだ無い
- 判定は REDESIGN のまま（AUTO 禁止条件）。同時実行での衝突と再試行など、呼び出し側に残る責務は決定の理由に書いてある

**根拠**

- STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります

**判定したルール**

- `SCAN-002` (AUTO, `scalardb_capability.yaml`): パーティションキーで絞れない走査です。JDBC バックエンドでは実行できますが、フィルタも順序もパーティションをまたぐため、JDBC 以外（Cassandra など）では同じ問い合わせが通りません。そこへ移すときは、キーで届く読み取りに直す必要があります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `STATE-001` (REDESIGN, `state.yaml`): Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります

**代替案**

- キーで届く読み取りに変える
- 全行を取得してアプリ側で絞る・並べる（docs/design/app-side-processing-plan.md）
- 引数で渡す
- 明示的な SessionContext か永続化に移す

**受け入れに必要なテスト**

- cross_partition_scan
- performance
- row_limit
- state_isolation_between_calls

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `emp_api.give_raise~1` — `emp_api.pkb:30`

**再設計の状態: 未決定**

- `STATE-001` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- `calls emp_api.validate_pct` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- `calls emp_dept_cap_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `calls emp_salary_audit_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `TRG-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります
- TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**判定したルール**

- `SQL-004` (REVIEW, `semantics.yaml`): SQL%ROWCOUNT を読んでいますが、静的な DML 以外（FORALL・動的 SQL・MERGE・呼び出し先の SQL）が件数を決めうる routine です
- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `STATE-001` (REDESIGN, `state.yaml`): Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります
- `TRG-002` (REDESIGN, `trigger.yaml`): trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**代替案**

- 件数を返す形に呼び出し先を直すか、件数を読む位置を静的な DML の直後に寄せる
- 引数で渡す
- 明示的な SessionContext か永続化に移す
- trigger の処理を、この書き込みをする Service に持たせる（値を書き換える trigger は採番 Service へ）
- 複数行の更新・DELETE・MERGE・複数イベントの trigger は、行ごとに何を渡すかを設計する

**受け入れに必要なテスト**

- all_write_paths_covered
- equivalent_result
- row_count
- state_isolation_between_calls

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `emp_api.give_raise~2` — `emp_api.pkb:36`

**再設計の状態: 未決定**

- `STATE-001` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- `calls emp_api.validate_pct` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- `TRG-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります
- TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**判定したルール**

- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `STATE-001` (REDESIGN, `state.yaml`): Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります
- `TRG-002` (REDESIGN, `trigger.yaml`): trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**代替案**

- 引数で渡す
- 明示的な SessionContext か永続化に移す
- trigger の処理を、この書き込みをする Service に持たせる（値を書き換える trigger は採番 Service へ）
- 複数行の更新・DELETE・MERGE・複数イベントの trigger は、行ごとに何を渡すかを設計する

**受け入れに必要なテスト**

- all_write_paths_covered
- equivalent_result
- state_isolation_between_calls

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `emp_api.hire` — `emp_api.pkb:15`

**再設計の状態: 未決定**

- `STATE-001` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- `TRG-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります
- TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**判定したルール**

- `EXC-001` (REVIEW, `semantics.yaml`): 移行先では自然には起こらない例外の handler があります。Oracle では DB 自身の誤りでも走っていた処理が、移行先では走りません
- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `STATE-001` (REDESIGN, `state.yaml`): Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります
- `TRG-002` (REDESIGN, `trigger.yaml`): trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**代替案**

- DUP_VAL_ON_INDEX は「読んで、無ければ INSERT、あれば UPDATE」か UPSERT に書き直す（ScalarDB は重複 INSERT のあとトランザクションを続けられない）
- INVALID_NUMBER / VALUE_ERROR は、変換の前に値を検査する形に書き直す
- 引数で渡す
- 明示的な SessionContext か永続化に移す
- trigger の処理を、この書き込みをする Service に持たせる（値を書き換える trigger は採番 Service へ）
- 複数行の更新・DELETE・MERGE・複数イベントの trigger は、行ごとに何を渡すかを設計する

**受け入れに必要なテスト**

- all_write_paths_covered
- equivalent_result
- exception_path
- state_isolation_between_calls

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `emp_api.validate_pct` — `emp_api.pkb:7`

**再設計の状態: 決定済み（実 DB では未検証、または相違あり）**

- `STATE-001` は決定済み（limits.yaml: packageState.carried）: g_calls はセッション単位の呼び出し回数で、Singleton の field に置くとプロセス単位になる。 呼び出し側が値を保持して各 routine に渡し、返された値を次の呼び出しに渡す（2026-09-25、利用者の決定）
- 実 DB の比較: まだ無い
- 判定は REDESIGN のまま（AUTO 禁止条件）。同時実行での衝突と再試行など、呼び出し側に残る責務は決定の理由に書いてある

**根拠**

- STATE-001: Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります

**判定したルール**

- `STATE-001` (REDESIGN, `state.yaml`): Package 変数はセッションに紐づく状態です。Singleton bean の field へ置くと意味が変わります

**代替案**

- 引数で渡す
- 明示的な SessionContext か永続化に移す

**受け入れに必要なテスト**

- state_isolation_between_calls

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `emp_biu_trg.body` — `emp_biu_trg.trg:2`

**再設計の状態: 未決定**

- `TRG-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります

**判定したルール**

- `TRG-001` (REDESIGN, `trigger.yaml`): Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります

**代替案**

- validation は Service の検証と DB 制約へ
- audit は interceptor / outbox へ
- before/after 値と失敗時の挙動を保つ

**受け入れに必要なテスト**

- all_write_paths_covered

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `emp_dept_cap_trg.body` — `emp_dept_cap_trg.trg:2`

**再設計の状態: 決定済み（実 DB では未検証、または相違あり）**

- `TRG-001` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- 実 DB の比較: まだ無い
- 判定は REDESIGN のまま（AUTO 禁止条件）。同時実行での衝突と再試行など、呼び出し側に残る責務は決定の理由に書いてある

**根拠**

- TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります

**判定したルール**

- `TRG-001` (REDESIGN, `trigger.yaml`): Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります

**代替案**

- validation は Service の検証と DB 制約へ
- audit は interceptor / outbox へ
- before/after 値と失敗時の挙動を保つ

**受け入れに必要なテスト**

- all_write_paths_covered

**確信度が 0 になっている要因**: testEvidence

## REDESIGN: `emp_dept_upd_v_trg.body` — `emp_dept_upd_v_trg.trg:2`

**再設計の状態: 決定済み（実 DB では未検証、または相違あり）**

- `TRG-001` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- 実 DB の比較: まだ無い
- 判定は REDESIGN のまま（AUTO 禁止条件）。同時実行での衝突と再試行など、呼び出し側に残る責務は決定の理由に書いてある

**根拠**

- TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります

**判定したルール**

- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `TRG-001` (REDESIGN, `trigger.yaml`): Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります

**代替案**

- validation は Service の検証と DB 制約へ
- audit は interceptor / outbox へ
- before/after 値と失敗時の挙動を保つ

**受け入れに必要なテスト**

- all_write_paths_covered
- equivalent_result

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `emp_salary_audit_trg.body` — `emp_salary_audit_trg.trg:2`

**再設計の状態: 決定済み（実 DB では未検証、または相違あり）**

- `TRG-001` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- 実 DB の比較: まだ無い
- 判定は REDESIGN のまま（AUTO 禁止条件）。同時実行での衝突と再試行など、呼び出し側に残る責務は決定の理由に書いてある

**根拠**

- TRG-001: Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります

**判定したルール**

- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `TRG-001` (REDESIGN, `trigger.yaml`): Trigger は隠れた副作用です。全書込経路を Service 側で統制する必要があります

**代替案**

- validation は Service の検証と DB 制約へ
- audit は interceptor / outbox へ
- before/after 値と失敗時の挙動を保つ

**受け入れに必要なテスト**

- all_write_paths_covered
- equivalent_result

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `log_msg` — `log_msg.prc:2`

**再設計の状態: 未決定**

- `TX-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- `TX-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TX-001: routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- TX-002: Autonomous Transaction は親が失敗しても残るという意味を持ちます

**判定したルール**

- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `TX-001` (REDESIGN, `transaction.yaml`): routine 内の COMMIT / ROLLBACK / SAVEPOINT は Service のトランザクション境界へ逐語変換できません
- `TX-002` (REDESIGN, `transaction.yaml`): Autonomous Transaction は親が失敗しても残るという意味を持ちます

**代替案**

- use case の耐久境界で分割する
- 再試行と冪等性の方針を決める
- audit / event / outbox として切り出す
- 親の失敗時に残ることが要件かを確認する

**受け入れに必要なテスト**

- equivalent_result
- parent_rollback_keeps_audit
- partial_failure
- rollback_boundary

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `raise_salary` — `raise_salary.prc:2`

**再設計の状態: 未決定**

- `calls emp_dept_cap_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `calls emp_salary_audit_trg.body` は決定済み（#12: 書き込む側が trigger を呼ぶ。他の書き込み経路の網羅は照合（TriggerChecks）で追う）: docs/plsql-migration/plsql-trigger-patterns.md
- `TRG-002` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- TRG-002: trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**判定したルール**

- `SQL-004` (REVIEW, `semantics.yaml`): SQL%ROWCOUNT を読んでいますが、静的な DML 以外（FORALL・動的 SQL・MERGE・呼び出し先の SQL）が件数を決めうる routine です
- `SQL-001` (REVIEW, `sql.yaml`): ScalarDB SQL で実行できない文があります
- `TRG-002` (REDESIGN, `trigger.yaml`): trigger の掛かる表へ書き込んでいますが、その trigger を呼び出しに置き換えられていません。移行先ではこの書き込みで trigger の処理が走りません

**代替案**

- 件数を返す形に呼び出し先を直すか、件数を読む位置を静的な DML の直後に寄せる
- trigger の処理を、この書き込みをする Service に持たせる（値を書き換える trigger は採番 Service へ）
- 複数行の更新・DELETE・MERGE・複数イベントの trigger は、行ごとに何を渡すかを設計する

**受け入れに必要なテスト**

- all_write_paths_covered
- equivalent_result
- row_count

**確信度が 0 になっている要因**: targetCapability, testEvidence

## REDESIGN: `setup_drop_objects` — `setup_drop_objects.prc:2`

**再設計の状態: 未決定**

- `DYN-001` は**未決定**。下の代替案から決めて、決定を記録する（limits.yaml）
- 実 DB の比較: まだ無い

**根拠**

- DYN-001: 表名など識別子が実行時に決まる SQL です。allowlist か専用 Repository への再設計が要ります

**判定したルール**

- `DYN-001` (REDESIGN, `dynamic_sql.yaml`): 表名など識別子が実行時に決まる SQL です。allowlist か専用 Repository への再設計が要ります
- `DYN-002` (REVIEW, `dynamic_sql.yaml`): 動的 SQL です。とりうる文をすべて展開して ScalarDB がそのまま実行できる、とは確かめられていません。bind の復元と権限の確認が要ります
- `DYN-002` (REVIEW, `dynamic_sql.yaml`): 動的 SQL です。とりうる文をすべて展開して ScalarDB がそのまま実行できる、とは確かめられていません。bind の復元と権限の確認が要ります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります

**代替案**

- allowlist 型の query builder にする
- 有限 variant なら variant ごとに静的な query にする

**受け入れに必要なテスト**

- bind_restoration
- dynamic_sql_variants
- performance
- row_limit

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `annual_comp` — `annual_comp.fnc:2`

**根拠**

- confidence factor testEvidence is 0

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- ルールに必要テストが書かれていない。ルール側に足すべき。

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `b04_1_variables` — `b04_1_variables.prc:2`

**根拠**

- confidence factor testEvidence is 0

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- ルールに必要テストが書かれていない。ルール側に足すべき。

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `b04_2_control_flow` — `b04_2_control_flow.prc:2`

**根拠**

- confidence factor testEvidence is 0

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- ルールに必要テストが書かれていない。ルール側に足すべき。

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `b04_4_1_explicit_cursor` — `b04_4_1_explicit_cursor.prc:2`

**根拠**

- CUR-003: 明示 cursor を先読みの走査に置き換えました。cursor が COMMIT をまたいでいたなら、読む時点が変わります
- CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります

**判定したルール**

- `SCAN-002` (AUTO, `scalardb_capability.yaml`): パーティションキーで絞れない走査です。JDBC バックエンドでは実行できますが、フィルタも順序もパーティションをまたぐため、JDBC 以外（Cassandra など）では同じ問い合わせが通りません。そこへ移すときは、キーで届く読み取りに直す必要があります
- `CUR-003` (REVIEW, `semantics.yaml`): 明示 cursor を先読みの走査に置き換えました。cursor が COMMIT をまたいでいたなら、読む時点が変わります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります

**代替案**

- キーで届く読み取りに変える
- 全行を取得してアプリ側で絞る・並べる（docs/design/app-side-processing-plan.md）

**受け入れに必要なテスト**

- cross_partition_scan
- cursor_lifetime
- performance
- row_limit

**確信度が 0 になっている要因**: symbolResolution, typeResolution, testEvidence

## REVIEW: `b04_4_2_cursor_for_loop` — `b04_4_2_cursor_for_loop.prc:2`

**根拠**

- CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- SQL-002: 実行計画（取得 + H2）に分解される文です。行数上限と性能を確認してください

**判定したルール**

- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `SQL-002` (REVIEW, `sql.yaml`): 実行計画（取得 + H2）に分解される文です。行数上限と性能を確認してください

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- performance
- row_limit

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `b04_4_4_ref_cursor` — `b04_4_4_ref_cursor.prc:2`

**根拠**

- CUR-003: 明示 cursor を先読みの走査に置き換えました。cursor が COMMIT をまたいでいたなら、読む時点が変わります
- CUR-003: 明示 cursor を先読みの走査に置き換えました。cursor が COMMIT をまたいでいたなら、読む時点が変わります
- CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります

**判定したルール**

- `SCAN-002` (AUTO, `scalardb_capability.yaml`): パーティションキーで絞れない走査です。JDBC バックエンドでは実行できますが、フィルタも順序もパーティションをまたぐため、JDBC 以外（Cassandra など）では同じ問い合わせが通りません。そこへ移すときは、キーで届く読み取りに直す必要があります
- `SCAN-002` (AUTO, `scalardb_capability.yaml`): パーティションキーで絞れない走査です。JDBC バックエンドでは実行できますが、フィルタも順序もパーティションをまたぐため、JDBC 以外（Cassandra など）では同じ問い合わせが通りません。そこへ移すときは、キーで届く読み取りに直す必要があります
- `CUR-003` (REVIEW, `semantics.yaml`): 明示 cursor を先読みの走査に置き換えました。cursor が COMMIT をまたいでいたなら、読む時点が変わります
- `CUR-003` (REVIEW, `semantics.yaml`): 明示 cursor を先読みの走査に置き換えました。cursor が COMMIT をまたいでいたなら、読む時点が変わります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります

**代替案**

- キーで届く読み取りに変える
- 全行を取得してアプリ側で絞る・並べる（docs/design/app-side-processing-plan.md）
- キーで届く読み取りに変える
- 全行を取得してアプリ側で絞る・並べる（docs/design/app-side-processing-plan.md）

**受け入れに必要なテスト**

- cross_partition_scan
- cursor_lifetime
- performance
- row_limit

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `b04_5_records_collections` — `b04_5_records_collections.prc:2`

**根拠**

- CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません
- CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります

**判定したルール**

- `CALL-001` (REVIEW, `lowering.yaml`): 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります

**代替案**

- 呼び先のソースを解析対象に加える
- 加えられない（Oracle 提供のパッケージなど）なら、移行先での代替を決める

**受け入れに必要なテスト**

- equivalent_result
- performance
- row_limit

**確信度が 0 になっている要因**: symbolResolution, testEvidence

## REVIEW: `b04_6_1_predefined_exceptions` — `b04_6_1_predefined_exceptions.prc:2`

**根拠**

- confidence factor testEvidence is 0

**判定したルール**

- `SELECT-OPT-001` (AUTO, `scalardb_capability.yaml`): この SELECT INTO はキーまたは一意制約による直接取得ではありません。意味論は生成コードによって維持されます（最大 2 件を読み、0 件は NO_DATA_FOUND、2 件以上は TOO_MANY_ROWS）。業務上一意である場合は、キーまたは一意制約としてデータモデルに明示することを推奨します

**代替案**

- 業務上一意なら、その列をキーまたは一意制約としてデータモデルに明示する
- 複数件から特定の 1 件を選ぶ形に変えるのは、別の仕様変更として扱う（LIMIT 1 や ORDER BY を足して済ませない）

**受け入れに必要なテスト**

- ルールに必要テストが書かれていない。ルール側に足すべき。

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `b06_1_bulk_collect_limit` — `b06_1_bulk_collect_limit.prc:2`

**根拠**

- CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- BULK-003: 分割読みは行をまとめて読む形になりました。メモリを守るのは LIMIT ではなく走査行数の上限です

**判定したルール**

- `SCAN-002` (AUTO, `scalardb_capability.yaml`): パーティションキーで絞れない走査です。JDBC バックエンドでは実行できますが、フィルタも順序もパーティションをまたぐため、JDBC 以外（Cassandra など）では同じ問い合わせが通りません。そこへ移すときは、キーで届く読み取りに直す必要があります
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `BULK-003` (REVIEW, `semantics.yaml`): 分割読みは行をまとめて読む形になりました。メモリを守るのは LIMIT ではなく走査行数の上限です

**代替案**

- キーで届く読み取りに変える
- 全行を取得してアプリ側で絞る・並べる（docs/design/app-side-processing-plan.md）

**受け入れに必要なテスト**

- cross_partition_scan
- performance
- row_limit

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `b06_4_collection_in_sql` — `b06_4_collection_in_sql.prc:2`

**根拠**

- SELECT-001: キーで届かない SELECT INTO で、ScalarDB がそのまま実行できる文ではありません。0 件と複数件の意味（NO_DATA_FOUND / TOO_MANY_ROWS）が生成コードで保たれることを保証できません
- SEM-004: 集約の SELECT INTO で、ScalarDB がそのまま実行できる文ではありません。集約は 0 件でも 1 行返り、NO_DATA_FOUND にならず NULL になる、という差が保たれることを保証できません
- SQL-002: 実行計画（取得 + H2）に分解される文です。行数上限と性能を確認してください
- SQL-002: 実行計画（取得 + H2）に分解される文です。行数上限と性能を確認してください
- BULK-001: BULK COLLECT は行数上限とメモリ上限が要ります

**判定したルール**

- `SELECT-001` (REVIEW, `scalardb_capability.yaml`): キーで届かない SELECT INTO で、ScalarDB がそのまま実行できる文ではありません。0 件と複数件の意味（NO_DATA_FOUND / TOO_MANY_ROWS）が生成コードで保たれることを保証できません
- `SEM-004` (REVIEW, `semantics.yaml`): 集約の SELECT INTO で、ScalarDB がそのまま実行できる文ではありません。集約は 0 件でも 1 行返り、NO_DATA_FOUND にならず NULL になる、という差が保たれることを保証できません
- `SQL-002` (REVIEW, `sql.yaml`): 実行計画（取得 + H2）に分解される文です。行数上限と性能を確認してください
- `SQL-002` (REVIEW, `sql.yaml`): 実行計画（取得 + H2）に分解される文です。行数上限と性能を確認してください
- `BULK-001` (REVIEW, `sql.yaml`): BULK COLLECT は行数上限とメモリ上限が要ります

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- aggregate_zero_rows
- no_data_found
- performance
- row_limit
- too_many_rows

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `b06_5_builtin_packages` — `b06_5_builtin_packages.prc:2`

**根拠**

- CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません

**判定したルール**

- `CALL-001` (REVIEW, `lowering.yaml`): 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません

**代替案**

- 呼び先のソースを解析対象に加える
- 加えられない（Oracle 提供のパッケージなど）なら、移行先での代替を決める

**受け入れに必要なテスト**

- equivalent_result

**確信度が 0 になっている要因**: symbolResolution, testEvidence

## REVIEW: `b06_6_conditional_compilation` — `b06_6_conditional_compilation.prc:2`

**根拠**

- confidence factor testEvidence is 0

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- ルールに必要テストが書かれていない。ルール側に足すべき。

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `dept_name_of` — `dept_name_of.fnc:2`

**根拠**

- confidence factor testEvidence is 0

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- ルールに必要テストが書かれていない。ルール側に足すべき。

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `dml_d_create_error_log` — `dml_d_create_error_log.prc:2`

**根拠**

- CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません

**判定したルール**

- `CALL-001` (REVIEW, `lowering.yaml`): 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません

**代替案**

- 呼び先のソースを解析対象に加える
- 加えられない（Oracle 提供のパッケージなど）なら、移行先での代替を決める

**受け入れに必要なテスト**

- equivalent_result

**確信度が 0 になっている要因**: symbolResolution, testEvidence

## REVIEW: `emp_grades` — `emp_grades.fnc:2`

**根拠**

- LOWER-001: lowering がまだ模していない構文です。意味が保てる保証がありません
- CUR-002: Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- SQL-002: 実行計画（取得 + H2）に分解される文です。行数上限と性能を確認してください

**判定したルール**

- `LOWER-001` (REVIEW, `lowering.yaml`): lowering がまだ模していない構文です。意味が保てる保証がありません
- `CUR-002` (REVIEW, `semantics.yaml`): Cursor FOR LOOP です。走査する行数の上限が決まっていません（limits.yaml）。N+1 とメモリ、fetch size を確認する必要があります
- `SQL-002` (REVIEW, `sql.yaml`): 実行計画（取得 + H2）に分解される文です。行数上限と性能を確認してください

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- equivalent_result
- performance
- row_limit

**確信度が 0 になっている要因**: ruleCoverage, testEvidence

## REVIEW: `normalize_name` — `normalize_name.prc:2`

**根拠**

- confidence factor testEvidence is 0

**代替案**

- ルールに代替案が書かれていない。ルール側に足すべき。

**受け入れに必要なテスト**

- ルールに必要テストが書かれていない。ルール側に足すべき。

**確信度が 0 になっている要因**: testEvidence

## REVIEW: `setup_gather_stats` — `setup_gather_stats.prc:2`

**根拠**

- CALL-001: 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません

**判定したルール**

- `CALL-001` (REVIEW, `lowering.yaml`): 解析した範囲に無い routine を呼んでいます。呼び先が COMMIT するか、外へ何かを送るか、ロックを取るかは分かりません

**代替案**

- 呼び先のソースを解析対象に加える
- 加えられない（Oracle 提供のパッケージなど）なら、移行先での代替を決める

**受け入れに必要なテスト**

- equivalent_result

**確信度が 0 になっている要因**: symbolResolution, testEvidence

