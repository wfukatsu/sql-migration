# PL/SQL 移行 インベントリ（Phase 1）

- 解析対象: 36 モジュール / 41 routine / 317 文
- DDL スナップショット: `schema.sql@d6f64025`
- 診断: 250 件（うち ERROR 7 件）

> この数値は**合成 corpus 上の値**であり、実案件の PL/SQL に対する耐性を示すものではない（実装計画 §9、docs/design/plsql-kpi.md §0）。

## KPI

| KPI | 値 | 目標 |
|---|---|---|
| parse 率 | 100.0%（37/37） | Phase 1 で 90% 以上 |
| 型解決率 | 99.2%（132/133） | Phase 1 で 95% 以上 |

## AUTO を妨げる条件が既に見えている routine

22 / 41 routine。判定そのものは P2-2 のルールが行う。ここは IR に既にある証拠を並べただけである。

| routine | 条件 |
|---|---|
| `b04_3_implicit_cursor_attrs` | transaction-control-in-routine |
| `b04_4_3_for_update_current_of` | transaction-control-in-routine, row-lock |
| `b04_6_2_user_exceptions` | transaction-control-in-routine |
| `b05_1_call_raise_salary` | transaction-control-in-routine |
| `b05_3_call_emp_api` | transaction-control-in-routine |
| `b05_4_call_log_msg` | transaction-control-in-routine |
| `b06_2_2_forall_returning` | transaction-control-in-routine |
| `b06_2_forall_save_exceptions` | transaction-control-in-routine |
| `b06_3_native_dynamic_sql` | transaction-control-in-routine, dynamic-sql, unmodelled-construct |
| `setup_drop_objects` | dynamic-sql |
| `emp_api.validate_pct` | package-state |
| `emp_api.hire` | package-state |
| `emp_api.give_raise~1` | package-state |
| `emp_api.give_raise~2` | package-state |
| `emp_api.get_by_dept` | package-state |
| `emp_api.call_count` | package-state |
| `emp_biu_trg.body` | trigger |
| `emp_dept_cap_trg.body` | trigger |
| `emp_dept_upd_v_trg.body` | trigger |
| `emp_grades` | unmodelled-construct |
| `emp_salary_audit_trg.body` | trigger |
| `log_msg` | transaction-control-in-routine, autonomous-transaction |

## 未解決

- **ERROR** `ORDER` main query: ORDER BY expression 1 -- sort in the application（b04_4_2_cursor_for_loop.prc:12-14）
- **ERROR** `SCAN_AFTER_WRITE` employees was written earlier in this transaction; ScalarDB refuses to scan it（b05_3_call_emp_api.prc:8）
- **ERROR** `SQL_PARSE` ParseError: Invalid expression / Unexpected token. Line 3, Col: 38.
  PDATE employees SET salary = salary + 10
    WHERE  department_id = v_depts(i)
    RETURNING salary [4mBULK COLLECT INTO[0m v_new_sal（b06_2_2_forall_returning.prc:8-10）
- **ERROR** `PROJECTION` main query: expressions in the select list (EMP_GRADE_T(employee_id, last_name, 'X')) -- compute them in the application（b06_4_collection_in_sql.prc:6-8）
- **ERROR** `UNSUPPORTED` function TABLE is not supported by ScalarDB SQL（b06_4_collection_in_sql.prc:10）
- **ERROR** `EXPR` SET last_name: only literals and bind markers are allowed, got ':NEW.last_name'（emp_dept_upd_v_trg.trg:6-10）
- **ERROR** `PRED` WHERE: IS is only supported as IS [NOT] NULL on a column（emp_grades.fnc:6-11）
- **WARN** `UNRESOLVED_TYPE` c_emp%ROWTYPE: no table c_emp in the DDL snapshot（b04_4_1_explicit_cursor.prc:1-16）
- **WARN** `UNRESOLVED_TYPE` c%ROWTYPE: no table c in the DDL snapshot（b06_1_bulk_collect_limit.prc:1-18）

## 資産

| モジュール | 種別 | routine | 文 | package 状態 |
|---|---|---|---|---|
| `annual_comp` | function | 1 | 1 | — |
| `b04_1_variables` | procedure | 1 | 6 | — |
| `b04_2_control_flow` | procedure | 1 | 22 | — |
| `b04_3_implicit_cursor_attrs` | procedure | 1 | 22 | — |
| `b04_4_1_explicit_cursor` | procedure | 1 | 4 | — |
| `b04_4_2_cursor_for_loop` | procedure | 1 | 6 | — |
| `b04_4_3_for_update_current_of` | procedure | 1 | 16 | — |
| `b04_4_4_ref_cursor` | procedure | 1 | 9 | — |
| `b04_5_records_collections` | procedure | 1 | 19 | — |
| `b04_6_1_predefined_exceptions` | procedure | 1 | 9 | — |
| `b04_6_2_user_exceptions` | procedure | 1 | 20 | — |
| `b05_1_call_raise_salary` | procedure | 1 | 5 | — |
| `b05_3_call_emp_api` | procedure | 1 | 10 | — |
| `b05_4_call_log_msg` | procedure | 1 | 15 | — |
| `b06_1_bulk_collect_limit` | procedure | 1 | 6 | — |
| `b06_2_2_forall_returning` | procedure | 1 | 6 | — |
| `b06_2_forall_save_exceptions` | procedure | 1 | 10 | — |
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
| `emp_api` | package | 6 | 49 | あり |
| `emp_biu_trg` | trigger | 1 | 3 | — |
| `emp_dept_cap_trg` | trigger | 1 | 2 | — |
| `emp_dept_upd_v_trg` | trigger | 1 | 1 | — |
| `emp_grades` | function | 1 | 4 | — |
| `emp_salary_audit_trg` | trigger | 1 | 4 | — |
| `log_msg` | procedure | 1 | 2 | — |
| `normalize_name` | procedure | 1 | 1 | — |
| `raise_salary` | procedure | 1 | 16 | — |

## 文の内訳

| 種別 | 件数 |
|---|---|
| Call | 94 |
| SqlOperation | 64 |
| If | 43 |
| Assignment | 29 |
| Loop | 28 |
| Raise | 19 |
| Rollback | 8 |
| DynamicSql | 7 |
| Return | 6 |
| Block | 6 |
| Exit | 4 |
| Commit | 3 |
| Unsupported | 2 |
| Case | 1 |
| Continue | 1 |
| Fetch | 1 |
| CloseCursor | 1 |
