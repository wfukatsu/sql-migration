"""#26 の続き: 記録された routine の MERGE を「読んでから UPDATE か INSERT を選ぶ」へ割る。

UPSERT にすると、既存行に当たったときに `WHEN MATCHED` が設定していない列まで上書きする。
#26 は converter を WARN のままにし、それが許容できない移行では読んでから選ぶ形にする、と決めた。
PL/SQL の移行はまさにそれである——Oracle と同じ行が残ることが目的だからである。
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from plsql.limits import RowLocks
from plsql.lower import _walk
from plsql.report import analyse as build_analysis

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
CONFIG = FIXTURES / "limits.yaml"


@pytest.fixture(scope="module")
def corpus():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json",
                          row_locks=RowLocks.load(CONFIG))


@pytest.fixture(scope="module")
def undecided():
    return build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")


def sql(analysis, routine_id):
    routine = next(r for _, r in analysis.routines() if r.id == routine_id)
    return [s for s in _walk(routine.body) if s.kind == "SqlOperation"]


def test_the_merge_is_classified_as_a_merge():
    """`USING (SELECT ...)` の SELECT を先に見つけて、MERGE を SELECT として扱っていた。"""
    from plsql.lower import lower_source
    from plsql.symbols import OracleSchema

    modules, _ = lower_source(SRC / "holdout" / "pkg_customer_import.pkb",
                              OracleSchema.from_ddl(SRC / "schema.sql"))
    routine = next(r for m in modules for r in m.routines if r.name == "import")
    assert [s.sql_kind for s in _walk(routine.body) if s.kind == "SqlOperation"] == ["MERGE"]


def test_the_recorded_merge_becomes_read_then_choose(corpus):
    statements = sql(corpus, "pkg_customer_import.import")
    assert [s.sql_kind for s in statements] == ["SELECT", "UPDATE", "INSERT"]
    assert all(s.target_status == "OK" for s in statements)


def test_the_update_sets_only_what_when_matched_set(corpus):
    """**これが要点である。** UPSERT は tier と registered_on も書き換えていた。"""
    update = next(s for s in sql(corpus, "pkg_customer_import.import") if s.sql_kind == "UPDATE")
    assert update.target_sql[0].startswith("UPDATE customers SET name = ")
    assert "tier" not in update.target_sql[0] and "registered_on" not in update.target_sql[0]


def test_the_source_values_replace_the_using_alias(corpus):
    """`s.customer_id` は USING が与えた式（`p_ids(i)`）に置き換わる。"""
    for statement in sql(corpus, "pkg_customer_import.import"):
        assert "s." not in statement.original_sql
        assert "p_ids(i)" in statement.original_sql


def test_the_concurrency_question_stays_attached(corpus):
    """割ると MERGE という語が消えて SEM-006 が外れる。**競合の問いは消えていない**ので、
    SEM-011 が同じ要求（concurrent_upsert）を持つ。"""
    from plsql.analysis import analyse as analyse_program
    from plsql.rules.engine import Evidence, RuleSet, decide

    decision = decide(corpus.program, analyse_program(corpus.program), RuleSet.load(),
                      Evidence())["pkg_customer_import.import"]
    assert decision.rule_verdict == "REVIEW"
    assert "concurrent_upsert" in decision.required_tests()


def test_without_a_record_the_merge_stays_an_upsert(undecided):
    """決めた人がいない routine は割らない。UPSERT と、上書きされる列を名指しする警告が残る。"""
    statements = sql(undecided, "pkg_customer_import.import")
    assert [s.sql_kind for s in statements] == ["MERGE"]
    assert statements[0].target_sql[0].startswith("UPSERT")


@pytest.mark.parametrize("merge", [
    # USING が表を読む。複数行になりうるので、1 回の選択では同じにならない
    "MERGE INTO customers c USING staging s ON (c.customer_id = s.customer_id) "
    "WHEN MATCHED THEN UPDATE SET c.name = s.name "
    "WHEN NOT MATCHED THEN INSERT (customer_id, name) VALUES (s.customer_id, s.name)",
    # WHEN MATCHED が書き込む先の列を読む。読んだ値に依存する別の話（#9 の RMW）
    "MERGE INTO customers c USING (SELECT 1 AS customer_id FROM dual) s ON (c.customer_id = s.customer_id) "
    "WHEN MATCHED THEN UPDATE SET c.credit_limit = c.credit_limit + 1 "
    "WHEN NOT MATCHED THEN INSERT (customer_id, name) VALUES (s.customer_id, 'x')",
])
def test_a_shape_it_cannot_split_is_left_alone(merge):
    from plsql.ir import model as M
    from plsql.merge import _split

    routine = M.Routine(id="p", kind="Routine", name="p")
    statement = M.SqlOperation(id="p#stmt-1", kind="SqlOperation", sql_kind="MERGE", original_sql=merge)
    assert _split(statement, routine) is None
