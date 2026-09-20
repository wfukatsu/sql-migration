"""difftest/catalog_snapshot.py: what it is allowed to send, what it never collects by default, and what it writes.

None of this needs a database or the Oracle driver: the statements are data, and the snapshot is assembled from
rows a test can supply. What does need a database -- that the three snapshots in `fixtures/explorer/` are what a
real Oracle answers -- was run by hand (`fixtures/explorer/db/setup.sh`), and the files are checked here as
committed.
"""

from __future__ import annotations

import ast
import builtins
import datetime
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "difftest"))

import catalog_snapshot as C  # noqa: E402

from plsql.explorer import snapshot as S  # noqa: E402

SOURCE = (ROOT / "difftest" / "catalog_snapshot.py").read_text(encoding="utf-8")
VALUE_COLUMNS = ("LOW_VALUE", "HIGH_VALUE", "ENDPOINT_VALUE", "ENDPOINT_ACTUAL_VALUE")


# --- what it may send ---------------------------------------------------------------------------------------

def test_everything_it_can_send_is_a_select_on_the_users_own_dictionary_views():
    for section, sql in C.statements(include_values=True).items():
        assert sql.strip().upper().startswith("SELECT"), section
        assert "*" not in re.sub(r"COUNT\(\*\)", "", sql), f"{section}: SELECT * would fetch columns nobody chose"
        sources = re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_$#]+)", sql, re.I)
        assert sources and all(s.upper().startswith("USER_") for s in sources), (section, sources)
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|GRANT|BEGIN|CALL|EXEC)\b", sql, re.I)


def test_the_one_statement_that_is_not_a_select_makes_the_transaction_read_only():
    assert C.READ_ONLY == "SET TRANSACTION READ ONLY"
    executed = [node for node in ast.walk(ast.parse(SOURCE))
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute"]
    arguments = {ast.unparse(call.args[0]) for call in executed}
    assert arguments == {"READ_ONLY", "sql"}, "nothing is sent that is not in the lists at the top of the file"


def test_without_the_option_no_statement_names_a_column_that_holds_real_data():
    for section, sql in C.statements(include_values=False).items():
        for column in VALUE_COLUMNS:
            assert column not in sql.upper(), f"{section} would fetch {column}"
    assert any("LOW_VALUE" in sql.upper() for sql in C.statements(include_values=True).values())


def test_it_imports_nothing_from_the_repository_and_the_driver_only_where_it_connects():
    tree = ast.parse(SOURCE)
    top_level = {alias.name.split(".")[0] for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                 for alias in ([ast.alias(node.module)] if isinstance(node, ast.ImportFrom) else node.names)}
    assert top_level <= set(sys.stdlib_module_names), top_level - set(sys.stdlib_module_names)
    assert "oracledb" not in top_level
    everywhere = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert "oracledb" in everywhere


def test_the_static_checks_run_without_the_driver(monkeypatch):
    """CI installs requirements.txt, and oracledb is not in it. If importing this module needed the driver, every
    test above would be skipped exactly where it is meant to run."""
    real = builtins.__import__

    def no_driver(name, *args, **kwargs):
        if name == "oracledb":
            raise ImportError("no driver here")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_driver)
    monkeypatch.delitem(sys.modules, "oracledb", raising=False)
    assert C.statements(False)
    with pytest.raises(C.Refused, match="pip install oracledb"):
        C.collect({"user": "u", "dsn": "h/s"}, "pw", False)


# --- decoding -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, data_type, text", [
    ("c102", "NUMBER", "1"), ("c20433", "NUMBER", "350"), ("80", "NUMBER", "0"), ("c1020b", "NUMBER", "1.1"),
    ("3e6466", "NUMBER", "-1"), ("c03f", "NUMBER", "0.62"),
    ("5348495050454420", "VARCHAR2", "SHIPPED "), ("e58f97e6b3a8", "VARCHAR2", "受注"),
    ("787e0101010101", "DATE", "2026-01-01 00:00:00"), ("787e071311251f", "DATE", "2026-07-19 16:36:30"),
    ("787e010101010100000000", "TIMESTAMP(6)", "2026-01-01 00:00:00"),
    ("787e01010101011dcd6500", "TIMESTAMP(6)", "2026-01-01 00:00:00.5"),
    ("00530041", "NVARCHAR2", "SA"),
])
def test_a_raw_value_is_decoded_by_the_type_of_its_column(raw, data_type, text):
    assert C.decode_raw(bytes.fromhex(raw), data_type) == {"dataType": data_type, "text": text}


