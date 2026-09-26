"""実 DB で比べる用の小さいプロジェクト plsql-run/ を、plsql/ から作る。

plsql/src には 05・06 の全ユニットと 04 の無名ブロックが入っていて、生成した Java の一部（package 変数、collection、
trigger、FORALL RETURNING）はまだコンパイルできない。Gradle は木ごとコンパイルするので、その routine が 1 つでもあると
capture が動かない。ここでは **コンパイルできるユニットだけ**を plsql-run/src に写し、シナリオを起こす。
判定と生成物の一覧は plsql/（全ユニット）の側で見る。

python3 samples/oracle-samples/make_runnable.py で作り直せる。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
FULL = HERE / "plsql" / "src"
RUN = HERE / "plsql-run"

# 2026-09-24 の --verify-compile で javac が落ちた routine を持つユニットを外していたが、#40（2026-09-25）で
# 判定によらず生成物がコンパイルできるようになったので、いまは全ユニットを写す
EXCLUDED: set[str] = set()

SYSDATE = "2026-09-24 09:30:00"


def seeds() -> list[str]:
    data = json.loads((HERE / "sql" / "queries-check.data.json").read_text(encoding="utf-8"))
    out = []
    for table in ("jobs", "departments", "employees"):
        for row in data[table]:
            cols = ", ".join(row)
            vals = []
            for k, v in row.items():
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, str):
                    vals.append(f"DATE '{v}'" if k.endswith("_date") else "'" + v.replace("'", "''") + "'")
                else:
                    vals.append(str(v))
            out.append(f"INSERT INTO {table} ({cols}) VALUES ({', '.join(vals)})")
    return out


def scenario(name: str, unit: str, kind: str, note: str, *, routine: str | None = None, args: dict | None = None,
             out: dict | None = None, returns: str | None = None, extra_setup: list[str] = (),
             mask: dict | None = None, capture: list[str] | None = None, name_override: str | None = None,
             boundary: str | None = None) -> dict:
    call = {"kind": kind, "name": name_override or unit}
    if args:
        call["args"] = args
    if out:
        call["out"] = out
    if returns:
        call["returns"] = returns
    s = {"name": name, "unit": unit, "routine": routine or unit, "pinned": {"sysdate": SYSDATE},
         "setup": seeds() + list(extra_setup), "call": call, "capture_tables": capture or ["employees", "emp_audit"], "note": note}
    if mask:
        s["mask"] = mask
    if boundary:
        # COMMIT / ROLLBACK を呼び出し側の境界に移した routine（limits.yaml transactions.callerBoundary）は、
        # 元の routine が自分でしていた終わり方をハーネス（＝呼び出し側）が再現する。Oracle 側は原文を動かすので無視する
        s["boundary"] = boundary
    return s


ROLL_NOTE = "呼び出し側（ハーネス）が ROLLBACK する（callerBoundary の決定）"

SCENARIOS = [
    scenario("annual_comp_with_comm", "annual_comp", "function", "年収 = 10000 × 12 × 1.3 = 156000",
             args={"p_salary": 10000, "p_comm": 0.3}, returns="NUMBER"),
    scenario("annual_comp_null_comm", "annual_comp", "function", "p_comm 省略（DEFAULT NULL → NVL で 0）: 72000",
             args={"p_salary": 6000}, returns="NUMBER"),   # p_comm は省く: DEFAULT NULL を plsql_setup.py が埋める（#41）
    scenario("dept_name_of_ok", "dept_name_of", "function", "部門 60 → 'IT'", args={"p_dept_id": 60}, returns="VARCHAR2"),
    scenario("dept_name_of_missing", "dept_name_of", "function", "部門なし: NO_DATA_FOUND を NULL に言い換える",
             args={"p_dept_id": 42}, returns="VARCHAR2"),
    scenario("normalize_name_ok", "normalize_name", "procedure", "IN OUT: '  john SMITH ' → 'John Smith'",
             args={"p_name": "  john SMITH "}, out={"p_name": "VARCHAR2"}),
    scenario("raise_salary_ok", "raise_salary", "procedure",
             "社員 104 を 10% 昇給: 6000 → 6600（RETURNING で受け取る）。trigger が emp_audit に書く changed_at は固定できないので mask",
             args={"p_emp_id": 104, "p_pct": 10}, out={"p_new_sal": "NUMBER"}, mask={"emp_audit": ["changed_at"]}),
    scenario("raise_salary_missing", "raise_salary", "procedure", "社員なし: SQL%ROWCOUNT = 0 → -20010",
             args={"p_emp_id": 999, "p_pct": 10}, out={"p_new_sal": "NUMBER"}),
    scenario("log_msg_ok", "log_msg", "procedure", "自律型トランザクションで emp_audit に 1 行（changed_at / changed_by は固定できないので mask）",
             args={"p_msg": "hello"}, mask={"emp_audit": ["changed_at", "changed_by"]}),
    scenario("b04_1_variables", "b04_1_variables", "procedure", "04-1: %TYPE / %ROWTYPE / 定数。読むだけ"),
    scenario("b04_2_control_flow", "b04_2_control_flow", "procedure", "04-2: IF / CASE / LOOP / WHILE / FOR REVERSE / ラベル。読むだけ"),
    scenario("b04_3_implicit_cursor_attrs", "b04_3_implicit_cursor_attrs", "procedure",
             "04-3: UPDATE → SQL%ROWCOUNT → DELETE → SQL%NOTFOUND → ROLLBACK。表は元に戻る。呼び出し側（ハーネス）が ROLLBACK する（callerBoundary の決定）", boundary="rollback"),
    scenario("b04_4_1_explicit_cursor", "b04_4_1_explicit_cursor", "procedure", "04-4-1: OPEN / FETCH / CLOSE。読むだけ"),
    scenario("b04_4_2_cursor_for_loop", "b04_4_2_cursor_for_loop", "procedure", "04-4-2: パラメータ付き cursor FOR ループ。読むだけ"),
    scenario("b04_4_3_for_update_current_of", "b04_4_3_for_update_current_of", "procedure",
             "04-4-3: FOR UPDATE + WHERE CURRENT OF で更新して ROLLBACK。表は元に戻る。呼び出し側（ハーネス）が ROLLBACK する（callerBoundary の決定）", boundary="rollback"),
    scenario("b04_4_4_ref_cursor", "b04_4_4_ref_cursor", "procedure", "04-4-4: SYS_REFCURSOR。読むだけ"),
    scenario("b04_6_1_predefined_exceptions", "b04_6_1_predefined_exceptions", "procedure",
             "04-6-1: NO_DATA_FOUND / TOO_MANY_ROWS / ZERO_DIVIDE を捕捉。正常終了"),
    scenario("b04_6_2_user_exceptions", "b04_6_2_user_exceptions", "procedure",
             "04-6-2: ユーザ定義例外・EXCEPTION_INIT(-2291)・RAISE_APPLICATION_ERROR を WHEN OTHERS で捕捉して ROLLBACK。正常終了。呼び出し側（ハーネス）が ROLLBACK する（callerBoundary の決定）", boundary="rollback"),
    scenario("b06_1_bulk_collect_limit", "b06_1_bulk_collect_limit", "procedure", "06-1: BULK COLLECT LIMIT 5。読むだけ"),
    scenario("b06_2_forall_save_exceptions", "b06_2_forall_save_exceptions", "procedure",
             "06-2: FORALL SAVE EXCEPTIONS で bulk_target へ。salary >= 15000 の 4 行が CHECK 違反、11 行が入る",
             extra_setup=["DELETE FROM bulk_target"], capture=["employees", "emp_audit", "bulk_target"]),
    scenario("b06_3_native_dynamic_sql", "b06_3_native_dynamic_sql", "procedure", "06-3: EXECUTE IMMEDIATE / OPEN FOR / 動的 PL/SQL。最後に ROLLBACK。呼び出し側（ハーネス）が ROLLBACK する（callerBoundary の決定）", boundary="rollback"),
    scenario("b06_3_6_dbms_sql", "b06_3_6_dbms_sql", "procedure", "06-3-6: DBMS_SQL で列数不定の問合せ。読むだけ"),
    scenario("b06_4_collection_in_sql", "b06_4_collection_in_sql", "procedure", "06-4: BULK COLLECT INTO オブジェクト型の表、TABLE() で SQL から数える"),
    scenario("b06_5_builtin_packages", "b06_5_builtin_packages", "procedure", "06-5: DBMS_APPLICATION_INFO / DBMS_SESSION.SLEEP / DBMS_RANDOM / DBMS_UTILITY"),
    scenario("b06_6_conditional_compilation", "b06_6_conditional_compilation", "procedure", "06-6: $IF DBMS_DB_VERSION.VERSION >= 23"),
    # 2026-09-25（#40 のあと）: 前は生成物がコンパイルできず外していたもの
    scenario("b04_5_records_collections", "b04_5_records_collections", "procedure", "04-5: レコード・連想配列・ネスト表・VARRAY。読むだけ"),
    scenario("b05_1_call_raise_salary", "b05_1_call_raise_salary", "procedure",
             "05-1: raise_salary を位置指定と名前指定で呼び、ROLLBACK。表は元に戻る（Oracle では trigger が emp_audit に書く）。呼び出し側（ハーネス）が ROLLBACK する（callerBoundary の決定）", boundary="rollback"),
    scenario("b05_3_call_emp_api", "b05_3_call_emp_api", "procedure",
             "05-3: emp_api.hire / give_raise（オーバーロード）/ call_count、ROLLBACK。" + ROLL_NOTE, boundary="rollback"),
    scenario("b05_4_call_log_msg", "b05_4_call_log_msg", "procedure",
             "05-4: 自律型トランザクションの log_msg を呼んで ROLLBACK。emp_audit の LOG 行だけ残る。" + ROLL_NOTE,
             mask={"emp_audit": ["changed_at", "changed_by"]}, boundary="rollback"),
    scenario("b06_2_2_forall_returning", "b06_2_2_forall_returning", "procedure", "06-2-2: FORALL … RETURNING BULK COLLECT INTO、SQL%BULK_ROWCOUNT、ROLLBACK。呼び出し側（ハーネス）が ROLLBACK する（callerBoundary の決定）", boundary="rollback"),
    scenario("emp_api_give_raise_ok", "emp_api", "procedure", "emp_api.give_raise(104, 10): 6000 → 6600（validate_pct が g_calls を数える）",
             routine="give_raise~1", args={"p_emp_id": 104, "p_pct": 10}, name_override="emp_api.give_raise",
             mask={"emp_audit": ["changed_at", "changed_by"]}),
    scenario("emp_api_give_raise_invalid", "emp_api", "procedure", "emp_api.give_raise(104, 50): e_invalid_raise（ユーザ定義例外、ORA-06510）",
             routine="give_raise~1", args={"p_emp_id": 104, "p_pct": 50}, name_override="emp_api.give_raise"),
]


def main() -> None:
    # src/ と scenarios/ だけを作り直す。golden/（Oracle の capture）と work/（生成物と ScalarDB の capture）は
    # 取り直すのに時間がかかるので残す（2026-09-25: 消してしまい、比較が 0 件になった）
    for sub in ("src", "scenarios"):
        if (RUN / sub).exists():
            shutil.rmtree(RUN / sub)
    (RUN / "src" / "blocks").mkdir(parents=True)
    (RUN / "scenarios").mkdir()
    copied = []
    for p in sorted(FULL.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(FULL).as_posix()
        if rel in EXCLUDED:
            continue
        shutil.copy(p, RUN / "src" / rel)
        copied.append(rel)
    # bulk_target は 06-2 が書く表。plsql/src/schema.sql に入っている
    shutil.copy(HERE / "scalardb-schema.json", RUN / "scalardb-schema.json")
    if (HERE / "plsql" / "limits.yaml").exists():
        shutil.copy(HERE / "plsql" / "limits.yaml", RUN / "limits.yaml")   # the project's decisions (#46)
    for s in SCENARIOS:
        (RUN / "scenarios" / f"{s['name']}.yaml").write_text(
            yaml.safe_dump(s, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    (RUN / "README.md").write_text(
        "# plsql-run/ — 実 DB で比べる用の部分集合\n\n"
        "`make_runnable.py` が `plsql/src` からコンパイルできるユニットだけを写し、シナリオを起こしたもの。手で直さない。\n\n"
        f"- 写したユニット: {len(copied)}（除いたもの: {len(EXCLUDED)}。理由は `make_runnable.py` の EXCLUDED）\n"
        f"- シナリオ: {len(SCENARIOS)}\n"
        "- `work/`（golden の写し、生成物、capture、比較）は git に入れない\n", encoding="utf-8")
    print(f"copied {len(copied)} units, {len(SCENARIOS)} scenarios -> {RUN}")


if __name__ == "__main__":
    main()
