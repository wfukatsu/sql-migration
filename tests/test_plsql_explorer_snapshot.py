"""The catalog snapshot, read from a file: what was collected, what was not, and what must not be there.

The snapshots here are written in the test. The three taken from a real Oracle (`fixtures/explorer/`) are checked
where they are produced, in `tests/test_catalog_snapshot.py`.
"""

from __future__ import annotations

import copy
import json

import pytest

from plsql.explorer import snapshot as S

FULL = {
    "formatVersion": 1,
    "collectedAt": "2026-09-20T10:00:00+09:00",
    "collector": {"name": "catalog_snapshot.py", "version": "1"},
    "database": {"name": "FREEPDB1", "version": "23.0.0.0.0"},
    "schema": "SHOP",
    "containsDataValues": False,
    "tables": [{"name": "ORDERS"}, {"name": "ORDER_ITEMS"}, {"name": "CUSTOMERS"}, {"name": "MixedCase"}],
    "columns": [
        {"table": "ORDER_ITEMS", "name": "LINE_NO", "position": 2, "dataType": "NUMBER", "nullable": False},
        {"table": "ORDER_ITEMS", "name": "ORDER_ID", "position": 1, "dataType": "NUMBER", "nullable": False},
    ],
    "constraints": [
        {"name": "PK_ORDER_ITEMS", "table": "ORDER_ITEMS", "type": "PRIMARY KEY", "columns": ["ORDER_ID", "LINE_NO"]},
        {"name": "FK_ITEMS_ORDER", "table": "ORDER_ITEMS", "type": "FOREIGN KEY", "columns": ["ORDER_ID"],
         "refOwner": "SHOP", "refTable": "ORDERS", "refColumns": ["ORDER_ID"], "deleteRule": "NO ACTION"},
        {"name": "FK_ORDER_CUSTOMER", "table": "ORDERS", "type": "FOREIGN KEY", "columns": ["CUSTOMER_ID"],
         "refOwner": "CRM", "refTable": "CUSTOMERS", "refColumns": ["CUSTOMER_ID"]},
        {"name": "SYS_C001", "table": "ORDER_ITEMS", "type": "CHECK", "columns": ["ORDER_ID"],
         "condition": '"ORDER_ID" IS NOT NULL', "generated": True},
        {"name": "CK_QTY", "table": "ORDER_ITEMS", "type": "CHECK", "columns": ["QUANTITY"],
         "condition": "quantity > 0"},
    ],
    "indexes": [],
    "triggers": [{"name": "TRG_ITEMS_STOCK", "table": "ORDER_ITEMS", "timing": "AFTER EACH ROW",
                  "event": "INSERT", "enabled": True, "body": "BEGIN NULL; END;"}],
    "views": [{"name": "V_ORDER_LINES", "text": "select * from v_open_orders o join order_items i on 1=1"},
              {"name": "V_OPEN_ORDERS", "text": "select * from orders"}],
    "dependencies": [
        {"name": "V_ORDER_LINES", "type": "VIEW", "refOwner": "SHOP", "refName": "V_OPEN_ORDERS", "refType": "VIEW"},
        {"name": "V_ORDER_LINES", "type": "VIEW", "refOwner": "SHOP", "refName": "ORDER_ITEMS", "refType": "TABLE"},
        {"name": "V_OPEN_ORDERS", "type": "VIEW", "refOwner": "SHOP", "refName": "ORDERS", "refType": "TABLE"},
    ],
    "segments": [{"name": "ORDER_ITEMS", "type": "TABLE", "bytes": 65536},
                 {"name": "PK_ORDER_ITEMS", "type": "INDEX", "bytes": 4096}],
    "tableStatistics": [
        {"table": "ORDER_ITEMS", "numRows": 1200, "blocks": 20, "avgRowLen": 31,
         "lastAnalyzed": "2026-08-30T02:00:00"},
        {"table": "ORDERS", "numRows": 0, "blocks": 0, "avgRowLen": 0, "lastAnalyzed": "2026-08-30T02:00:00"},
        {"table": "CUSTOMERS", "numRows": None, "blocks": None, "avgRowLen": None, "lastAnalyzed": None},
    ],
    "columnStatistics": [
        {"table": "ORDER_ITEMS", "column": "PRODUCT_ID", "numDistinct": 40, "numNulls": 0, "avgColLen": 4,
         "histogram": "FREQUENCY", "numBuckets": 40},
        {"table": "ORDER_ITEMS", "column": "ORDER_ID", "numDistinct": 400, "numNulls": 0, "avgColLen": 4,
         "histogram": "NONE", "numBuckets": 1},
        {"table": "ORDER_ITEMS", "column": "NOTE", "numDistinct": None, "numNulls": None, "histogram": None},
    ],
}


