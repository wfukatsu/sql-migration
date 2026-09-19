"""Lightweight table metadata used for access-path analysis (GET / partition SCAN / index SCAN / cross-partition SCAN).

The registry is populated from CREATE TABLE / CREATE INDEX statements found in the script, and/or from a
ScalarDB Schema Loader JSON file (https://scalardb.scalar-labs.com/docs/latest/schema-loader/).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# The keywords of the ScalarDB SQL grammar: the K_ tokens of the 3.19.1 lexer (SqlLexer in scalardb-sql-direct-mode).
# Checked on a cluster: every one of them is reserved (`SELECT type FROM t`, `FROM order` are syntax errors), a name
# in double quotes is a name whatever it spells (`"type"`, `"my col"`), a backtick is not a quote, and a double quote
# inside a name cannot be written (`"a""b"` is a syntax error).
SQL_KEYWORDS = set("""
    ABAC_COMPARTMENT ABAC_COMPARTMENTS ABAC_GROUP ABAC_GROUPS ABAC_LEVEL ABAC_LEVELS ABAC_NAMESPACE_POLICY
    ABAC_NAMESPACE_POLICIES ABAC_POLICY ABAC_POLICIES ABAC_READ_TAG ABAC_TABLE_POLICY ABAC_TABLE_POLICIES
    ABAC_USER_TAG_INFO ABAC_WRITE_TAG ABORT ACCESS ADMIN ADD ALL ALTER AND AS ASC AUTH_METHOD BEGIN BETWEEN ESCAPE BY
    CASCADE CLUSTERING COLUMN COMMIT COORDINATOR CREATE DATA DATA_TAG_COLUMN DEFAULT DEFAULT_LEVEL DELETE DESC
    DESCRIBE DISABLE DROP ENABLE ENCRYPTED EXIST EXISTS FALSE FOR FROM GROUP GRANT GRANTS HAVING IF IN INDEX INFO
    INNER INSERT INTO IS JOIN KEY LEFT LEVEL_NUMBER LIKE LIMIT LONG_NAME MODE NAMESPACE NAMESPACES NO_SUPERUSER NONE
    NOT NULL OIDC ON ONLY OPTION OR ORDER OUTER PASSWORD PARENT_GROUP POLICY POLICIES PREPARE PRIMARY PRIVILEGES READ
    READ_ONLY_ACCESS READ_WRITE_ACCESS REMOVE RENAME RESUME RIGHT ROLLBACK ROW ROW_LEVEL REVOKE ROLE ROLES SELECT SET
    SHOW START SUPERUSER SUSPEND TABLE TABLES TO TRANSACTION TRUE TRUNCATE TWO_PHASE_COMMIT_TRANSACTION TYPE UPDATE
    UPSERT USE USER USERPASS USERS USING VALIDATE VALUES WHERE WITH WRITE BIGINT BLOB BOOLEAN DOUBLE FLOAT INT TEXT
    DATE TIME TIMESTAMP TIMESTAMPTZ
""".split())


def needs_quotes(name: str) -> bool:
    """Whether ScalarDB SQL reads `name` as a name only in double quotes."""
    return name.upper() in SQL_KEYWORDS or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)


def quoted(name: str) -> str:
    """`name` as ScalarDB SQL has to see it. The caller has refused a name with a double quote in it."""
    return f'"{name}"' if needs_quotes(name) else name


@dataclass
class TableMeta:
    namespace: str | None
    name: str
    partition_key: list[str]
    clustering_key: list[str]
    clustering_order: dict[str, str]  # column -> ASC/DESC
    columns: dict[str, str]  # column -> ScalarDB type
    secondary_indexes: list[str] = field(default_factory=list)

    @property
    def primary_key(self) -> list[str]:
        return self.partition_key + self.clustering_key

    def qualified(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name

    def to_schema_loader(self) -> dict:
        d = {
            "transaction": True,
            "partition-key": list(self.partition_key),
            "clustering-key": [f"{c} {self.clustering_order.get(c, 'ASC')}" for c in self.clustering_key],
            "columns": dict(self.columns),
        }
        if self.secondary_indexes:
            d["secondary-index"] = list(self.secondary_indexes)
        return d


class SchemaRegistry:
    def __init__(self) -> None:
        self._tables: dict[str, TableMeta] = {}

    def add(self, meta: TableMeta) -> None:
        self._tables[meta.name.lower()] = meta

    def get(self, name: str) -> TableMeta | None:
        return self._tables.get(name.lower())

    def add_index(self, table: str, column: str) -> None:
        meta = self.get(table)
        if meta and column not in meta.secondary_indexes:
            meta.secondary_indexes.append(column)

    def tables(self) -> list[TableMeta]:
        return list(self._tables.values())

    def to_schema_loader_json(self) -> str:
        return json.dumps({t.qualified(): t.to_schema_loader() for t in self.tables()}, indent=2)

    @classmethod
    def from_schema_loader_json(cls, path: str) -> "SchemaRegistry":
        reg = cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for qualified, spec in data.items():
            ns, _, name = qualified.rpartition(".")
            ck, order = [], {}
            for item in spec.get("clustering-key", []):
                col, _, o = item.partition(" ")
                ck.append(col)
                order[col] = (o or "ASC").upper()
            reg.add(TableMeta(ns or None, name, list(spec.get("partition-key", [])), ck, order,
                              dict(spec.get("columns", {})), list(spec.get("secondary-index", []))))
        return reg
