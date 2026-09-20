"""Migration Explorer: one HTML page for the investigation that comes before a migration.

    python -m plsql.cli <src> --out-dir out/explore/analysis --quiet        # without --limits
    python -m plsql.explorer out/explore/analysis --src <src> --snapshot shop.snapshot.json --sql app/sql \\
        --out out/explore/explorer.html

The page is read-only and self-contained. It is made from files alone: the analysis directory, the application's
SQL files, and a catalog snapshot taken once by `difftest/catalog_snapshot.py` (docs/guide/explorer.md). Without a
snapshot the page still works, and everything only the database knows says 未取得.

Exit status: 0 the page was written, 1 it was written and carries a warning, 2 the input is wrong and nothing was.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import appsql, model, page
from . import snapshot as snapshots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plsql.explorer", description=__doc__.splitlines()[0])
    parser.add_argument("analysis", help="the directory `python -m plsql.cli --out-dir` wrote")
    parser.add_argument("--src", help="the PL/SQL source directory the analysis was made from: the page then shows the "
                                      "code, line by line. The files are embedded whole, comments included")
    parser.add_argument("--snapshot", help="catalog snapshot (difftest/catalog_snapshot.py)")
    parser.add_argument("--sql", action="append", default=[], metavar="PATH",
                        help="an application SQL file, or a directory searched for *.sql; repeatable")
    parser.add_argument("--spec-dir", help="plsql-spec documents; a routine links to <module>.md when it is there")
    parser.add_argument("--out", default=None, help="the page to write (default: <analysis>/../explorer.html)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    out = Path(args.out) if args.out else Path(args.analysis).resolve().parent / "explorer.html"
    try:
        snapshot = snapshots.load(args.snapshot) if args.snapshot else None
        if args.src and not Path(args.src).is_dir():
            raise model.InputError(f"--src: {args.src} はディレクトリではない")
        missing = [p for p in args.sql if not Path(p).exists()]
        if missing:
            raise model.InputError(f"--sql: {', '.join(missing)} が無い")
        data = model.build(args.analysis, snapshot, appsql.collect(args.sql), spec_dir=args.spec_dir,
                           out_dir=out.parent, src_root=args.src, app_files=appsql.files(args.sql))
    except (snapshots.SnapshotError, model.InputError) as exc:
        print(f"plsql.explorer: {exc}", file=sys.stderr)
        return 2
    page.write(data, out)
    if not args.quiet:
        counts = data["meta"]["counts"]
        print(f"wrote {out}: tables={counts['tables']} routines={counts['routines']} sql={counts['sql']}"
              + ("" if snapshot else "  (no --snapshot: what only the database knows says 未取得)"))
        if args.src:
            print(f"  the page holds the source files under {args.src}, whole (comments included)")
        else:
            print("  no --src: the page shows SQL statements but not the code around them")
        if snapshot and snapshot.contains_source_code:
            print("  this page holds code taken from the database (USER_SOURCE)")
        if snapshot and snapshot.contains_data_values:
            print("  this page CONTAINS REAL DATA VALUES (column low/high and frequent values)")
        for warning in data["warnings"]:
            print(f"  warning: {warning}")
    return 1 if data["warnings"] else 0


if __name__ == "__main__":
    sys.exit(main())
