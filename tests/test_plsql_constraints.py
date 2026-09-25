"""#50: CHECK / FOREIGN KEY guards for tables the project decided to enforce (`constraints.enforce`).

ScalarDB has neither constraint, so the writer evaluates the CHECK on the values it writes and reads the parent
row of a FOREIGN KEY first, raising Oracle's own numbers (-2290 / -2291). Found with samples/oracle-samples
(2026-09-25): b06_2 stored 4 rows a CHECK had refused, b04_6_2 stored a row whose job did not exist.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from plsql import constraints as guards
from plsql.ir import model as M
from plsql.limits import Constraints
from plsql.lower import _walk
from plsql.report import analyse
from plsql.symbols import OracleSchema

DDL = textwrap.dedent("""
    CREATE TABLE jobs (job_id VARCHAR2(10) CONSTRAINT jobs_pk PRIMARY KEY, title VARCHAR2(30));
    CREATE TABLE employees (
      employee_id NUMBER(6) CONSTRAINT emp_pk PRIMARY KEY,
      last_name   VARCHAR2(25) NOT NULL,
      job_id      VARCHAR2(10) NOT NULL CONSTRAINT emp_job_fk REFERENCES jobs,
      salary      NUMBER(8,2) CONSTRAINT emp_salary_ck CHECK (salary > 0),
      manager_id  NUMBER(6) CONSTRAINT emp_mgr_fk REFERENCES employees,
      status      VARCHAR2(10) DEFAULT 'NEW',
      CONSTRAINT emp_status_ck CHECK (status IN ('NEW', 'ACTIVE'))
    );
    CREATE TABLE bulk_target (
      employee_id NUMBER(6) PRIMARY KEY,
      last_name   VARCHAR2(25),
      salary      NUMBER(8,2) CHECK (salary < 15000)
    );
