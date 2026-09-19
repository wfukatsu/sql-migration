"""P3-1: take the ScalarDB-side captures for one money variant, end to end.

Each variant is a different ScalarDB schema, so the generated code differs too: the repository converts at the
bind boundary using the column's target type, and a column that is a scaled BIGINT under one convention is a
DOUBLE under the other. Generating once and running twice would measure one convention's code against the other
convention's tables, which is worse than measuring nothing -- it would look like a result.

    python difftest/plsql_capture.py --variant scaled
    python difftest/plsql_capture.py --variant double

Each run regenerates `generated/` against that variant's schema, converts the scenario setups the same way, and
runs the Java harness. The captures land in `difftest/work/plsql-scalardb-<variant>/`, ready for P3-2.

The variant's namespace has to exist in ScalarDB already; `--print-schema-command` says how to load it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "plsql"
WORK = ROOT / "difftest" / "work"

NAMESPACE = {"scaled": "plsqlpoc", "double": "plsqlpoc_dbl"}


def schema_for(variant: str) -> Path:
    """The scaled schema is the one shipped in fixtures; the double one is derived from the same DDL."""
    if variant == "scaled":
        return FIXTURES / "scalardb-schema.json"
    out = WORK / "plsql-schema-double.json"
    run([sys.executable, str(ROOT / "difftest" / "plsql_schema.py"), "--variant", "double",
         "--namespace", NAMESPACE["double"], "--out", str(out)])
    return out


def run(command: list[str], allow_failure: bool = False, **kwargs) -> None:
    print("$ " + " ".join(command))
    finished = subprocess.run(command, cwd=kwargs.pop("cwd", ROOT), **kwargs)
    if finished.returncode and not allow_failure:
        raise SystemExit(finished.returncode)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=["scaled", "double"], required=True)
    ap.add_argument("--print-schema-command", action="store_true",
                    help="print the Schema Loader command for this variant and stop")
    args = ap.parse_args(argv)

    schema = schema_for(args.variant)
    if args.print_schema_command:
        print(f"cd difftest && docker compose --profile tools --profile oracle --profile cassandra "
              f"--profile cluster run --rm schema-loader --config /conf/scalardb-in-docker.properties "
              f"--schema-file /work/{schema.name} --coordinator")
        return 0

    setup = WORK / ("plsql-setup.json" if args.variant == "scaled" else "plsql-setup-double.json")
    # a non-zero exit means a routine the rules called AUTO did not come out cleanly. That is worth knowing and
    # worth measuring, so it is reported and the capture goes ahead: the comparison is what says what it cost.
    # `--limits` は走査行数の上限（#19）だけでなく、**行ロックを落とす判断**（#9）も持っている。
    # 渡さずに生成すると、比較するのは「決定が効いていない生成物」になる——決定した形が Oracle と
    # 一致するかを測れない
    run([sys.executable, "-m", "plsql.generate", str(FIXTURES / "src"),
         "--scalardb-schema", str(schema), "--limits", str(FIXTURES / "limits.yaml"),
         "--out-dir", "generated"], allow_failure=True)
    run([sys.executable, str(ROOT / "difftest" / "plsql_setup.py"), "--variant", args.variant,
         "--schema", str(schema), "--out", str(setup)])
    # `--rerun`: このテストの入力（生成したコード・変換した準備行）は Gradle から見えない場所にある。
    # 付けないと、Java が変わっていない回は「up to date」として飛ばされ、**前回の capture がそのまま
    # 残って比較される**——準備行だけを直した回がそうなった（2026-09-19）
    run(["gradle", "test", "--rerun", "-Dplsql.generated=1", f"-Dplsql.variant={args.variant}",
         "--tests", "*ScalarDbCaptureIT*"], cwd=ROOT / "runtime-java",
        env={**__import__("os").environ, "SCALARDB_IT": "1"})
    _record_fingerprint(args.variant)
    return 0


def _record_fingerprint(variant: str) -> None:
    """Say what these captures are captures *of* (plsql/fingerprint.py): the source of each routine and the
    toolchain that generated and ran it. `plsql_compare.py` carries it into the report, and `plsql.cli` only
    believes a report whose fingerprint matches what it is judging. Written after the run, so a capture that
    died half-way keeps the previous run's fingerprint and its leftovers read as stale."""
    import json

    if str(ROOT) not in sys.path:     # run as a script, sys.path starts at difftest/
        sys.path.insert(0, str(ROOT))
    from plsql import fingerprint
    from plsql.report import analyse

    captures = WORK / f"plsql-scalardb-{variant}"
    captures.mkdir(parents=True, exist_ok=True)
    program = analyse(FIXTURES / "src").program
    (captures / "fingerprint.json").write_text(
        json.dumps(fingerprint.of(program, FIXTURES / "src"), indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