def snapshot(**changes) -> S.Snapshot:
    document = copy.deepcopy(FULL)
    for key, value in changes.items():
        if value is ...:
            document.pop(key, None)
        else:
            document[key] = value
    return S.parse(document, "test")


def test_a_complete_snapshot_gives_up_everything_it_holds():
    snap = snapshot()
    assert snap.table_names() == ["customers", "mixedcase", "order_items", "orders"]
    assert [c["name"] for c in snap.columns("order_items")] == ["order_id", "line_no"], "by position, not by file order"
    assert {c["type"] for c in snap.constraints("order_items")} == {"PRIMARY KEY", "FOREIGN KEY", "CHECK"}
    assert snap.triggers("order_items")[0]["name"] == "trg_items_stock"
    assert snap.size_bytes("order_items") == 65536, "the index is another segment"
    assert snap.table_statistics("order_items")["numRows"] == 1200


def test_names_are_folded_so_they_meet_the_names_the_sql_analysis_produced():
    snap = snapshot()
    assert snap.has_table("mixedcase") and not snap.has_table("MixedCase")
    assert snap.foreign_keys_out("order_items")[0]["refColumns"] == ["order_id"]


def test_the_not_null_check_oracle_generates_is_not_shown_as_a_constraint():
    conditions = [c.get("condition") for c in snapshot().constraints("order_items") if c["type"] == "CHECK"]
    assert conditions == ["quantity > 0"]


def test_a_section_that_is_absent_was_not_collected_and_that_is_not_the_same_as_empty():
    snap = snapshot(indexes=[], triggers=...)
    assert snap.indexes("order_items") == []
    assert snap.triggers("order_items") is None and not snap.collected("triggers")
    bare = snapshot(**{section: ... for section in S.SECTIONS})
    assert bare.table_names() is None and bare.size_bytes("orders") is None
    assert bare.table_statistics("orders") is None and bare.foreign_keys_in("orders") is None


def test_a_table_nobody_analysed_is_not_a_table_with_no_rows():
    snap = snapshot()
    assert snap.table_statistics("orders")["numRows"] == 0
    assert snap.table_statistics("customers")["numRows"] is None
    assert snap.table_statistics("mixedcase")["numRows"] is None, "in the catalog, absent from the statistics view"


def test_a_foreign_key_is_found_from_the_parent_as_well_as_from_the_child():
    snap = snapshot()
    assert [fk["refTable"] for fk in snap.foreign_keys_out("order_items")] == ["orders"]
    assert [fk["table"] for fk in snap.foreign_keys_in("orders")] == ["order_items"]


def test_a_parent_in_another_schema_is_named_and_marked_as_outside_this_snapshot():
    snap = snapshot()
    (fk,) = snap.foreign_keys_out("orders")
    assert fk["outside"] and fk["refOwner"] == "crm" and fk["refTable"] == "customers"
    assert snap.foreign_keys_in("customers") == [], "this schema's CUSTOMERS is a different table"


def test_a_view_is_followed_through_other_views_to_the_tables_underneath():
    snap = snapshot()
    assert snap.base_tables("v_order_lines") == ["order_items", "orders"]
    assert snap.views_on("orders") == ["v_open_orders", "v_order_lines"]
    assert snapshot(dependencies=...).base_tables("v_order_lines") is None


def test_skew_is_read_from_the_histogram_oracle_chose_to_build():
    by_column = {c["column"]: c["skewed"] for c in snapshot().column_statistics("order_items")}
    assert by_column == {"product_id": True, "order_id": False, "note": None}


# --- what must not get through -----------------------------------------------------------------------------

def with_value(document: dict) -> dict:
    document = copy.deepcopy(document)
    document["columnStatistics"][0]["lowValue"] = {"dataType": "NUMBER", "text": "17"}
    return document


def test_a_value_in_a_snapshot_that_says_it_has_none_is_refused_by_column():
    with pytest.raises(S.SnapshotError, match=r"(?i)order_items\.product_id"):
        S.parse(with_value(FULL), "test")


