"""P3-3: property tests over the conversion, and over the Oracle answers the runtime is checked against.

Two kinds of test live here.

**Generated properties** over the parts of the migration written in Python -- the type mapping, the temporal
literal fitting, the money scaling, the capture comparison. Hypothesis chooses the values, which is the point:
the cases that break a migration are the ones nobody thought to write down. Every property here is a claim the
generated code depends on, so a counterexample is a bug and not a curiosity.

**Coverage of the Oracle fixture** that `PlsqlPropertyTest` replays. Oracle itself decides what its operators
mean (`difftest/plsql_semantics.py` records it); what this file checks is that the recording still covers the
edges the plan named -- NULL, `''` = NULL, boundary values, NUMBER overflow, the DATE time component, time
zones. A fixture that quietly stopped covering one of those would leave the runtime unchecked there while every
test still passed.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path

import sqlglot
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from difftest.plsql_compare import compare_capture, decode, difference, unscale
from scalardb_migrate.converter import convert_script
from plsql.gen_java.types import java_type
from scalardb_migrate.types import map_type

FIXTURE = Path("fixtures/plsql/semantics.json")

# --------------------------------------------------------------------------------------------------
# strategies
# --------------------------------------------------------------------------------------------------

# money as the corpus holds it: two decimal places, within what a scaled BIGINT can carry
money = st.decimals(min_value=Decimal("-99999999999.99"), max_value=Decimal("99999999999.99"),
                    places=2, allow_nan=False, allow_infinity=False)

# the three that Oracle does not keep apart the way Java does
texts = st.one_of(st.none(), st.just(""), st.just(" "), st.text(max_size=20))

precisions = st.integers(min_value=1, max_value=38)
scales = st.integers(min_value=0, max_value=10)


# --------------------------------------------------------------------------------------------------
# the type mapping: boundaries and overflow
# --------------------------------------------------------------------------------------------------

@given(precisions)
def test_an_integral_number_maps_by_the_digits_it_holds(precision):
    """The boundaries are 9 and 18, because that is where a value stops fitting an INT and then a BIGINT."""
    mapped = map_type(sqlglot.parse_one(f"NUMBER({precision})", into=sqlglot.exp.DataType, read="oracle"),
                      "oracle")
    if precision <= 9:
        assert mapped.scalardb_type == "INT"
    elif precision <= 18:
        assert mapped.scalardb_type == "BIGINT"
    else:
        assert mapped.scalardb_type == "BIGINT" and mapped.severity == "WARN"


@given(precisions, st.integers(min_value=1, max_value=10))
def test_a_number_with_decimals_never_maps_to_an_integer_without_saying_so(precision, scale):
    """ScalarDB has no DECIMAL, so something is always lost or rescaled, and it is always reported."""
    assume(scale <= precision)
    mapped = map_type(sqlglot.parse_one(f"NUMBER({precision},{scale})", into=sqlglot.exp.DataType,
                                        read="oracle"), "oracle")
    assert mapped.severity == "WARN" and mapped.note


def test_the_plsql_path_keeps_an_oracle_dates_time_of_day():
    """Oracle DATE is not a date. For a PL/SQL migration it has to become TIMESTAMP or every time is lost."""
    assert java_type("DATE").storage == "TIMESTAMP"
    assert java_type("DATE").name == "LocalDateTime"


def test_the_general_converter_maps_an_oracle_date_to_date_but_says_what_that_costs():
    """The two mappers disagree on purpose, and the disagreement is reported rather than silent.

    `scalardb_migrate` serves any Oracle schema, where a DATE column often is a date; it maps to ScalarDB DATE
    and warns that the time of day goes. The PL/SQL path cannot take that trade -- a routine comparing
    `ordered_at` against SYSDATE needs the time -- so it maps to TIMESTAMP, and P0-3's corpus schema does too.
    A caller that wants the PL/SQL rule from the converter passes the column type in the schema.
    """
    mapped = map_type(sqlglot.parse_one("DATE", into=sqlglot.exp.DataType, read="oracle"), "oracle")
    assert mapped.scalardb_type == "DATE"
    assert mapped.severity == "WARN" and "time-of-day" in mapped.note


def test_the_corpus_schema_took_the_plsql_rule():
    """Every Oracle DATE column in the corpus is a ScalarDB TIMESTAMP, not a DATE."""
    ddl = Path("fixtures/plsql/src/schema.sql").read_text(encoding="utf-8")
    schema = json.loads(Path("fixtures/plsql/scalardb-schema.json").read_text(encoding="utf-8"))
    columns = {c: t for table in schema.values() for c, t in table["columns"].items()}
    for statement in sqlglot.parse(ddl, read="oracle"):
        if not isinstance(statement, sqlglot.exp.Create) or not isinstance(statement.this, sqlglot.exp.Schema):
            continue
        for column in statement.this.expressions:
            if isinstance(column, sqlglot.exp.ColumnDef) and column.kind is not None \
                    and column.kind.this == sqlglot.exp.DataType.Type.DATE:
                assert columns.get(column.name.lower()) == "TIMESTAMP", column.name


def test_a_timestamp_with_time_zone_keeps_its_zone():
    mapped = map_type(sqlglot.parse_one("TIMESTAMP WITH TIME ZONE", into=sqlglot.exp.DataType, read="oracle"),
                      "oracle")
    assert mapped.scalardb_type == "TIMESTAMPTZ"


# --------------------------------------------------------------------------------------------------
# temporal literals against the column they land in
# --------------------------------------------------------------------------------------------------

DDL = "CREATE TABLE ev (id BIGINT PRIMARY KEY, at_ts TIMESTAMP, on_date DATE);"


@given(st.dates(min_value=datetime.date(1900, 1, 1), max_value=datetime.date(2200, 1, 1)))
@settings(max_examples=40)
def test_a_date_literal_going_into_a_timestamp_column_is_given_a_time(day):
    """ScalarDB parses a TIMESTAMP strictly; a date-only literal is not one."""
    results, _ = convert_script(DDL + f"INSERT INTO ev (id, at_ts) VALUES (1, DATE '{day.isoformat()}');",
                                "oracle", decompose=False)
    assert f"'{day.isoformat()} 00:00:00'" in results[-1].converted[0]


@given(st.dates(min_value=datetime.date(1900, 1, 1), max_value=datetime.date(2200, 1, 1)))
@settings(max_examples=40)
def test_a_midnight_timestamp_going_into_a_date_column_loses_only_the_midnight(day):
    results, _ = convert_script(
        DDL + f"INSERT INTO ev (id, on_date) VALUES (1, TIMESTAMP '{day.isoformat()} 00:00:00');",
        "oracle", decompose=False)
    converted = results[-1].converted[0]
    assert f"'{day.isoformat()}'" in converted and "00:00:00" not in converted


@given(st.datetimes(min_value=datetime.datetime(1900, 1, 1), max_value=datetime.datetime(2200, 1, 1))
       .map(lambda when: when.replace(microsecond=0)))
@settings(max_examples=40)
def test_a_timestamp_with_a_real_time_is_left_alone_for_a_timestamp_column(when):
    literal = when.strftime("%Y-%m-%d %H:%M:%S")
    results, _ = convert_script(DDL + f"INSERT INTO ev (id, at_ts) VALUES (1, TIMESTAMP '{literal}');",
                                "oracle", decompose=False)
    assert f"'{literal}'" in results[-1].converted[0]


# --------------------------------------------------------------------------------------------------
# the money scaling
# --------------------------------------------------------------------------------------------------

@given(money)
def test_scaling_money_and_unscaling_it_gives_the_value_back(amount):
    """The property the whole scaled convention rests on: nothing is lost between Java and the column."""
    stored = int(amount.scaleb(2))
    capture = {"tables": {"t": {"columns": ["amount"], "rows": [[stored]]}}}
    unscale(capture, {"t": {"amount": 2}})
    assert decode(capture["tables"]["t"]["rows"][0][0]) == amount


@given(money)
def test_an_unscaled_money_value_compares_equal_to_the_same_number_written_plainly(amount):
    stored = {"tables": {"t": {"columns": ["amount"], "rows": [[int(amount.scaleb(2))]]}}}
    unscale(stored, {"t": {"amount": 2}})
    assert difference(amount, decode(stored["tables"]["t"]["rows"][0][0])) in (None, "scale")


# --------------------------------------------------------------------------------------------------
# the comparison's own rules
# --------------------------------------------------------------------------------------------------

@given(texts, texts)
def test_two_texts_agree_exactly_when_they_are_equal(a, b):
    assert (difference(a, b) is None) == (a == b)


@given(st.text(max_size=10))
def test_trailing_whitespace_is_never_silently_equal(text):
    assume(text == text.strip() and text != "")
    assert difference(text, text + " ") == "whitespace"


@given(money, money)
def test_numbers_agree_exactly_when_they_are_numerically_equal(a, b):
    assert (difference(a, b) in (None, "scale")) == (a == b)


@given(st.integers(min_value=-(2 ** 63), max_value=2 ** 63 - 1))
def test_an_integer_and_the_same_value_as_a_decimal_agree(value):
    """Oracle's driver returns NUMBER as a decimal and ScalarDB returns BIGINT as an integer."""
    assert difference(Decimal(value), value) is None


