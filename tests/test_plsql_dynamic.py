"""P4-7: enumerating what a dynamic SQL statement can actually run.

`EXECUTE IMMEDIATE v_sql` hides the statement from every check the pipeline makes. When the string is built
from literals and a few branches the set is small and knowable, and enumerating it turns one opaque statement
into ordinary ones. What matters here is the boundary: claiming to know a statement that is decided at run time
would be worse than admitting the pipeline cannot see it.
"""

from __future__ import annotations

import pytest

from plsql.dynamic import MAX_VARIANTS, enumerate_variants
from plsql.ir import model as M
from plsql.lower import _walk
from plsql.report import analyse

SRC = "fixtures/plsql/src"
DDL = "fixtures/plsql/src/schema.sql"
SCALARDB = "fixtures/plsql/scalardb-schema.json"


@pytest.fixture(scope="module")
def corpus():
    return analyse(SRC, DDL, scalardb_schema=SCALARDB)


def dynamic(corpus, routine_id: str):
    for _, routine in corpus.routines():
        if routine.id != routine_id:
            continue
        for statement in _walk(routine.body):
            if statement.kind == "DynamicSql":
                return routine, statement
    raise AssertionError(routine_id)


# ---------------------------------------------------------------- what is knowable
def test_a_constant_statement_folds_to_one_variant(corpus):
    _, statement = dynamic(corpus, "pkg_dynamic_search.refresh_stats")
    assert len(statement.variants) == 1
    assert statement.variants[0]["guard"] == ""
    assert statement.constant_sql and "batch_control" in statement.constant_sql


def test_branches_enumerate_into_one_variant_each(corpus):
    """Three paths through `count_orders`: two conditions and the case where neither holds."""
    _, statement = dynamic(corpus, "pkg_dynamic_search.count_orders")
    assert len(statement.variants) == 3
    guards = {v["guard"] for v in statement.variants}
    assert "" in guards, "the path where no branch was taken is a variant too"
    assert any("ordered_at" in g for g in guards) and any("total_amount" in g for g in guards)


def test_each_variant_keeps_the_bind_placeholder(corpus):
    _, statement = dynamic(corpus, "pkg_dynamic_search.count_orders")
    assert all(":s" in v["sql"] for v in statement.variants)


def test_every_variant_is_converted_like_ordinary_sql(corpus):
    """Enumerating without converting would show a reader plain SQL that nothing had looked at."""
    for routine_id in ("pkg_dynamic_search.refresh_stats", "pkg_dynamic_search.count_orders"):
        _, statement = dynamic(corpus, routine_id)
        assert len(statement.variant_statements) == len(statement.variants)
        for operation in statement.variant_statements:
            assert operation.target_status in {"OK", "WARN", "PLANNED", "ERROR"}


# ---------------------------------------------------------------- what is not
def test_a_table_name_from_a_parameter_is_not_enumerated(corpus):
    """`'DELETE FROM ' || p_table_name` can be any table; saying otherwise would be a guess."""
    _, statement = dynamic(corpus, "pkg_dynamic_search.purge")
    assert statement.variants == []
    assert statement.variant_statements == []


def test_a_sanitised_identifier_is_still_not_enumerated(corpus):
    """`DBMS_ASSERT.SIMPLE_SQL_NAME` makes it safe, not knowable."""
    _, statement = dynamic(corpus, "pkg_customer_import.truncate_staging")
    assert statement.variants == []


def test_a_loop_makes_the_set_unbounded():
    """A loop can append any number of times, so the set is not finite from here."""
    routine = M.Routine(id="r", kind="Routine", name="r", body=[
        M.Assignment(id="r#stmt-1", kind="Assignment", target="v_sql", expression="'SELECT 1'"),
        M.Loop(id="r#stmt-2", kind="Loop", loop_kind="basic", body=[
            M.Assignment(id="r#stmt-3", kind="Assignment", target="v_sql",
                         expression="v_sql || ' OR 1=1'")]),
        M.DynamicSql(id="r#stmt-4", kind="DynamicSql", expression="v_sql"),
    ])
    assert enumerate_variants(routine, routine.body[-1]) is None


