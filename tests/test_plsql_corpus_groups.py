"""The corpus manifest as groups: where a unit came from, and whether its expectations were written blind (#15)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from plsql import corpus

MANIFEST = Path("fixtures/plsql/manifest.yaml")


def write(tmp_path, units) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "corpus_root": "src", "units": units}), encoding="utf-8")
    return path


def unit(name="u", **changes):
    return {"name": name, "files": [f"{name}.prc"], "origin": "synthetic", "holdout": False,
            "routines": [{"name": name, "expected": "AUTO", "reason": "-"}], **changes}


def test_every_unit_of_the_corpus_has_an_origin_the_kpis_can_group_by():
    loaded = corpus.Corpus.load(MANIFEST)
    assert len(loaded.units) == 29 and {u.origin for u in loaded.units} <= set(corpus.ORIGINS)


def test_every_file_of_the_corpus_belongs_to_exactly_one_unit():
    loaded = corpus.Corpus.load(MANIFEST)
    names = [Path(f).name for u in loaded.units for f in u.files]
    assert len(names) == len(set(names)), "the IR knows a file by its name only: two units cannot share one"
    on_disk = {p.name for p in loaded.root.rglob("*") if p.suffix in (".pks", ".pkb", ".prc", ".fnc", ".trg")}
    assert on_disk == set(names), "a source file no unit claims would be measured and counted nowhere"


def test_a_holdout_somebody_opened_is_still_a_holdout_and_no_longer_independent():
    by_evidence = corpus.Corpus.load(MANIFEST).groups("evidence")
    assert all(any(f.startswith("holdout2/") for f in u.files) for u in by_evidence["independent-holdout"])
    assert all(any(f.startswith("holdout/") for f in u.files) for u in by_evidence["referenced-holdout"])
    assert len(by_evidence["independent-holdout"]) == 4 and len(by_evidence["referenced-holdout"]) == 5


def test_a_group_with_nothing_in_it_is_still_a_group():
    assert corpus.Corpus.load(MANIFEST).groups("origin")["real-anonymized"] == []


def test_real_code_is_grouped_apart(tmp_path):
    loaded = corpus.Corpus.load(write(tmp_path, [unit("a"), unit("b", origin="real-anonymized", holdout=True)]))
    assert [u.name for u in loaded.groups("origin")["real-anonymized"]] == ["b"]
    assert loaded.unit_of_file("somewhere/b.prc").name == "b" and loaded.unit_of_file("c.prc") is None
    assert loaded.units[1].evidence == "independent-holdout", "a holdout is independent until somebody says otherwise"


@pytest.mark.parametrize("changes, message", [
    ({"origin": "customer"}, "origin 'customer'"), ({"origin": None}, "origin None"),
    ({"holdout": False, "independent": True}, "independent but is not a holdout"),
])
def test_a_unit_the_kpis_could_not_place_is_refused(tmp_path, changes, message):
    with pytest.raises(corpus.ManifestError, match=message):
        corpus.Corpus.load(write(tmp_path, [unit("a", **changes)]))