@given(st.text(min_size=1, max_size=10))
def test_a_capture_always_agrees_with_itself(status):
    capture = {"pinned": {"sysdate": None, "sequences": {}},
               "result": {"returned": None, "out": {}}, "exception": None, "masked": {},
               "tables": {"orders": {"columns": ["order_id", "status"], "rows": [[1, status]]}}}
    assert compare_capture(capture, json.loads(json.dumps(capture))) == []


# --------------------------------------------------------------------------------------------------
# the Oracle fixture still covers what the plan named
# --------------------------------------------------------------------------------------------------

def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def answers(family: str, op: str) -> list[dict]:
    return [c for c in fixture()["cases"] if c["family"] == family and c["op"] == op]


def answer(family: str, op: str, args: list):
    for case in answers(family, op):
        if case["args"] == args:
            return case["oracle"]
    raise AssertionError(f"semantics.json has no {family}.{op}{tuple(args)}; re-run difftest/plsql_semantics.py")


def test_oracle_confirms_the_empty_string_is_null():
    assert answer("null", "is_null", [""]) == {"value": {"$dec": "1"}}
    assert answer("null", "is_null", [" "]) == {"value": {"$dec": "0"}}
    assert answer("null", "is_null", [None]) == {"value": {"$dec": "1"}}


