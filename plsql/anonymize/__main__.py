"""Anonymise a tree of PL/SQL and DDL, and check that the tool still concludes the same (#15).

    python -m plsql.anonymize <real-src> --out <anonymised-dir> --mapping-out /secure/dir/mapping.json

Exit status: 0 anonymised and the analysis is unchanged, 1 anonymised but the analysis differs or an original name
survives (read the report; nothing should be committed yet), 2 nothing was written.

The policy -- what is replaced, who reads the result, where the original and the mapping live -- is
docs/design/plsql-corpus-anonymization.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import AnonymizeError, Mapping, anonymize_tree, leaks, write_mapping
from .check import compare

REPOSITORY = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plsql.anonymize", description=__doc__.splitlines()[0])
    parser.add_argument("src", help="the original tree. It is read and never written to")
    parser.add_argument("--out", required=True, help="where the anonymised tree goes (must not be inside src)")
    parser.add_argument("--mapping-out", help="write original -> replacement here. Refused inside this repository: "
                                              "the mapping undoes the anonymisation")
    parser.add_argument("--keep", action="append", default=[], metavar="NAME",
                        help="a name that is Oracle's (or otherwise must stay); repeatable")
    parser.add_argument("--no-check", action="store_true", help="do not compare the analysis of the two trees")
    args = parser.parse_args(argv)

    mapping = Mapping()
    mapping.keep |= {name.upper() for name in args.keep}
    try:
        if args.mapping_out:   # refused before anything is written, not after
            resolved = Path(args.mapping_out).resolve()
            if REPOSITORY == resolved or REPOSITORY in resolved.parents:
                raise AnonymizeError(f"{resolved} is inside the repository. The mapping undoes the anonymisation: "
                                     "keep it where the original code is kept, never here")
        mapping, written = anonymize_tree(args.src, args.out, mapping)
        if args.mapping_out:
            write_mapping(mapping, args.mapping_out, REPOSITORY)
    except AnonymizeError as exc:
        print(f"plsql.anonymize: {exc}", file=sys.stderr)
        return 2

    print(f"anonymised {len(written)} file(s) into {args.out}: {len(mapping.names)} names, "
          f"{len(mapping.strings)} string literals, {len(mapping.numbers)} numbers replaced; comments removed")
    print("  mapping: " + (f"written to {args.mapping_out}" if args.mapping_out else
                           "not written (--mapping-out). Without it the result cannot be traced back"))
    status = 0
    survivors = leaks(args.out, mapping)
    if survivors:
        status = 1
        print(f"  {len(survivors)} original name(s) or literal(s) still appear in the output -- look at each:")
        for item in survivors[:20]:   # the replacement side only: this output may be pasted somewhere
            where = mapping.names.get(item["original"]) or mapping.strings.get(item["original"])
            print(f"    {item['file']}: a {item['kind']} that was mapped to {where} appears unreplaced")
    if not args.no_check:
        problems = compare(args.src, args.out, mapping)
        if problems:
            status = 1
            print(f"  the analysis of the anonymised tree DIFFERS from the original in {len(problems)} place(s).")
            print("  Either a name that is Oracle's was replaced (--keep NAME), or a rule depends on a name:")
            for problem in problems[:20]:
                print(f"    {problem}")
        else:
            print("  analysis unchanged: same statements, tables read and written, locks, diagnostics, rules and "
                  "verdicts, routine by routine")
    print("  A person still has to read the result before it is committed "
          "(docs/design/plsql-corpus-anonymization.md).")
    return status


if __name__ == "__main__":
    sys.exit(main())
