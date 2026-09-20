"""Anonymising real PL/SQL for the corpus (#15): what is replaced, what must stay, and the check that the tool
still concludes the same about the result."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from plsql import anonymize as A
from plsql.anonymize import check
from plsql.anonymize.__main__ import main

ROOT = Path(__file__).resolve().parent.parent
EXPLORER = ROOT / "fixtures" / "explorer" / "src"
CORPUS = ROOT / "fixtures" / "plsql" / "src"


def anonymise(text: str, mapping: A.Mapping | None = None) -> tuple[str, A.Mapping]:
    mapping = mapping or A.Mapping()
    return A.anonymize_text(text, mapping), mapping


# --- names -----------------------------------------------------------------------------------------------------

def test_a_name_becomes_the_same_nothing_everywhere_whatever_its_case():
    out, mapping = anonymise("SELECT Acme_Balance FROM acme_accounts a WHERE a.ACME_BALANCE > 0")
    assert "acme" not in out.lower()
    balance = mapping.names["ACME_BALANCE"]
    assert out.count(balance) == 2 and mapping.names["ACME_ACCOUNTS"] in out


def test_a_conventional_prefix_stays_because_it_is_the_authors_habit_not_the_business():
    out, _ = anonymise("p_customer_id := v_total; pkg_billing.run;")
    assert re.search(r"\bp_n\d{4} := v_n\d{4}; pkg_n\d{4}\.n\d{4};", out), out


def test_what_is_oracles_stays():
    text = ("BEGIN RAISE_APPLICATION_ERROR(-20001, 'x'); EXCEPTION WHEN NO_DATA_FOUND THEN "
            "v := NVL(order_seq.NEXTVAL, 0); DBMS_OUTPUT.PUT_LINE(SQLERRM); END;")
    out, mapping = anonymise(text)
    for name in ("RAISE_APPLICATION_ERROR", "NO_DATA_FOUND", "NVL", "NEXTVAL", "DBMS_OUTPUT", "PUT_LINE", "SQLERRM"):
        assert name in out, name
    assert "order_seq" not in out and set(mapping.names) == {"V", "ORDER_SEQ"}


def test_a_package_oracle_supplies_stays_with_its_member():
    """The first run over the corpus renamed DBMS_ASSERT.SIMPLE_SQL_NAME; the analysis then saw a call to a routine
    it did not know, and the check reported a rule that had not fired on the original."""
    out, _ = anonymise("EXECUTE IMMEDIATE 'TRUNCATE TABLE ' || DBMS_ASSERT.SIMPLE_SQL_NAME(p_table); UTL_FILE.FCLOSE(f);")
    assert "DBMS_ASSERT.SIMPLE_SQL_NAME" in out and "UTL_FILE.FCLOSE" in out and "p_table" not in out


def test_a_quoted_name_is_a_name():
    out, mapping = anonymise('SELECT "Acme Code" FROM t')
    assert "Acme" not in out and '"' + mapping.names["ACME CODE"].upper() + '"' in out


# --- literals, numbers, comments -------------------------------------------------------------------------------

def test_a_literal_becomes_a_dummy_of_its_length_and_equal_literals_stay_equal():
    out, mapping = anonymise("UPDATE o SET status = 'SHIPPED' WHERE status = 'SHIPPED' OR status = 'RECEIVED'")
    shipped, received = mapping.strings["SHIPPED"], mapping.strings["RECEIVED"]
    assert (len(shipped), len(received)) == (7, 8) and shipped != received
    assert out.count(f"'{shipped}'") == 2 and "SHIPPED" not in out and "RECEIVED" not in out


def test_a_format_mask_is_structure_and_stays():
    out, _ = anonymise("v := TO_CHAR(d, 'YYYY-MM-DD HH24:MI:SS') || TO_CHAR(n, 'FM999,990.00') || 'Tokyo'")
    assert "'YYYY-MM-DD HH24:MI:SS'" in out and "'FM999,990.00'" in out and "Tokyo" not in out


def test_a_literal_that_is_sql_is_anonymised_as_sql():
    out, mapping = anonymise("EXECUTE IMMEDIATE 'DELETE FROM acme_audit WHERE region = ''EAST''';")
    assert "acme_audit" not in out and "EAST" not in out
    assert f"'DELETE FROM {mapping.names['ACME_AUDIT']} WHERE" in out, "the keywords are what makes it analysable"


def test_numbers_that_are_structure_stay_and_the_rest_do_not():
    text = ("v_amount NUMBER(12, 2); v_name VARCHAR2(100); IF v_amount > 50000 THEN "
            "RAISE_APPLICATION_ERROR(-20017, 'x'); END IF; FOR i IN 1..10 LOOP NULL; END LOOP; v := 0.085;")
    out, mapping = anonymise(text)
    assert "NUMBER(12, 2)" in out and "VARCHAR2(100)" in out and "-20017" in out and "1..10" in out
    assert "50000" not in out and "0.085" not in out and set(mapping.numbers) == {"50000", "0.085"}


def test_comments_go_and_their_lines_stay():
    text = "-- Acme billing, J. Smith 2019\nBEGIN\n  /* call\n     the vendor */\n  NULL; -- fixme\nEND;\n"
    out, _ = anonymise(text)
    assert "Acme" not in out and "Smith" not in out and "vendor" not in out and "fixme" not in out
    assert out.count("\n") == text.count("\n") and out.split("\n")[4].strip() == "NULL;"


# --- a tree ------------------------------------------------------------------------------------------------------

def test_a_tree_is_anonymised_with_one_mapping_and_its_file_names_go_too(tmp_path):
    mapping, written = A.anonymize_tree(EXPLORER, tmp_path / "out")
    assert "schema.sql" in written and not any("order" in name or "shipping" in name for name in written)
    body = next(p for p in (tmp_path / "out").glob("*.pkb")).read_text(encoding="utf-8")
    assert mapping.names["SHIPMENTS"] in body, "the table named in schema.sql is the same table in the package"
    assert "shipments" not in body.lower()


def test_the_original_tree_is_never_the_place_to_write(tmp_path):
    with pytest.raises(A.AnonymizeError, match="untouched"):
        A.anonymize_tree(EXPLORER, EXPLORER / "anonymised")
    with pytest.raises(A.AnonymizeError, match="not a directory"):
        A.anonymize_tree(tmp_path / "nowhere", tmp_path / "out")


def test_the_mapping_is_never_written_inside_the_repository(tmp_path, capsys):
    mapping = A.Mapping()
    with pytest.raises(A.AnonymizeError, match="undoes the anonymisation"):
        A.write_mapping(mapping, ROOT / "out" / "mapping.json", ROOT)
    assert main([str(EXPLORER), "--out", str(tmp_path / "o"), "--mapping-out", str(ROOT / "mapping.json"),
                 "--no-check"]) == 2
    assert not (tmp_path / "o").exists(), "refused before anything is written"
    assert "inside the repository" in capsys.readouterr().err


def test_a_name_that_survives_is_pointed_at(tmp_path):
    mapping, _ = A.anonymize_tree(EXPLORER, tmp_path / "out")
    assert A.leaks(tmp_path / "out", mapping) == []
    victim = next((tmp_path / "out").glob("*.prc"))
    victim.write_text(victim.read_text(encoding="utf-8") + "\n-- see ORDER_ITEMS\nv := 'RECEIVED';\n", encoding="utf-8")
    found = {(item["kind"], item["original"]) for item in A.leaks(tmp_path / "out", mapping)}
    assert found == {("name", "ORDER_ITEMS"), ("string", "RECEIVED")}


# --- the tool concludes the same ------------------------------------------------------------------------------------

def test_the_explorer_fixture_is_analysed_the_same_after_anonymising(tmp_path, capsys):
    out = tmp_path / "out"
    assert main([str(EXPLORER), "--out", str(out), "--mapping-out", str(tmp_path / "secure" / "mapping.json")]) == 0
    said = capsys.readouterr().out
    assert "analysis unchanged" in said and "A person still has to read the result" in said
    mapping = json.loads((tmp_path / "secure" / "mapping.json").read_text(encoding="utf-8"))
    assert "CREATE_ORDER" in mapping["names"] and "RECEIVED" in mapping["strings"]


def test_no_rule_in_the_whole_corpus_depends_on_what_something_is_called(tmp_path):
    """Every name and literal of all 67 routines replaced, and the statements, the tables read and written, the
    locks, the diagnostics, the rules that fire and the verdicts stay what they were. If this fails after a rule is
    added, the rule is matching on a name -- and would not fire on real code that calls the thing something else."""
    mapping, _ = A.anonymize_tree(CORPUS, tmp_path / "out")
    assert check.compare(CORPUS, tmp_path / "out", mapping) == []


def test_replacing_a_name_that_is_oracles_is_what_the_check_is_for(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "prc_purge.prc").write_text(
        "CREATE OR REPLACE PROCEDURE prc_purge (p_table IN VARCHAR2) IS\nBEGIN\n"
        "  EXECUTE IMMEDIATE 'TRUNCATE TABLE ' || DBMS_ASSERT.SIMPLE_SQL_NAME(p_table);\nEND prc_purge;\n/\n",
        encoding="utf-8")
    monkeypatch.setattr(A, "_ORACLE_PACKAGE", re.compile(r"^\b$"))   # as if nobody had listed Oracle's packages
    assert main([str(src), "--out", str(tmp_path / "out")]) == 1
    said = capsys.readouterr().out
    assert "DIFFERS" in said and "CALL-001" in said and "--keep NAME" in said
    assert "prc_purge" not in said and "p_table" not in said, "the report names only the anonymised side"


def test_the_report_never_prints_an_original_name(tmp_path, capsys):
    main([str(EXPLORER), "--out", str(tmp_path / "out"), "--no-check"])
    said = capsys.readouterr().out.lower()
    assert "create_order" not in said and "shipments" not in said
