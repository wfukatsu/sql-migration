"""#52: dynamic SQL whose text is a constant takes the static path.

* `EXECUTE IMMEDIATE 'UPDATE ... RETURNING c INTO :n' USING x RETURNING INTO v` is the static UPDATE ... RETURNING,
  so the read-modify-write split (#9) and its RETURNING apply -- including RETURNING of a column the UPDATE does
  not write, which the read before the write already holds.
* `EXECUTE IMMEDIATE 'BEGIN :x := :x * 10; END;' USING IN OUT v` is a block the routine could have written in place.
  Dynamic PL/SQL binds by name: the placeholder written twice is one argument.
* `OPEN rc FOR 'SELECT ... :s' USING 15000` is the static `OPEN rc FOR SELECT` (#44).
* DDL in a routine is not run on the target; the generator refuses it with the reason.

Found with samples/oracle-samples b06_3_native_dynamic_sql.
"""

from __future__ import annotations

import pathlib

from plsql.limits import RowLocks
from plsql.lower import _walk
from plsql.report import analyse

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SCHEMA = FIXTURES / "src" / "schema.sql"
SCALARDB = FIXTURES / "scalardb-schema.json"


def _analyse(tmp_path, body: str, declare: str = "", decided: bool = False):
    (tmp_path / "p.prc").write_text(
        f"CREATE OR REPLACE PROCEDURE p(p_id NUMBER) IS\n{declare}\nBEGIN\n{body}\nEND;\n/\n", encoding="utf-8")
    row_locks = None
    if decided:
        limits = tmp_path / "limits.yaml"
        limits.write_text("rowLocks:\n  optimistic:\n    p: テストの決定\n", encoding="utf-8")
        row_locks = RowLocks.load(limits)
    analysis = analyse(tmp_path, SCHEMA, scalardb_schema=SCALARDB, row_locks=row_locks)
    routine = next(r for _, r in analysis.routines() if r.id == "p")
    return analysis, routine


def _service(analysis) -> str:
    from plsql.gen_java.service import generate_module

    module = next(m for m in analysis.program.modules if any(r.id == "p" for r in m.routines))
    return generate_module(module, "g.app", "g.infra", "g.domain", analysis.program).file.render()


RETURNING = ("EXECUTE IMMEDIATE 'UPDATE products SET stock_qty = stock_qty + :q WHERE product_id = :id "
             "RETURNING stock_qty, name INTO :s, :n' USING 5, p_id RETURNING INTO v_qty, v_name;")
DECLARE = "  v_qty NUMBER;\n  v_name VARCHAR2(200);"


def test_a_constant_dml_with_returning_becomes_the_static_statement(tmp_path):
    _, routine = _analyse(tmp_path, RETURNING, DECLARE)
    statement = routine.body[0]
    assert statement.kind == "SqlOperation" and statement.sql_kind == "UPDATE"
    assert statement.original_sql == ("UPDATE products SET stock_qty = stock_qty + 5 WHERE product_id = p_id "
                                      "RETURNING stock_qty, name INTO v_qty, v_name")
    codes = {d.code for d in statement.diagnostics}
    assert {"DYN_STATIC", "DYN_PRIVILEGE"} <= codes, "the statement must still say it was dynamic"
    assert routine.external_effects.dynamic_sql


def test_the_rmw_split_returns_a_column_it_does_not_write_from_its_read(tmp_path):
    _, routine = _analyse(tmp_path, RETURNING, DECLARE, decided=True)
    sql = [s for s in _walk(routine.body) if s.kind == "SqlOperation"]
    read, write = sql[0], sql[1]
    assert read.original_sql.startswith("SELECT stock_qty, name FROM products WHERE product_id = p_id")
    assert read.into_targets == ["v_rmw_1", "v_name"]
    assert "RETURNING" not in write.original_sql
    assigned = [s for s in _walk(routine.body) if s.kind == "Assignment"]
    assert [(a.target, a.expression) for a in assigned] == [("v_qty", "v_rmw_1 + 5")]


def test_undecided_the_rmw_stays_refused(tmp_path):
    """The split needs the rowLocks decision (#9); being dynamic does not change that."""
    from plsql.gen_java.repository import generate_module

    analysis, routine = _analyse(tmp_path, RETURNING, DECLARE)
    assert routine.body[0].kind == "SqlOperation", "still the static statement, refused the static way"
    module = next(m for m in analysis.program.modules if any(r.id == "p" for r in m.routines))
    repository = generate_module(module, "g.infra", "g.domain").file.render()
    assert "UnsupportedOperationException" in repository.split("pStmt1(")[1].split("\n    }")[0]


