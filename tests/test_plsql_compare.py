"""P3-2: comparing an Oracle capture with a ScalarDB one.

The comparison decides what counts as a difference, so what it does *not* report matters as much as what it
does. Each dimension the plan names has a test here, and so does each thing deliberately left uncompared.
"""

from decimal import Decimal

import pytest

from difftest.plsql_compare import _scale_only, compare_capture, decode, difference, show, unscale


def capture(**overrides):
    base = {
        "scenario": "s", "unit": "u", "routine": "r", "source": "oracle",
        "pinned": {"sysdate": "2026-01-15 09:30:00", "sequences": {}},
        "result": {"returned": None, "out": {}},
        "exception": None,
        "tables": {},
        "masked": {},
    }
    base.update(overrides)
    return base


def table(columns, rows):
    return {"columns": columns, "rows": rows}


# ---------------------------------------------------------------- values
def test_null_and_empty_string_are_not_the_same():
    """Oracle stores '' as NULL, so a target that keeps them apart is a difference worth seeing."""
    assert difference(None, "") == "value"
    assert difference("", None) == "value"


def test_trailing_whitespace_is_reported_as_its_own_kind():
    assert difference("GOLD", "GOLD ") == "whitespace"
    assert difference("GOLD", "SILVER") == "value"


def test_numbers_differing_in_value_and_in_scale_are_told_apart():
    assert difference(Decimal("190"), Decimal("190.5")) == "value"
    assert difference(Decimal("190"), Decimal("190.00")) == "scale"
    assert difference(Decimal("190.00"), Decimal("190.00")) is None


def test_an_integer_and_a_decimal_of_the_same_value_agree():
    """Oracle's driver returns NUMBER as a decimal and ScalarDB returns BIGINT as an integer."""
    assert difference(Decimal("1001"), 1001) is None


def test_masked_on_one_side_only_is_a_difference():
    assert difference(decode({"$masked": "x"}), decode({"$masked": "x"})) is None
    assert difference(decode({"$masked": "x"}), "value") == "masked on one side only"


def test_show_distinguishes_null_empty_and_blank():
    assert show(None) == "NULL"
    assert show("") == "''"
    assert show(" ") == "' '"


# ---------------------------------------------------------------- captures
def test_identical_captures_have_no_differences():
    assert compare_capture(capture(), capture()) == []


def test_a_different_return_value_is_reported():
    diffs = compare_capture(capture(result={"returned": {"$dec": "2"}, "out": {}}),
                            capture(result={"returned": {"$dec": "3"}, "out": {}}))
    assert len(diffs) == 1 and "returned (value)" in diffs[0]
    assert "expected=2" in diffs[0] and "actual=3" in diffs[0]


def test_out_parameters_are_compared_by_name():
    diffs = compare_capture(capture(result={"returned": None, "out": {"p_count": 2}}),
                            capture(result={"returned": None, "out": {"p_count": 3}}))
    assert any("out p_count (value)" in d for d in diffs)


def test_a_missing_out_parameter_is_reported_as_missing():
    diffs = compare_capture(capture(result={"returned": None, "out": {"p_count": 2}}),
                            capture(result={"returned": None, "out": {}}))
    assert any("out p_count: missing (expected only)" in d for d in diffs)


def test_the_business_error_code_is_the_contract_not_the_message():
    raised = {"code": -20022, "message": "ORA-20022: order is not open"}
    same_code = {"code": -20022, "message": "order 1001 is not open"}
    assert compare_capture(capture(exception=raised), capture(exception=same_code)) == []
    other = {"code": -20023, "message": "x"}
    assert any("exception code" in d for d in compare_capture(capture(exception=raised), capture(exception=other)))


def test_an_exception_where_oracle_raised_none_is_reported():
    diffs = compare_capture(capture(), capture(exception={"code": -1422, "message": "too many rows"}))
    assert any("exception: expected=none" in d for d in diffs)


def test_a_missing_exception_is_reported_the_other_way():
    diffs = compare_capture(capture(exception={"code": -20022, "message": "x"}), capture())
    assert any("actual=none" in d for d in diffs)


def test_a_different_setup_makes_the_comparison_meaningless_and_says_so():
    diffs = compare_capture(capture(), capture(pinned={"sysdate": "2020-01-01 00:00:00", "sequences": {}}))
    assert any(d.startswith("pinned:") for d in diffs)