""")


@pytest.fixture
def schema(tmp_path: pathlib.Path) -> OracleSchema:
    (tmp_path / "schema.sql").write_text(DDL, encoding="utf-8")
    return OracleSchema.from_ddl(tmp_path / "schema.sql")


ENFORCED = Constraints(enforce={"employees": "test", "bulk_target": "test"})


def program(*statements: M.Statement, declarations=(), parameters=()) -> tuple[M.Program, M.Routine]:
    routine = M.Routine(id="r", kind="Routine", name="r", body=list(statements),
                        declarations=[M.Declaration(id=f"r#d{n}", kind="Declaration", name=n) for n in declarations],
                        parameters=[M.Parameter(id=f"r#p{n}", kind="Parameter", name=n) for n in parameters])
    return M.Program(id="p", kind="Program", modules=[M.Module(id="m", kind="Module", name="m", module_kind="procedure",
                                                               routines=[routine])]), routine


def write(sql: str, kind: str = "INSERT") -> M.SqlOperation:
    return M.SqlOperation(id="r#stmt-1", kind="SqlOperation", sql_kind=kind, original_sql=sql)


def raises(statement: M.Statement) -> list[int]:
    return [s.error_code for s in _walk([statement]) if s.kind == "Raise"]


# ---------------------------------------------------------------- the DDL
def test_the_schema_records_checks_and_foreign_keys(schema):
    assert schema.checks["employees"] == [("emp_salary_ck", "salary > 0"), ("emp_status_ck", "status IN ('NEW', 'ACTIVE')")]
    assert schema.checks["bulk_target"] == [("bulk_target_check1", "salary < 15000")], "an unnamed CHECK gets a name"
    keys = {k.name: k for k in schema.foreign_keys["employees"]}
    assert keys["emp_job_fk"].parent == "jobs" and keys["emp_job_fk"].parent_columns == ("job_id",), \
        "REFERENCES jobs without columns is the parent's primary key"
    assert keys["emp_mgr_fk"].parent == "employees" and keys["emp_mgr_fk"].parent_columns == ("employee_id",), \
        "a self reference resolves to the table's own key"


# ---------------------------------------------------------------- guards
def test_an_insert_on_an_enforced_table_gets_a_check_and_a_parent_read(schema):
    prog, routine = program(write("INSERT INTO employees (employee_id, last_name, job_id, salary) "
                                  "VALUES (p_id, p_name, p_job, p_sal)"), parameters=("p_id", "p_name", "p_job", "p_sal"))
    guards.rewrite(prog, ENFORCED, schema)
    kinds = [(s.kind, getattr(s, "sql_kind", None)) for s in routine.body]
    assert kinds == [("If", None), ("If", None), ("SqlOperation", "SELECT"), ("If", None), ("SqlOperation", "INSERT")]
    salary_check, status_check, parent, fk_check, insert = routine.body
    assert salary_check.branches[0].condition == "NOT (p_sal > 0)" and raises(salary_check) == [-2290]
    assert status_check.branches[0].condition == "NOT (NULL IN ('NEW', 'ACTIVE'))", "an omitted column is NULL"
    assert parent.original_sql == "SELECT job_id FROM jobs WHERE job_id = p_job"
    assert parent.cardinality == "AT_MOST_ONE" and parent.not_found_flag == "fk3" and parent.into_targets == ["v_fk_1"]
    assert fk_check.branches[0].condition == "p_job IS NOT NULL AND fk3%NOTFOUND" and raises(fk_check) == [-2291]
    assert [d.name for d in routine.declarations[-1:]] == ["v_fk_1"]
    assert {d.code for d in insert.diagnostics} == {"CONSTRAINT_GUARD"}
    assert "manager_id" not in " ".join(s.original_sql for s in routine.body if s.kind == "SqlOperation"), \
        "a FOREIGN KEY on a column the INSERT leaves out (NULL) is not checked, as in Oracle"


def test_an_insert_without_a_column_list_uses_the_ddl_order(schema):
    prog, routine = program(write("INSERT INTO bulk_target VALUES (v_ids(i), v_names(i), v_sals(i))"),
                            declarations=("v_ids", "v_names", "v_sals"))
    guards.rewrite(prog, ENFORCED, schema)
    assert routine.body[0].branches[0].condition == "NOT (v_sals(i) < 15000)"


def test_a_null_literal_skips_the_foreign_key(schema):
    prog, routine = program(write("INSERT INTO employees (employee_id, last_name, job_id, manager_id) "
                                  "VALUES (1, 'X', 'IT', NULL)"))
    guards.rewrite(prog, ENFORCED, schema)
    reads = [s for s in routine.body if s.kind == "SqlOperation" and s.sql_kind == "SELECT"]
    assert [r.original_sql for r in reads] == ["SELECT job_id FROM jobs WHERE job_id = 'IT'"]


def test_an_update_guards_only_what_it_writes(schema):
    prog, routine = program(write("UPDATE employees SET salary = v_new WHERE employee_id = p_id", "UPDATE"),
                            declarations=("v_new",), parameters=("p_id",))
    guards.rewrite(prog, ENFORCED, schema)
    assert [s.kind for s in routine.body] == ["If", "SqlOperation"]
    assert routine.body[0].branches[0].condition == "NOT (v_new > 0)"
    update = routine.body[1]
    assert any(d.code == "CONSTRAINT_NOT_GUARDED" and "emp_status_ck" in d.message for d in update.diagnostics), \
        "a CHECK on a column this UPDATE does not write cannot be evaluated before the write, and says so"


def test_a_value_that_reads_the_stored_row_is_not_guessed(schema):
    """`SET salary = salary + 1` nobody split: the written value is not in hand."""
    prog, routine = program(write("UPDATE employees SET salary = salary + 1 WHERE employee_id = p_id", "UPDATE"),
                            parameters=("p_id",))
    guards.rewrite(prog, ENFORCED, schema)
    assert [s.kind for s in routine.body] == ["SqlOperation"]
    assert any(d.code == "CONSTRAINT_NOT_GUARDED" for d in routine.body[0].diagnostics)


def test_an_undecided_table_is_reported_and_left_alone(schema):
    prog, routine = program(write("INSERT INTO employees (employee_id, last_name, job_id) VALUES (1, 'X', 'IT')"))
    guards.rewrite(prog, Constraints(), schema)
    assert [s.kind for s in routine.body] == ["SqlOperation"]
    assert {d.code for d in routine.body[0].diagnostics} == {"CONSTRAINT_UNDECIDED"}


def test_guards_reach_nested_blocks(schema):
    inner = write("INSERT INTO bulk_target (employee_id, salary) VALUES (1, 20000)")
    prog, routine = program(M.If(id="r#if", kind="If", branches=[M.Branch(condition="TRUE", body=[inner])]))
    guards.rewrite(prog, ENFORCED, schema)
    assert [s.kind for s in routine.body[0].branches[0].body] == ["If", "SqlOperation"]


# ---------------------------------------------------------------- end to end: the bound exception meets the guard
SOURCE = """
CREATE OR REPLACE PROCEDURE p_hire(p_job VARCHAR2) IS
  e_fk_violation EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_fk_violation, -2291);
BEGIN
  INSERT INTO employees (employee_id, last_name, job_id, salary) VALUES (999, 'X', p_job, 100);
EXCEPTION
  WHEN e_fk_violation THEN
    NULL;
END;
/
"""


def test_the_generated_guard_raises_the_class_the_handler_catches(tmp_path):
    from plsql.gen_java.exception import class_of, collect
    from plsql.gen_java.service import generate_module

    (tmp_path / "schema.sql").write_text(DDL, encoding="utf-8")
    (tmp_path / "p_hire.prc").write_text(SOURCE, encoding="utf-8")
    analysis = analyse(str(tmp_path), tmp_path / "schema.sql", constraints=ENFORCED)
    module = analysis.program.modules[0]
    routine = module.routines[0]
    assert class_of("e_fk_violation", routine, module) == (-2291, "EFkViolationException")
    assert collect(analysis.program).codes[-2291].class_name == "EFkViolationException"
    java = generate_module(module, "g.app", "g.infra", "g.domain", program=analysis.program).file.render()
    assert "throw new EFkViolationException(" in java, java
    assert "catch (EFkViolationException e)" in java
    assert "MigratedException(-2290" in java, "the CHECK has no bound exception, so it is raised by number"
