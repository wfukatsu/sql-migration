"""P1-7 / P3-5: `python -m plsql.cli` -- analyse a directory and write the report a reviewer works from.

    python -m plsql.cli fixtures/plsql/src --schema fixtures/plsql/src/schema.sql --out-dir out/plsql \
        --evidence difftest/work/plsql-diff.json --generated generated

Without `--evidence` nothing can be AUTO. That is not a quirk: the confidence a verdict rests on includes
whether the routine was verified, and a routine nobody compared against Oracle has not been (rules/engine.py).
A decisions file written without it reports every routine as REVIEW or worse -- true, but only because the
question was never asked. `difftest/plsql_diff.py --full --json ...` writes the file that answers it.

Exit status is 1 when anything failed to parse, so the command can gate a pipeline. Unresolved types and
warnings do not fail the run: Phase 1 exists to show them, not to hide the run behind them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import review
from .analysis import analyse as analyse_program
from .report import analyse, inventory, write
from .rules.engine import RuleSet, decide


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plsql.cli", description=__doc__.splitlines()[0])
    parser.add_argument("root", help="directory holding the PL/SQL sources")
    parser.add_argument("--schema", help="Oracle DDL snapshot used to resolve %%TYPE / %%ROWTYPE")
    parser.add_argument("--scalardb-schema", help="Schema Loader JSON; enables the ScalarDB capability check")
    parser.add_argument("--out-dir", help="write inventory.json / diagnostics.sarif / summary.md and, "
                                          "with the review files, decisions.json / unresolved.md / "
                                          "traceability.csv here")
    parser.add_argument("--evidence", help="P3-2's comparison report (difftest/work/plsql-diff.json). "
                                           "Without it no routine can reach AUTO.")
    parser.add_argument("--variant", choices=["scaled", "double"],
                        help="credit a routine for one money convention only; the default requires it to "
                             "agree under every convention the report covers")
    parser.add_argument("--generated", help="the generated tree, so traceability.csv can be checked against it")
    parser.add_argument("--package", default="com.example.migrated")
    parser.add_argument("--fix-times", help="YAML of measured human fix minutes per routine, for KPI-6")
    parser.add_argument("--quiet", action="store_true", help="print nothing but the exit status")
    args = parser.parse_args(argv)

    schema = args.schema
    if schema is None:
        candidate = Path(args.root) / "schema.sql"
        schema = str(candidate) if candidate.exists() else None

    scalardb = args.scalardb_schema
    if scalardb is None:
        candidate = Path(args.root).parent / "scalardb-schema.json"
        scalardb = str(candidate) if candidate.exists() else None
    analysis = analyse(args.root, schema, scalardb_schema=scalardb)
    data = inventory(analysis)
    kpi, totals = data["kpi"], data["totals"]

    if not args.quiet:
        print(f"modules={totals['modules']} routines={totals['routines']} statements={totals['statements']}")
        print(f"parse rate      {kpi['parseRate']:.1%}  ({kpi['parsedFiles']}/{kpi['totalFiles']} files)")
        print(f"type resolution {kpi['typeResolutionRate']:.1%}  "
              f"({kpi['resolvedSymbols']}/{kpi['typedSymbols']} typed symbols)")
        print(f"issues={totals['issues']} errors={totals['errors']}")
        capability = data.get("targetCapability")
        if capability:
            print(f"scalardb        {capability['runnableRate']:.1%} runnable  {capability['statuses']}")
        for failed in kpi["failedFiles"]:
            print(f"  PARSE FAILED {failed}")
    if args.out_dir:
        written = write(analysis, args.out_dir)
        known = review.routine_ids(analysis.program)
        program_analysis = analyse_program(analysis.program)
        evidence = review.credit_private_callees(
            review.evidence_from_diff(args.evidence, args.variant, known),
            analysis.program, program_analysis.call_graph)
        unmatched = review.unmatched_scenarios(args.evidence, known, args.variant)
        decisions = decide(analysis.program, program_analysis, RuleSet.load(), evidence)
        written.update(review.write(analysis.program, decisions, args.out_dir,
                                    generated_root=args.generated, package=args.package,
                                    fix_times=review.FixTimes.load(args.fix_times), unmatched=unmatched))
        if not args.quiet:
            counts: dict[str, int] = {}
            for decision in decisions.values():
                counts[decision.verdict] = counts.get(decision.verdict, 0) + 1
            if unmatched:
                print(f"  {len(unmatched)} scenario(s) match no routine: {unmatched}")
            print(f"verdicts        {dict(sorted(counts.items()))}"
                  + ("" if args.evidence else "  (no --evidence: nothing can be AUTO)"))
            for name, path in written.items():
                print(f"  {name:12} {path}")
    return 1 if kpi["failedFiles"] else 0


if __name__ == "__main__":
    sys.exit(main())