# ---------------------------------------------------------------- tables
def test_a_changed_column_is_reported_as_a_changed_column():
    """Not as one row missing and another unexpected, which would hide which column moved."""
    left = capture(tables={"orders": table(["order_id", "status"], [[1001, "NEW"]])})
    right = capture(tables={"orders": table(["order_id", "status"], [[1001, "CLOSED"]])})
    diffs = compare_capture(left, right)
    assert len(diffs) == 1
    assert "status: expected='NEW' actual='CLOSED' (value)" in diffs[0]


def test_row_counts_and_missing_rows_are_both_reported():
    left = capture(tables={"t": table(["id"], [[1], [2]])})
    right = capture(tables={"t": table(["id"], [[1]])})
    diffs = compare_capture(left, right)
    assert any("row count: expected=2 actual=1" in d for d in diffs)
    assert any("missing (expected only)" in d for d in diffs)


def test_an_extra_row_is_reported_as_unexpected():
    diffs = compare_capture(capture(tables={"t": table(["id"], [[1]])}),
                            capture(tables={"t": table(["id"], [[1], [9]])}))
    assert any("unexpected (actual only)" in d for d in diffs)


def test_row_multiplicity_is_part_of_the_answer():
    diffs = compare_capture(capture(tables={"t": table(["id"], [[1], [1]])}),
                            capture(tables={"t": table(["id"], [[1]])}))
    assert diffs, "a duplicate row that vanished is a difference"


def test_a_side_effect_table_is_compared_like_any_other():
    """Audit rows are the point of several corpus routines, so they are never out of scope."""
    left = capture(tables={"audit_log": table(["audit_id", "action"], [[1, "CLOSE"]])})
    right = capture(tables={"audit_log": table(["audit_id", "action"], [])})
    assert any("audit_log" in d for d in compare_capture(left, right))


def test_a_masked_column_is_not_compared_but_the_mask_set_is():
    left = capture(tables={"t": table(["id", "at"], [[1, {"$masked": "clock"}]])},
                   masked={"t": ["at"]})
    right = capture(tables={"t": table(["id", "at"], [[1, {"$masked": "clock"}]])},
                    masked={"t": ["at"]})
    assert compare_capture(left, right) == []
    assert any("masked columns" in d for d in compare_capture(left, capture(
        tables={"t": table(["id", "at"], [[1, {"$masked": "clock"}]])}, masked={})))


def test_a_different_column_order_stops_the_row_comparison():
    """Comparing rows positionally against a different column order says nothing true."""
    diffs = compare_capture(capture(tables={"t": table(["a", "b"], [[1, 2]])}),
                            capture(tables={"t": table(["b", "a"], [[2, 1]])}))
    assert len(diffs) == 1 and "columns" in diffs[0]


def test_timestamps_are_compared_exactly():
    diffs = compare_capture(capture(tables={"t": table(["at"], [[{"$ts": "2026-01-10T11:00:00"}]])}),
                            capture(tables={"t": table(["at"], [[{"$ts": "2026-01-10T11:00:01"}]])}))
    assert any("11:00:00" in d and "11:00:01" in d for d in diffs)


# ---------------------------------------------------------------- storage conventions
def test_unscale_turns_stored_cents_back_into_the_logical_value():
    stored = capture(tables={"orders": table(["order_id", "total_amount"], [[1001, 19000]])})
    unscale(stored, {"orders": {"total_amount": 2}})
    assert decode(stored["tables"]["orders"]["rows"][0][1]) == Decimal("190.00")


def test_unscale_leaves_columns_that_are_not_money_alone():
    stored = capture(tables={"orders": table(["order_id", "total_amount"], [[1001, 19000]])})
    unscale(stored, {"orders": {"total_amount": 2}})
    assert stored["tables"]["orders"]["rows"][0][0] == 1001


def test_an_unscaled_money_column_agrees_with_oracle_in_value():
    """Only the scale differs, because Oracle's driver drops a NUMBER(14,2)'s trailing zeros."""
    oracle = capture(tables={"orders": table(["order_id", "total_amount"],
                                             [[{"$dec": "1001"}, {"$dec": "190"}]])})
    stored = capture(tables={"orders": table(["order_id", "total_amount"], [[1001, 19000]])})
    unscale(stored, {"orders": {"total_amount": 2}})
    diffs = compare_capture(oracle, stored)
    assert len(diffs) == 1 and all(_scale_only(d) for d in diffs), diffs


