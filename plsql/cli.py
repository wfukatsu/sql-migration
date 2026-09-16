"""P1-7: `python -m plsql.cli` -- run the Phase 1 analysis over a directory and write the report.

    python -m plsql.cli fixtures/plsql/src --schema fixtures/plsql/src/schema.sql --out-dir out/plsql

Exit status is 1 when anything failed to parse, so the command can gate a pipeline. Unresolved types and
warnings do not fail the run: Phase 1 exists to show them, not to hide the run behind them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .report import analyse, inventory, write


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plsql.cli", description=__doc__.splitlines()[0])
    parser.add_argument("root", help="directory holding the PL/SQL sources")
    parser.add_argument("--schema", help="Oracle DDL snapshot used to resolve %%TYPE / %%ROWTYPE")
    parser.add_argument("--out-dir", help="write inventory.json / diagnostics.sarif / summary.md here")
    parser.add_argument("--quiet", action="store_true", help="print nothing but the exit status")
    args = parser.parse_args(argv)

    schema = args.schema
    if schema is None:
        candidate = Path(args.root) / "schema.sql"
        schema = str(candidate) if candidate.exists() else None

    analysis = analyse(args.root, schema)
    data = inventory(analysis)
    kpi, totals = data["kpi"], data["totals"]

    if not args.quiet:
        print(f"modules={totals['modules']} routines={totals['routines']} statements={totals['statements']}")
        print(f"parse rate      {kpi['parseRate']:.1%}  ({kpi['parsedFiles']}/{kpi['totalFiles']} files)")
        print(f"type resolution {kpi['typeResolutionRate']:.1%}  "
              f"({kpi['resolvedSymbols']}/{kpi['typedSymbols']} typed symbols)")
        print(f"issues={totals['issues']} errors={totals['errors']}")
        for failed in kpi["failedFiles"]:
            print(f"  PARSE FAILED {failed}")
    if args.out_dir:
        written = write(analysis, args.out_dir)
        if not args.quiet:
            for name, path in written.items():
                print(f"  {name:12} {path}")
    return 1 if kpi["failedFiles"] else 0


if __name__ == "__main__":
    sys.exit(main())