def test_a_constant_plsql_block_is_lowered_in_place_binding_by_name(tmp_path):
    body = "v_cnt := 1;\nEXECUTE IMMEDIATE 'BEGIN :x := :x * 10; END;' USING IN OUT v_cnt;"
    analysis, routine = _analyse(tmp_path, body, "  v_cnt NUMBER;")
    block = routine.body[1]
    assert block.kind == "Block" and "DYN_INLINED" in {d.code for d in block.diagnostics}
    assert [(s.target, s.expression) for s in block.body] == [("v_cnt", "v_cnt * 10")]
    assert "vCnt = Plsql.dec(Plsql.mul(vCnt, 10));" in _service(analysis)


def test_a_placeholder_bound_to_a_value_cannot_be_assigned(tmp_path):
    body = "EXECUTE IMMEDIATE 'BEGIN :x := 1; END;' USING 5;"
    _, routine = _analyse(tmp_path, body)
    assert routine.body[0].kind == "DynamicSql", "an assignment to a literal is not a block to inline"


def test_a_string_in_the_block_keeps_its_colon(tmp_path):
    body = "EXECUTE IMMEDIATE 'BEGIN :v := ''a:b''; END;' USING OUT v_text;"
    _, routine = _analyse(tmp_path, body, "  v_text VARCHAR2(10);")
    assert [(s.target, s.expression) for s in routine.body[0].body] == [("v_text", "'a:b'")]


def test_open_for_a_constant_string_is_the_static_open_for(tmp_path):
    body = ("OPEN rc FOR 'SELECT name FROM products WHERE unit_price > :s ORDER BY 1' USING 100;\n"
            "LOOP FETCH rc INTO v_name; EXIT WHEN rc%NOTFOUND; END LOOP;\nCLOSE rc;")
    _, routine = _analyse(tmp_path, body, "  rc SYS_REFCURSOR;\n  v_name VARCHAR2(200);")
    assert not [s for s in _walk(routine.body) if s.kind == "Unsupported"]
    # #44 turns the OPEN / FETCH loop into a loop over the query
    loop = next(s for s in _walk(routine.body) if s.kind == "Loop")
    assert loop.query.original_sql == "SELECT name FROM products WHERE unit_price > 100 ORDER BY 1"


def test_ddl_in_a_routine_is_refused_with_the_reason(tmp_path):
    body = "EXECUTE IMMEDIATE 'CREATE TABLE dyn_tmp (id NUMBER)';\nEXECUTE IMMEDIATE 'DROP TABLE dyn_tmp PURGE';"
    analysis, _ = _analyse(tmp_path, body)
    java = _service(analysis)
    assert "routine の中の DDL（CREATE）" in java
    assert "repository.pStmt2Variant1" not in java, "the DROP TABLE must not be emitted as a call"


def test_ddl_a_person_decided_to_omit_is_left_as_a_comment(tmp_path):
    from plsql.dynamic import set_omitted_ddl
    from plsql.gen_java.repository import generate_module
    from plsql.limits import DynamicDdl

    limits = tmp_path / "limits.yaml"
    limits.write_text("ddl:\n  omit:\n    p: 一時表を作ってすぐ消す\n", encoding="utf-8")
    decided = DynamicDdl.load(limits)
    assert decided.why("p") == "一時表を作ってすぐ消す"
    body = "EXECUTE IMMEDIATE 'CREATE TABLE dyn_tmp (id NUMBER)';\nEXECUTE IMMEDIATE 'DROP TABLE dyn_tmp PURGE';"
    analysis, _ = _analyse(tmp_path, body)
    set_omitted_ddl(decided.omit)
    try:
        java = _service(analysis)
    finally:
        set_omitted_ddl({})
    assert "UnsupportedOperationException" not in java
    assert "DROP: 移行先では実行しない（limits.yaml ddl.omit: 一時表を作ってすぐ消す）。元の文: DROP TABLE dyn_tmp PURGE" in java
    module = next(m for m in analysis.program.modules if any(r.id == "p" for r in m.routines))
    assert "DROP TABLE" not in generate_module(module, "g.infra", "g.domain").file.render()


def test_an_omission_without_a_reason_is_refused(tmp_path):
    import pytest

    from plsql.limits import DynamicDdl

    limits = tmp_path / "limits.yaml"
    limits.write_text("ddl:\n  omit:\n    p:\n", encoding="utf-8")
    with pytest.raises(ValueError):
        DynamicDdl.load(limits)
