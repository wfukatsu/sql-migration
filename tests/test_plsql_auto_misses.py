"""Review #27 (21-24): ways a routine reached AUTO without the rules ever seeing what it does.

The corpus is upper-case, single-package, has no overloads and parses cleanly, so none of these showed up there.
Each case is a small source of its own; what is asserted is the *rule* verdict, which is what AUTO rests on before
any evidence arrives.
"""

from __future__ import annotations

import pytest

from plsql.analysis import analyse as analyse_program
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
    these routines had no diagnostic at all -- while a routine whose trigger *was* applied is REDESIGN."""
    found, program = decisions(tmp_path, **{"trg.trg": TRIGGER.format(events=events),
                                            "p.prc": procedure("p", statement)})
    assert found["p"].rule_verdict == "REDESIGN" and "TRG-002" in rules_of(found["p"])


def test_a_write_the_trigger_does_not_fire_on_is_left_alone(tmp_path):
    found, _ = decisions(tmp_path, **{"trg.trg": TRIGGER.format(events="AFTER DELETE"),
                                      "p.prc": procedure("p", "UPDATE payments SET amount = 1 WHERE payment_id = p_id;")})
    assert "TRG-002" not in rules_of(found["p"])


def test_every_event_of_a_trigger_is_kept(tmp_path):
    _, program = decisions(tmp_path, **{"trg.trg": TRIGGER.format(events="BEFORE INSERT OR UPDATE OF amount, status")})
    module = next(m for m in program.modules if m.module_kind == "trigger")
    assert module.trigger_event == "INSERT OR UPDATE" and module.trigger_columns == ["amount", "status"]