def test_a_money_value_that_really_differs_is_not_filed_under_scale():
    """The DOUBLE convention keeps 1234.565 where Oracle rounded to 1234.57; that is a value difference."""
    oracle = capture(tables={"orders": table(["total_amount"], [[{"$dec": "1234.57"}]])})
    stored = capture(tables={"orders": table(["total_amount"], [[1234.565]])})
    diffs = compare_capture(oracle, stored)
    assert len(diffs) == 1 and not _scale_only(diffs[0]), diffs
    assert "(value)" in diffs[0]


@pytest.mark.parametrize("stored,expected", [(123457, "1234.57"), (0, "0.00"), (-4250, "-42.50")])
def test_unscale_round_trips_the_money_values_the_corpus_uses(stored, expected):
    held = capture(tables={"t": table(["amount"], [[stored]])})
    unscale(held, {"t": {"amount": 2}})
    assert decode(held["tables"]["t"]["rows"][0][0]) == Decimal(expected)


# --- #22: `pinned` は両側が同じものを写す ------------------------------------------------------------

def test_pinned_holds_only_what_the_scenario_declared():
    """golden の `pinned` に、scenario が宣言していない鍵があってはならない。

    ターゲット側の capture は scenario の宣言をそのまま写す（`Scenario.java`）。Oracle 側だけが
    実測値を書き足すと、**golden を取り直した瞬間に全件が `pinned` の差分になる**——比較が通って
    いたのは golden が古いおかげだった、という状態になる（#22）。実測した USER の置き場所は
    `sessionUser` 1 つに決めてある。
    """
    import json
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    declared = {}
    for spec in sorted((root / "scenarios").glob("*.yaml")):
        loaded = yaml.safe_load(spec.read_text(encoding="utf-8"))
        declared[loaded["name"]] = set((loaded.get("pinned") or {}))
    offenders = {}
    for golden in sorted((root / "golden").glob("*.json")):
        capture = json.loads(golden.read_text(encoding="utf-8"))
        extra = set(capture.get("pinned") or {}) - {"sysdate", "sequences"} \
            - declared.get(golden.stem, set())
        if extra:
            offenders[golden.stem] = sorted(extra)
    assert not offenders, f"scenario が宣言していない pinned の鍵: {offenders}"


def test_the_measured_oracle_user_has_exactly_one_home():
    """実測値は `sessionUser` に置く。`pinned` にも置くと、置き場所が 2 つになって食い違う。"""
    import pathlib

    run = (pathlib.Path(__file__).resolve().parent.parent / "difftest" / "plsql_run.py") \
        .read_text(encoding="utf-8")
    assert '"sessionUser": session_user' in run
    assert '"user": pinned.get("user") or session_user' not in run, \
        "実測値が pinned にも書かれている。#22 の状態に戻っている"


# --- 戻り値と OUT の桁の差（2026-09-19） ---------------------------------------------------------

def test_a_return_value_differing_only_in_scale_is_set_aside():
    """`order_total` が 900 を返し、ScalarDB 側が 900.00 を返す。値は同じで、違うのは桁の書き方だけ。

    表の列の差は `... (scale)` と末尾に種類を書くが、戻り値と OUT は `returned (scale): ...` と
    **コロンの前**に書く。末尾しか見ていなかったので、戻り値の桁だけの差が「差」として数えられていた。
    """
    diffs = compare_capture(capture(result={"returned": {"$dec": "900"}, "out": {}}),
                            capture(result={"returned": {"$dec": "900.00"}, "out": {}}))
    assert diffs == ["returned (scale): expected=900 actual=900.00"]
    assert _scale_only(diffs[0])


def test_an_out_value_differing_only_in_scale_is_set_aside():
    diffs = compare_capture(capture(result={"returned": None, "out": {"p_total": {"$dec": "0"}}}),
                            capture(result={"returned": None, "out": {"p_total": {"$dec": "0.00"}}}))
    assert len(diffs) == 1 and _scale_only(diffs[0]), diffs


def test_a_return_value_differing_in_value_is_still_a_difference():
    """桁を揃えるのは値が同じときだけ。900 と 900.01 は差である。"""
    diffs = compare_capture(capture(result={"returned": {"$dec": "900"}, "out": {}}),
                            capture(result={"returned": {"$dec": "900.01"}, "out": {}}))
    assert len(diffs) == 1 and not _scale_only(diffs[0])


def test_a_missing_out_value_is_never_scale_only():
    diffs = compare_capture(capture(result={"returned": None, "out": {"p_count": 2}}),
                            capture(result={"returned": None, "out": {}}))
    assert not any(_scale_only(d) for d in diffs)
