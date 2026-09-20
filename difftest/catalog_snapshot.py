#!/usr/bin/env python3
"""Catalog snapshot of one Oracle schema, for the Migration Explorer (docs/guide/explorer.md).

    SRC_ORACLE_HOST=db SRC_ORACLE_SERVICE=ORCLPDB1 SRC_ORACLE_USER=shop \\
        python catalog_snapshot.py --out /secure/dir/shop.snapshot.json

This one file is the whole tool: it imports nothing from the repository, so it can be handed to the person who has
access to the database. It needs Python 3.9+ and `pip install oracledb`. The default is thin mode, which needs no
Oracle client; `--thick` uses an installed Oracle Client, for a database that demands Native Network Encryption.

## What it does to the database: nothing

Every statement it can send is in `STATEMENTS` below -- read them. They are SELECTs on the `USER_*` dictionary
views of the connecting user, preceded by one `SET TRANSACTION READ ONLY`. It creates nothing, gathers no
statistics, and reads no table of yours: the row counts are the optimizer's, as of `LAST_ANALYZED`.

## Real data values are not collected unless you ask

Column statistics hold the lowest and highest value of each column, and histograms hold its most frequent values:
rows of real data. By default the statements that would fetch them are not sent at all. `--include-values` sends
them -- but only together with `--acknowledge-real-data`, because one mistyped option must not be enough to carry
somebody's data out of their database. The snapshot then says `containsDataValues: true`, so everything made from
it can say so too.

## Source code is not collected unless you ask

`--include-source` also reads USER_SOURCE: the text of your procedures, functions, packages and types. Without it
that statement is not sent, and the snapshot says `containsSourceCode: false`. A wrapped (obfuscated) unit is
never collected: the snapshot records that it is wrapped, and no text.

That flag is about USER_SOURCE only. **View definitions, materialized view queries, trigger bodies and CHECK
conditions are collected either way** (the explorer needs them to say what touches a table), and they may contain
literals. A snapshot taken without `--include-source` is therefore not "structure only".

What is never collected, whatever the options: where a DB link points (host, account), and the bounds of
partitions (they are data).

## Connection

    SRC_ORACLE_DSN       a full connect descriptor or Easy Connect string; wins over the three below.
                         `tcps://host:2484/service` connects over TLS.
    SRC_ORACLE_HOST      default localhost
    SRC_ORACLE_PORT      default 1521
    SRC_ORACLE_SERVICE   the service name
    SRC_ORACLE_USER      the schema to snapshot: the snapshot is this user's objects
    SRC_ORACLE_PASSWORD  asked for on the terminal when unset. Never an argument: arguments end up in `ps`
                         and in shell history.

Exit status: 0 written, 1 written but a section could not be read (see `skipped` in the file), 2 not written.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

VERSION = "2"
FORMAT_VERSION = 2

READ_ONLY = "SET TRANSACTION READ ONLY"

# Everything this program can send, by the section of the snapshot it fills. Nothing else reaches the database.
STATEMENTS = {
    "tables": """
        SELECT t.table_name, t.temporary, t.partitioned, c.comments
          FROM user_tables t
          LEFT JOIN user_tab_comments c ON c.table_name = t.table_name
         WHERE t.dropped = 'NO' AND t.nested = 'NO' AND t.secondary = 'N'
         ORDER BY t.table_name""",
    "columns": """
        SELECT c.table_name, c.column_name, c.column_id, c.data_type,
               CASE WHEN c.char_used IS NOT NULL THEN c.char_length ELSE c.data_length END,
               c.data_precision, c.data_scale, c.nullable
          FROM user_tab_columns c
          JOIN user_tables t ON t.table_name = c.table_name
         WHERE t.dropped = 'NO' AND t.nested = 'NO' AND t.secondary = 'N'
         ORDER BY c.table_name, c.column_id""",
    "constraints": """
        SELECT c.constraint_name, c.table_name, c.constraint_type, c.search_condition_vc,
               c.r_owner, c.r_constraint_name, c.delete_rule, c.status, c.generated
          FROM user_constraints c
         WHERE c.constraint_type IN ('P', 'U', 'C', 'R') AND c.table_name NOT LIKE 'BIN$%'
         ORDER BY c.table_name, c.constraint_name""",
    "constraintColumns": """
        SELECT cc.constraint_name, cc.table_name, cc.column_name, cc.position
          FROM user_cons_columns cc
         WHERE cc.table_name NOT LIKE 'BIN$%'
         ORDER BY cc.constraint_name, cc.position""",
    "indexes": """
        SELECT i.index_name, i.table_name, i.uniqueness
          FROM user_indexes i
         WHERE i.table_name NOT LIKE 'BIN$%' AND i.index_type <> 'LOB'
         ORDER BY i.table_name, i.index_name""",
    "indexColumns": """
        SELECT ic.index_name, ic.column_name, ic.column_position
          FROM user_ind_columns ic
         WHERE ic.table_name NOT LIKE 'BIN$%'
         ORDER BY ic.index_name, ic.column_position""",
    "triggers": """
        SELECT g.trigger_name, g.table_name, g.trigger_type, g.triggering_event, g.status, g.trigger_body
          FROM user_triggers g
         WHERE g.trigger_name NOT LIKE 'BIN$%'
         ORDER BY g.trigger_name""",
    "views": """
        SELECT v.view_name, v.text
          FROM user_views v
         ORDER BY v.view_name""",
    "dependencies": """
        SELECT d.name, d.type, d.referenced_owner, d.referenced_name, d.referenced_type
          FROM user_dependencies d
         WHERE d.referenced_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW', 'SYNONYM', 'SEQUENCE',
                                     'PROCEDURE', 'FUNCTION', 'PACKAGE', 'TYPE')
           AND d.referenced_owner NOT IN ('SYS', 'PUBLIC', 'SYSTEM')
         ORDER BY d.name, d.type, d.referenced_owner, d.referenced_name, d.referenced_type""",
    "segments": """
        SELECT s.segment_name, s.segment_type, SUM(s.bytes)
          FROM user_segments s
         WHERE s.segment_name NOT LIKE 'BIN$%'
         GROUP BY s.segment_name, s.segment_type
         ORDER BY s.segment_name, s.segment_type""",
    "tableStatistics": """
        SELECT s.table_name, s.num_rows, s.blocks, s.avg_row_len, s.last_analyzed, s.stale_stats
          FROM user_tab_statistics s
         WHERE s.object_type = 'TABLE' AND s.table_name NOT LIKE 'BIN$%'
         ORDER BY s.table_name""",
    "columnStatistics": """
        SELECT s.table_name, s.column_name, s.num_distinct, s.num_nulls, s.avg_col_len, s.density,
               s.histogram, s.num_buckets, s.last_analyzed
          FROM user_tab_col_statistics s
         WHERE s.table_name NOT LIKE 'BIN$%'
         ORDER BY s.table_name, s.column_name""",
    "objects": """
        SELECT o.object_name, o.object_type, o.status, o.created, o.last_ddl_time
          FROM user_objects o
         WHERE o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW', 'SEQUENCE', 'SYNONYM', 'PROCEDURE',
                                 'FUNCTION', 'PACKAGE', 'PACKAGE BODY', 'TRIGGER', 'TYPE', 'TYPE BODY')
           AND o.object_name NOT LIKE 'BIN$%' AND o.generated = 'N'
         ORDER BY o.object_type, o.object_name""",
    # the table-level row (no partition name) already is the total of the partitions: adding them would count twice
    "tableModifications": """
        SELECT m.table_name, m.inserts, m.updates, m.deletes, m.truncated, m.timestamp
          FROM user_tab_modifications m
         WHERE m.partition_name IS NULL AND m.table_name NOT LIKE 'BIN$%'
         ORDER BY m.table_name""",
    "indexStatistics": """
        SELECT i.index_name, i.distinct_keys, i.leaf_blocks, i.clustering_factor, i.num_rows, i.last_analyzed
          FROM user_indexes i
         WHERE i.table_name NOT LIKE 'BIN$%' AND i.index_type <> 'LOB'
         ORDER BY i.index_name""",
    "sequences": """
        SELECT q.sequence_name, q.increment_by, q.cache_size, q.last_number, q.cycle_flag
          FROM user_sequences q
         ORDER BY q.sequence_name""",
    # how a table is partitioned and on what. Not the bounds of the partitions: those are data
    "partitions": """
        SELECT p.table_name, p.partitioning_type, p.subpartitioning_type, p.partition_count
          FROM user_part_tables p
         WHERE p.table_name NOT LIKE 'BIN$%'
         ORDER BY p.table_name""",
    "partitionKeys": """
        SELECT k.name, k.column_name, k.column_position
          FROM user_part_key_columns k
         WHERE k.object_type = 'TABLE' AND k.name NOT LIKE 'BIN$%'
         ORDER BY k.name, k.column_position""",
    "subpartitionKeys": """
        SELECT k.name, k.column_name, k.column_position
          FROM user_subpart_key_columns k
         WHERE k.object_type = 'TABLE' AND k.name NOT LIKE 'BIN$%'
         ORDER BY k.name, k.column_position""",
    "synonyms": """
        SELECT y.synonym_name, y.table_owner, y.table_name, y.db_link
          FROM user_synonyms y
         ORDER BY y.synonym_name""",
    # the name of a link and nothing about where it points
    "dbLinks": """
        SELECT l.db_link
          FROM user_db_links l
         ORDER BY l.db_link""",
    "lobs": """
        SELECT b.table_name, b.column_name
          FROM user_lobs b
         WHERE b.table_name NOT LIKE 'BIN$%'
         ORDER BY b.table_name, b.column_name""",
    "materializedViews": """
        SELECT v.mview_name, v.query, v.refresh_mode, v.refresh_method, v.last_refresh_date
          FROM user_mviews v
         ORDER BY v.mview_name""",
    "columnComments": """
        SELECT c.table_name, c.column_name, c.comments
          FROM user_col_comments c
         WHERE c.comments IS NOT NULL AND c.table_name NOT LIKE 'BIN$%'
         ORDER BY c.table_name, c.column_name""",
}

# Sent only with --include-source: the one statement that reads your code.
SOURCE_STATEMENTS = {
    "sources": """
        SELECT u.type, u.name, u.line, u.text
          FROM user_source u
         ORDER BY u.type, u.name, u.line""",
}

# Sent only with --include-values. These two are the only statements that name a column holding real data.
VALUE_STATEMENTS = {
    "columnValues": """
        SELECT s.table_name, s.column_name, c.data_type, s.low_value, s.high_value
          FROM user_tab_col_statistics s
          JOIN user_tab_columns c ON c.table_name = s.table_name AND c.column_name = s.column_name
         WHERE s.table_name NOT LIKE 'BIN$%'
         ORDER BY s.table_name, s.column_name""",
    "histograms": """
        SELECT h.table_name, h.column_name, c.data_type, s.histogram, h.endpoint_number, h.endpoint_value,
               h.endpoint_actual_value, h.endpoint_repeat_count
          FROM user_tab_histograms h
          JOIN user_tab_columns c ON c.table_name = h.table_name AND c.column_name = h.column_name
          JOIN user_tab_col_statistics s ON s.table_name = h.table_name AND s.column_name = h.column_name
         WHERE s.histogram IN ('FREQUENCY', 'TOP-FREQUENCY', 'HYBRID') AND h.table_name NOT LIKE 'BIN$%'
         ORDER BY h.table_name, h.column_name, h.endpoint_number""",
}

MOST_FREQUENT = 20  # per column: a histogram can have 2048 endpoints, and the page wants the head of it

CONSTRAINT_TYPES = {"P": "PRIMARY KEY", "U": "UNIQUE", "C": "CHECK", "R": "FOREIGN KEY"}


def statements(include_values: bool, include_source: bool = False) -> dict[str, str]:
    return {**STATEMENTS, **(VALUE_STATEMENTS if include_values else {}),
            **(SOURCE_STATEMENTS if include_source else {})}


# --- decoding Oracle's internal formats ------------------------------------------------------------------
# LOW_VALUE / HIGH_VALUE are RAW in the column's internal format. The database's own decoder
# (DBMS_STATS.CONVERT_RAW_VALUE) is a PL/SQL procedure, which a program that only SELECTs cannot call.

def decode_number(raw: bytes) -> str:
    if raw == b"\x80":
        return "0"
    positive = raw[0] >= 0x80
    if positive:
        exponent, digits = raw[0] - 193, [b - 1 for b in raw[1:]]
    else:
        body = raw[1:-1] if raw[-1] == 102 else raw[1:]
        exponent, digits = 62 - raw[0], [101 - b for b in body]
    if any(not 0 <= d <= 99 for d in digits):
        raise ValueError("not an Oracle NUMBER")
    value = sum(Decimal(d) * (Decimal(100) ** (exponent - i)) for i, d in enumerate(digits))
    text = format(value if positive else -value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def decode_date(raw: bytes) -> str:
    if len(raw) not in (7, 11, 13):
        raise ValueError("not an Oracle DATE or TIMESTAMP")
    year = (raw[0] - 100) * 100 + (raw[1] - 100)
    stamp = datetime.datetime(year, raw[2], raw[3], raw[4] - 1, raw[5] - 1, raw[6] - 1)
    if len(raw) == 7:
        return stamp.isoformat(sep=" ")
    nanos = int.from_bytes(raw[7:11], "big")
    return stamp.isoformat(sep=" ") + (f".{nanos:09d}".rstrip("0") if nanos else "")


def decode_raw(raw, data_type: str) -> dict:
    """The value as text, or a statement that it could not be decoded. Never the hex: a reader cannot tell a hex
    string nobody decoded from a value that happens to look like one."""
    kind = (data_type or "").upper()
    try:
        if raw is None:
            raise ValueError("no value")
        raw = bytes(raw)
        if kind in ("NUMBER", "FLOAT"):
            return {"dataType": kind, "text": decode_number(raw)}
        if kind in ("VARCHAR2", "CHAR"):
            return {"dataType": kind, "text": raw.decode("utf-8")}
        if kind in ("NVARCHAR2", "NCHAR"):
            return {"dataType": kind, "text": raw.decode("utf-16-be")}
        if kind == "DATE" or kind.startswith("TIMESTAMP"):
            return {"dataType": kind, "text": decode_date(raw)}
    except (ValueError, UnicodeDecodeError, IndexError, OverflowError):
        pass
    return {"dataType": kind or "?", "undecodable": True}


def decode_endpoint(data_type: str, endpoint_value, actual_value) -> dict:
    """A histogram endpoint. ENDPOINT_VALUE is a number whatever the column is: the value itself for a NUMBER, a
    Julian day for a DATE, and a lossy encoding of the first bytes for a string -- for which Oracle also keeps the
    text in ENDPOINT_ACTUAL_VALUE when it had to."""
    kind = (data_type or "").upper()
    try:
        if actual_value is not None and kind in ("VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR"):
            return {"dataType": kind, "text": str(actual_value)}
        if endpoint_value is None:
            raise ValueError("no value")
        if kind in ("NUMBER", "FLOAT"):
            text = format(Decimal(str(endpoint_value)), "f")
            return {"dataType": kind, "text": text.rstrip("0").rstrip(".") if "." in text else text}
        if kind == "DATE" or kind.startswith("TIMESTAMP"):
            day = datetime.date.fromordinal(int(endpoint_value) - 1721425)  # Julian day -> proleptic ordinal
            seconds = round((float(endpoint_value) % 1) * 86400)
            stamp = datetime.datetime.combine(day, datetime.time()) + datetime.timedelta(seconds=seconds)
            return {"dataType": kind, "text": stamp.isoformat(sep=" ")}
    except (ValueError, ArithmeticError, OverflowError):
        pass
    return {"dataType": kind or "?", "undecodable": True}


# --- building the snapshot -------------------------------------------------------------------------------

def _int(value):
    return None if value is None else int(value)


def _time(value):
    return None if value is None else value.isoformat(timespec="seconds")


def _group(rows, key_index: int, value_index: int) -> dict:
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(row[key_index], []).append(row[value_index])
    return grouped


def build(fetch, *, include_values: bool, schema: str, database: dict, collected_at: str,
          include_source: bool = False) -> dict:
    """`fetch(section, sql)` returns rows or raises; a section that raises is recorded and the rest goes on --
    one dictionary view the user may not read must not cost the whole snapshot."""
    skipped: list[dict] = []
    rows: dict[str, list] = {}
    for section, sql in statements(include_values, include_source).items():
        try:
            rows[section] = list(fetch(section, sql))
        except Exception as exc:  # noqa: BLE001 - whatever the driver raises, the section is what failed
            skipped.append({"section": section, "reason": str(exc).strip().split("\n")[0][:300]})

    snapshot: dict = {"formatVersion": FORMAT_VERSION, "collectedAt": collected_at,
                      "collector": {"name": Path(__file__).name, "version": VERSION},
                      "database": database, "schema": schema, "containsDataValues": bool(include_values),
                      "containsSourceCode": bool(include_source), "skipped": skipped}

    if "tables" in rows:
        snapshot["tables"] = [{"name": n, "temporary": t == "Y", "partitioned": p == "YES", "comment": c}
                              for n, t, p, c in rows["tables"]]
    if "columns" in rows:
        snapshot["columns"] = [{"table": t, "name": n, "position": _int(i), "dataType": d, "length": _int(ln),
                                "precision": _int(p), "scale": _int(s), "nullable": nullable == "Y"}
                               for t, n, i, d, ln, p, s, nullable in rows["columns"]]
    if "constraints" in rows and "constraintColumns" in rows:
        columns = _group(rows["constraintColumns"], 0, 2)
        tables = {name: table for name, table, *_ in rows["constraints"]}
        snapshot["constraints"] = []
        for name, table, kind, condition, r_owner, r_name, delete_rule, status, generated in rows["constraints"]:
            record = {"name": name, "table": table, "type": CONSTRAINT_TYPES[kind],
                      "columns": columns.get(name, []), "enabled": status == "ENABLED",
                      "generated": generated == "GENERATED NAME"}
            if kind == "C":
                record["condition"] = condition
            if kind == "R":
                # a parent in this schema is resolved here; one in another schema is known by owner and
                # constraint name only (its table is in ALL_CONSTRAINTS, which this program does not read)
                same_schema = r_owner == schema
                record.update({"refOwner": r_owner, "refConstraint": r_name,
                               "refTable": tables.get(r_name) if same_schema else None,
                               "refColumns": columns.get(r_name, []) if same_schema else [],
                               "deleteRule": delete_rule})
            snapshot["constraints"].append(record)
    elif "constraints" in rows or "constraintColumns" in rows:
        skipped.append({"section": "constraints", "reason": "the constraints or their columns could not be read"})
    if "indexes" in rows and "indexColumns" in rows:
        columns = _group(rows["indexColumns"], 0, 1)
        snapshot["indexes"] = [{"name": n, "table": t, "unique": u == "UNIQUE", "columns": columns.get(n, [])}
                               for n, t, u in rows["indexes"]]
    if "triggers" in rows:
        snapshot["triggers"] = [{"name": n, "table": t, "timing": timing, "event": event,
                                 "enabled": status == "ENABLED", "body": None if body is None else str(body)}
                                for n, t, timing, event, status, body in rows["triggers"]]
    if "views" in rows:
        snapshot["views"] = [{"name": n, "text": None if text is None else str(text)} for n, text in rows["views"]]
    if "dependencies" in rows:
        snapshot["dependencies"] = [{"name": n, "type": t, "refOwner": o, "refName": rn, "refType": rt}
                                    for n, t, o, rn, rt in rows["dependencies"]]
    if "segments" in rows:
        snapshot["segments"] = [{"name": n, "type": t, "bytes": _int(b)} for n, t, b in rows["segments"]]
    if "tableStatistics" in rows:
        snapshot["tableStatistics"] = [{"table": t, "numRows": _int(n), "blocks": _int(b), "avgRowLen": _int(a),
                                        "lastAnalyzed": _time(at), "stale": None if stale is None else stale == "YES"}
                                       for t, n, b, a, at, stale in rows["tableStatistics"]]
    if "columnStatistics" in rows:
        values = {(t, c): (d, low, high) for t, c, d, low, high in rows.get("columnValues", [])}
        snapshot["columnStatistics"] = []
        for t, c, distinct, nulls, avg, density, histogram, buckets, at in rows["columnStatistics"]:
            record = {"table": t, "column": c, "numDistinct": _int(distinct), "numNulls": _int(nulls),
                      "avgColLen": _int(avg), "density": None if density is None else float(density),
                      "histogram": histogram, "numBuckets": _int(buckets), "lastAnalyzed": _time(at)}
            if (t, c) in values:
                data_type, low, high = values[(t, c)]
                if low is not None:
                    record["lowValue"] = decode_raw(low, data_type)
                if high is not None:
                    record["highValue"] = decode_raw(high, data_type)
            snapshot["columnStatistics"].append(record)
    if "histograms" in rows:
        snapshot["histograms"] = _histograms(rows["histograms"])
    if "objects" in rows:
        snapshot["objects"] = [{"name": n, "type": t, "status": status, "created": _time(created),
                                "lastDdl": _time(ddl)} for n, t, status, created, ddl in rows["objects"]]
    if "tableModifications" in rows:
        snapshot["tableModifications"] = [
            {"table": t, "inserts": _int(i) or 0, "updates": _int(u) or 0, "deletes": _int(d) or 0,
             "truncated": None if truncated is None else truncated == "YES", "timestamp": _time(at)}
            for t, i, u, d, truncated, at in rows["tableModifications"]]
    if "indexStatistics" in rows:
        snapshot["indexStatistics"] = [{"index": n, "distinctKeys": _int(k), "leafBlocks": _int(b),
                                        "clusteringFactor": _int(f), "numRows": _int(r), "lastAnalyzed": _time(at)}
                                       for n, k, b, f, r, at in rows["indexStatistics"]]
    if "sequences" in rows:
        snapshot["sequences"] = [{"name": n, "incrementBy": _int(i), "cacheSize": _int(c), "lastNumber": _int(last),
                                  "cycle": None if cycle is None else cycle == "Y"}
                                 for n, i, c, last, cycle in rows["sequences"]]
    if "partitions" in rows and "partitionKeys" in rows:
        keys = _group(rows["partitionKeys"], 0, 1)
        subkeys = _group(rows.get("subpartitionKeys", []), 0, 1)
        snapshot["partitions"] = [{"table": t, "type": kind, "subpartitionType": sub, "count": _int(count),
                                   "keyColumns": keys.get(t, []), "subpartitionKeyColumns": subkeys.get(t, [])}
                                  for t, kind, sub, count in rows["partitions"]]
    if "synonyms" in rows:
        snapshot["synonyms"] = [{"name": n, "tableOwner": o, "tableName": t, "dbLink": link}
                                for n, o, t, link in rows["synonyms"]]
    if "dbLinks" in rows:
        snapshot["dbLinks"] = [{"name": n} for (n,) in rows["dbLinks"]]
    if "lobs" in rows:
        snapshot["lobs"] = [{"table": t, "column": c} for t, c in rows["lobs"]]
    if "materializedViews" in rows:
        snapshot["materializedViews"] = [{"name": n, "query": None if q is None else str(q), "refreshMode": mode,
                                          "refreshMethod": method, "lastRefresh": _time(at)}
                                         for n, q, mode, method, at in rows["materializedViews"]]
    if "columnComments" in rows:
        snapshot["columnComments"] = [{"table": t, "column": c, "comment": str(text)}
                                      for t, c, text in rows["columnComments"]]
    if "sources" in rows:
        snapshot["sources"] = _sources(rows["sources"])
    return snapshot


def _sources(rows) -> list[dict]:
    """USER_SOURCE is a row per line. A unit whose first line ends in `wrapped` is obfuscated code -- often a
    vendor's, and reversible with public tools -- so it is named, and its text is left where it is."""
    units: dict = {}
    for kind, name, line, text in rows:
        units.setdefault((kind, name), []).append((line, "" if text is None else str(text)))
    out = []
    for (kind, name), lines in units.items():
        lines.sort(key=lambda item: item[0])
        first = next((text for _, text in lines if text.strip()), "")
        if first.rstrip().lower().endswith(" wrapped"):
            out.append({"type": kind, "name": name, "wrapped": True})
        else:
            out.append({"type": kind, "name": name, "text": "".join(text for _, text in lines)})
    return out


