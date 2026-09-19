"""Review #27 (21-24): ways a routine reached AUTO without the rules ever seeing what it does.

The corpus is upper-case, single-package, has no overloads and parses cleanly, so none of these showed up there.
Each case is a small source of its own; what is asserted is the *rule* verdict, which is what AUTO rests on before
any evidence arrives.
"""

from __future__ import annotations

import pytest

from plsql.analysis import analyse as analyse_program
from plsql.lower import _walk
from plsql.report import analyse
from plsql.rules.engine import Evidence, RuleSet, decide

SCHEMA = "fixtures/plsql/src/schema.sql"


def decisions(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    analysis = analyse(str(tmp_path), SCHEMA)
    program = analysis.program
    perfect = Evidence(captures={r.id: (3, 3) for m in program.modules for r in m.routines})
    return decide(program, analyse_program(program), RuleSet.load(), perfect), program


def rules_of(decision) -> set[str]:
    return {m.rule.id for m in decision.matches}


def procedure(name: str, body: str, declare: str = "") -> str:
    return f"CREATE OR REPLACE PROCEDURE {name}(p_id NUMBER) IS\n{declare}\nBEGIN\n{body}\nEND;\n/\n"


# --- 21: calls into code nobody analysed --------------------------------------------------------------------
@pytest.mark.parametrize("body,callee", [
    ("billing_pkg.post_and_commit(p_id);", "billing_pkg.post_and_commit"),
    ("UTL_MAIL.SEND('a@example.com', 'b@example.com', NULL, NULL, 'subject', 'body');", "UTL_MAIL.SEND"),
    ("DBMS_LOCK.SLEEP(5);", "DBMS_LOCK.SLEEP"),
    ("IF billing_pkg.is_open(p_id) THEN NULL; END IF;", "billing_pkg.is_open"),
])
def test_a_call_that_resolves_to_nothing_in_the_program_is_not_auto(tmp_path, body, callee):
    found, _ = decisions(tmp_path, **{"p.prc": procedure("p", body)})
    assert found["p"].rule_verdict != "AUTO" and "CALL-001" in rules_of(found["p"])
    assert callee.lower() in " ".join(m.detail for m in found["p"].matches).lower()
    assert found["p"].confidence.symbol_resolution == 0.0


def test_console_output_and_collection_methods_are_not_external_calls(tmp_path):
    body = "DBMS_OUTPUT.PUT_LINE('x'); IF v_ids.COUNT > 0 AND v_ids.EXISTS(1) THEN NULL; END IF;"
    declare = "  TYPE t_ids IS TABLE OF NUMBER; v_ids t_ids := t_ids();"
    found, _ = decisions(tmp_path, **{"p.prc": procedure("p", body, declare)})
    assert "CALL-001" not in rules_of(found["p"])


# --- 22: expressions outside the four statement kinds ------------------------------------------------------
@pytest.mark.parametrize("declare,body,rule", [
    ("  v_x NUMBER := ROUND(p_id / 3, 2);", "NULL;", "SEM-001"),
    ("", "WHILE ROUND(p_id / 3, 2) > 1 LOOP EXIT; END LOOP;", "SEM-001"),
    ("", "LOOP EXIT WHEN ROUND(p_id / 3, 2) > 1; END LOOP;", "SEM-001"),
    ("  v_c INTEGER := DBMS_SQL.OPEN_CURSOR;", "NULL;", "DYN-003"),
    ("", "DBMS_SQL.PARSE(p_id, 'DELETE FROM audit_log', 1);", "DYN-003"),
])
def test_a_text_rule_sees_every_place_an_expression_is_evaluated(tmp_path, declare, body, rule):
    found, _ = decisions(tmp_path, **{"p.prc": procedure("p", body, declare)})
    assert rule in rules_of(found["p"]) and found["p"].rule_verdict != "AUTO"


# --- 23: names that are not unique -------------------------------------------------------------------------
PKG = """CREATE OR REPLACE PACKAGE BODY {name} AS
  PROCEDURE helper(p_id NUMBER) IS BEGIN {helper} END helper;
  PROCEDURE run(p_id NUMBER) IS BEGIN helper(p_id); END run;
END {name};
/
"""


def test_a_bare_call_means_the_callers_own_package_first(tmp_path):
    """`pkg_b.run` calls its own `helper`, which commits. It was linked to `pkg_a.helper`, which does not."""
    found, _ = decisions(tmp_path, **{
        "pkg_a.pkb": PKG.format(name="pkg_a", helper="NULL;"),
        "pkg_b.pkb": PKG.format(name="pkg_b", helper="UPDATE orders SET status = 'X' WHERE order_id = p_id; COMMIT;")})
    assert found["pkg_b.run"].rule_verdict == "REDESIGN"
    assert found["pkg_a.run"].rule_verdict == "AUTO"


def test_a_call_from_a_declaration_is_an_edge_of_the_call_graph(tmp_path):
    source = """CREATE OR REPLACE PACKAGE BODY pkg_c AS
  FUNCTION f_commits(p_id NUMBER) RETURN NUMBER IS BEGIN COMMIT; RETURN 1; END f_commits;
  PROCEDURE run(p_id NUMBER) IS
    v_n NUMBER := f_commits(p_id);
  BEGIN
    NULL;
  END run;
END pkg_c;
/
"""
    found, _ = decisions(tmp_path, **{"pkg_c.pkb": source})
    assert found["pkg_c.run"].rule_verdict == "REDESIGN"


def test_overloads_are_held_back_rather_than_decided_for_each_other(tmp_path):
    source = """CREATE OR REPLACE PACKAGE BODY pkg_o AS
  PROCEDURE put(p_id NUMBER) IS BEGIN UPDATE orders SET status = 'X' WHERE order_id = p_id; ROLLBACK; END put;
  PROCEDURE put(p_id NUMBER, p_note VARCHAR2) IS BEGIN NULL; END put;
  PROCEDURE other(p_id NUMBER) IS BEGIN NULL; END other;
END pkg_o;
/
"""
    found, program = decisions(tmp_path, **{"pkg_o.pkb": source})
    assert found["pkg_o.put"].rule_verdict == "REDESIGN", "the overload that rolls back decides, not the last one"
    assert "LOWER-001" in rules_of(found["pkg_o.put"])
    assert found["pkg_o.other"].rule_verdict == "AUTO", "only the overloads are held back"
    overloads = [r for m in program.modules for r in m.routines if r.name == "put"]
    assert len(overloads) == 2 and all(r.body[0].construct == "OverloadedRoutine" for r in overloads)


# --- 24: a unit that did not parse cleanly -------------------------------------------------------------------
def test_a_routine_lowered_from_a_recovered_parse_tree_is_not_auto(tmp_path):
    broken = procedure("p_broken", "v_total ::= compute_something(p_id) ?? 3;\n  NULL;")
    found, _ = decisions(tmp_path, **{"p_broken.prc": broken, "p_fine.prc": procedure("p_fine", "NULL;")})
    assert found["p_fine"].rule_verdict == "AUTO"
    if "p_broken" in found:   # the parser may give up on the unit entirely; then there is nothing to decide
        assert found["p_broken"].rule_verdict != "AUTO"
        assert found["p_broken"].confidence.rule_coverage == 0.0


# --- 25: a write to a table whose trigger nobody applied ------------------------------------------------------
TRIGGER = """CREATE OR REPLACE TRIGGER trg_payments_any
{events} ON payments
FOR EACH ROW
BEGIN
  NULL;
END;
/
"""


@pytest.mark.parametrize("events,statement", [
    ("AFTER INSERT OR UPDATE OR DELETE", "UPDATE payments SET amount = 1 WHERE payment_id = p_id;"),
    ("AFTER INSERT OR UPDATE OR DELETE", "DELETE FROM payments WHERE payment_id = p_id;"),
    ("AFTER DELETE", "DELETE FROM payments WHERE payment_id = p_id;"),
    ("AFTER UPDATE OR DELETE", "DELETE FROM payments WHERE payment_id = p_id;"),
])
def test_a_write_the_trigger_fires_on_is_never_silently_clean(tmp_path, events, statement):
    """Only the first event was kept (`INSERT OR UPDATE OR DELETE` -> INSERT) and DELETE writers were skipped, so
    these routines had no diagnostic at all -- while a routine whose trigger *was* applied is REDESIGN.

    Issue #29 (25): DELETE and multi-event triggers are applied now, so what makes the writer REDESIGN is the call
    to the trigger body (a trigger is REDESIGN, and the verdict travels along the call), not TRG-002."""
    found, program = decisions(tmp_path, **{"trg.trg": TRIGGER.format(events=events),
                                            "p.prc": procedure("p", statement)})
    routine = next(r for m in program.modules for r in m.routines if r.id == "p")
    codes = {d.code for s in _walk(routine.body) for d in s.diagnostics}
    assert "TRIGGER_CALL" in codes and "TRIGGER_NOT_APPLIED" not in codes
    assert found["p"].rule_verdict == "REDESIGN"


@pytest.mark.parametrize("body,statement", [
    ("NULL;", "MERGE INTO payments t USING (SELECT 1 AS id FROM dual) s ON (t.payment_id = s.id) "
              "WHEN MATCHED THEN UPDATE SET t.amount = 1;"),
    ("IF UPDATING('AMOUNT') THEN NULL; END IF;", "UPDATE payments SET amount = 1 WHERE payment_id = p_id;"),
    ("NULL;", "DELETE FROM payments WHERE amount > 1;"),                  # not one row
])
def test_a_shape_nobody_knows_how_to_apply_still_says_so(tmp_path, body, statement):
    trigger = TRIGGER.format(events="AFTER INSERT OR UPDATE OR DELETE").replace("  NULL;", "  " + body)
    found, _ = decisions(tmp_path, **{"trg.trg": trigger, "p.prc": procedure("p", statement)})
    assert found["p"].rule_verdict == "REDESIGN" and "TRG-002" in rules_of(found["p"])


def test_a_write_the_trigger_does_not_fire_on_is_left_alone(tmp_path):
    found, _ = decisions(tmp_path, **{"trg.trg": TRIGGER.format(events="AFTER DELETE"),
                                      "p.prc": procedure("p", "UPDATE payments SET amount = 1 WHERE payment_id = p_id;")})
    assert "TRG-002" not in rules_of(found["p"])


def test_every_event_of_a_trigger_is_kept(tmp_path):
    _, program = decisions(tmp_path, **{"trg.trg": TRIGGER.format(events="BEFORE INSERT OR UPDATE OF amount, status")})
    module = next(m for m in program.modules if m.module_kind == "trigger")
    assert module.trigger_event == "INSERT OR UPDATE" and module.trigger_columns == ["amount", "status"]


# --- review #27, 26a-26d -----------------------------------------------------------------------------------
SCALARDB = "fixtures/plsql/scalardb-schema.json"


def checked(tmp_path, **files):
    """`decisions`, with the ScalarDB capability check run: the rules below read what it leaves on the IR."""
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    program = analyse(str(tmp_path), SCHEMA, scalardb_schema=SCALARDB).program
    perfect = Evidence(captures={r.id: (3, 3) for m in program.modules for r in m.routines})
    return decide(program, analyse_program(program), RuleSet.load(), perfect)


def test_an_unresolved_type_in_a_nested_block_costs_confidence(tmp_path):
    """26a: only the routine's own declarations were counted."""
    body = "DECLARE v_x nowhere_pkg.t_thing%TYPE; BEGIN NULL; END;"
    found, _ = decisions(tmp_path, **{"p.prc": procedure("p", body)})
    assert found["p"].confidence.type_resolution == 0.0


def test_a_locking_cursor_declared_on_the_package_reaches_the_routine_that_uses_it(tmp_path):
    """26a: LOCK-002 looked at the routine's declarations, and the cursor was the package's."""
    package = ("CREATE OR REPLACE PACKAGE BODY pkg_l AS\n"
               "  CURSOR c_lock IS SELECT order_id FROM orders WHERE status = 'NEW' FOR UPDATE;\n"
               "  PROCEDURE uses IS\n  BEGIN\n    FOR r IN c_lock LOOP NULL; END LOOP;\n  END;\n"
               "  PROCEDURE ignores IS\n  BEGIN\n    NULL;\n  END;\n"
               "END pkg_l;\n/\n")
    found, _ = decisions(tmp_path, **{"pkg_l.pkb": package})
    assert "LOCK-002" in rules_of(found["pkg_l.uses"])
    assert "LOCK-002" not in rules_of(found["pkg_l.ignores"])


def test_a_variable_named_like_a_column_is_the_column(tmp_path):
    """26b: `WHERE status = status` is true for every row in Oracle; binding the right-hand side changed that."""
    source = ("CREATE OR REPLACE PROCEDURE p(status VARCHAR2, p_note VARCHAR2) IS\nBEGIN\n"
              "  UPDATE orders SET note = p_note WHERE status = status;\n"
              "  INSERT INTO audit_log (audit_id, action) VALUES (1, status);\nEND;\n/\n")
    (tmp_path / "p.prc").write_text(source, encoding="utf-8")
    program = analyse(str(tmp_path), SCHEMA, scalardb_schema=SCALARDB).program
    update, insert = [s for s in program.modules[0].routines[0].body if s.kind == "SqlOperation"]
    assert [b.plsql_variable for b in update.binds] == ["p_note"]
    assert any(d.code == "BIND_SHADOWED" for d in update.diagnostics)
    assert "status" in [b.plsql_variable for b in insert.binds], "a VALUES list has no columns in scope"
    assert "SQL-003" in rules_of(checked(tmp_path)["p"])


@pytest.mark.parametrize("body", [
    # through a callee: the write is the helper's
    "pkg_s.touch(p_id); SELECT COUNT(*) INTO v_n FROM orders WHERE status = 'NEW';",
    # the back edge: the second iteration scans what the first one wrote
    "FOR i IN 1..2 LOOP SELECT COUNT(*) INTO v_n FROM orders WHERE status = 'NEW'; "
    "UPDATE orders SET note = 'x' WHERE order_id = p_id; END LOOP;",
])
def test_a_scan_after_a_write_is_found_through_calls_and_loops(tmp_path, body):
    """26c."""
    package = ("CREATE OR REPLACE PACKAGE BODY pkg_s AS\n"
               "  PROCEDURE touch(p_id NUMBER) IS\n  BEGIN\n"
               "    UPDATE orders SET note = 'x' WHERE order_id = p_id;\n  END;\n"
               f"  PROCEDURE run(p_id NUMBER) IS\n    v_n NUMBER;\n  BEGIN\n    {body}\n  END;\n"
               "END pkg_s;\n/\n")
    assert "SCAN-001" in rules_of(checked(tmp_path, **{"pkg_s.pkb": package})["pkg_s.run"])


@pytest.mark.parametrize("declare,body", [
    # the write is a function's, and the function is called from an assignment, not from a Call statement
    ("", "v_n := pkg_f.bump(p_id); SELECT COUNT(*) INTO v_n FROM orders WHERE status = 'NEW';"),
    # ... from a condition
    ("", "IF pkg_f.bump(p_id) > 0 THEN SELECT COUNT(*) INTO v_n FROM orders WHERE status = 'NEW'; END IF;"),
    # ... from a declaration, which runs before the first statement
    ("    v_m NUMBER := pkg_f.bump(p_id);\n", "SELECT COUNT(*) INTO v_n FROM orders WHERE status = 'NEW';"),
    # ... two levels down
    ("", "v_n := pkg_f.outer_bump(p_id); SELECT COUNT(*) INTO v_n FROM orders WHERE status = 'NEW';"),
    # the back edge, where the write at the bottom of the loop is a callee's
    ("", "FOR i IN 1..2 LOOP SELECT COUNT(*) INTO v_n FROM orders WHERE status = 'NEW'; "
         "v_n := pkg_f.bump(p_id); END LOOP;"),
    # the scan is the function's, the write is the caller's
    ("", "UPDATE orders SET note = 'x' WHERE order_id = p_id; v_n := pkg_f.count_new;"),
])
def test_a_scan_after_a_write_is_found_through_a_function_in_an_expression(tmp_path, declare, body):
    """Issue #29 (26c): only Call statements were followed, so a function called from an expression hid its write."""
    package = ("CREATE OR REPLACE PACKAGE BODY pkg_f AS\n"
               "  FUNCTION bump(p_id NUMBER) RETURN NUMBER IS\n  BEGIN\n"
               "    UPDATE orders SET note = 'x' WHERE order_id = p_id;\n    RETURN 1;\n  END;\n"
               "  FUNCTION outer_bump(p_id NUMBER) RETURN NUMBER IS\n  BEGIN\n    RETURN bump(p_id) + 1;\n  END;\n"
               "  FUNCTION count_new RETURN NUMBER IS\n    v_c NUMBER;\n  BEGIN\n"
               "    SELECT COUNT(*) INTO v_c FROM orders WHERE status = 'NEW';\n    RETURN v_c;\n  END;\n"
               f"  PROCEDURE run(p_id NUMBER) IS\n    v_n NUMBER;\n{declare}  BEGIN\n    {body}\n  END;\n"
               "END pkg_f;\n/\n")
    assert "SCAN-001" in rules_of(checked(tmp_path, **{"pkg_f.pkb": package})["pkg_f.run"])


def test_a_function_that_only_reads_by_key_does_not_make_a_scan_after_write(tmp_path):
    package = ("CREATE OR REPLACE PACKAGE BODY pkg_g AS\n"
               "  FUNCTION note_of(p_id NUMBER) RETURN VARCHAR2 IS\n    v_s VARCHAR2(100);\n  BEGIN\n"
               "    SELECT note INTO v_s FROM orders WHERE order_id = p_id;\n    RETURN v_s;\n  END;\n"
               "  PROCEDURE run(p_id NUMBER) IS\n    v_s VARCHAR2(100);\n  BEGIN\n"
               "    UPDATE orders SET note = 'x' WHERE order_id = p_id;\n    v_s := note_of(p_id);\n  END;\n"
               "END pkg_g;\n/\n")
    assert "SCAN-001" not in rules_of(checked(tmp_path, **{"pkg_g.pkb": package})["pkg_g.run"])


def test_a_scan_in_a_handler_after_the_body_wrote_is_found(tmp_path):
    source = ("CREATE OR REPLACE PROCEDURE p(p_id NUMBER) IS\n  v_n NUMBER;\nBEGIN\n"
              "  UPDATE orders SET note = 'x' WHERE order_id = p_id;\nEXCEPTION\n  WHEN NO_DATA_FOUND THEN\n"
              "    SELECT COUNT(*) INTO v_n FROM orders WHERE status = 'NEW';\nEND;\n/\n")
    assert "SCAN-001" in rules_of(checked(tmp_path, **{"p.prc": source})["p"])


def test_a_cursor_for_loop_that_updates_its_own_table_is_not_a_scan_after_write(tmp_path):
    """The loop's query opens once, before the body writes anything."""
    body = ("FOR r IN (SELECT order_id FROM orders WHERE status = 'NEW') LOOP "
            "UPDATE orders SET note = 'x' WHERE order_id = r.order_id; END LOOP;")
    assert "SCAN-001" not in rules_of(checked(tmp_path, **{"p.prc": procedure("p", body)})["p"])


def test_dynamic_sql_built_in_a_variable_is_read_through_the_variable(tmp_path):
    """26c: `EXECUTE IMMEDIATE v_sql` showed the rules neither the spliced table name nor the COMMIT."""
    splice = procedure("p", "v_sql := 'DELETE FROM ' || v_table; EXECUTE IMMEDIATE v_sql;",
                       "  v_sql VARCHAR2(200); v_table VARCHAR2(30) := 'orders';")
    commit = procedure("q", "v_sql := 'BEGIN UPDATE orders SET note = NULL; COMMIT; END;'; EXECUTE IMMEDIATE v_sql;",
                       "  v_sql VARCHAR2(200);")
    found, _ = decisions(tmp_path, **{"p.prc": splice, "q.prc": commit})
    assert "DYN-001" in rules_of(found["p"]) and found["p"].rule_verdict == "REDESIGN"
    assert "TX-001" in rules_of(found["q"]) and found["q"].rule_verdict == "REDESIGN"


def test_bodies_are_found_whatever_the_suffix_looks_like(tmp_path):
    """26d: `PKG.PKB` and `proc.sql` were parsed for KPI-1 and then never analysed."""
    from plsql.report import source_bodies

    (tmp_path / "A.PRC").write_text(procedure("a", "NULL;"), encoding="utf-8")
    (tmp_path / "b.sql").write_text(procedure("b", "NULL;"), encoding="utf-8")
    (tmp_path / "c.pls").write_text(procedure("c", "NULL;"), encoding="utf-8")
    (tmp_path / "schema.sql").write_text("CREATE TABLE t (id NUMBER PRIMARY KEY);\n", encoding="utf-8")
    (tmp_path / "data.sql").write_text("INSERT INTO t VALUES (1);\n", encoding="utf-8")
    assert [p.name for p in source_bodies(tmp_path, tmp_path / "schema.sql")] == ["A.PRC", "b.sql", "c.pls"]
    program = analyse(str(tmp_path), str(tmp_path / "schema.sql")).program
    assert sorted(m.name for m in program.modules) == ["a", "b", "c"]


def test_swallowing_others_and_an_untracked_rowcount_are_review(tmp_path):
    swallow = procedure("p", "NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    NULL;")
    count = procedure("q", "EXECUTE IMMEDIATE 'DELETE FROM orders'; v_n := SQL%ROWCOUNT;", "  v_n NUMBER;")
    plain = procedure("r", "UPDATE orders SET note = 'x' WHERE order_id = p_id; v_n := SQL%ROWCOUNT;", "  v_n NUMBER;")
    found, _ = decisions(tmp_path, **{"p.prc": swallow, "q.prc": count, "r.prc": plain})
    assert "EXC-002" in rules_of(found["p"]) and found["p"].rule_verdict == "REVIEW"
    assert "SQL-004" in rules_of(found["q"])
    assert "SQL-004" not in rules_of(found["r"]), "a static UPDATE is what the generated rowCount follows"


def test_update_of_columns_end_at_the_next_event(tmp_path):
    """Issue #29 (25): `UPDATE OF amount OR DELETE ON payments` was read as the column `amount or delete`, so an
    update of amount did not fire the trigger and the routine was clean."""
    trigger = TRIGGER.format(events="AFTER INSERT OR UPDATE OF amount, method OR DELETE")
    found, program = decisions(tmp_path, **{"trg.trg": trigger,
                                            "p.prc": procedure("p", "UPDATE payments SET amount = 1 WHERE payment_id = p_id;")})
    module = next(m for m in program.modules if m.module_kind == "trigger")
    assert module.trigger_columns == ["amount", "method"] and module.trigger_event == "INSERT OR UPDATE OR DELETE"
    assert found["p"].rule_verdict == "REDESIGN"
