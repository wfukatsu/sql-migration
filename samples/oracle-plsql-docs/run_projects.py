"""組み立てたプロジェクト（work/projects/ex_N_M）を、実 DB で流して比べる。

    .venv/bin/python samples/oracle-plsql-docs/run_projects.py [ex_2_1 ...] [--from STEP]

プロジェクトごとに:
  1. Oracle のユーザ plsqldoc_r を空にして配備（difftest/plsql_run.py deploy）し、シナリオを流す（run -> golden/）
  2. ScalarDB の namespace をスキーマ（scalardb-schema.json）で作り直す（Schema Loader）
  3. 生成・準備行の変換・ScalarDB Cluster での実行（difftest/plsql_capture.py --variant double）
  4. 比べる（difftest/plsql_compare.py）と、証拠つきの判定（plsql.cli --evidence）
ログは work/projects/ex_N_M/work/*.log。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from examples import WORK  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
STEPS = ["oracle", "schema", "capture", "compare"]
ENV = {**os.environ, "SRC_ORACLE_USER": "plsqldoc_r", "SRC_ORACLE_PASSWORD": "plsqldoc_r"}


def sh(command: list[str], log: Path, cwd: Path = ROOT, check: bool = False) -> int:
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(command)}\n")
        f.flush()
        code = subprocess.run(command, cwd=cwd, env=ENV, stdout=f, stderr=subprocess.STDOUT).returncode
        f.write(f"[exit {code}]\n")
    if check and code:
        raise SystemExit(f"{' '.join(command[:4])} ... failed ({code}); see {log}")
    return code


def clear_oracle_user() -> None:
    sys.path.insert(0, str(HERE))
    os.environ.update(SRC_ORACLE_USER="plsqldoc_r", SRC_ORACLE_PASSWORD="plsqldoc_r")
    from oracle_run import DROP_ORDER, connect
    import oracledb
    con = connect()
    cur = con.cursor()
    cur.execute("SELECT object_type, object_name FROM user_objects WHERE object_type IN (%s)"
                % ", ".join(f"'{t}'" for t in DROP_ORDER))
    for kind, name in sorted(cur.fetchall(), key=lambda o: DROP_ORDER.index(o[0])):
        try:
            cur.execute(f'DROP {kind} "{name}"' + {"TABLE": " CASCADE CONSTRAINTS PURGE", "TYPE": " FORCE"}.get(kind, ""))
        except oracledb.DatabaseError:
            pass
    con.close()


def recompile(log: Path) -> None:
    """配備はファイル名の順なので、後から来る手続きを呼ぶものは一度 INVALID になる。まとめて作り直して残りを記録する。"""
    from oracle_run import connect
    con = connect()
    cur = con.cursor()
    cur.callproc("DBMS_UTILITY.COMPILE_SCHEMA", ["PLSQLDOC_R", False])
    cur.execute("SELECT object_type, object_name FROM user_objects WHERE status <> 'VALID'")
    invalid = cur.fetchall()
    con.close()
    with log.open("a", encoding="utf-8") as f:
        f.write(f"after COMPILE_SCHEMA: {len(invalid)} invalid {invalid}\n")


def run_project(project: Path, start: str) -> None:
    work = project / "work"
    work.mkdir(exist_ok=True)
    ns = project.name
    steps = STEPS[STEPS.index(start):]
    if "oracle" in steps:
        clear_oracle_user()
        log = work / "oracle.log"
        log.unlink(missing_ok=True)
        shutil.rmtree(project / "golden", ignore_errors=True)
        sh([PY, "difftest/plsql_run.py", "deploy", "--project", str(project)], log)
        recompile(log)
        sh([PY, "difftest/plsql_run.py", "run", "--project", str(project)], log)
    if "schema" in steps:
        log = work / "schema.log"
        log.unlink(missing_ok=True)
        shutil.copy(project / "scalardb-schema.json", ROOT / "difftest" / "work" / f"{ns}-schema.json")
        base = ["docker", "compose", "--profile", "tools", "--profile", "oracle", "--profile", "cassandra",
                "--profile", "cluster", "run", "--rm", "schema-loader", "--config", "/conf/scalardb-in-docker.properties",
                "--schema-file", f"/work/{ns}-schema.json"]
        sh(base + ["-D"], log, cwd=ROOT / "difftest")
        sh(base + ["--coordinator"], log, cwd=ROOT / "difftest", check=True)
    if "capture" in steps:
        log = work / "capture.log"
        log.unlink(missing_ok=True)
        sh([PY, "difftest/plsql_capture.py", "--project", str(project), "--namespace", ns, "--variant", "double"], log)
    if "compare" in steps:
        log = work / "compare.log"
        log.unlink(missing_ok=True)
        sh([PY, "difftest/plsql_compare.py", "--project", str(project), "--variant", "double",
            "--json", str(work / "plsql-diff.json")], log)
        sh([PY, "-m", "plsql.cli", str(project / "src"), "--schema", str(project / "src" / "schema.sql"),
            "--scalardb-schema", str(project / "scalardb-schema.json"), "--evidence", str(work / "plsql-diff.json"),
            "--variant", "double", "--generated", str(work / "generated"), "--out-dir", str(work / "analysis")], log)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--from", dest="start", choices=STEPS, default="oracle")
    args = ap.parse_args(argv)
    projects = sorted((WORK / "projects").glob("ex_*"))
    if args.projects:
        projects = [p for p in projects if p.name in args.projects]
    for p in projects:
        print(f"== {p.name}", flush=True)
        run_project(p, args.start)
        tail = (p / "work" / "compare.log").read_text(encoding="utf-8").splitlines() if (p / "work" / "compare.log").exists() else []
        print("\n".join(l for l in tail if "compared:" in l or "verdicts" in l), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