def _histograms(rows) -> list[dict]:
    """The most frequent values of each column. In a frequency histogram ENDPOINT_NUMBER is cumulative, so a
    value's rows are the step from the endpoint before it; a hybrid one says so in ENDPOINT_REPEAT_COUNT."""
    by_column: dict = {}
    for table, column, data_type, histogram, number, value, actual, repeat in rows:
        by_column.setdefault((table, column, data_type, histogram), []).append((number, value, actual, repeat))
    out = []
    for (table, column, data_type, histogram), points in by_column.items():
        endpoints, previous = [], 0
        for number, value, actual, repeat in sorted(points, key=lambda p: p[0]):
            count = _int(repeat) if histogram == "HYBRID" else _int(number) - previous
            previous = _int(number)
            endpoints.append({"value": decode_endpoint(data_type, value, actual), "rows": count})
        endpoints.sort(key=lambda e: -(e["rows"] or 0))
        out.append({"table": table, "column": column, "endpoints": endpoints[:MOST_FREQUENT]})
    return out


# --- talking to the database -----------------------------------------------------------------------------

class Refused(Exception):
    """The snapshot cannot be taken, and the message is safe to print: it never holds the password."""


def connection_settings(environ) -> dict:
    user = environ.get("SRC_ORACLE_USER")
    if not user:
        raise Refused("SRC_ORACLE_USER is not set: it names the schema to snapshot")
    dsn = environ.get("SRC_ORACLE_DSN")
    if not dsn:
        service = environ.get("SRC_ORACLE_SERVICE")
        if not service:
            raise Refused("set SRC_ORACLE_DSN, or SRC_ORACLE_SERVICE (with SRC_ORACLE_HOST and SRC_ORACLE_PORT)")
        dsn = f"{environ.get('SRC_ORACLE_HOST', 'localhost')}:{environ.get('SRC_ORACLE_PORT', '1521')}/{service}"
    return {"user": user, "dsn": dsn}