def test_oracle_confirms_a_comparison_with_null_does_not_run_its_branch():
    for op in ("eq", "ne", "lt", "le", "gt", "ge"):
        assert answer("comparison", op, [None, None]) == {"value": {"$dec": "0"}}, op
        assert answer("comparison", op, [None, "a"]) == {"value": {"$dec": "0"}}, op


def test_oracle_confirms_rounding_is_half_up_not_half_even():
    """Java's default is half-even, which would answer 1.00 and 2.67 here."""
    assert answer("rounding", "round", [{"$dec": "1.005"}, 2]) == {"value": {"$dec": "1.01"}}
    assert answer("rounding", "round", [{"$dec": "2.675"}, 2]) == {"value": {"$dec": "2.68"}}


def test_oracle_confirms_storing_into_a_scaled_column_rounds_rather_than_truncates():
    assert answer("storage", "cast_number_14_2", [{"$dec": "1234.565"}]) == {"value": {"$dec": "1234.57"}}


def test_oracle_confirms_a_value_past_the_column_is_refused_not_truncated():
    result = answer("storage", "cast_number_14_2", [{"$dec": "9" * 38}])
    assert "error" in result, "a value that does not fit must be refused, never silently cut down"


def test_oracle_confirms_a_date_carries_a_time_of_day():
    kept = answer("date", "to_char_datetime", [{"$ts": "2026-01-15T09:30:00"}])
    assert kept == {"value": "2026-01-15 09:30:00"}


def test_the_fixture_covers_every_edge_the_plan_named():
    """NULL, ''=NULL, boundary values, NUMBER overflow, the DATE time component, time zones."""
    cases = fixture()["cases"]
    assert any(c["args"] == [""] for c in cases), "the empty string"
    assert any(None in c["args"] for c in cases), "NULL"
    assert any(999999999 in c["args"] for c in cases), "the INT boundary"
    assert any(999999999999999999 in c["args"] for c in cases), "the BIGINT boundary"
    assert any("error" in c["oracle"] for c in cases), "a value past what the column holds"
    assert any(c["family"] == "date" for c in cases), "the DATE time component"
    assert fixture()["session"]["time_zone"], "the time zone the answers were recorded under"


def test_the_fixture_names_the_oracle_that_answered():
    """The answers belong to a version; a fixture that does not say which is not evidence about anything."""
    assert "oracle" in fixture()["source"].lower()
