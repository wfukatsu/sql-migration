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


def run(command: list[str], **kwargs) -> None:
    print("$ " + " ".join(command))
    subprocess.run(command, check=True, cwd=kwargs.pop("cwd", ROOT), **kwargs)


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
    run([sys.executable, "-m", "plsql.generate", str(FIXTURES / "src"),
         "--scalardb-schema", str(schema), "--out-dir", "generated", "--quiet"])
    run([sys.executable, str(ROOT / "difftest" / "plsql_setup.py"), "--variant", args.variant,
         "--schema", str(schema), "--out", str(setup)])
    run(["gradle", "test", "-Dplsql.generated=1", f"-Dplsql.variant={args.variant}",
         "--tests", "*ScalarDbCaptureIT*"], cwd=ROOT / "runtime-java",
        env={**__import__("os").environ, "SCALARDB_IT": "1"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