def explain(error: Exception, password: str | None) -> str:
    text = str(error).strip().split("\n")[0]
    if password:
        text = text.replace(password, "***")
    if "DPY-3001" in text or "DPY-4011" in text or "ORA-12660" in text:
        text += ("\n  this database requires Native Network Encryption, which python-oracledb's thin mode does not "
                 "speak.\n  Connect over TLS instead (SRC_ORACLE_DSN=tcps://host:port/service), or run with --thick "
                 "on a machine that has an Oracle Client installed")
    return text


def collect(settings: dict, password: str, include_values: bool, thick: bool = False,
            include_source: bool = False) -> dict:
    # imported here and nowhere else: the statements above and `build` are readable, and testable, without a driver
    try:
        import oracledb
    except ImportError as exc:
        raise Refused("python-oracledb is not installed: pip install oracledb") from exc
    if thick:
        try:
            oracledb.init_oracle_client()
        except oracledb.Error as exc:
            raise Refused(f"--thick needs an Oracle Client (Instant Client is enough) that python-oracledb can find: "
                          f"{explain(exc, None)}") from exc
    try:
        connection = oracledb.connect(user=settings["user"], password=password, dsn=settings["dsn"])
    except oracledb.Error as exc:
        raise Refused(f"cannot connect as {settings['user']} to {settings['dsn']}: {explain(exc, password)}") from exc
    with connection:
        cursor = connection.cursor()
        cursor.execute(READ_ONLY)

        def fetch(_section: str, sql: str):
            cursor.execute(sql)
            return cursor.fetchall()

        # from the connection, not from a query: V$ views and SYS_CONTEXT are outside USER_*
        database = {"name": getattr(connection, "db_name", None) or getattr(connection, "service_name", None),
                    "version": getattr(connection, "version", None)}
        collected_at = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
        return build(fetch, include_values=include_values, schema=settings["user"].upper(), database=database,
                     collected_at=collected_at, include_source=include_source)