def test_a_snapshot_that_says_it_has_values_may_carry_them_and_says_so():
    document = with_value(FULL)
    document["containsDataValues"] = True
    document["histograms"] = [{"table": "ORDER_ITEMS", "column": "PRODUCT_ID", "endpoints": [
        {"value": {"dataType": "NUMBER", "text": "17"}, "rows": 300},
        {"value": {"dataType": "BLOB", "undecodable": True}, "rows": 2}]}]
    snap = S.parse(document, "test")
    assert snap.contains_data_values
    assert snap.histogram("order_items", "product_id")[0]["value"]["text"] == "17"


def test_a_value_is_decoded_text_or_a_statement_that_it_could_not_be_never_hex_on_its_own():
    document = with_value(FULL)
    document["containsDataValues"] = True
    document["columnStatistics"][0]["lowValue"] = {"dataType": "NUMBER"}
    with pytest.raises(S.SnapshotError, match="lowValue"):
        S.parse(document, "test")


@pytest.mark.parametrize("section, key", [("columns", "lowValue"), ("columnStatistics", "low_value"),
                                          ("tableStatistics", "sample")])
def test_a_key_nobody_defined_does_not_ride_along(section, key):
    """A value-bearing column under another name is how real data would get into a snapshot marked clean."""
    document = copy.deepcopy(FULL)
    document[section][0][key] = "C102"
    with pytest.raises(S.SnapshotError, match=section):
        S.parse(document, "test")


def test_a_format_this_build_does_not_know_is_refused_before_anything_is_read():
    with pytest.raises(S.SnapshotError, match="formatVersion 3"):
        S.parse({"formatVersion": 3, "whatever": True}, "test")


