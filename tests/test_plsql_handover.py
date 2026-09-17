"""P4-9: the banner the generated code carries, and which documents describe the patterns it refuses.

After handover the regeneration model ends (plan §9), so "do not edit" stops being true. A banner that keeps
saying it tells maintainers not to do the thing they now have to do.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plsql.gen_java import emit, project
from plsql.generate import main as generate

SRC = "fixtures/plsql/src"
DOCS = Path("docs")


@pytest.fixture(autouse=True)
def restore_banner():
    yield
    project.regeneration_banner()


def header_of(root: Path) -> str:
    sample = next(root.rglob("*.java"))
    return "\n".join(sample.read_text(encoding="utf-8").splitlines()[:4])


def test_the_default_banner_says_not_to_edit(tmp_path):
    assert generate([SRC, "--out-dir", str(tmp_path), "--quiet"]) in (0, 1)
    assert "Do not edit" in header_of(tmp_path)
    assert "conversion rules" in header_of(tmp_path)


def test_the_handover_banner_does_not(tmp_path):
    assert generate([SRC, "--out-dir", str(tmp_path), "--handover", "--quiet"]) in (0, 1)
    header = header_of(tmp_path)
    assert "Do not edit" not in header
    assert "maintained by hand" in header


def test_the_handover_banner_keeps_what_stays_true(tmp_path):
    """Where the code came from: that is what makes it reviewable against the PL/SQL it replaced."""
    generate([SRC, "--out-dir", str(tmp_path), "--handover", "--quiet"])
    header = header_of(tmp_path)
    assert "Source:" in header
    assert "traceability.csv" in header
    assert re.search(r"\d{4}-\d{2}-\d{2}", header), "the handover date belongs in the file"


def test_a_run_carries_one_banner_not_two(tmp_path):
    generate([SRC, "--out-dir", str(tmp_path), "--handover", "--quiet"])
    banners = {"\n".join(p.read_text(encoding="utf-8").splitlines()[:1])
               for p in Path(tmp_path).rglob("*.java")}
    assert len(banners) == 1, banners


def test_the_banner_returns_to_the_default_afterwards(tmp_path):
    project.handover_banner("2026-01-01")
    project.regeneration_banner()
    assert emit._banner() is emit.HEADER


# ---------------------------------------------------------------- P4-5 / P4-6 / P4-8 の設計テンプレート
@pytest.mark.parametrize("name,must_mention", [
    ("plsql-cursor-patterns.md", ["行数の上限", "scan", "BULK COLLECT"]),
    ("plsql-transaction-patterns.md", ["再試行の責務", "DB-CORE-20013", "自律トランザクション"]),
    ("plsql-trigger-patterns.md", ["書込経路の網羅性", "USER", "WHEN"]),
])
def test_each_pattern_document_covers_what_its_rules_leave_to_a_person(name, must_mention):
    text = (DOCS / name).read_text(encoding="utf-8")
    for phrase in must_mention:
        assert phrase in text, f"{name} does not mention {phrase}"


def test_the_transaction_document_starts_from_what_was_measured():
    """Not from an assumption about Consensus Commit: P3-4 ran it and recorded the error it returned."""
    text = (DOCS / "plsql-transaction-patterns.md").read_text(encoding="utf-8")
    assert "P3-4" in text and "測定" in text


def test_the_trigger_document_puts_coverage_before_the_patterns():
    """A pattern applied without deciding coverage leaves the migration worse than the trigger was."""
    text = (DOCS / "plsql-trigger-patterns.md").read_text(encoding="utf-8")
    coverage = text.index("書込経路の網羅性")
    first_pattern = text.index("## A.")
    assert coverage < first_pattern
