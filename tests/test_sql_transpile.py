"""sql-transpile スキル (skills/sql-transpile/) のテスト。

**スキルは常に別プロセスで動かす。** スキルは scalardb_migrate/ のコピーを
skills/sql-transpile/scripts/_scalardb/ に同梱している。両者はどちらも SQLGlot の
グローバルな方言表に "scalardb" という名前で方言を登録するため、同じプロセスで両方を
import すると後勝ちで上書きされ、先に読んだ側の UPSERT 生成が壊れる。
pytest は全テストファイルを 1 プロセスで動かすので、このファイルがスキルを import すると
test_converter.py の UPSERT テストを巻き込む。そこで結果は JSON で受け取る。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sql-transpile" / "scripts"
CLI = SCRIPTS / "transpile.py"

PRELUDE = (f"import sys, json, logging; sys.path.insert(0, {str(SCRIPTS)!r}); "
           "logging.getLogger('sqlglot').setLevel(logging.ERROR); import generic, report\n")


def _run(code: str):
    """スキルのモジュールを別プロセスで動かし、最終行の JSON を受け取る。"""
    p = subprocess.run([sys.executable, "-c", PRELUDE + code], capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def convert(sql: str, source: str = "oracle", target: str = "postgres", ddl: str = "",
            case_insensitive: bool = False) -> dict:
    """1 文を変換する。ddl を渡すと、CLI と同じくスクリプト内の CREATE TABLE を型の判定に使う。"""
    return _run(
        f"schema = generic.schema_from_ddl([{ddl!r}], {source!r}) if {ddl!r} else None\n"
        f"r = generic.convert_statement({sql!r}, {source!r}, {target!r}, schema, {case_insensitive!r})\n"
        "print(json.dumps({'status': r.status, 'sql': r.converted[0] if r.converted else '',"
        " 'codes': sorted({i.code for i in r.issues}), 'all_codes': [i.code for i in r.issues],"
        " 'sev': {i.code: i.severity for i in r.issues}}))")


# ---- 前処理: SQLGlot が直さない構文を直す -------------------------------------------------

def test_rownum_becomes_limit():
    r = convert("SELECT ename FROM emp WHERE ROWNUM <= 5")
    assert r["status"] == "OK" and r["sql"] == "SELECT ename FROM emp LIMIT 5"


def test_rownum_strict_less_than_is_one_fewer():
    assert "LIMIT 4" in convert("SELECT ename FROM emp WHERE ROWNUM < 5")["sql"]


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM emp WHERE ROWNUM <= 5",
    "SELECT DISTINCT deptno FROM emp WHERE ROWNUM <= 5",
    "SELECT deptno, COUNT(*) FROM emp WHERE ROWNUM <= 10 GROUP BY deptno",
    "SELECT ename, ROW_NUMBER() OVER (ORDER BY sal) FROM emp WHERE ROWNUM <= 5",
    "SELECT ename FROM emp WHERE ROWNUM <= 2.5",
])
def test_rownum_is_not_a_limit_where_it_counts_something_else(sql):
    # ROWNUM は入力の行を、LIMIT は出力の行を数える。COUNT(*) ... LIMIT 5 は全件を数えてしまう
    r = convert(sql)
    assert r["status"] == "ERROR" and r["sev"]["ROWNUM"] == "ERROR"


def test_rownum_in_a_union_branch_gets_parentheses():
    r = convert("SELECT * FROM emp WHERE ROWNUM <= 5 UNION ALL SELECT * FROM emp2 WHERE ROWNUM <= 5")
    assert r["status"] == "OK"
    assert r["sql"] == "(SELECT * FROM emp LIMIT 5) UNION ALL (SELECT * FROM emp2 LIMIT 5)"


def test_with_ties_is_refused_where_it_would_become_a_limit():
    sql = "SELECT ename FROM emp ORDER BY sal FETCH FIRST 3 ROWS WITH TIES"
    lost = convert(sql, target="mysql")
    assert lost["status"] == "ERROR" and lost["sev"]["WITH_TIES"] == "ERROR"
    kept = convert(sql, target="postgres")
    assert kept["status"] == "OK" and "WITH TIES" in kept["sql"]


def test_rownum_with_order_by_warns():
    # Oracle は ORDER BY より前に ROWNUM を適用するので、件数の意味が変わりうる
    r = convert("SELECT ename FROM emp WHERE ROWNUM <= 3 ORDER BY sal")
    assert r["status"] == "WARN" and r["sev"]["ROWNUM"] == "WARN"


def test_oracle_outer_join_becomes_left_join():
    r = convert("SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+)")
    assert r["status"] == "OK" and "LEFT JOIN dept AS d ON e.deptno = d.deptno" in r["sql"]


def test_outer_join_with_marked_table_first_in_from():
    # (+) の付いた表が FROM の先頭にあると、SQLGlot の書き換えは表が重複した SQL を作っていた
    r = convert("SELECT d.dname, e.ename FROM dept d, emp e WHERE d.deptno(+) = e.deptno")
    assert r["status"] == "OK" and "FROM emp AS e LEFT JOIN dept AS d ON d.deptno = e.deptno" in r["sql"]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM a, b WHERE a.id(+) = b.id AND a.x = b.x(+)",       # 向きが混ざっている
    "SELECT * FROM a, b, c WHERE a.id = b.id(+) AND c.id = b.id(+)",  # 1 つの表を 2 つの表に外部結合
    "SELECT * FROM a, b WHERE a.id(+) = b.id(+)",                     # 両側に (+)
])
def test_outer_join_that_cannot_be_rewritten_is_error(sql):
    r = convert(sql)
    assert r["status"] == "ERROR" and "ORACLE_JOIN_MARK" in r["codes"]


# ---- 前処理: 方言の差を構文木の上で埋める ---------------------------------------------------

@pytest.mark.parametrize("sql,source,target,expected", [
    # TRUNC(date, 'MM') の書式を単位名にする。MySQL では日単位の切り捨てに化けていた
    ("SELECT TRUNC(hiredate, 'MM') FROM emp", "oracle", "postgres", "DATE_TRUNC('MONTH', hiredate)"),
    # FROM dual を落とす。MySQL では引用符付きの表名になって失敗していた
    ("SELECT SYSDATE FROM dual", "oracle", "mysql", "SELECT CURRENT_TIMESTAMP()"),
    # 再帰 CTE の RECURSIVE を変換先に合わせる
    ("WITH t (n) AS (SELECT 1 FROM dual UNION ALL SELECT n + 1 FROM t WHERE n < 3) SELECT n FROM t",
     "oracle", "postgres", "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL"),
    ("WITH RECURSIVE t AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM t WHERE n < 3) SELECT n FROM t",
     "postgres", "oracle", "WITH t(n) AS (SELECT 1 AS n UNION ALL"),
    # 日付リテラルは書式を明示した TO_DATE にする。Oracle の CAST は既定の日付書式に依存する
    ("SELECT ename FROM emp WHERE hiredate > DATE '1981-01-01'", "postgres", "oracle",
     "hiredate > TO_DATE('1981-01-01', 'YYYY-MM-DD')"),
    # MySQL の DIV は切り捨て。SQLGlot の CAST は四捨五入になる
    ("SELECT empno DIV 4 FROM emp", "mysql", "oracle", "SELECT TRUNC(empno / 4) FROM emp"),
    # INTERVAL の月加算は ADD_MONTHS にする（複数形の単位は Oracle が拒む）
    ("SELECT hiredate + INTERVAL '6 months' FROM emp", "postgres", "oracle", "SELECT ADD_MONTHS(hiredate, 6) FROM emp"),
    # MySQL の DATEDIFF は Oracle に無いので日付の引き算にする
    ("SELECT DATEDIFF(hiredate, '1981-01-01') FROM emp", "mysql", "oracle",
     "(TRUNC(hiredate) - TO_DATE('1981-01-01', 'YYYY-MM-DD'))"),
    # WITH ROLLUP は標準の ROLLUP にする
    ("SELECT deptno, SUM(sal) FROM emp GROUP BY deptno WITH ROLLUP", "mysql", "postgres", "GROUP BY ROLLUP (deptno)"),
    # MySQL と SQL Server は派生表に別名を要求する
    ("SELECT * FROM (SELECT 1 AS a)", "postgres", "mysql", "(SELECT 1 AS a) AS subq1"),
    # 結果を変えないヒントは外す
    ("SELECT /*+ INDEX(emp ix) */ ename FROM emp", "oracle", "postgres", "SELECT ename FROM emp"),
])
def test_rewrite(sql, source, target, expected):
    r = convert(sql, source, target)
    assert r["status"] == "OK" and expected in r["sql"], r


def test_distinct_on_for_oracle_uses_valid_identifiers():
    # SQLGlot の書き換えはアンダースコアで始まる別名を作り、Oracle はそれを拒む
    r = convert("SELECT DISTINCT ON (deptno) deptno, ename FROM emp ORDER BY deptno, sal DESC", "postgres", "oracle")
    assert r["status"] == "OK" and "ROW_NUMBER() OVER (PARTITION BY deptno" in r["sql"]
    assert " _" not in r["sql"] and "(_" not in r["sql"]


@pytest.mark.parametrize("target,expected", [("oracle", 'SELECT ename, "ORDER" FROM emp'),
                                             ("postgres", 'SELECT ename, "order" FROM emp')])
def test_backticks_follow_target_case_folding(target, expected):
    # バッククォートは大文字小文字の区別に意味を持たない。予約語だけ引用符を残し、変換先の畳み方にそろえる
    assert convert("SELECT `ename`, `order` FROM `emp`", "mysql", target)["sql"] == expected


# ---- 型に依存する意味の差: 表定義から型を付けて書き換える ------------------------------------

EMP_DDL = "CREATE TABLE emp (empno INT, ename VARCHAR(10), sal DECIMAL(7,2), hiredate DATE, deptno INT)"


@pytest.mark.parametrize("target,expected", [("oracle", "TRUNC(empno / 4)"), ("mysql", "TRUNCATE(empno / 4, 0)"),
                                             ("duckdb", "empno // 4")])
def test_integer_division_keeps_truncation(target, expected):
    # PostgreSQL の整数どうしの除算は切り捨て。Oracle・MySQL・DuckDB は小数を返す
    r = convert("SELECT empno / 4 FROM emp", "postgres", target, EMP_DDL)
    assert r["status"] == "OK" and expected in r["sql"]


def test_decimal_division_is_left_alone():
    r = convert("SELECT sal / 4 FROM emp", "postgres", "oracle", EMP_DDL)
    assert r["status"] == "OK" and r["sql"] == "SELECT sal / 4 FROM emp"


def test_division_without_schema_warns():
    r = convert("SELECT empno / 4 FROM emp", "postgres", "oracle")
    assert r["status"] == "WARN" and "DIVISION" in r["codes"]


def test_date_difference_for_mysql_becomes_datediff():
    # MySQL で日付どうしを引いても日数にならない
    r = convert("SELECT hiredate - DATE '1981-01-01' FROM emp", "postgres", "mysql", EMP_DDL)
    assert r["status"] == "OK" and "DATEDIFF(hiredate, " in r["sql"]


# ---- 変換先の能力 -----------------------------------------------------------------------

def test_aggregate_filter_depends_on_target():
    sql = "SELECT COUNT(*) FILTER (WHERE sal > 1000) FROM emp"
    assert convert(sql, "postgres", "oracle")["status"] == "OK"
    r = convert(sql, "postgres", "mysql")
    assert r["status"] == "ERROR" and "AGG_FILTER" in r["codes"]


def test_mysql_collation_warns_by_default():
    r = convert("SELECT ename FROM emp WHERE ename LIKE 'k%'", "mysql", "postgres")
    assert r["status"] == "WARN" and "COLLATION" in r["codes"]


def test_mysql_collation_rewrite_is_opt_in():
    r = convert("SELECT ename FROM emp WHERE ename LIKE 'k%' AND job = 'Clerk'", "mysql", "postgres",
                case_insensitive=True)
    assert r["status"] == "OK"
    assert r["sql"] == "SELECT ename FROM emp WHERE ename ILIKE 'k%' AND LOWER(job) = LOWER('Clerk')"


# ---- 残存構文の検出: 素の transpile が素通りさせるもの ------------------------------------

SILENT = [
    ("SELECT ename, LEVEL FROM emp START WITH mgr IS NULL CONNECT BY PRIOR empno = mgr", "CONNECT_BY", "CONNECT BY"),
    ("SELECT ROWID FROM emp", "ROWID", "ROWID"),
    ("INSERT INTO emp (empno) VALUES (emp_seq.NEXTVAL)", "SEQUENCE", "NEXTVAL"),
]


@pytest.mark.parametrize("sql,code,marker", SILENT)
def test_plain_transpile_lets_it_through(sql, code, marker):
    """対比: 素の sqlglot.transpile は例外を出さずにそのまま出力する（このスキルが要る理由）。"""
    out = _run(f"import sqlglot; print(json.dumps(sqlglot.transpile({sql!r}, read='oracle', write='postgres')[0]))")
    assert marker in out.upper()


@pytest.mark.parametrize("sql,code,marker", SILENT)
def test_skill_reports_it(sql, code, marker):
    r = convert(sql)
    assert r["status"] == "ERROR" and code in r["codes"]


def test_simple_connect_by_to_duckdb_is_rewritten_by_sqlglot():
    # DuckDB の生成器は単純な形を再帰 CTE に書き換え、結果が一致することを実行して確かめた
    r = convert("SELECT ename, LEVEL FROM emp START WITH mgr IS NULL CONNECT BY PRIOR empno = mgr", "oracle", "duckdb")
    assert r["status"] == "OK" and r["sql"].startswith("WITH RECURSIVE")


@pytest.mark.parametrize("sql", [
    "SELECT SYS_CONNECT_BY_PATH(ename, '/') FROM emp START WITH mgr IS NULL CONNECT BY PRIOR empno = mgr",
    "SELECT ename FROM emp START WITH mgr IS NULL CONNECT BY NOCYCLE PRIOR empno = mgr",
    "SELECT LEVEL FROM dual CONNECT BY LEVEL <= 3",
])
def test_other_connect_by_to_duckdb_is_error(sql):
    r = convert(sql, "oracle", "duckdb")
    assert r["status"] == "ERROR" and "CONNECT_BY" in r["codes"]


def test_nextval_to_target_with_sequences_warns():
    # DuckDB にも nextval はあるが、PostgreSQL の SERIAL が暗黙に作るシーケンスは DuckDB には無い
    r = convert("SELECT nextval('emp_id_seq')", "postgres", "duckdb")
    assert r["status"] == "WARN" and "SEQUENCE" in r["codes"]


def test_auto_increment_to_oracle_becomes_identity():
    r = convert("CREATE TABLE t (id INT AUTO_INCREMENT PRIMARY KEY, v INT)", "mysql", "oracle")
    assert r["status"] == "OK" and "GENERATED BY DEFAULT AS IDENTITY" in r["sql"]


def test_within_group_and_on_conflict_are_not_functions():
    assert convert("SELECT STRING_AGG(ename, ',' ORDER BY ename) FROM emp", "postgres", "oracle")["status"] == "OK"
    assert convert("INSERT INTO t (id) VALUES (1) ON CONFLICT (id) DO NOTHING", "postgres", "duckdb")["status"] == "OK"


def test_connect_by_is_reported_once():
    # CONNECT BY / START WITH / PRIOR は同じ問題。指摘は 1 件にまとめる
    assert convert(SILENT[0][0])["all_codes"].count("CONNECT_BY") == 1


def test_keyword_inside_string_literal_is_not_flagged():
    assert convert("SELECT 'CONNECT BY ROWID' AS note FROM emp")["status"] == "OK"


def test_create_sequence_is_error_only_where_there_are_no_sequences():
    # 変換先の能力で判定する。PostgreSQL と Oracle にはシーケンスがあり、MySQL には無い
    assert convert("CREATE SEQUENCE emp_seq START WITH 1", "postgres", "oracle")["status"] == "OK"
    r = convert("CREATE SEQUENCE emp_seq START WITH 1", "postgres", "mysql")
    assert r["status"] == "ERROR" and "SEQUENCE" in r["codes"]


def test_keyword_inside_comment_is_not_flagged():
    # 以前は正規表現で文面を見ていたので、コメント中の PRIOR を CONNECT BY と誤認していた
    r = convert("SELECT ename FROM emp -- PRIOR to 1990\nWHERE ROWNUM <= 3")
    assert r["status"] == "OK" and "LIMIT 3" in r["sql"]


def test_rownum_that_cannot_become_limit_is_error():
    r = convert("SELECT ROWNUM, ename FROM emp")
    assert r["status"] == "ERROR" and "ROWNUM" in r["codes"]


# ---- 関数の移植性 ----------------------------------------------------------------------

def test_source_specific_function_needs_review():
    # SQLGlot は MONTHS_BETWEEN を型付きノードとして扱うので、Target に無くても素通りする
    r = convert("SELECT MONTHS_BETWEEN(SYSDATE, hiredate) FROM emp")
    assert r["status"] == "WARN" and "FUNC_PORTABILITY" in r["codes"]


@pytest.mark.parametrize("sql", [
    "SELECT NVL(comm, 0) FROM emp",                            # COALESCE に書き換わる
    "SELECT DECODE(deptno, 10, 'A', '?') FROM emp",            # CASE に書き換わる
    "SELECT UPPER(ename), COUNT(*) FROM emp GROUP BY ename",   # 移植性の高い関数
])
def test_rewritten_or_portable_functions_pass(sql):
    assert convert(sql)["status"] == "OK"


def test_function_missing_from_target_catalog_warns():
    # 変換先の組み込み関数一覧で判定する。generate_series は SQLGlot の内部名が別なので、以前は見逃していた
    r = convert("SELECT generate_series(1, 3)", "postgres", "oracle")
    assert r["status"] == "WARN" and "FUNC_PORTABILITY" in r["codes"]


def test_catalog_does_not_flag_table_names_or_types():
    r = convert("INSERT INTO emp (empno, ename) VALUES (1, CAST('x' AS VARCHAR(10)))", "postgres", "mysql")
    assert r["status"] == "OK"


@pytest.mark.parametrize("sql", [
    "CREATE TABLE t (id INT PRIMARY KEY, v INT, INDEX idx_v (v))",   # 索引の名前
    "REPLACE INTO customers (id, name) VALUES (1, 'A')",              # SQLGlot が解析できない文
])
def test_catalog_does_not_flag_index_or_unparsed_table_names(sql):
    assert "FUNC_PORTABILITY" not in convert(sql, "mysql", "postgres")["codes"]


def test_same_dialect_does_not_flag_functions():
    assert convert("SELECT MONTHS_BETWEEN(SYSDATE, hiredate) FROM emp", "oracle", "oracle")["status"] == "OK"


# ---- 往復検証: 生成結果は Target 方言として読み直せる ---------------------------------------

@pytest.mark.parametrize("target", ["postgres", "mysql", "tsql", "snowflake", "bigquery"])
def test_output_reparses_in_target(target):
    sql = "SELECT e.ename, NVL(e.comm, 0) FROM emp e, dept d WHERE e.deptno = d.deptno(+) AND ROWNUM <= 5"
    out = convert(sql, "oracle", target)["sql"]
    assert out
    assert _run(f"import sqlglot; sqlglot.parse_one({out!r}, read={target!r}); print(json.dumps(True))")


# ---- レポート -------------------------------------------------------------------------

def test_summary_counts_ok_and_warn_as_converted():
    s = _run("from _scalardb.converter import Result\n"
             "rs = [Result(1, 'a', 'SELECT', 'OK'), Result(2, 'b', 'SELECT', 'WARN'),"
             " Result(3, 'c', 'SELECT', 'ERROR'), Result(4, 'd', 'SELECT', 'OK')]\n"
             "print(json.dumps(report.summarize(rs)))")
    assert (s["total"], s["converted"], s["error"], s["rate"]) == (4, 3, 1, 75.0)


def test_unconverted_statement_is_kept_as_comment():
    out = _run("rs = generic.convert_script('SELECT ename FROM emp; SELECT ROWID FROM emp', 'oracle', 'postgres')\n"
               "print(json.dumps(report.render_sql(rs, 'postgres')))")
    assert "-- [NOT CONVERTED #2]" in out and "-- SELECT ROWID FROM emp" in out


# ---- ScalarDB パス: 同梱コピーは本体と同じ結果を返す（回帰の砦） --------------------------

DUMP = """
import sys, json, importlib, logging
logging.getLogger('sqlglot').setLevel(logging.ERROR)
prefix, mod = sys.argv[1], sys.argv[2]
if prefix:
    sys.path.insert(0, prefix)