@pytest.mark.parametrize("raw, data_type", [("0102", "BLOB"), ("ff", "VARCHAR2"), ("7878", "DATE"), (None, "NUMBER")])
def test_a_value_nobody_can_decode_is_recorded_as_one_and_never_as_hex(raw, data_type):
    value = C.decode_raw(None if raw is None else bytes.fromhex(raw), data_type)
    assert value == {"dataType": data_type, "undecodable": True}


def test_a_histogram_endpoint_is_a_number_a_julian_day_or_the_text_oracle_kept():
    assert C.decode_endpoint("NUMBER", 7, None)["text"] == "7"
    assert C.decode_endpoint("DATE", 2461042, None)["text"] == "2026-01-01 00:00:00"
    assert C.decode_endpoint("VARCHAR2", 4.3e35, "RECEIVED")["text"] == "RECEIVED"
    assert C.decode_endpoint("VARCHAR2", 4.3e35, None) == {"dataType": "VARCHAR2", "undecodable": True}


# --- assembling a snapshot from rows ------------------------------------------------------------------------

NOW = datetime.datetime(2026, 8, 30, 2, 0, 0)
ROWS = {
    "tables": [("ORDERS", "N", "NO", None)],
    "columns": [("ORDERS", "ORDER_ID", 1, "NUMBER", 22, 12, 0, "N")],
    "constraints": [("PK_ORDERS", "ORDERS", "P", None, None, None, None, "ENABLED", "USER NAME"),
                    ("FK_ORDERS_CUSTOMER", "ORDERS", "R", None, "CRM", "PK_CUSTOMERS", "NO ACTION", "ENABLED",
                     "USER NAME")],
    "constraintColumns": [("PK_ORDERS", "ORDERS", "ORDER_ID", 1), ("FK_ORDERS_CUSTOMER", "ORDERS", "CUSTOMER_ID", 1)],
    "indexes": [("PK_ORDERS", "ORDERS", "UNIQUE")],
    "indexColumns": [("PK_ORDERS", "ORDER_ID", 1)],
    "triggers": [], "views": [], "dependencies": [], "segments": [("ORDERS", "TABLE", 65536)],
    "tableStatistics": [("ORDERS", 400, 5, 31, NOW, "NO")],
    "columnStatistics": [("ORDERS", "ORDER_ID", 400, 0, 4, 0.0025, "NONE", 1, NOW)],
    "columnValues": [("ORDERS", "ORDER_ID", "NUMBER", bytes.fromhex("c102"), bytes.fromhex("c205"))],
    "histograms": [("ORDERS", "STATUS", "VARCHAR2", "FREQUENCY", 240, 1.0, "RECEIVED", 0),
                   ("ORDERS", "STATUS", "VARCHAR2", "FREQUENCY", 400, 2.0, "SHIPPED", 0)],
}


def build(include_values=False, failing=()):
    sent = []

    def fetch(section, sql):
        sent.append(section)
        if section in failing:
            raise RuntimeError("ORA-00942: table or view does not exist\nHelp: https://docs.oracle.com/...")
        return ROWS[section]

    document = C.build(fetch, include_values=include_values, schema="SHOP", database={"name": "X", "version": "23"},
                       collected_at="2026-08-30T02:00:00+09:00")
    return document, sent


def test_what_it_writes_is_a_snapshot_the_explorer_accepts():
    document, sent = build()
    snap = S.parse(document)
    assert "columnValues" not in sent and "histograms" not in sent, "the value statements are not even sent"
    assert snap.table_statistics("orders")["numRows"] == 400 and not snap.contains_data_values
    (fk,) = snap.foreign_keys_out("orders")
    assert fk["outside"] and fk["refOwner"] == "crm" and fk["refTable"] is None, \
        "a parent in another schema is in ALL_CONSTRAINTS, which is not read"


