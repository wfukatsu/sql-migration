"""P2-7 / P2-10: `python -m plsql.generate` -- analyse, judge, and write the Java.

    python -m plsql.generate fixtures/plsql/src --out-dir generated

The pipeline is the one the earlier phases built: parse and lower (P1), analyse (P2-1), ask ScalarDB (P2-4),
judge (P2-2), then write. Nothing new is decided here; this is the command that puts the parts together.

Exit status is 1 when a routine the rules called AUTO could not be generated cleanly. Everything else -- REVIEW,
REDESIGN, statements ScalarDB refuses -- is written with its refusal in place and reported, because those are
findings, not failures of the run.

"Cleanly" also means "nobody is guessing": `--limits-strict` fails the run when a rule asked for the row
limit to be checked and nobody decided one -- the built-in default is the value that means nobody did (#19).

"Cleanly" means two things, and `--verify-compile` is the second (#21). Without it the gate reads the IR only,
which cannot see a body that reads a name its own signature does not provide -- `javac` can, and until it is
asked, "AUTO" is a claim about code nobody has compiled. It is opt-in because it needs a JVM, Gradle and the
dependency cache, which generating does not; `PLSQL_VERIFY_COMPILE=1` turns it on without changing the command,
which is how CI asks for it.
"""

from __future__ import annotations

import argparse
import os
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
    parser.add_argument("--limits", help="走査行数の上限を書いた YAML（既定と routine ごとの上書き）。"
                                        "渡さなければ組み込みの既定を使う")
    parser.add_argument("--limits-strict", action="store_true",
                        default=bool(os.environ.get("PLSQL_LIMITS_STRICT")),
                        help="fail the run when a rule asked for the row limit to be checked (requiredTests: "
                             "row_limit) and nobody decided one. Deciding includes deciding not to use a "
                             "limit -- `notLimited` in the config records that, with its reason (#19)")
    parser.add_argument("--verify-compile", action="store_true",
                        default=bool(os.environ.get("PLSQL_VERIFY_COMPILE")),
                        help="compile the generated tree (gradle compileJava) and fail the run on any javac "
                             "error, naming the routine each one came from. Needs a JVM and Gradle. "
                             "PLSQL_VERIFY_COMPILE=1 has the same effect")
    parser.add_argument("--no-verify-compile", action="store_false", dest="verify_compile",
                        help="skip the compile check even when PLSQL_VERIFY_COMPILE is set")
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

    from .gen_java.repository import set_limits
    from .gen_java.split import set_boundaries
    from .limits import Boundaries, Limits, RowLocks

    limits = Limits.load(args.limits) if args.limits else Limits()
    set_limits(limits)
    # #9: 行ロックを落として楽観制御へ移すと決めた routine。同じ config に書く——どちらも
    # 「生成器が推測してはならない、routine ごとの決定」である
    row_locks = RowLocks.load(args.limits) if args.limits else RowLocks()
    # #24 / #14: トランザクション境界を 1 反復 = 1 トランザクションに割ると決めた routine。
    # 決めていない routine は 1 つの method のまま出て、`COMMIT` のところで止まる
    set_boundaries(Boundaries.load(args.limits) if args.limits else Boundaries())
    # 2026-09-19: 表名が実行時に決まる動的 SQL の、受け付けてよい表名。書いていなければ数えない
    from .dynamic import set_allowed_tables
    from .limits import DynamicTables

    set_allowed_tables((DynamicTables.load(args.limits) if args.limits else DynamicTables()).allowed)

    analysis = build_analysis(root, schema, scalardb_schema=scalardb, row_locks=row_locks)
    decisions = decide(analysis.program, analyse_program(analysis.program), RuleSet.load(), Evidence())
    plans = analysis.capability.plans if analysis.capability is not None else {}
    project = generate(analysis.program, args.out_dir, args.package, decisions, plans)
    written = write(project, decisions)

    summary = project.summary()
    auto = [r for r, d in decisions.items() if d.rule_verdict == "AUTO"]
    dirty = _dirty_auto(project, decisions)
    report = _verify_compile(project, args) if args.verify_compile else None
    undecided = _undecided_limits(decisions, limits) if args.limits_strict else []

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
        if report is not None:
            _print_compile(report, decisions)
        for routine in undecided:
            print(f"  the rules ask for a row limit and nobody decided one: {routine}")
        _print_no_limits_file(undecided, limits)
    failed = bool(dirty) or bool(undecided) or (report is not None and not report.ok)
    if args.quiet and failed:
        # `--quiet` means "say nothing when it goes well". A run that returns 1 and says nothing about why is
        # a failure nobody can act on, so the reason goes to stderr, where quiet output belongs anyway.
        for routine in dirty:
            print(f"  AUTO but not cleanly generated: {routine}", file=sys.stderr)
        for routine in undecided:
            print(f"  the rules ask for a row limit and nobody decided one: {routine}", file=sys.stderr)
        _print_no_limits_file(undecided, limits, stream=sys.stderr)
        if report is not None and not report.ok:
            _print_compile(report, decisions, stream=sys.stderr)
    return 1 if failed else 0


