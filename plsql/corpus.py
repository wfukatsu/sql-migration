"""The corpus manifest, read as groups: where a unit came from, and whether its expectations were written blind.

`fixtures/plsql/manifest.yaml` has always said, per unit, `origin` (synthetic | real-anonymized) and `holdout`. Until
now nothing read them when the KPIs were computed, so one number stood for everything -- and the day real code is
added, a parse rate of 97% would not say whether the 3% is the customer's code or ours (#15).

Two splits, because they answer different questions:

* **origin** -- is this number about code somebody wrote for a business, or code written to exercise the tool?
  Every KPI on synthetic code is, at best, evidence that the tool is not broken.
* **evidence** -- were the expected verdicts written without looking at what the rules say? The rules and the
  expectations share an author, so agreement on the units the rules were developed against is self-scoring. A
  holdout is only independent until somebody opens it while changing a rule; `fixtures/plsql/README.md` records
  when that happened, and the manifest carries it as `independent: false`.

A group with nothing in it is reported as empty, not left out: "no real code has been measured" has to be
something the report says, not something a reader must notice is missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ORIGINS = ("synthetic", "real-anonymized")
# in the order a reader should trust them
EVIDENCE = ("independent-holdout", "referenced-holdout", "development")
EVIDENCE_LABELS = {
    "independent-holdout": "独立した holdout（期待判定を、ルールを見ずに先に決めた）",
    "referenced-holdout": "参照済みの holdout（ルール作成中に開いたので、独立した証拠ではない）",
    "development": "開発用（ルールと期待判定を同じ人が突き合わせながら書いた。自己採点）",
}
ORIGIN_LABELS = {
    "synthetic": "合成（ツールを試すために書いたコード）",
    "real-anonymized": "実案件（匿名化したもの）",
}


class ManifestError(ValueError):
    """The manifest says something the KPIs cannot be grouped by."""


@dataclass
class Unit:
    name: str
    files: list[str]
    origin: str
    holdout: bool
    independent: bool
    routines: list[str] = field(default_factory=list)

    @property
    def evidence(self) -> str:
        if not self.holdout:
            return "development"
        return "independent-holdout" if self.independent else "referenced-holdout"


@dataclass
class Corpus:
    units: list[Unit]
    root: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Corpus":
        path = Path(path)
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        units = []
        for raw in document.get("units") or []:
            origin = raw.get("origin")
            if origin not in ORIGINS:
                raise ManifestError(f"{path}: unit {raw.get('name')!r} has origin {origin!r}; it must be one of "
                                    f"{', '.join(ORIGINS)}. A unit of unknown origin cannot be counted anywhere")
            holdout = bool(raw.get("holdout", False))
            independent = bool(raw.get("independent", holdout))
            if independent and not holdout:
                raise ManifestError(f"{path}: unit {raw['name']!r} is marked independent but is not a holdout")
            units.append(Unit(name=raw["name"], files=list(raw.get("files") or []), origin=origin, holdout=holdout,
                              independent=independent,
                              routines=[r["name"].lower() for r in raw.get("routines") or []]))
        return cls(units, path.parent / document.get("corpus_root", "src"))

    def unit_of_file(self, file: str | None) -> Unit | None:
        """By file name: the IR knows a source file by its name only."""
        if not file:
            return None
        name = Path(file).name
        return next((u for u in self.units if any(Path(f).name == name for f in u.files)), None)

    def groups(self, by: str) -> dict[str, list[Unit]]:
        """Every group of the split, empty ones included."""
        keys = ORIGINS if by == "origin" else EVIDENCE
        out: dict[str, list[Unit]] = {key: [] for key in keys}
        for unit in self.units:
            out[unit.origin if by == "origin" else unit.evidence].append(unit)
        return out
