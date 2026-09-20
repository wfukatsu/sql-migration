"""The application's SQL files: where each statement starts, and what it touches -- without changing the converter."""

from __future__ import annotations

import pathlib

from plsql.explorer import appsql

ROOT = pathlib.Path(__file__).resolve().parent.parent
POINTS = ROOT / "samples" / "tutorial" / "sql" / "points.sql"


def write(tmp_path, text: str, name: str = "app.sql", encoding: str = "utf-8") -> pathlib.Path:
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return path


def test_every_statement_of_the_tutorial_script_starts_where_the_file_says():
    lines = POINTS.read_text(encoding="utf-8").split("\n")
    found = appsql.statements(POINTS)
    assert len(found) > 10
    for statement in found:
        assert lines[statement.line - 1].strip().startswith(statement.text.split("\n")[0].strip()[:20]), statement
        assert not lines[statement.line - 1].lstrip().startswith("--"), "a comment is not where a statement starts"


def test_comment_lines_and_blank_lines_above_a_statement_are_not_its_first_line(tmp_path):
    path = write(tmp_path, "-- header\n\n/* block\n   comment */\n\nSELECT 1 FROM orders;\n\n-- next\nDELETE FROM orders;\n")
    assert [(s.line, s.kind) for s in appsql.statements(path)] == [(6, "SELECT"), (9, "DELETE")]


def test_a_plsql_block_ended_by_a_slash_is_one_statement_and_the_next_one_is_not_shifted(tmp_path):
    path = write(tmp_path, "BEGIN\n  UPDATE orders SET status = 'X';\n  COMMIT;\nEND;\n/\nSELECT * FROM customers;\n")
    block, select = appsql.statements(path)
    assert (block.line, block.end_line, block.kind) == (1, 4, "PL/SQL")
    assert not block.visible and block.reason == appsql.PLSQL_INSIDE
    assert (select.line, select.reads) == (6, ["customers"])


def test_a_semicolon_inside_a_string_ends_nothing(tmp_path):
    path = write(tmp_path, "INSERT INTO notes (body) VALUES ('a;\nb');\nSELECT * FROM notes;\n")
    insert, select = appsql.statements(path)
    assert (insert.line, insert.end_line, insert.writes) == (1, 2, ["notes"])
    assert select.line == 3


def test_a_byte_order_mark_shifts_no_line(tmp_path):
    path = write(tmp_path, "SELECT 1 FROM orders;\nSELECT 2 FROM customers;\n", encoding="utf-8-sig")
    assert [s.line for s in appsql.statements(path)] == [1, 2]


def test_the_same_statement_twice_is_found_twice_in_order(tmp_path):
    path = write(tmp_path, "DELETE FROM staging;\nINSERT INTO staging SELECT * FROM orders;\nDELETE FROM staging;\n")
    assert [s.line for s in appsql.statements(path)] == [1, 2, 3]


def test_a_statement_nobody_can_parse_keeps_its_place_and_is_marked_not_visible(tmp_path):
    path = write(tmp_path, "SELECT 1 FROM orders;\nSELEC oops FROM FROM;\nSELECT 2 FROM customers;\n")
    broken = appsql.statements(path)[1]
    assert (broken.line, broken.visible, broken.reason) == (2, False, appsql.NOT_PARSED)
    assert broken.reads == [] and broken.writes == []


def test_reads_and_writes_are_told_apart_and_ddl_counts_as_neither(tmp_path):
    path = write(tmp_path, "CREATE TABLE t (a NUMBER);\nINSERT INTO archive SELECT * FROM orders o JOIN customers c ON 1=1;\n")
    ddl, insert = appsql.statements(path)
    assert (ddl.kind, ddl.reads, ddl.writes) == ("DDL", [], [])
    assert (insert.kind, insert.reads, insert.writes) == ("INSERT", ["orders", "customers"], ["archive"])


def test_a_cte_is_not_a_table(tmp_path):
    path = write(tmp_path, "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent r JOIN customers c ON 1=1;\n")
    assert sorted(appsql.statements(path)[0].reads) == ["customers", "orders"]


def test_two_files_of_the_same_name_in_different_directories_stay_two_files(tmp_path):
    for sub in ("billing", "orders"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "queries.sql").write_text("SELECT 1 FROM t;\n", encoding="utf-8")
    assert {s.file for s in appsql.collect([tmp_path])} == {"billing/queries.sql", "orders/queries.sql"}

