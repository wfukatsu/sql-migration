"""skills/sql-transpile/examples/dml: the Oracle / PostgreSQL / MySQL files stay in step (same test ids, same data),
every statement parses in its own dialect, every DML statement carries a @check query, and the ScalarDB conversion of
each test statement matches its @expect-scalardb annotation."""

import logging
import re
from pathlib import Path

import pytest
import sqlglot

from scalardb_migrate.converter import _split_statements, convert_script

logging.getLogger("sqlglot").setLevel(logging.ERROR)
DML = Path(__file__).resolve().parents[1] / "skills/sql-transpile/examples/dml"
DIALECTS = ("oracle", "postgres", "mysql")


def body_of(chunk: str) -> str:
    return "\n".join(ln for ln in chunk.splitlines() if not ln.strip().startswith("--")).strip()


def load(dialect: str) -> list[tuple[str, dict, str, object]]:
    """(test id, annotations, SQL body, conversion result) for every annotated test statement."""
    text = (DML / f"{dialect}.sql").read_text(encoding="utf-8")
    chunks = _split_statements(text, dialect)
    results, _ = convert_script(text, dialect)
    assert len(chunks) == len(results)
    out = []
    for chunk, r in zip(chunks, results):
        ann = dict(re.findall(r"--\s*@([\w-]+):\s*(.+)", chunk))
        if "id" in ann:
            out.append((ann["id"].strip(), ann, body_of(chunk), r))
    return out


CASES = {d: load(d) for d in DIALECTS}


def setup_inserts(dialect: str) -> dict[str, int]:
    head = (DML / f"{dialect}.sql").read_text(encoding="utf-8").split("-- @tests")[0]
    counts: dict[str, int] = {}
    for table in re.findall(r"^INSERT INTO (\w+)", head, re.M):
        counts[table] = counts.get(table, 0) + 1
    return counts


def test_dialects_share_test_ids_and_setup_rows():
    ids = {d: [c[0] for c in CASES[d]] for d in DIALECTS}
    assert ids["oracle"] == ids["postgres"] == ids["mysql"]
    assert len(set(ids["oracle"])) == len(ids["oracle"]) >= 45
    assert setup_inserts("oracle") == setup_inserts("postgres") == setup_inserts("mysql")


@pytest.mark.parametrize("dialect", DIALECTS)
def test_statements_parse_and_dml_has_a_check(dialect):
    for test_id, ann, body, _ in CASES[dialect]:
        sqlglot.parse_one(body, read=dialect)
        assert ann.get("note") and ann.get("expect-scalardb") in ("OK", "WARN", "PLANNED", "ERROR"), test_id
        if test_id[0] in "IUD":
            assert ann.get("check"), f"{dialect} {test_id}: DML without @check"
            sqlglot.parse_one(ann["check"], read=dialect)


@pytest.mark.parametrize("dialect,test_id", [(d, c[0]) for d in DIALECTS for c in CASES[d]])
def test_scalardb_conversion_matches_expectation(dialect, test_id):
    _, ann, _, r = next(c for c in CASES[dialect] if c[0] == test_id)
    assert r.status == ann["expect-scalardb"].strip(), [(i.code, i.message) for i in r.issues if i.severity != "INFO"]
