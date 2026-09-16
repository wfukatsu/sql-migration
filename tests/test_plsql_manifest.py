"""P0-2: the manifest describes the corpus, completely and accurately.

判定適合率（計画 §8）は、ルールエンジンの出す判定をこの manifest の `expected` と突き合わせて測る。
manifest が corpus からずれていると、その数値は何も意味しなくなる。ここで守るのはその一点である。

ルーチン名はソースから ANTLR パーサで取り出して突き合わせる。正規表現にしないのは、書式を変えただけで
検査が通らなくなる／黙って通ってしまうのを避けるため。
"""

from __future__ import annotations

import functools
import pathlib

import pytest
import yaml
from antlr4 import CommonTokenStream, InputStream

from plsql.grammar import PlSqlLexer, PlSqlParser

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"
SUFFIXES = {".pks", ".pkb", ".prc", ".trg"}
VERDICTS = {"AUTO", "REVIEW", "REDESIGN"}
ORIGINS = {"synthetic", "real-anonymized"}
CATEGORIES = {
    "simple-crud", "select-into-exception", "private-call", "type-rowtype", "cursor-loop",
    "bulk", "commit-in-routine", "dynamic-sql", "trigger-dblink", "datatype-edge", "concurrency-lock",
}

# ルーチンの「定義」を表す文脈。宣言だけの package 仕様 (.pks) は含まれない
DEFINITION_CONTEXTS = {
    "Procedure_bodyContext", "Function_bodyContext",
    "Create_procedure_bodyContext", "Create_function_bodyContext", "Create_triggerContext",
}


@functools.lru_cache(maxsize=None)
def manifest() -> dict:
    return yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def defined_routines(relative: str) -> tuple[str, ...]:
    """Routine names a file actually defines, lower-cased."""
    tree = PlSqlParser(CommonTokenStream(PlSqlLexer(
        InputStream((SRC / relative).read_text(encoding="utf-8"))))).sql_script()
    found: list[str] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if type(node).__name__ in DEFINITION_CONTEXTS:
            for accessor in ("identifier", "procedure_name", "function_name", "trigger_name"):
                getter = getattr(node, accessor, None)
                if getter is None:
                    continue
                try:
                    name = getter()
                except TypeError:
                    continue
                if name is not None:
                    found.append(name.getText().lower())
                    break
        for i in range(node.getChildCount()):
            child = node.getChild(i)
            if hasattr(child, "getChildCount"):
                stack.append(child)
    return tuple(sorted(set(found)))


def units() -> list[dict]:
    return manifest()["units"]


def corpus_files() -> set[str]:
    return {str(p.relative_to(SRC)) for p in SRC.rglob("*") if p.suffix in SUFFIXES}


def test_every_corpus_file_is_listed_exactly_once():
    listed: list[str] = []
    for unit in units():
        listed.extend(unit["files"])
    assert sorted(listed) == sorted(set(listed)), "a file is listed by more than one unit"
    assert set(listed) == corpus_files(), (
        f"unlisted: {sorted(corpus_files() - set(listed))} / missing from disk: {sorted(set(listed) - corpus_files())}")


@pytest.mark.parametrize("unit", units(), ids=lambda u: u["name"])
def test_unit_fields_are_valid(unit: dict):
    assert unit["origin"] in ORIGINS
    assert isinstance(unit["holdout"], bool)
    assert unit["categories"], "every unit carries at least one category"
    assert set(unit["categories"]) <= CATEGORIES, f"unknown category in {unit['name']}"
    for routine in unit["routines"]:
        assert routine["expected"] in VERDICTS, f"{unit['name']}.{routine['name']}: {routine['expected']}"
        assert routine["reason"].strip(), f"{unit['name']}.{routine['name']} has no reason"


@pytest.mark.parametrize("unit", units(), ids=lambda u: u["name"])
def test_holdout_flag_matches_the_directory(unit: dict):
    in_holdout_dir = all(f.startswith("holdout/") for f in unit["files"])
    assert unit["holdout"] == in_holdout_dir, f"{unit['name']}: holdout flag and location disagree"


@pytest.mark.parametrize("unit", units(), ids=lambda u: u["name"])
def test_manifest_routines_match_the_source(unit: dict):
    """Both directions: no invented routine, and no routine left undescribed."""
    defined: set[str] = set()
    for f in unit["files"]:
        defined |= set(defined_routines(f))
    described = {r["name"].lower() for r in unit["routines"]}
    assert described - defined == set(), f"{unit['name']}: manifest names routines the source does not define"
    assert defined - described == set(), f"{unit['name']}: source defines routines the manifest does not describe"


def test_every_category_has_a_non_holdout_unit():
    covered = {c for u in units() if not u["holdout"] for c in u["categories"]}
    assert CATEGORIES - covered == set(), f"categories with only holdout coverage: {sorted(CATEGORIES - covered)}"


def test_the_corpus_is_all_synthetic_for_now():
    """計画 §9 の決定。実案件コードを入れたら、この検査を出自別 KPI の検査に置き換える。"""
    assert {u["origin"] for u in units()} == {"synthetic"}


def test_all_three_verdicts_are_exercised_inside_and_outside_holdout():
    for holdout in (False, True):
        verdicts = {r["expected"] for u in units() if u["holdout"] is holdout for r in u["routines"]}
        assert verdicts >= {"REVIEW", "REDESIGN"}, f"holdout={holdout} exercises only {verdicts}"
    every = {r["expected"] for u in units() for r in u["routines"]}
    assert every == VERDICTS
