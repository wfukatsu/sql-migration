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


def convert(sql: str, source: str = "oracle", target: str = "postgres") -> dict:
    return _run(
        f"r = generic.convert_statement({sql!r}, {source!r}, {target!r})\n"
        "print(json.dumps({'status': r.status, 'sql': r.converted[0] if r.converted else '',"
        " 'codes': sorted({i.code for i in r.issues}), 'all_codes': [i.code for i in r.issues],"
        " 'sev': {i.code: i.severity for i in r.issues}}))")


# ---- 前処理: SQLGlot が直さない構文を直す -------------------------------------------------

def test_rownum_becomes_limit():
    r = convert("SELECT ename FROM emp WHERE ROWNUM <= 5")
    assert r["status"] == "OK" and r["sql"] == "SELECT ename FROM emp LIMIT 5"


def test_rownum_strict_less_than_is_one_fewer():
    assert "LIMIT 4" in convert("SELECT ename FROM emp WHERE ROWNUM < 5")["sql"]


def test_rownum_with_order_by_warns():
    # Oracle は ORDER BY より前に ROWNUM を適用するので、件数の意味が変わりうる
    r = convert("SELECT ename FROM emp WHERE ROWNUM <= 3 ORDER BY sal")
    assert r["status"] == "WARN" and r["sev"]["ROWNUM"] == "WARN"


def test_oracle_outer_join_becomes_left_join():
    r = convert("SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+)")
    assert r["status"] == "OK" and "LEFT JOIN dept AS d ON e.deptno = d.deptno" in r["sql"]


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


def test_connect_by_is_reported_once():
    # CONNECT BY / START WITH / PRIOR は同じ問題。指摘は 1 件にまとめる
    assert convert(SILENT[0][0])["all_codes"].count("CONNECT_BY") == 1


def test_keyword_inside_string_literal_is_not_flagged():
    assert convert("SELECT 'CONNECT BY ROWID' AS note FROM emp")["status"] == "OK"


def test_create_sequence_is_error():
    r = convert("CREATE SEQUENCE emp_seq START WITH 1")
    assert r["status"] == "ERROR" and "SEQUENCE" in r["codes"]


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


def test_cli_ignores_scalardb_only_options_for_other_targets(tmp_path):
    p = _cli(tmp_path, "SELECT 1;", "--source", "postgres", "--target", "mysql", "--keys", "emp=empno")
    assert "--target scalardb のときだけ有効" in p.stderr
