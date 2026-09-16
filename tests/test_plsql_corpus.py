"""P0-1: the corpus parses, and it covers the categories the PoC has to exercise.

These are corpus-integrity checks, not conversion tests. A file that stops parsing means either the corpus or the
vendored grammar (P0-6) changed; the failure should be visible before Phase 1 depends on it.
"""

from __future__ import annotations

import pathlib

import pytest
from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from plsql.grammar import PlSqlLexer, PlSqlParser

SRC = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql" / "src"
SUFFIXES = {".pks", ".pkb", ".prc", ".trg"}

# 設計書 §16 の 11 カテゴリ -> src/ 直下にある代表ユニット（holdout は数えない）
CATEGORIES = {
    "simple-crud": ["pkg_customer_crud", "prc_add_product"],
    "select-into-exception": ["pkg_order_status"],
    "private-call": ["pkg_order_pricing"],
    "type-rowtype": ["pkg_customer_view"],
    "cursor-loop": ["pkg_order_report"],
    "bulk": ["pkg_bulk_load"],
    "commit-in-routine": ["prc_nightly_close", "prc_audit_autonomous"],
    "dynamic-sql": ["pkg_dynamic_search"],
    "trigger-dblink": ["trg_orders_audit", "trg_orders_seq", "prc_remote_sync"],
    "datatype-edge": ["pkg_money_calc"],
    "concurrency-lock": ["pkg_stock_reserve"],
}


def corpus_files() -> list[pathlib.Path]:
    return sorted(p for p in SRC.rglob("*") if p.suffix in SUFFIXES)


class _Collector(ErrorListener):
    def __init__(self) -> None:
        self.errors: list[str] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append(f"line {line}:{column} {msg}")


@pytest.mark.parametrize("path", corpus_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_corpus_file_parses(path: pathlib.Path):
    collector = _Collector()
    lexer = PlSqlLexer(InputStream(path.read_text(encoding="utf-8")))
    lexer.removeErrorListeners()
    lexer.addErrorListener(collector)
    parser = PlSqlParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(collector)
    parser.sql_script()
    assert collector.errors == [], f"{path.name}: " + "; ".join(collector.errors[:3])


def test_schema_ddl_covers_every_table_the_corpus_writes():
    ddl = (SRC / "schema.sql").read_text(encoding="utf-8").lower()
    # tables the corpus touches; a missing one blocks P0-3 (ScalarDB schema) and P0-5 (golden)
    for table in ["customers", "products", "orders", "order_lines", "payments",
                  "inventory_tx", "audit_log", "counters", "batch_control"]:
        assert f"create table {table}" in ddl, f"schema.sql has no DDL for {table}"


@pytest.mark.parametrize("category,units", sorted(CATEGORIES.items()))
def test_every_category_has_a_non_holdout_unit(category: str, units: list[str]):
    present = {p.stem for p in SRC.glob("*") if p.suffix in SUFFIXES}
    assert [u for u in units if u in present], f"category {category} has no unit outside holdout"


def test_holdout_is_at_least_twenty_percent_of_the_units():
    def units(directory: pathlib.Path) -> set[str]:
        return {p.stem for p in directory.glob("*") if p.suffix in SUFFIXES}

    main, held = units(SRC), units(SRC / "holdout")
    total = len(main) + len(held)
    assert held, "holdout must not be empty: the acceptance KPI is measured on it"
    assert len(held) / total >= 0.20, f"holdout is {len(held)}/{total} units, below the 20% floor"


def test_corpus_size_is_in_the_planned_range():
    """P0-1 asks for 20-30 program units; packages count once, not twice."""
    units = {p.stem for p in SRC.rglob("*") if p.suffix in SUFFIXES}
    assert 20 <= len(units) <= 30, f"corpus has {len(units)} units"
