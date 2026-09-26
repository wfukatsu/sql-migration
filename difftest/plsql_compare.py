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
        if "$rows" in value:
            return Rows(tuple(decode(v) for v in row) for row in value["$rows"])
        if "$dec" in value:
            return Decimal(value["$dec"])
        if "$ts" in value or "$date" in value or "$raw" in value or "$str" in value:
            return next(iter(value.values()))
        if "$masked" in value:
            return MASKED
    return value


class Rows(list):
    """A collection a function returned, as rows (#54). Compared as a multiset: a query without ORDER BY does not
    fix the order Oracle piped its rows in, and neither side is wrong about it."""


def _row_key(row) -> str:
    return repr(tuple(v.normalize() if isinstance(v, Decimal) else v for v in row))


def _rows_difference(expected: "Rows", actual: "Rows") -> str | None:
    if len(expected) != len(actual):
        return "value"
    worst = None
    for left, right in zip(sorted(expected, key=_row_key), sorted(actual, key=_row_key)):
        if len(left) != len(right):
            return "type"
        for a, b in zip(left, right):
            kind = difference(a, b)
            if kind in ("value", "type"):
                return kind
            worst = worst or kind
    return worst


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
    if isinstance(expected, Rows) or isinstance(actual, Rows):
        return _rows_difference(expected, actual) if isinstance(expected, Rows) and isinstance(actual, Rows) \
            else "type"
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
        if _same_day(expected, actual):
            return None
        return "whitespace" if expected.strip() == actual.strip() else "value"
    return None if expected == actual else "type"


_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00:00(?:\.0+)?$")


def _same_day(a: str, b: str) -> bool:
    """An Oracle DATE (`2013-06-17T00:00:00`, a timestamp with no time of day) against a ScalarDB DATE column
    (`2013-06-17`): the same day, written by two different column types (#42). rowcompare has the same rule."""
    for stamp, day in ((a, b), (b, a)):
        m = _MIDNIGHT.match(stamp)
        if m and m.group(1) == day:
            return True
    return False


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


# One condition, two numbers. Oracle raises NO_DATA_FOUND to the client as ORA-01403 while PL/SQL's own SQLCODE for
# it is +100 -- the number a handler reads, and so the one the generated exception carries. No other predefined
# exception is split like this.
CLIENT_CODE_FOR_SQLCODE = {-1403: 100}


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
    if CLIENT_CODE_FOR_SQLCODE.get(left.get("code"), left.get("code")) != right.get("code"):
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
        # a `(scale)` column says nothing about the data (module docs); reported on a line of its own so that a
        # row with one real difference and one scale difference is not counted as two real ones (2026-09-25:
        # `old_salary 6000 / 6000.0` next to a NULL DEFAULT column made every such row look like a value difference)
        scale = [d for d in details if d.endswith("(scale)")]
        real = [d for d in details if not d.endswith("(scale)")]
        for part in (real, scale):
            if part:
                diffs.append(f"table {table} row {_row(columns, row)}: " + "; ".join(part))
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
    if not (FIXTURES / "manifest.yaml").exists():   # a project from outside states no expectations
        return {}
    manifest = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
    out = {}
    for unit in manifest.get("units") or []:
        for routine in unit.get("routines") or []:
            out[f"{unit['name']}.{routine['name']}"] = routine.get("expected", "REVIEW")
    return out


def use_project(directory: str | Path) -> Path:
    """Compare a project other than the corpus: DIR/golden against DIR/work/plsql-scalardb-<variant>."""
    global FIXTURES, GOLDEN, WORK, DDL
    FIXTURES = Path(directory).resolve()
    GOLDEN, WORK, DDL = FIXTURES / "golden", FIXTURES / "work", FIXTURES / "src" / "schema.sql"
    return FIXTURES


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
        # a scenario the capture run named as unrunnable is not compared even if a file of that name is there:
        # it can only be a leftover of an earlier run
        if name in unrunnable or not target.exists():
            report["not_compared"][name] = {
                "routine": routine, "verdict": verdicts.get(routine, "REVIEW"),
                "reason": unrunnable.get(name, "no ScalarDB capture")}
            continue
        actual = unscale(json.loads(target.read_text(encoding="utf-8")), scales) if variant == "scaled" \
            else json.loads(target.read_text(encoding="utf-8"))
        nondeterministic = _nondeterministic(name)
        if nondeterministic:
            oracle, actual = (_without(c, nondeterministic["ignore_columns"]) for c in (oracle, actual))
        found = compare_capture(oracle, actual)
        accepted = _accepted(name, oracle, actual)
        report["scenarios"][name] = {
            "routine": routine, "verdict": verdicts.get(routine, "REVIEW"),
            "differences": [d for d in found if not _scale_only(d) and not (accepted and _is_exception_code(d))],
            # a difference somebody decided to live with is not hidden: it moves here, with who decided and why
            "accepted": [{"difference": d, **accepted} for d in found if accepted and _is_exception_code(d)],
            "scale_only": [d for d in found if _scale_only(d)],
            "direct": _is_direct_dml(name)}
        if nondeterministic:
            report["scenarios"][name]["nondeterministic"] = nondeterministic
    # what the captures were taken from (plsql_capture.py). Absent for captures older than the fingerprint --
    # `plsql.cli --evidence` then counts none of this report, which is the point: nobody can say what it measured
    recorded = captures / "fingerprint.json"
    if recorded.exists():
        report["fingerprints"] = json.loads(recorded.read_text(encoding="utf-8"))
    return report