def test_too_many_branches_is_not_an_enumeration():
    """Past the limit the honest answer is that nobody knows what this runs."""
    body: list = [M.Assignment(id="r#stmt-0", kind="Assignment", target="v", expression="'SELECT 1'")]
    for i in range(MAX_VARIANTS):
        body.append(M.If(id=f"r#if-{i}", kind="If", branches=[
            M.Branch(condition=f"p = {i}", body=[
                M.Assignment(id=f"r#a-{i}", kind="Assignment", target="v",
                             expression=f"v || ' /*{i}*/'")])]))
    body.append(M.DynamicSql(id="r#exec", kind="DynamicSql", expression="v"))
    routine = M.Routine(id="r", kind="Routine", name="r", body=body)
    assert enumerate_variants(routine, body[-1]) is None


def test_a_value_that_becomes_unknown_stays_unknown():
    routine = M.Routine(id="r", kind="Routine", name="r", body=[
        M.Assignment(id="r#stmt-1", kind="Assignment", target="v", expression="'SELECT 1'"),
        M.Assignment(id="r#stmt-2", kind="Assignment", target="v", expression="p_whatever"),
        M.DynamicSql(id="r#stmt-3", kind="DynamicSql", expression="v"),
    ])
    assert enumerate_variants(routine, routine.body[-1]) is None


# ---------------------------------------------------------------- folding is not clearance
def test_folding_records_that_privileges_were_not_checked(corpus):
    """A reader who sees plain SQL will assume it was, unless it is said."""
    _, statement = dynamic(corpus, "pkg_dynamic_search.refresh_stats")
    codes = {d.code for d in statement.diagnostics}
    assert "DYN_FOLDED" in codes and "DYN_PRIVILEGE" in codes


def test_an_unenumerable_statement_carries_no_folding_claim(corpus):
    _, statement = dynamic(corpus, "pkg_dynamic_search.purge")
    assert "DYN_FOLDED" not in {d.code for d in statement.diagnostics}


def test_the_dynamic_sql_rule_still_applies(corpus):
    """Enumeration does not clear the statement; DYNAMIC_SQL stays on it."""
    for routine_id in ("pkg_dynamic_search.refresh_stats", "pkg_dynamic_search.count_orders"):
        _, statement = dynamic(corpus, routine_id)
        assert "DYNAMIC_SQL" in {d.code for d in statement.diagnostics}


# --- P4-7 の続き: 畳んだ variant を実際に生成する（2026-09-18） --------------------------------

def _corpus():
    import pathlib

    from plsql.report import analyse as build_analysis

    fixtures = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    return build_analysis(fixtures / "src", fixtures / "src" / "schema.sql",
                          scalardb_schema=fixtures / "scalardb-schema.json")


def _dynamic_statement(corpus, routine_id):
    from plsql.lower import _walk

    routine = next(r for _, r in corpus.routines() if r.id == routine_id)
    return routine, next(s for s in _walk(routine.body) if s.kind == "DynamicSql")


def test_into_and_using_are_read_off_the_statement():
    """`INTO v_count` を変数名として持つと、`INTO v_count` という名前の変数を探すことになる。
    `USING` は見てさえいなかった——**束縛する値が無ければ `:s` を渡せない**。"""
    corpus = _corpus()
    _, statement = _dynamic_statement(corpus, "pkg_dynamic_search.count_orders")
    assert statement.into_targets == ["v_count"]
    assert [b.plsql_variable for b in statement.using] == ["p_status"]


def test_the_using_values_land_on_the_placeholders():
    """Oracle は `USING` を**位置で**束縛する。placeholder の名前は呼び出し側と関係が無い。

    変数名に直しておくと、畳んだ文がそのあと**静的な文とまったく同じ道**を通る——列への帰属も
    型の変換も、書き分けずに済む。
    """
    corpus = _corpus()
    _, statement = _dynamic_statement(corpus, "pkg_dynamic_search.count_orders")
    for variant in statement.variant_statements:
        assert ":s" not in (variant.target_sql or [""])[0]
        assert [(b.plsql_variable, b.column) for b in variant.binds] == [("p_status", "status")]