def _verify_compile(project, args):
    from .verify import verify

    return verify(args.out_dir, project)


def _print_compile(report, decisions: dict, stream=None) -> None:
    """What the compiler said, per routine, with the verdict the rules had given it.

    An error under an AUTO routine is the one this check was added for: the rules said it could be generated
    unattended and the compiler says it cannot be compiled at all.
    """
    out = stream or sys.stdout
    if not report.ran:
        print(f"  compile check did not run: {report.unavailable}", file=out)
        print("  asked for --verify-compile, so this run fails rather than reporting an "
              "unverified AUTO", file=out)
        return
    if report.ok:
        print("  compile check: gradle compileJava succeeded over the generated tree", file=out)
        return
    print(f"  compile check: {len(report.errors)} javac error(s)", file=out)
    for error in report.errors:
        verdict = decisions[error.routine].rule_verdict if error.routine in decisions else "?"
        if error.routine:
            print(f"    {error.routine} [{verdict}]: {error.message} "
                  f"({Path(error.file).name}:{error.line})", file=out)
        else:
            print(f"    {Path(error.file).name}:{error.line}: {error.message}", file=out)


def _print_no_limits_file(undecided: list[str], limits, stream=None) -> None:
    """Say when the whole list is explained by there being no config at all.

    Without this the names read as "somebody forgot to write these values", when the actual state is
    "nobody passed a file". The two need different next steps, and a list of routine names cannot tell
    them apart on its own.
    """
    if undecided and limits.source is None:
        print("  (--limits was not given, so no routine has a decided limit)", file=stream or sys.stdout)


def _undecided_limits(decisions: dict, limits) -> list[str]:
    """Routines whose rules ask for a row limit that nobody has decided (#19).

    The hook is the rules' own `requiredTests: row_limit` -- `CUR-002` (cursor FOR loop), `BULK-001`
    (BULK COLLECT), `SQL-002` (a statement that becomes a fetch plus H2). Each of those says, in the rule
    file, that the row limit has to be checked; none of them could tell whether it ever was.

    Not "AUTO routines", which was the first shape of this check and could never fire: `CUR-002` floors
    every cursor FOR loop at REVIEW, so the set of AUTO routines that scan is empty by construction. Asking
    the rules what they require, rather than asking the verdict, also means the check follows `BULK COLLECT`
    and the plan path without knowing they exist.

    Deciding includes deciding *not* to use a row limit: `notLimited` in the config records that with its
    reason, which is why `prc_nightly_close` does not appear here.
    """
    return sorted(routine for routine, decision in decisions.items()
                  if "row_limit" in decision.required_tests() and not limits.decided(routine))


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