convert = importlib.import_module(mod).convert_script
out = {}
for f, d in [("samples/oracle.sql", "oracle"), ("samples/postgres.sql", "postgres"),
             ("samples/mysql.sql", "mysql"), ("difftest/cases/oracle-features.sql", "oracle")]:
    res, _ = convert(open(f).read(), d, decompose=False)
    out[f] = [(r.index, r.status, r.converted, [i.code for i in r.issues]) for r in res]
print(json.dumps(out))
"""


def _dump(prefix: str, module: str) -> dict:
    p = subprocess.run([sys.executable, "-c", DUMP, prefix, module], capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_vendored_scalardb_matches_upstream():
    # 両者は別プロセスで動かす（同じプロセスだと "scalardb" 方言の登録が衝突する）
    assert _dump(str(SCRIPTS), "_scalardb.converter") == _dump("", "scalardb_migrate.converter")


def test_vendored_copy_is_in_sync():
    p = subprocess.run([sys.executable, str(SCRIPTS / "vendor_sync.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, p.stdout
    assert p.stdout.strip().splitlines()[-1] == "VENDOR_DRIFT=0"


# ---- CLI: 終了コードと機械可読な最終行 -------------------------------------------------------

def _cli(tmp_path: Path, sql: str, *args: str) -> subprocess.CompletedProcess:
    f = tmp_path / "in.sql"
    f.write_text(sql, encoding="utf-8")
    return subprocess.run([sys.executable, str(CLI), str(f), *args], capture_output=True, text=True, cwd=ROOT)


def test_cli_exit_0_and_machine_readable_tail(tmp_path):
    p = _cli(tmp_path, "SELECT ename FROM emp WHERE ROWNUM <= 5;", "--source", "oracle", "--target", "postgres")
    assert p.returncode == 0
    assert p.stdout.strip().splitlines()[-3:] == ["TOTAL=1", "CONVERTED=1", "RATE=100.0"]


def test_cli_exit_1_when_manual_work_remains(tmp_path):
    p = _cli(tmp_path, "SELECT ROWID FROM emp;", "--source", "oracle", "--target", "postgres")
    assert p.returncode == 1 and p.stdout.strip().splitlines()[-1] == "RATE=0.0"


def test_cli_exit_2_on_missing_file():
    p = subprocess.run([sys.executable, str(CLI), "no-such.sql", "--source", "oracle", "--target", "postgres"],
                       capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 2


def test_cli_writes_three_outputs(tmp_path):
    out = tmp_path / "out"
    p = _cli(tmp_path, "SELECT NVL(comm, 0) FROM emp;", "--source", "oracle", "--target", "mysql", "--out-dir", str(out))
    assert p.returncode == 0
    assert sorted(x.name for x in out.iterdir()) == ["in.mysql.sql", "in.report.json", "in.report.md"]


def test_cli_scalardb_target(tmp_path):
    ddl = "CREATE TABLE emp (empno NUMBER(4) PRIMARY KEY, sal NUMBER(7,2));\nSELECT empno FROM emp WHERE empno = 1;"
    p = _cli(tmp_path, ddl, "--source", "oracle", "--target", "scalardb")
    assert p.returncode == 0 and "CONVERTED=2" in p.stdout


def test_cli_reads_types_from_create_table_in_the_script(tmp_path):
    sql = EMP_DDL + ";\nSELECT empno / 4 FROM emp;"
    out = tmp_path / "out"
    p = _cli(tmp_path, sql, "--source", "postgres", "--target", "oracle", "--out-dir", str(out))
    assert p.returncode == 0 and "TRUNC(empno / 4)" in (out / "in.oracle.sql").read_text(encoding="utf-8")


def test_cli_mysql_case_insensitive(tmp_path):
    out = tmp_path / "out"
    p = _cli(tmp_path, "SELECT ename FROM emp WHERE ename LIKE 'k%';", "--source", "mysql", "--target", "postgres",
             "--mysql-case-insensitive", "--out-dir", str(out))
    assert p.returncode == 0 and "ILIKE 'k%'" in (out / "in.postgres.sql").read_text(encoding="utf-8")


def test_cli_ignores_scalardb_only_options_for_other_targets(tmp_path):
    p = _cli(tmp_path, "SELECT 1;", "--source", "postgres", "--target", "mysql", "--keys", "emp=empno")
    assert "--target scalardb のときだけ有効" in p.stderr


# ---- #27-41: 終了コードと、1 文の失敗の切り離し ------------------------------------------------------------
def test_an_unterminated_string_is_one_error_not_a_traceback(tmp_path):
    p = _cli(tmp_path, "SELECT 'unterminated FROM emp;\nSELECT 1 FROM dual;", "--source", "oracle", "--target", "postgres")
    assert p.returncode == 1 and "TOKENIZE" in p.stdout and "Traceback" not in p.stderr


@pytest.mark.parametrize("sql", ["", "-- only a comment\n"])
def test_nothing_to_convert_is_an_input_error(tmp_path, sql):
    p = _cli(tmp_path, sql, "--source", "oracle", "--target", "postgres")
    assert p.returncode == 2 and "変換する文がありません" in p.stderr


def test_input_errors_exit_with_two_not_with_a_traceback(tmp_path):
    bad_schema = _cli(tmp_path, "SELECT 1 FROM dual;", "--source", "oracle", "--target", "scalardb",
                      "--schema", str(tmp_path / "absent.json"))
    assert bad_schema.returncode == 2 and "Traceback" not in bad_schema.stderr
    latin1 = tmp_path / "latin1.sql"
    latin1.write_bytes("SELECT 'caf\xe9' FROM dual;".encode("latin-1"))
    p = subprocess.run([sys.executable, str(ROOT / "skills/sql-transpile/scripts/transpile.py"), str(latin1),
                        "--source", "oracle", "--target", "postgres"], capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 2 and "UTF-8 として読めません" in p.stderr