def test_a_file_that_is_not_a_snapshot_says_what_is_wrong(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(S.SnapshotError, match="not JSON"):
        S.load(path)
    with pytest.raises(S.SnapshotError, match="cannot be read"):
        S.load(tmp_path / "missing.json")


def test_a_snapshot_is_named_by_its_file_and_its_content(tmp_path):
    path = tmp_path / "shop.json"
    path.write_text(json.dumps(FULL), encoding="utf-8")
    first = S.load(path).ident
    assert first.startswith("shop.json@") and len(first) == len("shop.json@") + 8
    path.write_text(json.dumps(dict(FULL, schema="OTHER")), encoding="utf-8")
    assert S.load(path).ident != first


# --- version 2 -----------------------------------------------------------------------------------------------

V2 = {
    "formatVersion": 2, "containsSourceCode": False,
    "indexes": [{"name": "PK_ORDER_ITEMS", "table": "ORDER_ITEMS", "unique": True, "columns": ["ORDER_ID", "LINE_NO"]},
                {"name": "IX_ITEMS_PRODUCT", "table": "ORDER_ITEMS", "unique": False, "columns": ["PRODUCT_ID"]}],
    "indexStatistics": [{"index": "PK_ORDER_ITEMS", "distinctKeys": 1200, "leafBlocks": 4, "clusteringFactor": 9,
                         "numRows": 1200, "lastAnalyzed": "2026-08-30T02:00:00"}],
    "objects": [{"name": "CREATE_ORDER", "type": "PROCEDURE", "status": "VALID", "lastDdl": "2026-08-01T10:00:00"},
                {"name": "OLD_REPORT", "type": "PROCEDURE", "status": "INVALID", "lastDdl": "2019-01-01T00:00:00"},
                {"name": "ORDERS", "type": "TABLE", "status": "VALID"}],
    "tableModifications": [{"table": "ORDER_ITEMS", "inserts": 310, "updates": 12, "deletes": 4, "truncated": False,
                            "timestamp": "2026-09-01T00:00:00"}],
    "sequences": [{"name": "ORDER_SEQ", "incrementBy": 1, "cacheSize": 0, "lastNumber": 100412, "cycle": False}],
    "partitions": [{"table": "ORDERS", "type": "RANGE", "subpartitionType": "NONE", "count": 4,
                    "keyColumns": ["ORDER_DATE"]}],
    "synonyms": [], "dbLinks": [{"name": "WAREHOUSE_LINK"}], "lobs": [{"table": "CUSTOMERS", "column": "PHOTO"}],
    "materializedViews": [],
    "columnComments": [{"table": "ORDERS", "column": "STATUS", "comment": "RECEIVED / SHIPPED / CANCELLED"}],
}


def v2(**changes) -> S.Snapshot:
    document = {**copy.deepcopy(FULL), **copy.deepcopy(V2)}
    for key, value in changes.items():
        if value is ...:
            document.pop(key, None)
        else:
            document[key] = value
    return S.parse(document, "test")


def test_a_version_2_snapshot_gives_up_what_version_2_added():
    snap = v2()
    primary, secondary = snap.indexes("order_items")
    assert primary["statistics"]["distinctKeys"] == 1200 and primary["sizeBytes"] == 4096
    assert secondary["statistics"]["distinctKeys"] is None, "in the catalog, never analysed"
    assert snap.modifications("order_items")["inserts"] == 310
    assert snap.partitioning("orders")["keyColumns"] == ["order_date"] and snap.partitioning("customers") is False
    assert snap.column_comments("orders") == {"status": "RECEIVED / SHIPPED / CANCELLED"}
    assert snap.lob_columns("customers") == ["photo"] and snap.lob_columns("orders") == []
    assert [o["name"] for o in snap.objects("PROCEDURE")] == ["create_order", "old_report"]
    assert snap.sequences()[0]["name"] == "order_seq"


def test_a_version_1_snapshot_is_still_read_and_what_it_never_had_is_not_collected():
    """AE7."""
    snap = snapshot()
    assert snap.format_version == 1 and not snap.contains_source_code
    assert snap.indexes("order_items") == [], "FULL has an empty index section"
    with_index = snapshot(indexes=V2["indexes"])
    assert with_index.indexes("order_items")[0]["statistics"] is None, "the index is known; its statistics were never asked for"
    for answer in (snap.modifications("orders"), snap.partitioning("orders"), snap.column_comments("orders"),
                   snap.lob_columns("orders"), snap.objects(), snap.sequences()):
        assert answer is None


def test_no_writes_flushed_is_zero_and_not_collected_is_not():
    """AE8."""
    assert v2().modifications("orders") == {"table": "orders", "inserts": 0, "updates": 0, "deletes": 0,
                                            "truncated": None, "timestamp": None}
    assert v2(tableModifications=...).modifications("orders") is None


def test_source_code_in_a_snapshot_that_says_it_has_none_is_refused_by_object():
    sources = [{"type": "PROCEDURE", "name": "CREATE_ORDER", "text": "PROCEDURE create_order IS BEGIN NULL; END;"}]
    with pytest.raises(S.SnapshotError, match="PROCEDURE CREATE_ORDER"):
        v2(sources=sources)
    snap = v2(sources=sources, containsSourceCode=True)
    assert snap.contains_source_code and snap.source_of("PROCEDURE", "create_order")["text"].startswith("PROCEDURE")
    assert snap.source_of("PROCEDURE", "missing") is None


def test_a_wrapped_unit_says_it_is_wrapped_and_carries_no_text():
    snap = v2(containsSourceCode=True, sources=[{"type": "PACKAGE BODY", "name": "VENDOR_PKG", "wrapped": True}])
    assert snap.source_of("PACKAGE BODY", "vendor_pkg") == {"type": "PACKAGE BODY", "name": "vendor_pkg", "wrapped": True}
    with pytest.raises(S.SnapshotError, match="sources"):
        v2(containsSourceCode=True, sources=[{"type": "PACKAGE BODY", "name": "X", "wrapped": True, "text": "a000000"}])


def test_version_2_must_say_whether_it_holds_source_code():
    with pytest.raises(S.SnapshotError, match="containsSourceCode"):
        v2(containsSourceCode=...)


@pytest.mark.parametrize("section, key", [("dbLinks", "host"), ("dbLinks", "username"), ("sequences", "maxValue"),
                                          ("partitions", "highValue"), ("objects", "source")])
def test_the_new_sections_take_no_key_nobody_defined(section, key):
    """A DB link's host and account, a partition's bounds: what must not ride along has no place to sit."""
    document = {**copy.deepcopy(FULL), **copy.deepcopy(V2)}
    document[section] = document[section] or [{"name": "X"}]
    document[section][0][key] = "db.internal.example"
    with pytest.raises(S.SnapshotError, match=section):
        S.parse(document, "test")
