"""The last shapes samples/oracle-samples left over (2026-09-26): #51, #53, #54, #56, and the optional filter.

* #51 `FORALL … UPDATE … RETURNING c BULK COLLECT INTO v` and `SQL%BULK_ROWCOUNT(i)`
* #53 DBMS_SQL over a constant query -> a static cursor FOR loop
* #54 schema object types: a constructor in the select list, `TABLE(collection)`, PIPELINED
* #56 an INSTEAD OF trigger on a view: :NEW / :OLD typed from the view, a scalar subquery in SET read first
* `WHERE p IS NULL OR col = p` -> two queries
"""

from __future__ import annotations

import pathlib

from plsql.limits import RowLocks
from plsql.lower import _walk
from plsql.report import analyse

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SCALARDB = FIXTURES / "scalardb-schema.json"
EXTRA_DDL = """
CREATE OR REPLACE TYPE grade_t FORCE AS OBJECT (   -- a comment
  customer_id NUMBER,
  name        VARCHAR2(100),
  grade       VARCHAR2(1)
);
CREATE OR REPLACE TYPE grade_tab AS TABLE OF grade_t;
CREATE OR REPLACE VIEW order_customer_v AS
  SELECT o.order_id, o.status, c.name
  FROM   orders o JOIN customers c ON c.customer_id = o.customer_id;
"""


