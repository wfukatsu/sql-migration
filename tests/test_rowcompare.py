"""difftest/rowcompare.py: what the harnesses mean by "the two databases returned the same result".

Review #27-36. The comparison this replaces normalised each value on its own and compared the normal forms; every
case under `test_values_that_used_to_match` is a pair it reported as equal.
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "difftest"))
import rowcompare  # noqa: E402
from rowcompare import difference, same, same_value  # noqa: E402


@pytest.mark.parametrize("expected,actual", [
    (Decimal("12345678901234567"), Decimal("12345678901234568")),   # Decimal -> float dropped the last digits
    (9007199254740993, 9007199254740992),
    (Decimal("0.1234561"), Decimal("0.1234564")),                   # round(v, 6)
    ("20240101", "2024-01-01"),                                     # every string was tried as a date
    ("2024-01-01 10:00:00.123", "2024-01-01 10:00:00.999"),         # and its fraction cut off
    (dt.datetime(2024, 1, 1, 10, 0, 0, 123000), "2024-01-01 10:00:00.999"),
    (True, 1),
    (None, ""),
    (1, "1"),
])
def test_values_that_used_to_match(expected, actual):
    assert not same_value(expected, actual)
    assert not same_value(actual, expected)


@pytest.mark.parametrize("expected,actual", [
    (Decimal("10.50"), Decimal("10.5")),                            # scale is not value
    (Decimal("3"), 3), (3, 3.0),
    (Decimal("0.3"), Decimal("0.30000000000000004")),               # a NUMBER(10,2) that came back through a double
    (Decimal("1234.56"), 1234.56),
    (dt.date(2024, 1, 1), "2024-01-01"),                            # drivers and JSON hand dates back as text
    (dt.datetime(2024, 1, 1), "2024-01-01"),                        # Oracle's DATE at midnight
    (dt.datetime(2024, 1, 1), dt.date(2024, 1, 1)),
    (dt.datetime(2024, 1, 1, 10, 0, 0, 123456), "2024-01-01T10:00:00.123"),   # TIMESTAMP keeps milliseconds
    (dt.datetime(2024, 1, 1, 1, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))), "2023-12-31T16:00:00+00:00"),
    (dt.time(10, 30), "10:30:00"),
    (True, True), (None, None), ("x ", "x "), (b"\x01", bytearray(b"\x01")),
])
def test_values_that_are_the_same_value(expected, actual):
    assert same_value(expected, actual) and same_value(actual, expected)


def test_trailing_spaces_and_case_are_differences():
    assert not same_value("A", "a") and not same_value("A ", "A")


def test_runner_output_keeps_its_digits():
    rows = rowcompare.loads('{"rows": [[12345678901234567.89, 9007199254740993, 0.5]]}')["rows"]
    assert rows == [[Decimal("12345678901234567.89"), 9007199254740993, Decimal("0.5")]]
    assert same([(9007199254740993,)], [[rows[0][1]]], ordered=True)


def test_an_ordered_result_is_compared_in_order():
    assert same([(1, "a"), (2, "b")], [[1, "a"], [2, "b"]], ordered=True)
    assert "row 1" in difference([(1, "a"), (2, "b")], [[2, "b"], [1, "a"]], ordered=True)
    assert same([(1, "a"), (2, "b")], [[2, "b"], [1, "a"]], ordered=False)


def test_an_unordered_result_is_a_multiset():
    assert not same([(1,), (1,), (2,)], [[1], [2], [2]], ordered=False)
    assert "row count" in difference([(1,)], [[1], [1]], ordered=False)
    assert same([(None, 2), (dt.date(2024, 1, 1), 1)], [["2024-01-01", 1], [None, 2]], ordered=False)


def test_rows_pair_up_even_where_the_sort_key_cannot_put_them_side_by_side():
    expected = [(dt.datetime(2024, 1, 1, 10, 0), 1), (dt.datetime(2024, 1, 1, 9, 0), 2)]
    actual = [["2024-01-01T09:00:00", 2], ["2024-01-01T10:00:00.000", 1]]
    assert same(expected, actual, ordered=False)


@pytest.mark.parametrize("sql,ordered", [
    ("SELECT a FROM t ORDER BY a", True),
    ("SELECT a FROM t ORDER  BY a", True),
    ("SELECT a FROM t ORDER\nBY a", True),
    ("SELECT a FROM t WHERE b = 'ORDER BY'", False),
    ("SELECT a, ROW_NUMBER() OVER (ORDER BY b) FROM t", False),
    ("SELECT a FROM (SELECT a FROM t ORDER BY a) x", False),
    ("SELECT a FROM t UNION SELECT a FROM u ORDER BY 1", True),
])
def test_ordered_is_read_from_the_syntax_tree(sql, ordered):
    """The substring test missed `ORDER  BY`, and saw an ORDER BY that only a window or a subquery had."""
    assert rowcompare.is_ordered(sql, "oracle") is ordered


def test_a_run_that_compared_nothing_does_not_pass():
    pytest.importorskip("oracledb")
    pytest.importorskip("psycopg")
    import run

    base = {"PASS": 0, "FAIL": 0, "SKIP": 0, "CASE_ERROR": 0, "EMPTY": 0}
    assert run.exit_code({**base, "PASS": 3, "SKIP": 2}) == 0
    assert run.exit_code({**base, "PASS": 3, "FAIL": 1}) == 1
    assert run.exit_code({**base, "PASS": 3, "CASE_ERROR": 1}) == 1, "a case the source rejected is a broken case"
    assert run.exit_code({**base, "SKIP": 9}) == 2, "everything skipped is not a pass"
    assert run.compare([(Decimal("1.0"),)], [[1]], True) and not run.compare([(True,)], [[1]], True)


def test_two_json_samples_compare_their_date_columns_as_dates():
    """Issue #28: the benchmark gets both samples as JSON. Oracle's DATE ('2023-09-29T00:00') and ScalarDB's DATE
    ('2023-09-29') were two different texts, and every statement returning a date was a FAIL."""
    oracle, scalardb = [[106, "2023-09-29T00:00", "S4"]], [[106, "2023-09-29", "S4"]]
    assert rowcompare.difference(oracle, scalardb, ordered=True) is not None
    assert rowcompare.difference(rowcompare.with_dates(oracle, [1]), scalardb, ordered=True) is None
    # a text column that happens to look like a date stays text
    assert rowcompare.with_dates([["2023-09-29"]], []) == [["2023-09-29"]]
    assert rowcompare.difference(rowcompare.with_dates([[1, "2023-09-29T09:00"]], [1]), [[1, "2023-09-29"]], True) is not None
