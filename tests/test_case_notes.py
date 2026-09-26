"""difftest/case_notes.py: what a case may declare about a statement (Issues #57 and #58), and that a declaration
only ever weakens the comparison it names -- a real difference outside it still fails.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "difftest"))
import case_notes  # noqa: E402
from case_notes import NoteError, parse, weaken  # noqa: E402
import rowcompare  # noqa: E402


def _diff(expected, actual, ordered, sql, columns):
    note = parse(sql).nondeterministic
    e, a = weaken(expected, actual, note, columns)
    return rowcompare.difference(e, a, ordered and not note.unordered and not note.count_only)


def test_nondeterministic_flags_and_a_reason_with_semicolons():
    note = parse("-- A-4\n-- @nondeterministic: unordered; ignore=rn, 3; reason=同順位; 並びは決まらない\nSELECT 1").nondeterministic
    assert (note.unordered, note.ignore, note.count_only) == (True, ("rn", "3"), False)
    assert note.reason == "同順位; 並びは決まらない"
    assert parse("SELECT 1") == case_notes.Notes()


@pytest.mark.parametrize("line", [
    "-- @nondeterministic: unordered",                        # no reason
    "-- @nondeterministic: unordered; reason=",               # empty reason
    "-- @nondeterministic: reason=何も言わない",               # no way to compare
    "-- @nondeterministic: sorted; reason=x",                 # unknown flag
    "-- @source-rejects: reason=x",                           # no kind
    "-- @source-rejects: flaky; reason=x",                    # unknown kind
    "-- @nondeterministic: count; reason=a\n-- @nondeterministic: count; reason=b",
])
def test_a_declaration_that_cannot_be_read_is_refused(line):
    with pytest.raises(NoteError):
        parse(line + "\nSELECT 1")


def test_ties_pass_unordered_but_other_rows_still_fail():
    sql = "-- @nondeterministic: unordered; reason=同順位\nSELECT"
    expected = [("King", Decimal(24000)), ("Kochhar", Decimal(17000)), ("De Haan", Decimal(17000))]
    assert rowcompare.difference(expected, [["King", 24000], ["De Haan", 17000], ["Kochhar", 17000]], True)
    assert _diff(expected, [["King", 24000], ["De Haan", 17000], ["Kochhar", 17000]], True, sql, ["LAST_NAME", "SALARY"]) is None
    assert _diff(expected, [["King", 24000], ["De Haan", 17000], ["Kochhar", 17001]], True, sql, ["LAST_NAME", "SALARY"])


def test_ignored_columns_by_name_or_position_and_the_order_still_counts():
    cols = ["LAST_NAME", "SALARY"]
    expected = [("Hunold", 9000), ("Tuvault", 7000)]
    for spec in ("last_name", "1"):
        sql = f"-- @nondeterministic: ignore={spec}; reason=境目の同順位\nSELECT"
        assert _diff(expected, [["Hunold", 9000], ["Grant", 7000]], True, sql, cols) is None
        assert _diff(expected, [["Grant", 7000], ["Hunold", 9000]], True, sql, cols), "still compared in order"
        assert _diff(expected, [["Hunold", 9000], ["Grant", 6000]], True, sql, cols), "salary is still compared"


def test_count_compares_the_row_count_only():
    sql = "-- @nondeterministic: count; reason=ROWNUM に ORDER BY が無い\nSELECT"
    assert _diff([(1, "a"), (2, "b")], [[3, "c"], [4, "d"]], False, sql, ["ID", "V"]) is None
    assert _diff([(1, "a"), (2, "b")], [[3, "c"]], False, sql, ["ID", "V"])


def test_a_result_of_another_width_is_not_cut_into_agreement():
    sql = "-- @nondeterministic: ignore=v; reason=x\nSELECT"
    assert _diff([(1, "a")], [[1]], True, sql, ["ID", "V"]), "the actual side lost a column; that must show"


@pytest.mark.parametrize("spec,columns", [("nope", ["ID"]), ("3", ["ID", "V"]), ("id", ["ID", "id"])])
def test_an_ignored_column_must_name_exactly_one_column(spec, columns):
    note = parse(f"-- @nondeterministic: ignore={spec}; reason=x\nSELECT").nondeterministic
    with pytest.raises(NoteError):
        case_notes.ignored_positions(note, columns)


def test_source_rejects_kinds():
    rej = parse("-- @source-rejects: harness; reason=ORA-01466\nSELECT").source_rejects
    assert (rej.kind, rej.label(), rej.reason) == ("harness", "ハーネスの都合", "ORA-01466")
    assert parse("-- @source-rejects: sample; reason=予約語\nSELECT").source_rejects.label() == "移行元の不備"


def test_the_oracle_samples_case_declares_every_known_nondeterminism():
    """The five statements #57 names and the two #58 names; each declaration reads."""
    from scalardb_migrate.converter import convert_script

    text = (ROOT / "samples/oracle-samples/sql/queries-check.sql").read_text(encoding="utf-8")
    results, _ = convert_script(text, "oracle")
    notes = {r.index: parse(r.source_sql) for r in results}
    assert sorted(i for i, n in notes.items() if n.nondeterministic) == [10, 13, 34, 37, 38]
    assert {i: n.source_rejects.kind for i, n in notes.items() if n.source_rejects} == {33: "sample", 44: "harness"}