def _nondeterministic(name: str) -> dict | None:
    """The scenario's `nondeterministic` declaration: which columns Oracle itself does not fix, and why.

    `SELECT … WHERE status = 'NEW' AND ROWNUM <= :n` with no ORDER BY claims *some* n rows; which ones is the heap
    order in Oracle and the scan order in ScalarDB, and neither is wrong (fixtures `stock_claim_batch`, 2026-09-26,
    matched under one money convention and not the other). The declared columns are left out of both tables, which
    are compared as multisets anyway, so what is still checked is how many rows changed and to what. A reason is
    required; a declaration without one is refused rather than read as permission.
    """
    import yaml

    path = FIXTURES / "scenarios" / f"{name}.yaml"
    if not path.exists():
        return None
    declared = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("nondeterministic")
    if not declared:
        return None
    columns = declared.get("ignore_columns") or {}
    if not str(declared.get("reason") or "").strip() or not columns:
        raise SystemExit(f"{path}: nondeterministic needs a reason and ignore_columns ({{table: [column, ...]}})")
    return {"reason": " ".join(str(declared["reason"]).split()), "decided": str(declared.get("decided", "")),
            "ignore_columns": {t.lower(): [c.lower() for c in cs] for t, cs in columns.items()}}


def _without(capture: dict, ignore: dict[str, list[str]]) -> dict:
    """The capture with the declared columns removed from the declared tables."""
    tables = {}
    for table, body in (capture.get("tables") or {}).items():
        drop = set(ignore.get(table.lower(), []))
        columns = body.get("columns") or []
        if not drop or not columns:
            tables[table] = body
            continue
        keep = [i for i, c in enumerate(columns) if c.lower() not in drop]
        tables[table] = {**body, "columns": [columns[i] for i in keep],
                         "rows": [[row[i] for i in keep] for row in body.get("rows") or []]}
    return {**capture, "tables": tables}


def _is_exception_code(line: str) -> bool:
    return line.startswith("exception code: ")


def _accepted(name: str, oracle: dict, target: dict) -> dict | None:
    """The scenario's `accepted_difference`, if it names exactly the two error codes that were captured.

    The target cannot always raise what Oracle raised -- ORA-02055 belongs to a DB link that no longer exists.
    Whether that is acceptable is a person's call, so it is written in the scenario with its reason, and it covers
    one pair of codes only: any other code on either side, or any difference in the tables or the result, is
    still a difference.
    """
    import yaml

    path = FIXTURES / "scenarios" / f"{name}.yaml"
    if not path.exists():
        return None
    declared = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("accepted_difference") or {}
    pair = declared.get("exception") or {}
    left, right = oracle.get("exception") or {}, target.get("exception") or {}
    if not pair or left.get("code") != pair.get("oracle") or right.get("code") != pair.get("target"):
        return None
    return {"reason": declared.get("reason", ""), "decided": str(declared.get("decided", ""))}


def _is_direct_dml(name: str) -> bool:
    """シナリオが routine を呼ばず、**素の DML だけ**を流すものか（trigger のシナリオ）。

    その経路は PL/SQL の外から表へ直接書くもので、移行先の trigger は掛からない（#12 §0）。差が出るのは
    **決めたとおり**であって、生成器の不具合ではない。見分けて書かないと、読む人が不具合として追う。
    """
    import yaml

    from difftest.plsql_setup import BLOCK, DML

    path = FIXTURES / "scenarios" / f"{name}.yaml"
    if not path.exists():
        return False
    call = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("call") or {}
    matched = BLOCK.match(call.get("body") or "") if call.get("kind") == "block" else None
    parts = [p.strip() for p in matched.group("body").split(";") if p.strip()] if matched else []
    return bool(parts) and all(DML.match(p) for p in parts)


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
        note = "  -- 直接の DML: 移行先の trigger は掛からない（#12 §0 で決めた穴）" \
            if scenario.get("direct") else ""
        print(f"{marker} {name}  [{scenario['verdict']}] {scenario['routine']}{note}")
        for line in scenario["differences"]:
            print(f"     {line}")

    for name, scenario in sorted(report["scenarios"].items()):
        declared = scenario.get("nondeterministic")
        if declared:
            ignored = "、".join(f"{t}.{c}" for t, cs in declared["ignore_columns"].items() for c in cs)
            print(f"   {name}  [{scenario['verdict']}] 非決定として比べない列: {ignored}")
            print(f"     {declared['reason']}")
        for item in scenario.get("accepted") or []:
            print(f"   {name}  [{scenario['verdict']}] 受け入れた差（{item['decided']}）: {item['difference'][:90]}")
            print(f"     {item['reason']}")

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
    # An AUTO routine whose scenario could not run has not been shown to agree with Oracle. Leaving it out of the
    # count let a run with nothing compared for it exit 0.
    auto_unverified = sorted(n for n, s in report["not_compared"].items() if s["verdict"] == "AUTO")
    if auto_unverified:
        print(f"\n  {len(auto_unverified)} AUTO scenario(s) were not compared at all: {', '.join(auto_unverified)}")
    if not report["scenarios"]:
        print("\n  nothing was compared for this variant")
    return len(auto_failures) + len(auto_unverified) + (0 if report["scenarios"] else 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", action="append", choices=["scaled", "double"],
                    help="repeatable; the default compares both")
    ap.add_argument("--json", help="write the full report here, for P3-5")
    ap.add_argument("--project", metavar="DIR", help="a project other than the corpus (see plsql_run.py --project)")
    args = ap.parse_args(argv)
    if args.project:
        use_project(args.project)

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