def test_each_variant_becomes_a_method_the_service_can_call():
    """走りうる文が数えられるなら、その分だけ生成する。呼ぶ側は分岐だけを持つ。"""
    from plsql.gen_java.repository import generate_module as generate_repository
    from plsql.gen_java.service import generate_module as generate_service

    corpus = _corpus()
    module = next(m for m in corpus.program.modules if m.name == "pkg_dynamic_search")
    service = generate_service(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    repository = generate_repository(module, "g.infra", "g.domain").file.render()
    for index in (1, 2, 3):
        assert f"countOrdersStmt5Variant{index}(" in service
        assert f"countOrdersStmt5Variant{index}(" in repository, "service が呼ぶ method が repository に無い"
    assert "if (Plsql.eq(pSortColumn, \"ordered_at\"))" in service
    assert "else {" in service, "条件の付かない variant が else になっていない"


def test_a_statement_nobody_can_enumerate_is_still_refused():
    """表名が実行時に決まるものは**推測で 1 つに決めない**。allowlist / 専用 Repository は再設計である。"""
    from plsql.gen_java.service import generate_module as generate_service

    corpus = _corpus()
    module = next(m for m in corpus.program.modules if m.name == "pkg_dynamic_search")
    service = generate_service(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    body = service[service.index("public void purge("):]
    assert "UnsupportedOperationException" in body.split("\n    }")[0]


def test_the_folded_statement_still_says_it_was_dynamic():
    """畳んだ文を見た人が「静的な文と同じに検査された」と思わないようにする。`EXECUTE IMMEDIATE`
    は呼び出し側の権限で走る（`AUTHID`）。"""
    corpus = _corpus()
    _, statement = _dynamic_statement(corpus, "pkg_dynamic_search.refresh_stats")
    assert [d.code for d in statement.diagnostics if d.code == "DYNAMIC_SQL"]


# --- 許された表名（2026-09-19 の決定） ------------------------------------------------------------

def _with_allowed(allowed):
    from plsql.dynamic import set_allowed_tables

    set_allowed_tables(allowed)
    try:
        return _corpus()
    finally:
        set_allowed_tables({})


def test_without_a_list_the_table_name_stays_unknowable():
    """書いていない routine は数えない。数えられないのが正しい答えである。"""
    corpus = _corpus()
    _, statement = _dynamic_statement(corpus, "pkg_dynamic_search.purge")
    assert statement.variant_statements == []


def test_each_allowed_table_becomes_a_variant():
    corpus = _with_allowed({"pkg_dynamic_search.purge": ["inventory_tx"]})
    _, statement = _dynamic_statement(corpus, "pkg_dynamic_search.purge")
    assert [v["guard"] for v in statement.variants] == ["UPPER(p_table_name) = 'INVENTORY_TX'"]
    # created_at は鍵でないので走査になる（WARN）。拒否でなければよい
    assert statement.variant_statements[0].target_status in ("OK", "WARN")


def test_dbms_assert_does_not_stop_the_list_from_applying():
    """`DBMS_ASSERT.SIMPLE_SQL_NAME` は名前の形を確かめるだけで、どの表かは決めない。"""
    corpus = _with_allowed({"pkg_customer_import.truncate_staging": ["inventory_tx"]})
    _, statement = _dynamic_statement(corpus, "pkg_customer_import.truncate_staging")
    assert statement.variants[0]["sql"] == "TRUNCATE TABLE inventory_tx"


def test_a_name_not_on_the_list_is_refused_at_run_time():
    """どの variant にも当たらないとき、黙って何もしないのは最悪である。"""
    from plsql.gen_java.service import generate_module as generate_service

    corpus = _with_allowed({"pkg_dynamic_search.purge": ["inventory_tx"]})
    module = next(m for m in corpus.program.modules if m.name == "pkg_dynamic_search")
    java = generate_service(module, "g.app", "g.infra", "g.domain", corpus.program).file.render()
    body = java[java.index("public void purge("):].split("\n    }")[0]
    assert "IllegalArgumentException" in body and "dynamicTables" in body
