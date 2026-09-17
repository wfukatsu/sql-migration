"""P3-3: ask Oracle what it actually does, and write the answers down.

The generated Java reproduces Oracle's expression semantics through `Plsql` -- three-valued comparison, `''`
being NULL, half-up rounding, a DATE that carries a time of day. Those are claims about Oracle, and a test that
asserts them from memory tests the memory. This runs each claim on a real Oracle and records what came back.

    python difftest/plsql_semantics.py                 # -> fixtures/plsql/semantics.json

The file it writes is the fixture both sides are then checked against: `tests/test_plsql_property.py` for the
conversion, `PlsqlPropertyTest` for the runtime. Neither needs a database, and neither gets to decide what
Oracle does. Re-run this when the Oracle version changes -- the answers belong to a version, and the file says
which one.

Cases are generated rather than listed: each family crosses its operators with a value set built to sit on the
edges (NULL, the empty string, a blank string, zero, negatives, the NUMBER precision limits, values past what a
64-bit integer holds, midnight, a time of day). A property that only holds away from the edges is not a
property, and every one of those edges is somewhere a migration has gone wrong.
"""

from __future__ import annotations

import datetime
import decimal
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from difftest.plsql_run import connect, encode  # noqa: E402
from difftest.sources import parse_profile_args, source_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "plsql" / "semantics.json"

# --------------------------------------------------------------------------------------------------
# the value sets
# --------------------------------------------------------------------------------------------------

# `''` is NULL in Oracle and ' ' is not; keeping all three apart is the point of having them here
TEXTS = [None, "", " ", "a", "a ", "A", "ab"]

NUMBERS = [
    None, 0, 1, -1,
    decimal.Decimal("0.5"), decimal.Decimal("-0.5"),
    decimal.Decimal("1.005"), decimal.Decimal("2.675"),   # the classic half-up / half-even disagreements
    decimal.Decimal("1234.565"),                          # rounds into a NUMBER(14,2) the corpus uses
    999999999, 1000000000,                                # the INT boundary the type mapper draws at 9 digits
    999999999999999999, 1000000000000000000,              # the BIGINT boundary, at 18
    decimal.Decimal("99999999999999999999999999999999999999"),   # past 64 bits entirely
]

DATES = [None,
         datetime.datetime(2026, 1, 15, 0, 0, 0),
         datetime.datetime(2026, 1, 15, 9, 30, 0),
         datetime.datetime(2026, 1, 15, 23, 59, 59),
         datetime.datetime(2024, 2, 29, 12, 0, 0)]        # a leap day, which date arithmetic gets wrong


def cases() -> list[dict]:
    """Every case, as {family, op, args, sql}. `sql` is the expression Oracle is asked to evaluate."""
    out: list[dict] = []

    # -- three-valued comparison, over texts and over numbers
    for op, sql in (("eq", "{a} = {b}"), ("ne", "{a} <> {b}"), ("lt", "{a} < {b}"),
                    ("le", "{a} <= {b}"), ("gt", "{a} > {b}"), ("ge", "{a} >= {b}")):
        for values in (TEXTS, NUMBERS[:9]):
            for a in values:
                for b in values:
                    out.append(_bool("comparison", op, [a, b], sql))

    # -- NULL and the empty string
    for value in TEXTS:
        out.append(_bool("null", "is_null", [value], "{a} IS NULL"))
        out.append(_value("null", "nvl", [value, "fallback"], "NVL({a}, {b})"))
        out.append(_value("null", "concat", [value, "x"], "{a} || {b}"))
        out.append(_value("null", "length", [value], "LENGTH({a})"))
        out.append(_value("null", "rtrim", [value], "RTRIM({a})"))

    # -- arithmetic, including NULL propagation and division
    for op, sql in (("add", "{a} + {b}"), ("sub", "{a} - {b}"), ("mul", "{a} * {b}")):
        for a in NUMBERS[:9]:
            for b in NUMBERS[:9]:
                out.append(_value("arithmetic", op, [a, b], sql))
    for a in NUMBERS[:9]:
        for b in NUMBERS[1:9]:
            if b != 0:
                out.append(_value("arithmetic", "div", [a, b], "{a} / {b}"))
                out.append(_value("arithmetic", "mod", [a, b], "MOD({a}, {b})"))

    # -- rounding, which is half-up in Oracle and half-even in Java's default
    for value in NUMBERS:
        for scale in (0, 1, 2):
            out.append(_value("rounding", "round", [value, scale], "ROUND({a}, {b})"))
            out.append(_value("rounding", "trunc_number", [value, scale], "TRUNC({a}, {b})"))

    # -- a DATE carries a time of day, and subtracting two of them gives days with a fraction
    for value in DATES:
        out.append(_value("date", "trunc", [value], "TRUNC({a})"))
        out.append(_value("date", "to_char_date", [value], "TO_CHAR({a}, 'YYYY-MM-DD')"))
        out.append(_value("date", "to_char_datetime", [value], "TO_CHAR({a}, 'YYYY-MM-DD HH24:MI:SS')"))
    for a in DATES[1:]:
        for b in DATES[1:]:
            out.append(_value("date", "sub", [a, b], "{a} - {b}"))

    # -- NUMBER against the storage the migration picks, at and past the boundaries
    for value in NUMBERS:
        out.append(_value("storage", "cast_number_14_2", [value], "CAST({a} AS NUMBER(14,2))"))
    return out


def _bool(family: str, op: str, args: list, sql: str) -> dict:
    """Oracle has no boolean in SQL, so a condition is asked as a CASE returning 1, 0 or NULL."""
    return {"family": family, "op": op, "args": args, "kind": "bool",
            "sql": f"CASE WHEN {sql} THEN 1 ELSE 0 END"}


def _value(family: str, op: str, args: list, sql: str) -> dict:
    return {"family": family, "op": op, "args": args, "kind": "value", "sql": sql}


# --------------------------------------------------------------------------------------------------
# running them
# --------------------------------------------------------------------------------------------------

def evaluate(cur, case: dict) -> dict:
    """One case, evaluated on Oracle. A case Oracle itself rejects is recorded as the error it raised."""
    binds = {chr(ord("a") + i): value for i, value in enumerate(case["args"])}
    sql = case["sql"].format(**{k: f":{k}" for k in binds})
    try:
        cur.execute(f"SELECT {sql} FROM dual", binds)
        return {"value": encode(cur.fetchone()[0])}
    except Exception as e:  # noqa: BLE001 -- the error is the answer for an out-of-range case
        code = getattr(getattr(e, "args", [None])[0], "code", None)
        return {"error": code if code is not None else str(e).split("\n")[0][:120]}


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", action="append")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    cfg = source_config("oracle", parse_profile_args(args.profile), writes=False)
    con = connect(cfg)
    cur = con.cursor()
    cur.execute("SELECT banner FROM v$version WHERE ROWNUM = 1")
    banner = cur.fetchone()[0]

    recorded = []
    for case in cases():
        recorded.append({**case, "args": [encode(a) for a in case["args"]], "oracle": evaluate(cur, case)})
    con.close()

    families = sorted({c["family"] for c in recorded})
    document = {"source": banner, "session": {"nls_numeric_characters": ". ", "time_zone": "UTC"},
                "families": families, "cases": recorded}
    out = Path(args.out)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(recorded)} case(s) across {len(families)} famil(ies) -> {out}")
    print(f"  {banner}")
    for family in families:
        print(f"  {family:<12} {sum(1 for c in recorded if c['family'] == family)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