def main(argv: list[str] | None = None, environ=None) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="catalog_snapshot.py", description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="where to write the snapshot. Keep a real one out of any "
                                                     "repository: it describes somebody's database")
    parser.add_argument("--include-values", action="store_true",
                        help="also collect real data values (column low/high, most frequent values)")
    parser.add_argument("--acknowledge-real-data", action="store_true",
                        help="required with --include-values: you know the snapshot will then hold rows of real data")
    parser.add_argument("--include-source", action="store_true",
                        help="also collect USER_SOURCE: the text of procedures, functions, packages and types")
    parser.add_argument("--thick", action="store_true",
                        help="use an installed Oracle Client instead of thin mode (for Native Network Encryption)")
    parser.add_argument("--print-statements", action="store_true",
                        help="print every statement that would be sent, and send none")
    args = parser.parse_args(argv)

    if args.print_statements:
        print(READ_ONLY + ";")
        for section, sql in statements(args.include_values, args.include_source).items():
            print(f"\n-- {section}{sql};")
        return 0
    if args.include_values and not args.acknowledge_real_data:
        print("catalog_snapshot: --include-values collects rows of real data: the lowest and highest value of every "
              "column, and the most frequent values of the columns that have a histogram. The snapshot, and every "
              "page made from it, will hold them.\n  If that is what you want, and you may take it out of this "
              "database, add --acknowledge-real-data. Without --include-values none of it is collected.",
              file=sys.stderr)
        return 2
    try:
        settings = connection_settings(environ)
        password = environ.get("SRC_ORACLE_PASSWORD") or getpass.getpass(f"password for {settings['user']}: ")
        snapshot = collect(settings, password, args.include_values, args.thick, args.include_source)
    except Refused as exc:
        print(f"catalog_snapshot: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tables = len(snapshot.get("tables", []))
    print(f"wrote {out}: schema {snapshot['schema']}, {tables} tables"
          + (", WITH real data values" if args.include_values else ", no data values")
          + (", WITH USER_SOURCE code" if args.include_source
             else ", no USER_SOURCE code (view and trigger definitions are included either way)"))
    for item in snapshot["skipped"]:
        print(f"  not collected: {item['section']} -- {item['reason']}", file=sys.stderr)
    return 1 if snapshot["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
