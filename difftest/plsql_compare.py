"""P3-2: compare what Oracle did with what the generated Java did on ScalarDB.

P0-5 captured every scenario on Oracle; P3-1 captured the same scenarios on a real ScalarDB Cluster, in the same
canonical form. This puts the two side by side and says where they disagree. Nothing here runs anything: both
sides are already recorded, so a comparison cannot change what it is comparing.

    python difftest/plsql_compare.py --variant scaled
    python difftest/plsql_compare.py --variant scaled --variant double --json difftest/work/plsql-diff.json

## What is compared

Everything the capture holds, because a migration is wrong in whichever part nobody looked at:

* the return value, and each OUT / IN OUT value by name
* whether an exception was raised, and its business error code
* every captured table: its columns, its rows as a multiset, and each value
* NULL against the empty string against trailing whitespace -- Oracle stores `''` as NULL and the target may
  not, so these are never normalised together
* numbers by value, and separately by scale. `190` against `190.5` is a difference. `190` against `190.00` is
  not evidence of anything: Oracle's driver returns a NUMBER(14,2) as `190`, having dropped the trailing zeros
  the column actually stores, so the scale that reaches the capture describes the driver rather than the data.
  Scale-only differences are counted and reported under their own heading, never mixed in with the rest.
* timestamps exactly, to the microsecond

## What is deliberately not compared

* columns the scenario masked, because their value comes from a clock the harness cannot pin. The mask *sets*
  are compared, so a column masked on one side only is a difference.
* how a value was encoded. Oracle's driver returns NUMBER as a decimal and ScalarDB returns BIGINT as an
  integer; that is a fact about the two drivers, already known, and reporting it on every row would drown the
  differences that matter.

## Storage conventions

A money column under the scaled convention holds cents, so the capture holds cents. Comparing that against
Oracle's `190.00` would report a hundredfold difference on every money column -- a difference in storage, not in
behaviour. The scale is read back off the Oracle DDL and undone before comparing, which is the same map the
generator binds with. Under the `double` convention nothing is undone, and whatever rounding DOUBLE introduces
shows up as a value difference, which is exactly the thing the PoC is measuring.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from difftest.plsql_schema import decimal_columns  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "plsql"
GOLDEN = FIXTURES / "golden"
WORK = ROOT / "difftest" / "work"
DDL = FIXTURES / "src" / "schema.sql"


# --------------------------------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------------------------------

def decode(value):
    """The canonical encoding back into something comparable. Unknown tags stay as they are."""
    if isinstance(value, dict):
        if "$dec" in value:
            return Decimal(value["$dec"])
        if "$ts" in value or "$date" in value or "$raw" in value or "$str" in value:
            return next(iter(value.values()))
        if "$masked" in value:
            return MASKED
    return value


class _Masked:
    def __repr__(self):
        return "<masked>"


MASKED = _Masked()


def show(value) -> str:
    """One value, written so that NULL, '' and ' ' cannot be mistaken for each other."""
    if value is None:
        return "NULL"
    if value is MASKED:
        return "<masked>"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, Decimal):
        return str(value)
    return repr(value)


def difference(expected, actual) -> str | None:
    """None when the two mean the same thing, otherwise how they differ.

    The distinction that earns its keep here is between a number that differs in value and one that differs only
    in how many decimals it was written with. Both are reported, but never as the same kind of thing.
    """
    if expected is MASKED or actual is MASKED:
        return None if expected is MASKED and actual is MASKED else "masked on one side only"
    if expected is None or actual is None:
        return None if expected is None and actual is None else "value"
    if isinstance(expected, bool) or isinstance(actual, bool):
        return None if expected == actual else "value"
    if isinstance(expected, (int, float, Decimal)) and isinstance(actual, (int, float, Decimal)):
        left, right = Decimal(str(expected)), Decimal(str(actual))
        if left != right:
            return "value"
        return None if left.as_tuple().exponent == right.as_tuple().exponent else "scale"
    if isinstance(expected, str) and isinstance(actual, str):
        if expected == actual:
            return None
        return "whitespace" if expected.strip() == actual.strip() else "value"
    return None if expected == actual else "type"


# --------------------------------------------------------------------------------------------------
# one scenario
# --------------------------------------------------------------------------------------------------

def unscale(capture: dict, scales: dict[str, dict[str, int]]) -> dict:
    """Undo the storage scale, so the comparison is between logical values."""
    for table, dumped in capture.get("tables", {}).items():
        by_name = scales.get(table, {})
        if not by_name:
            continue
        columns = dumped.get("columns") or []
        for row in dumped.get("rows") or []:
            for column, scale in by_name.items():
                if column not in columns:
                    continue
                index = columns.index(column)
                value = row[index]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    row[index] = {"$dec": str(Decimal(str(value)).scaleb(-scale))}
    return capture


def compare_capture(oracle: dict, target: dict) -> list[str]:
    """Every way the two captures disagree, in the wording GoldenCheck uses."""
    diffs: list[str] = []
    _compare_pinned(oracle, target, diffs)
    _compare_result(oracle, target, diffs)
    _compare_exception(oracle, target, diffs)
    _compare_masks(oracle, target, diffs)
    _compare_tables(oracle, target, diffs)
    return diffs


def _compare_pinned(oracle: dict, target: dict, diffs: list[str]) -> None:
    """Two runs of different setups are not comparable, however well their results happen to match."""
    left, right = oracle.get("pinned") or {}, target.get("pinned") or {}
    if left != right:
        diffs.append(f"pinned: expected={json.dumps(left, sort_keys=True)} "
                     f"actual={json.dumps(right, sort_keys=True)}")


def _compare_result(oracle: dict, target: dict, diffs: list[str]) -> None:
    left, right = oracle.get("result") or {}, target.get("result") or {}
    kind = difference(decode(left.get("returned")), decode(right.get("returned")))
    if kind:
        diffs.append(f"returned ({kind}): expected={show(decode(left.get('returned')))} "
                     f"actual={show(decode(right.get('returned')))}")
    out_left, out_right = left.get("out") or {}, right.get("out") or {}
    for name in sorted(set(out_left) | set(out_right)):
        if name not in out_left:
            diffs.append(f"out {name}: unexpected (actual only) = {show(decode(out_right[name]))}")
        elif name not in out_right:
            diffs.append(f"out {name}: missing (expected only) = {show(decode(out_left[name]))}")
        else:
            kind = difference(decode(out_left[name]), decode(out_right[name]))
            if kind:
                diffs.append(f"out {name} ({kind}): expected={show(decode(out_left[name]))} "
                             f"actual={show(decode(out_right[name]))}")


def _compare_exception(oracle: dict, target: dict, diffs: list[str]) -> None:
    """The business error code is the contract; the message text is not, and is shown only for context."""
    left, right = oracle.get("exception"), target.get("exception")
    if left is None and right is None:
        return
    if left is None:
        diffs.append(f"exception: expected=none actual={right.get('code')} ({right.get('message')})")
        return
    if right is None:
        diffs.append(f"exception: expected={left.get('code')} ({left.get('message')}) actual=none")
        return
    if left.get("code") != right.get("code"):
        diffs.append(f"exception code: expected={left.get('code')} actual={right.get('code')} "
                     f"({right.get('message')})")


def _compare_masks(oracle: dict, target: dict, diffs: list[str]) -> None:
    left = {t: sorted(c) for t, c in (oracle.get("masked") or {}).items()}
    right = {t: sorted(c) for t, c in (target.get("masked") or {}).items()}
    if left != right:
        diffs.append(f"masked columns: expected={json.dumps(left, sort_keys=True)} "
                     f"actual={json.dumps(right, sort_keys=True)}")


def _compare_tables(oracle: dict, target: dict, diffs: list[str]) -> None:
    left, right = oracle.get("tables") or {}, target.get("tables") or {}
    for table in sorted(set(left) | set(right)):
        if table not in left:
            diffs.append(f"table {table}: unexpected (actual only)")
            continue
        if table not in right:
            diffs.append(f"table {table}: missing (expected only)")
            continue
        _compare_one_table(table, left[table], right[table], diffs)


def _compare_one_table(table: str, expected: dict, actual: dict, diffs: list[str]) -> None:
    columns = expected.get("columns") or []
    if columns and (actual.get("columns") or []) and columns != actual["columns"]:
        diffs.append(f"table {table} columns: expected={columns} actual={actual['columns']}")
        return  # comparing rows positionally against a different column order says nothing
    rows_left = [[decode(v) for v in row] for row in expected.get("rows") or []]
    rows_right = [[decode(v) for v in row] for row in actual.get("rows") or []]
    if len(rows_left) != len(rows_right):
        diffs.append(f"table {table} row count: expected={len(rows_left)} actual={len(rows_right)}")

    # rows are a multiset: pair each expected row with an actual one that differs least, so that a single
    # changed column is reported as a changed column and not as one row missing and another unexpected
    remaining = list(rows_right)
    for row in rows_left:
        best, score = None, None
        for candidate in remaining:
            n = sum(1 for a, b in zip(row, candidate) if difference(a, b))
            if score is None or n < score:
                best, score = candidate, n
        if best is None:
            diffs.append(f"table {table}: missing (expected only): {_row(columns, row)}")
            continue
        remaining.remove(best)
        if score == 0:
            continue
        details = [f"{columns[i] if i < len(columns) else i}: expected={show(a)} actual={show(b)} ({kind})"
                   for i, (a, b) in enumerate(zip(row, best)) if (kind := difference(a, b))]
        diffs.append(f"table {table} row {_row(columns, row)}: " + "; ".join(details))
    for row in remaining:
        diffs.append(f"table {table}: unexpected (actual only): {_row(columns, row)}")


def _row(columns: list[str], row: list) -> str:
    """Identify a row by its first column, which is its key in every corpus table."""
    return f"{columns[0] if columns else 'row'}={show(row[0]) if row else '?'}"


# --------------------------------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------------------------------

def expected_verdicts() -> dict[str, str]:
    """{unit.routine: AUTO|REVIEW|REDESIGN} from the corpus manifest."""
    manifest = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
    out = {}
    for unit in manifest.get("units") or []:
        for routine in unit.get("routines") or []:
            out[f"{unit['name']}.{routine['name']}"] = routine.get("expected", "REVIEW")
    return out


def compare_variant(variant: str, scales: dict) -> dict:
    captures = WORK / f"plsql-scalardb-{variant}"
    if not captures.is_dir():
        raise SystemExit(f"{captures} is missing; run `python difftest/plsql_capture.py --variant {variant}`")
    unrunnable = json.loads((captures / "unrunnable.json").read_text(encoding="utf-8")) \
        if (captures / "unrunnable.json").exists() else {}
    verdicts = expected_verdicts()

    report = {"variant": variant, "scenarios": {}, "not_compared": {}}
    for golden in sorted(GOLDEN.glob("*.json")):
        name = golden.stem
        oracle = json.loads(golden.read_text(encoding="utf-8"))
        routine = f"{oracle['unit']}.{oracle['routine']}"
        target = captures / f"{name}.json"
        if not target.exists():
            report["not_compared"][name] = {
                "routine": routine, "verdict": verdicts.get(routine, "REVIEW"),
                "reason": unrunnable.get(name, "no ScalarDB capture")}
            continue
        actual = unscale(json.loads(target.read_text(encoding="utf-8")), scales) if variant == "scaled" \
            else json.loads(target.read_text(encoding="utf-8"))
        found = compare_capture(oracle, actual)
        report["scenarios"][name] = {
            "routine": routine, "verdict": verdicts.get(routine, "REVIEW"),
            "differences": [d for d in found if not _scale_only(d)],
            "scale_only": [d for d in found if _scale_only(d)]}
    return report


# `column: expected=... actual=... (kind)` -- the kind this comparison assigned to one difference
KIND = re.compile(r"\((value|scale|whitespace|type|masked on one side only)\)$")
# `returned (kind): ...` / `out p_x (kind): ...` -- the same kind, written before the colon. Two places write a
# kind and they do not put it in the same spot; recognising only the trailing one left every scale-only
# *return value* counted as a difference (2026-09-19: `order_total` returning 900 against 900.00)
LEADING_KIND = re.compile(r"^(?:returned|out \S+) \((value|scale|whitespace|type|masked on one side only)\):")


def _scale_only(line: str) -> bool:
    """A line whose every reported difference is `(scale)` says nothing about the data (see the module docs).

    Matching the kinds this module writes, rather than "ends with a bracket": an exception message can end with
    one too, and reading that as a kind made the comparison crash on a capture that happened to contain one.
    """
    leading = LEADING_KIND.match(line)
    if leading:
        return leading.group(1) == "scale"
    kinds = [m.group(1) for part in line.split("; ") if (m := KIND.search(part))]
    return bool(kinds) and all(kind == "scale" for kind in kinds)


def render(report: dict) -> int:
    """Print the report and return the number of AUTO scenarios that disagreed."""
    variant = report["variant"]
    matched = [n for n, s in report["scenarios"].items() if not s["differences"]]
    differing = {n: s for n, s in report["scenarios"].items() if s["differences"]}
    auto_failures = [n for n, s in differing.items() if s["verdict"] == "AUTO"]

    print(f"\n=== {variant} ===")
    print(f"{len(report['scenarios'])} compared: {len(matched)} identical, {len(differing)} differing; "
          f"{len(report['not_compared'])} not compared")

    for name, scenario in sorted(differing.items()):
        marker = "!!" if scenario["verdict"] == "AUTO" else "  "
        print(f"{marker} {name}  [{scenario['verdict']}] {scenario['routine']}")
        for line in scenario["differences"]:
            print(f"     {line}")

    scale_only = {n: s["scale_only"] for n, s in report["scenarios"].items() if s.get("scale_only")}
    if scale_only:
        print(f"\n  {len(scale_only)} scenario(s) differ only in how many decimals a number was written with.")
        print("  Oracle's driver drops a NUMBER(14,2)'s trailing zeros, so this describes the driver, not the data.")

    if report["not_compared"]:
        print("\n  not compared:")
        for name, scenario in sorted(report["not_compared"].items()):
            print(f"    {name:<34} [{scenario['verdict']}] {scenario['reason'][:110]}")

    if auto_failures:
        print(f"\n  {len(auto_failures)} AUTO scenario(s) disagree with Oracle: {', '.join(sorted(auto_failures))}")
        print("  An AUTO verdict claims the routine needs no human review. A difference here is either a bug in")
        print("  the generator or a verdict that should not have been AUTO; it is never something to accept.")
    return len(auto_failures)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", action="append", choices=["scaled", "double"],
                    help="repeatable; the default compares both")
    ap.add_argument("--json", help="write the full report here, for P3-5")
    args = ap.parse_args(argv)

    scales = decimal_columns(DDL)
    reports = [compare_variant(v, scales) for v in (args.variant or ["scaled", "double"])]
    failures = sum(render(r) for r in reports)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({r["variant"]: r for r in reports}, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        print(f"\nreport written to {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