def _analyse(tmp_path, files: dict[str, str], decided: tuple[str, ...] = ()):
    schema = tmp_path / "schema.sql"
    schema.write_text((FIXTURES / "src" / "schema.sql").read_text(encoding="utf-8") + EXTRA_DDL, encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    for name, text in files.items():
        (src / name).write_text(text, encoding="utf-8")
    row_locks = None
    if decided:
        limits = tmp_path / "limits.yaml"
        limits.write_text("rowLocks:\n  optimistic:\n" + "".join(f"    {r}: テスト\n" for r in decided),
                          encoding="utf-8")
        row_locks = RowLocks.load(limits)
    return analyse(src, schema, scalardb_schema=SCALARDB, row_locks=row_locks)


def _routine(analysis, routine_id):
    return next(r for _, r in analysis.routines() if r.id == routine_id)


def _java(analysis, module_name: str) -> str:
    from plsql.gen_java.service import generate_module

    module = next(m for m in analysis.program.modules if m.name == module_name)
    return generate_module(module, "g.app", "g.infra", "g.domain", analysis.program).file.render()


def _repository(analysis, module_name: str) -> str:
    from plsql.gen_java.repository import generate_module

    module = next(m for m in analysis.program.modules if m.name == module_name)
    return generate_module(module, "g.infra", "g.domain").file.render()


def _proc(name: str, body: str, declare: str = "", params: str = "") -> str:
    return f"CREATE OR REPLACE PROCEDURE {name}{params} IS\n{declare}\nBEGIN\n{body}\nEND;\n/\n"


# --- #51 -------------------------------------------------------------------------------------------------

FORALL = _proc("bump", """
  FORALL i IN 1 .. v_ids.COUNT
    UPDATE orders SET total_amount = total_amount + 10 WHERE customer_id = v_ids(i)
    RETURNING total_amount BULK COLLECT INTO v_totals;
  FOR i IN 1 .. v_ids.COUNT LOOP
    DBMS_OUTPUT.PUT_LINE(SQL%BULK_ROWCOUNT(i));
  END LOOP;""", """  TYPE t_num IS TABLE OF NUMBER;
  v_ids    t_num := t_num(1, 2);
  v_totals t_num;""")


def test_forall_returning_bulk_collect_adds_each_written_row(tmp_path):
    analysis = _analyse(tmp_path, {"bump.prc": FORALL}, decided=("bump",))
    routine = _routine(analysis, "bump")
    assert routine.body[0].kind == "Assignment" and (routine.body[0].target, routine.body[0].expression) == \
        ("v_totals", "t_num()"), "the collection is emptied before the FORALL"
    statements = _walk(routine.body[1].body)
    assert [s.callee for s in statements if s.kind == "Call"] == ["v_totals.EXTEND"]
    assert any(s.kind == "Assignment" and s.target == "v_totals(v_totals.LAST)" for s in statements)
    assert all("RETURNING" not in (s.original_sql or "") for s in statements if s.kind == "SqlOperation")
    java = _java(analysis, "bump")
    assert "Plsql.extend(vTotals);" in java
    assert "bulkRowCount.add(Plsql.dec(rowCount));" in java and "Plsql.at(bulkRowCount, i)" in java


def test_forall_returning_undecided_stays_refused(tmp_path):
    analysis = _analyse(tmp_path, {"bump.prc": FORALL})
    assert "unresolved in Loop: forall loop" in _java(analysis, "bump")


# --- #53 -------------------------------------------------------------------------------------------------

DBMS_SQL = _proc("dump", """
  DBMS_SQL.PARSE(c, 'SELECT * FROM products WHERE ROWNUM <= 2', DBMS_SQL.NATIVE);
  DBMS_SQL.DESCRIBE_COLUMNS2(c, ncols, cols);
  FOR i IN 1 .. ncols LOOP DBMS_SQL.DEFINE_COLUMN(c, i, v_val, 4000); END LOOP;
  n := DBMS_SQL.EXECUTE(c);
  WHILE DBMS_SQL.FETCH_ROWS(c) > 0 LOOP
    FOR i IN 1 .. ncols LOOP
      DBMS_SQL.COLUMN_VALUE(c, i, v_val);
      DBMS_OUTPUT.PUT(cols(i).col_name || '=' || v_val);
    END LOOP;
    DBMS_OUTPUT.NEW_LINE;
  END LOOP;
  DBMS_SQL.CLOSE_CURSOR(c);""", """  c INTEGER := DBMS_SQL.OPEN_CURSOR;
  n INTEGER;
  cols DBMS_SQL.DESC_TAB2;
  ncols INTEGER;
  v_val VARCHAR2(4000);""")


def test_dbms_sql_over_a_constant_query_is_a_cursor_loop(tmp_path):
    analysis = _analyse(tmp_path, {"dump.prc": DBMS_SQL})
    java = _java(analysis, "dump")
    assert "DBMS_SQL" not in java.split("public void dump(")[1]
    assert "ncols = 5;" in java, "products has five columns"
    assert '"PRODUCT_ID"' in java and "Plsql.text(rSql1.productId())" in java
    assert "SELECT product_id, name, unit_price, stock_qty, discontinued FROM products" in _repository(analysis, "dump")


def test_dbms_sql_over_a_runtime_string_says_why_it_stays(tmp_path):
    analysis = _analyse(tmp_path, {"dump.prc": DBMS_SQL.replace(
        "'SELECT * FROM products WHERE ROWNUM <= 2'", "p_sql").replace("PROCEDURE dump IS", "PROCEDURE dump(p_sql VARCHAR2) IS")})
    routine = _routine(analysis, "dump")
    assert any(d.code == "DBMS_SQL_DYNAMIC" and "実行時に決まる" in d.message for d in routine.diagnostics)


# --- #54 -------------------------------------------------------------------------------------------------

def test_a_constructor_in_the_select_list_is_built_in_the_application(tmp_path):
    body = """
  SELECT grade_t(customer_id, name, 'X') BULK COLLECT INTO v_list FROM customers WHERE tier = 'GOLD';
  SELECT COUNT(*) INTO v_cnt FROM TABLE(v_list) WHERE name LIKE 'H%';"""
    analysis = _analyse(tmp_path, {"grades.prc": _proc("grades", body, "  v_list grade_tab;\n  v_cnt NUMBER;")})
    java = _java(analysis, "grades")
    assert "List<GradeT> vList" in java
    assert 'new GradeT(Plsql.dec(rObj1.customerId()), Plsql.text(rObj1.name()), "X")' in java
    assert "(GradeT) Plsql.at(vList," in java and "Plsql.like(vObj2.name(), \"H%\")" in java
    assert "UnsupportedOperationException" not in java
    from plsql.gen_java.dto import dtos_for

    module = next(m for m in analysis.program.modules if m.name == "grades")
    records = {d.file.name: d.file.render() for d in dtos_for(module, "g.domain")}
    assert "public record GradeT(BigDecimal customerId, String name, String grade)" in records["GradeT"]


def test_a_pipelined_function_returns_the_rows_it_piped(tmp_path):
    function = """CREATE OR REPLACE FUNCTION grades_of(p_tier VARCHAR2) RETURN grade_tab PIPELINED IS
BEGIN
  FOR r IN (SELECT customer_id, name FROM customers WHERE p_tier IS NULL OR tier = p_tier) LOOP
    PIPE ROW (grade_t(r.customer_id, r.name, 'A'));
  END LOOP;
  RETURN;
END;
/
"""
    analysis = _analyse(tmp_path, {"grades_of.fnc": function})
    java = _java(analysis, "grades_of")
    assert "public List<GradeT> gradesOf(String pTier)" in java
    assert "return vPiped;" in java and "Plsql.extend(vPiped);" in java
    # the optional filter: two queries, chosen by whether p_tier is NULL
    assert "if (Plsql.isNull(pTier))" in java
    repository = _repository(analysis, "grades_of")
    assert "FROM customers\"" in repository or 'FROM customers";' in repository
    assert "WHERE tier = :p_tier" in repository
    assert "UnsupportedOperationException" not in java + repository


# --- #56 -------------------------------------------------------------------------------------------------

TRIGGER = """CREATE OR REPLACE TRIGGER order_customer_v_trg
  INSTEAD OF UPDATE ON order_customer_v
  FOR EACH ROW
BEGIN
  UPDATE orders
  SET    status      = :NEW.status,
         customer_id = (SELECT customer_id FROM customers WHERE name = :NEW.name)
  WHERE  order_id    = :OLD.order_id;
END;
/
"""


def test_an_instead_of_trigger_on_a_view_compiles_on_its_own(tmp_path):
    analysis = _analyse(tmp_path, {"t.trg": TRIGGER})
    java = _java(analysis, "order_customer_v_trg")
    # :NEW / :OLD typed from the view's columns (NUMBER(19) is passed as a NUMBER, like any trigger row)
    assert "public void body(String newName, String newStatus, BigDecimal oldOrderId)" in java
    from plsql.gen_java.exception import collect

    too_many = collect(analysis.program).codes[-1427].class_name
    assert "catch (NoDataFoundException e)" in java and f"throw new {too_many}(" in java, \
        "a subquery with 0 rows is NULL, with 2 an error (ORA-01427)"
    repository = _repository(analysis, "order_customer_v_trg")
    assert "UnsupportedOperationException" not in repository
    assert "SELECT customer_id FROM customers WHERE name = " in repository


def test_a_correlated_subquery_in_set_is_left_alone(tmp_path):
    body = "UPDATE orders o SET total_amount = (SELECT MAX(unit_price) FROM order_lines l WHERE l.order_id = o.order_id) WHERE order_id = 1;"
    analysis = _analyse(tmp_path, {"p.prc": _proc("p", body)})
    routine = _routine(analysis, "p")
    assert [s.kind for s in routine.body] == ["SqlOperation"], "per row, not before: nothing is read first"


def test_view_columns_come_from_the_tables_they_select():
    from plsql.symbols import OracleSchema
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write((FIXTURES / "src" / "schema.sql").read_text(encoding="utf-8") + EXTRA_DDL)
    schema = OracleSchema.from_ddl(f.name)
    assert schema.columns("order_customer_v") == {"order_id": "NUMBER(19)", "status": "VARCHAR2(20)",
                                                  "name": "VARCHAR2(100)"}
    assert schema.object_types["grade_t"][1] == ("name", "VARCHAR2(100)")
    assert schema.table_types == {"grade_tab": "grade_t"}


def test_an_object_constructor_is_not_a_call_into_unanalysed_code(tmp_path):
    from plsql.analysis import analyse as analyse_program
    from plsql.rules.engine import Evidence, RuleSet, decide

    body = "SELECT grade_t(customer_id, name, 'X') BULK COLLECT INTO v_list FROM customers WHERE tier = 'GOLD';"
    analysis = _analyse(tmp_path, {"grades.prc": _proc("grades", body, "  v_list grade_tab;")})
    decision = decide(analysis.program, analyse_program(analysis.program), RuleSet.load(),
                      Evidence(captures={"grades": (1, 1)}))["grades"]
    assert "CALL-001" not in {m.rule.id for m in decision.matches}


def test_a_cursor_rowtype_is_the_cursor_select_list(tmp_path):
    body = "OPEN c;\n  FETCH c INTO r;\n  CLOSE c;"
    declare = "  CURSOR c IS SELECT o.order_id, c.name AS customer FROM orders o JOIN customers c ON c.customer_id = o.customer_id;\n  r c%ROWTYPE;"
    analysis = _analyse(tmp_path, {"p.prc": _proc("p", body, declare)})
    routine = _routine(analysis, "p")
    row = next(d for d in routine.declarations if d.name == "r")
    assert row.type.origin == "rowtype"
    assert row.type.resolved == "RECORD(order_id NUMBER(19), customer VARCHAR2(100))"
