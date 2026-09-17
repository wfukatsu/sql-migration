"""P2-7 / P2-10: `python -m plsql.generate` -- analyse, judge, and write the Java.

    python -m plsql.generate fixtures/plsql/src --out-dir generated

The pipeline is the one the earlier phases built: parse and lower (P1), analyse (P2-1), ask ScalarDB (P2-4),
judge (P2-2), then write. Nothing new is decided here; this is the command that puts the parts together.

Exit status is 1 when a routine the rules called AUTO could not be generated cleanly. Everything else -- REVIEW,
REDESIGN, statements ScalarDB refuses -- is written with its refusal in place and reported, because those are
findings, not failures of the run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import analyse as analyse_program
from .gen_java.project import generate, write
from .report import analyse as build_analysis
from .rules.engine import Evidence, RuleSet, decide


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plsql.generate", description=__doc__.splitlines()[0])
    parser.add_argument("root")
    parser.add_argument("--schema", help="Oracle DDL snapshot")
    parser.add_argument("--scalardb-schema", help="ScalarDB Schema Loader JSON")
    parser.add_argument("--out-dir", default="generated")
    parser.add_argument("--package", default="com.example.migrated")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--handover", action="store_true",
                        help="write the handover banner instead of 'do not edit'. After handover the "
                             "regeneration model ends (plan §9) and the code is maintained by hand, so the "
                             "default banner would tell maintainers not to do the thing they now have to do.")
    args = parser.parse_args(argv)

    root = Path(args.root)
    schema = args.schema or (str(root / "schema.sql") if (root / "schema.sql").exists() else None)
    scalardb = args.scalardb_schema
    if scalardb is None:
        candidate = root.parent / "scalardb-schema.json"
        scalardb = str(candidate) if candidate.exists() else None

    if args.handover:
        from datetime import date

        from .gen_java.project import handover_banner
        handover_banner(date.today().isoformat())

    analysis = build_analysis(root, schema, scalardb_schema=scalardb)
    decisions = decide(analysis.program, analyse_program(analysis.program), RuleSet.load(), Evidence())
    project = generate(analysis.program, args.out_dir, args.package, decisions)
    written = write(project, decisions)

    summary = project.summary()
    auto = [r for r, d in decisions.items() if d.rule_verdict == "AUTO"]
    dirty = _dirty_auto(project, decisions)

    if not args.quiet:
        print(f"wrote {len(written)} files to {args.out_dir}/")
        print(f"routines: {len(decisions)}  AUTO {len(auto)}  "
              f"REVIEW {sum(1 for d in decisions.values() if d.rule_verdict == 'REVIEW')}  "
              f"REDESIGN {sum(1 for d in decisions.values() if d.rule_verdict == 'REDESIGN')}")
        print(f"untranslated statements {summary['untranslatedStatements']}  "
              f"SQL ScalarDB refuses {summary['unsupportedSql']}  planned {summary['plannedSql']}")
        if summary["unknownNames"]:
            print(f"names the translator could not place: {', '.join(summary['unknownNames'])}")
        for routine in dirty:
            print(f"  AUTO but not cleanly generated: {routine}")
    return 1 if dirty else 0


def _dirty_auto(project, decisions) -> list[str]:
    """AUTO routines that still hold something the generator refused.

    A routine the rules cleared for unattended generation must come out whole. If it does not, the disagreement
    is between the rules and the generator, and that is worth failing the run over.
    """
    dirty: list[str] = []
    for routine_id, decision in decisions.items():
        if decision.rule_verdict != "AUTO":
            continue
        prefix = routine_id + "#"
        if any(s.startswith(prefix) for s in project.untranslated + project.unsupported_sql):
            dirty.append(routine_id)
    return sorted(dirty)


if __name__ == "__main__":
    sys.exit(main())