def test_with_the_option_the_snapshot_says_so_and_the_values_are_readable():
    document, _ = build(include_values=True)
    snap = S.parse(document)
    assert snap.contains_data_values
    assert snap.column_statistics("orders")[0]["highValue"] == {"dataType": "NUMBER", "text": "400"}
    assert [(e["value"]["text"], e["rows"]) for e in snap.histogram("orders", "status")] == \
        [("RECEIVED", 240), ("SHIPPED", 160)]


def test_a_section_that_cannot_be_read_is_recorded_and_the_rest_is_still_written():
    document, _ = build(failing={"segments", "triggers"})
    snap = S.parse(document)
    assert {item["section"] for item in snap.skipped} == {"segments", "triggers"}
    assert all("\n" not in item["reason"] for item in snap.skipped)
    assert snap.size_bytes("orders") is None and snap.triggers() is None, "not collected is not the same as none"
    assert snap.table_names() == ["orders"]


# --- connecting -----------------------------------------------------------------------------------------------

def test_a_password_is_never_an_argument(capsys):
    with pytest.raises(SystemExit) as stop:
        C.main(["--out", "x.json", "--password", "hunter2"], environ={})
    assert stop.value.code == 2 and "unrecognized arguments" in capsys.readouterr().err


def test_a_full_connect_string_wins_so_tls_is_possible():
    assert C.connection_settings({"SRC_ORACLE_USER": "shop", "SRC_ORACLE_DSN": "tcps://db:2484/svc",
                                  "SRC_ORACLE_SERVICE": "ignored"})["dsn"] == "tcps://db:2484/svc"
    assert C.connection_settings({"SRC_ORACLE_USER": "shop", "SRC_ORACLE_SERVICE": "svc"})["dsn"] == "localhost:1521/svc"
    with pytest.raises(C.Refused, match="SRC_ORACLE_USER"):
        C.connection_settings({})


def test_a_failed_connection_says_why_without_the_password_and_names_the_way_round_encryption():
    message = C.explain(RuntimeError("DPY-3001: Native Network Encryption and Data Integrity is only supported in "
                                     "python-oracledb thick mode (hunter2)"), "hunter2")
    assert "hunter2" not in message
    assert "tcps://" in message and "Native Network Encryption" in message


def test_the_statements_can_be_printed_for_review_without_connecting(capsys):
    assert C.main(["--out", "unused.json", "--print-statements"], environ={}) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("SET TRANSACTION READ ONLY;") and "LOW_VALUE" not in printed.upper()


# --- the snapshots taken from a real Oracle --------------------------------------------------------------------

FIXTURES = ROOT / "fixtures" / "explorer"


def test_the_three_committed_snapshots_are_valid_and_differ_in_the_way_their_names_say():
    plain, no_stats, values = (S.load(FIXTURES / name) for name in
                               ("snapshot.json", "snapshot-no-stats.json", "snapshot-with-values.json"))
    assert plain.table_statistics("orders")["numRows"] == 400
    assert plain.table_statistics("audit_log")["numRows"] is None, "the one table nobody analysed"
    assert no_stats.table_statistics("orders")["numRows"] is None
    assert not plain.contains_data_values and not no_stats.contains_data_values and values.contains_data_values
    assert values.histogram("orders", "status")[0]["value"]["text"] == "RECEIVED"
    assert {t["name"] for t in plain.triggers()} == {"trg_items_stock_guard", "trg_orders_audit"}
    assert plain.base_tables("v_order_lines") == ["order_items", "orders"]


def test_the_snapshot_taken_without_the_option_holds_no_value_from_the_data():
    text = (FIXTURES / "snapshot.json").read_text(encoding="utf-8")
    for value in ("RECEIVED", "GROUND", "example.test", "Customer 1"):
        assert value not in text, value
    assert "'CANCELLED'" in text, "a literal written in a view definition is collected either way, and the guide says so"
