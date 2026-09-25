# PL/SQL 移行 インベントリ（Phase 1）

- 解析対象: 24 モジュール / 24 routine / 140 文
- DDL スナップショット: `schema.sql@d6f64025`
- 診断: 76 件（うち ERROR 7 件）

> この数値は**合成 corpus 上の値**であり、実案件の PL/SQL に対する耐性を示すものではない（実装計画 §9、docs/design/plsql-kpi.md §0）。

## KPI

| KPI | 値 | 目標 |
|---|---|---|
| parse 率 | 100.0%（24/24） | Phase 1 で 90% 以上 |
| 型解決率 | 97.7%（43/44） | Phase 1 で 95% 以上 |

## AUTO を妨げる条件が既に見えている routine

7 / 24 routine。判定そのものは P2-2 のルールが行う。ここは IR に既にある証拠を並べただけである。

| routine | 条件 |
|---|---|
| `b04_3_implicit_cursor_attrs` | transaction-control-in-routine |
| `b04_4_3_for_update_current_of` | transaction-control-in-routine, row-lock |
| `b04_6_2_user_exceptions` | transaction-control-in-routine |
| `b06_2_forall_save_exceptions` | transaction-control-in-routine |
| `b06_3_native_dynamic_sql` | transaction-control-in-routine, dynamic-sql, unmodelled-construct |
| `setup_drop_objects` | dynamic-sql |
| `log_msg` | transaction-control-in-routine, autonomous-transaction |

## 未解決

- **ERROR** `RMW` SET salary = salary + 100: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction（b04_3_implicit_cursor_attrs.prc:4）
- **ERROR** `ORDER` main query: ORDER BY expression 1 -- sort in the application（b04_4_2_cursor_for_loop.prc:12-14）
- **ERROR** `EXPR` SET salary: only literals and bind markers are allowed, got ':r_salary * 1.1'（b04_4_3_for_update_current_of.prc:9）
- **ERROR** `PROJECTION` main query: expressions in the select list (EMP_GRADE_T(employee_id, last_name, 'X')) -- compute them in the application（b06_4_collection_in_sql.prc:6-8）
- **ERROR** `UNSUPPORTED` function TABLE is not supported by ScalarDB SQL（b06_4_collection_in_sql.prc:10）
- **ERROR** `PK` INSERT must specify the full primary key; missing ['audit_id']（log_msg.prc:5）
- **ERROR** `RETURNING` RETURNING is not supported（raise_salary.prc:8-11）
- **WARN** `UNRESOLVED_TYPE` c_emp%ROWTYPE: no table c_emp in the DDL snapshot（b04_4_1_explicit_cursor.prc:1-16）
- **WARN** `UNRESOLVED_TYPE` c%ROWTYPE: no table c in the DDL snapshot（b06_1_bulk_collect_limit.prc:1-18）

## 資産

| モジュール | 種別 | routine | 文 | package 状態 |
|---|---|---|---|---|
| `annual_comp` | function | 1 | 1 | — |
| `b04_1_variables` | procedure | 1 | 6 | — |
| `b04_2_control_flow` | procedure | 1 | 22 | — |
| `b04_3_implicit_cursor_attrs` | procedure | 1 | 6 | — |
| `b04_4_1_explicit_cursor` | procedure | 1 | 6 | — |
| `b04_4_2_cursor_for_loop` | procedure | 1 | 6 | — |
| `b04_4_3_for_update_current_of` | procedure | 1 | 4 | — |
| `b04_6_1_predefined_exceptions` | procedure | 1 | 9 | — |
| `b04_6_2_user_exceptions` | procedure | 1 | 11 | — |
| `b06_1_bulk_collect_limit` | procedure | 1 | 6 | — |
| `b06_2_forall_save_exceptions` | procedure | 1 | 8 | — |
| `b06_3_6_dbms_sql` | procedure | 1 | 11 | — |
| `b06_3_native_dynamic_sql` | procedure | 1 | 16 | — |
| `b06_4_collection_in_sql` | procedure | 1 | 3 | — |
| `b06_5_2_scheduler_job` | procedure | 1 | 1 | — |
| `b06_5_builtin_packages` | procedure | 1 | 5 | — |
| `b06_6_conditional_compilation` | procedure | 1 | 2 | — |
| `dml_d_create_error_log` | procedure | 1 | 1 | — |
| `setup_drop_objects` | procedure | 1 | 6 | — |
| `setup_gather_stats` | procedure | 1 | 1 | — |
| `dept_name_of` | function | 1 | 3 | — |
| `log_msg` | procedure | 1 | 2 | — |
| `normalize_name` | procedure | 1 | 1 | — |
| `raise_salary` | procedure | 1 | 3 | — |

## 文の内訳

| 種別 | 件数 |
|---|---|
| Call | 48 |
| SqlOperation | 22 |
| Loop | 18 |
| Assignment | 9 |
| DynamicSql | 7 |
| If | 5 |
| Exit | 5 |
| Block | 5 |
| Rollback | 4 |
| Return | 3 |
| Raise | 3 |
| Commit | 3 |
| Fetch | 2 |
| CloseCursor | 2 |
| Case | 1 |
| Continue | 1 |
| OpenCursor | 1 |
| Unsupported | 1 |
