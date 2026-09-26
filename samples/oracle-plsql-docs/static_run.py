"""実行できない例（work/static/<ex_n_m>）を、解析（plsql.cli）と Java の生成・コンパイル（plsql.generate --verify-compile）に通す。

    .venv/bin/python samples/oracle-plsql-docs/static_run.py [ex_2_2 ...]

ログは work/static/<ex_n_m>/work/{analysis,generate}.log。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
STATIC = HERE / "work" / "static"


def main(argv=None) -> int:
    only = set(argv if argv is not None else sys.argv[1:])
    for p in sorted(STATIC.glob("ex_*")):
        if only and p.name not in only:
            continue
        work = p / "work"
        work.mkdir(exist_ok=True)
        common = ["--schema", str(p / "src" / "schema.sql"), "--scalardb-schema", str(p / "scalardb-schema.json")]
        with (work / "analysis.log").open("w", encoding="utf-8") as f:
            subprocess.run([PY, "-m", "plsql.cli", str(p / "src"), *common, "--out-dir", str(work / "analysis")],
                           cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
        with (work / "generate.log").open("w", encoding="utf-8") as f:
            code = subprocess.run([PY, "-m", "plsql.generate", str(p / "src"), *common, "--out-dir",
                                   str(work / "generated"), "--verify-compile"],
                                  cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode
        print(f"{p.name:<12} generate exit {code}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
