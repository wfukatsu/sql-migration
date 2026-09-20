"""The catalog snapshot: what only the database knows, read from a file.

`difftest/catalog_snapshot.py` writes it once against the real Oracle; everything here reads the file and never a
database. One snapshot is one Oracle schema (the `USER_*` dictionary views), so a name in it needs no owner.

## Three different kinds of "nothing"

The page this feeds has to tell them apart, so the reader does too:

* a section whose key is **absent** was not collected -- every accessor answers `None`, and the page says 未取得;
* a table whose statistics are **null** was never analysed -- `numRows` is `None`, and the page says 統計なし;
* an **empty list** or a `0` was looked at and held nothing.

Folding any two of these together turns "nobody looked" into "there is none", which is the mistake the page exists
to prevent.

## Real data values

`LOW_VALUE`, `HIGH_VALUE` and histogram endpoints are rows of the customer's data. They are allowed only in a
snapshot that says `containsDataValues: true`; one that says `false` and carries a value anyway is refused, by column
name, before anything is rendered from it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("snapshot.schema.json")
FORMAT_VERSION = 1

# Oracle builds a histogram on a column (with the default METHOD_OPT) when its values are unevenly spread and a
# query has filtered on it. "NONE" is therefore "no skew worth recording", anything else is "skewed". A column
# nobody analysed has no histogram value at all, and that is not an answer either way.
SKEW_HISTOGRAMS = {"FREQUENCY", "TOP-FREQUENCY", "HEIGHT BALANCED", "HYBRID"}

SECTIONS = ("tables", "columns", "constraints", "indexes", "triggers", "views", "dependencies", "segments",
            "tableStatistics", "columnStatistics", "histograms")


class SnapshotError(ValueError):
    """The file is not a snapshot this build can trust. The message says where."""


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def name(identifier: str | None) -> str | None:
    """Catalog names are joined to names the SQL analysis produced, and those are bare and lower case
    (`plsql/sqlbridge.py` drops the owner and folds the case, quoted or not)."""
    return identifier.lower() if isinstance(identifier, str) else identifier


@dataclass
class Snapshot:
    ident: str
    collected_at: str
    database: dict
    schema_name: str
    collector: dict
    contains_data_values: bool
    skipped: list[dict] = field(default_factory=list)
    sections: dict[str, list[dict]] = field(default_factory=dict)

    # --- whether something was collected at all -------------------------------------------------------

    def collected(self, section: str) -> bool:
        return section in self.sections

    def _rows(self, section: str) -> list[dict] | None:
        return self.sections.get(section)

    def _of(self, section: str, table: str, key: str = "table") -> list[dict] | None:
        rows = self._rows(section)
        return None if rows is None else [row for row in rows if row.get(key) == table]

    # --- tables ---------------------------------------------------------------------------------------

    def table_names(self) -> list[str] | None:
        rows = self._rows("tables")
        return None if rows is None else sorted(row["name"] for row in rows)

    def has_table(self, table: str) -> bool | None:
        names = self.table_names()
        return None if names is None else table in names

    def columns(self, table: str) -> list[dict] | None:
        rows = self._of("columns", table)
        return None if rows is None else sorted(rows, key=lambda row: row["position"])

    def constraints(self, table: str) -> list[dict] | None:
        """Without the NOT NULL checks Oracle generates for a NOT NULL column: the column list already says it."""
        rows = self._of("constraints", table)
        return None if rows is None else [row for row in rows if not _is_not_null_check(row)]

    def foreign_keys_out(self, table: str) -> list[dict] | None:
        rows = self.constraints(table)
        return None if rows is None else [self._foreign_key(row) for row in rows if row["type"] == "FOREIGN KEY"]

    def foreign_keys_in(self, table: str) -> list[dict] | None:
        rows = self._rows("constraints")
        if rows is None:
            return None
        return [self._foreign_key(row) for row in rows
                if row["type"] == "FOREIGN KEY" and row.get("refTable") == table and not self._outside(row)]

    def _outside(self, row: dict) -> bool:
        owner = row.get("refOwner")
        return owner is not None and owner != name(self.schema_name)

    def _foreign_key(self, row: dict) -> dict:
        """`outside` marks a parent in another schema: it is named, and it is not in this snapshot."""
        return {"name": row["name"], "table": row["table"], "columns": row["columns"],
                "refTable": row.get("refTable"), "refColumns": row.get("refColumns", []),
                "refOwner": row.get("refOwner"), "deleteRule": row.get("deleteRule"),
                "outside": self._outside(row)}

    def indexes(self, table: str) -> list[dict] | None:
        return self._of("indexes", table)

    def triggers(self, table: str | None = None) -> list[dict] | None:
        rows = self._rows("triggers")
        if rows is None or table is None:
            return rows
        return [row for row in rows if row.get("table") == table]

    # --- views ----------------------------------------------------------------------------------------

    def views(self) -> list[dict] | None:
        return self._rows("views")

    def view_names(self) -> set[str] | None:
        rows = self.views()
        return None if rows is None else {row["name"] for row in rows}

    def base_tables(self, view: str) -> list[str] | None:
        """The tables a view reads, through other views if need be. `None` when dependencies were not collected:
        then a view is a name and nothing more."""
        rows = self._rows("dependencies")
        if rows is None:
            return None
        views = self.view_names() or set()
        found, seen, todo = set(), set(), [view]
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            for row in rows:
                if row["name"] != current or row["type"] != "VIEW":
                    continue
                if row["refType"] == "TABLE":
                    found.add(row["refName"])
                elif row["refType"] == "VIEW" or row["refName"] in views:
                    todo.append(row["refName"])
        return sorted(found)

    def views_on(self, table: str) -> list[str] | None:
        views = self.view_names()
        if views is None or not self.collected("dependencies"):
            return None
        return sorted(view for view in views if table in (self.base_tables(view) or []))

    # --- volume and statistics ------------------------------------------------------------------------

    def size_bytes(self, table: str) -> int | None:
        """The table's own segments. Indexes and LOBs are other segments and are not added in: the number is
        "how much table is there", and a reader adding it to an index size would count nothing twice."""
        rows = self._rows("segments")
        if rows is None:
            return None
        return sum(row["bytes"] for row in rows if row["name"] == table and row["type"].startswith("TABLE"))

    def table_statistics(self, table: str) -> dict | None:
        """`None`: statistics were not collected. A dict whose `numRows` is `None`: collected, never analysed."""
        rows = self._of("tableStatistics", table)
        if rows is None:
            return None
        return rows[0] if rows else {"table": table, "numRows": None, "blocks": None, "avgRowLen": None,
                                     "lastAnalyzed": None, "stale": None}

    def column_statistics(self, table: str) -> list[dict] | None:
        rows = self._of("columnStatistics", table)
        if rows is None:
            return None
        return [dict(row, skewed=skewed(row)) for row in rows]

    def histogram(self, table: str, column: str) -> list[dict] | None:
        rows = self._of("histograms", table)
        if rows is None:
            return None
        return next((row["endpoints"] for row in rows if row["column"] == column), [])


def skewed(column_statistics: dict) -> bool | None:
    histogram = column_statistics.get("histogram")
    if histogram is None:
        return None
    return histogram.upper() in SKEW_HISTOGRAMS


def _is_not_null_check(row: dict) -> bool:
    if row["type"] != "CHECK":
        return False
    condition = (row.get("condition") or "").replace('"', "").strip().lower()
    return len(row["columns"]) == 1 and condition == f"{row['columns'][0]} is not null"


# --- reading ---------------------------------------------------------------------------------------------

_NAME_KEYS = {"name", "table", "column", "refTable", "refOwner", "refName"}


def _normalise(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if key in _NAME_KEYS:
            out[key] = name(value)
        elif key in ("columns", "refColumns"):
            out[key] = [name(v) for v in value]
        else:
            out[key] = value
    return out


def _data_values(document: dict) -> list[str]:
    found = [f"{row['table']}.{row['column']}" for row in document.get("columnStatistics", [])
             if "lowValue" in row or "highValue" in row]
    found += [f"{row['table']}.{row['column']} (histogram)" for row in document.get("histograms", [])
              if row.get("endpoints")]
    return found


def parse(document: dict, ident: str = "<memory>") -> Snapshot:
    if not isinstance(document, dict):
        raise SnapshotError(f"{ident}: a snapshot is a JSON object")
    version = document.get("formatVersion")
    if version != FORMAT_VERSION:
        raise SnapshotError(f"{ident}: snapshot formatVersion {version!r} cannot be read by this build "
                            f"(it reads {FORMAT_VERSION}); take the snapshot again with the matching collector")
    import jsonschema  # imported lazily: only validation needs it

    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(document), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(part) for part in first.path) or "(top level)"
        raise SnapshotError(f"{ident}: not a valid snapshot at {where}: {first.message}"
                            + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""))
    if not document["containsDataValues"]:
        values = _data_values(document)
        if values:
            raise SnapshotError(f"{ident}: says containsDataValues=false but carries real data values for "
                                f"{', '.join(values[:5])}" + (" ..." if len(values) > 5 else "")
                                + "; refusing to render a page that would pass them on unmarked")
    return Snapshot(ident=ident, collected_at=document["collectedAt"], database=document["database"],
                    schema_name=document["schema"], collector=document["collector"],
                    contains_data_values=document["containsDataValues"], skipped=document.get("skipped", []),
                    sections={section: [_normalise(row) for row in document[section]]
                              for section in SECTIONS if section in document})


def load(path: str | Path) -> Snapshot:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SnapshotError(f"{path}: cannot be read: {exc.strerror}") from exc
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"{path}: not JSON: {exc}") from exc
    # named like `OracleSchema.snapshot` (plsql/symbols.py): the file, and enough of its digest to tell two apart
    return parse(document, f"{path.name}@{hashlib.sha1(raw).hexdigest()[:8]}")
