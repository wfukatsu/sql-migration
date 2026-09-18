"""P2-5..P2-7: write the generated sources out as a project tree.

The layout follows the plan's §9: application services, domain types, infrastructure repositories, and the
report beside them. Generated files go in one place that nobody edits by hand -- the plan's risk table says a
direct edit is how a project stops being regenerable, so keeping them apart is not tidiness, it is the mechanism.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..ir import model as M
from ..rules.engine import Decision
from .dto import dtos_for
from .emit import JavaFile
from .exception import generate as generate_exceptions
from .repository import generate_module as generate_repository
from .service import generate_module as generate_service

SOURCE_ROOT = "src/main/java"


@dataclass
class GeneratedProject:
    root: Path
    base_package: str
    files: list[JavaFile] = field(default_factory=list)
    untranslated: list[str] = field(default_factory=list)
    unsupported_sql: list[str] = field(default_factory=list)
    planned_sql: list[str] = field(default_factory=list)
    unknown_names: list[str] = field(default_factory=list)
    error_codes: dict = field(default_factory=dict)
    # the program this was generated from, so a check made after the fact -- `verify`, which attributes a
    # javac error to a routine (#21) -- can get back from a Java method name to the PL/SQL it came from
    program: "M.Program | None" = None

    @property
    def app_package(self) -> str:
        return f"{self.base_package}.application"

    @property
    def domain_package(self) -> str:
        return f"{self.base_package}.domain"

    @property
    def infra_package(self) -> str:
        return f"{self.base_package}.infrastructure"

    def summary(self) -> dict:
        return {
            "files": len(self.files),
            "untranslatedStatements": len(self.untranslated),
            "unsupportedSql": len(self.unsupported_sql),
            "plannedSql": len(self.planned_sql),
            "unknownNames": sorted(set(self.unknown_names)),
        }


def handover_banner(date: str) -> None:
    """Switch every file generated from here on to the handover banner (P4-9).

    A module-level switch rather than a parameter threaded through every generator: the banner is a property of
    the run, and a tree carrying both would be telling two different stories about who maintains it.
    """
    from . import emit

    emit.set_banner(emit.HANDOVER_HEADER, date)


def regeneration_banner() -> None:
    """Back to the default: the tree is rewritten from the rules, so editing it by hand loses the edit."""
    from . import emit

    emit.set_banner(emit.HEADER)


def generate(program: M.Program, root: str | Path, base_package: str = "com.example.migrated",
             decisions: dict[str, Decision] | None = None) -> GeneratedProject:
    project = GeneratedProject(root=Path(root), base_package=base_package, program=program)

    exception_files, registry = generate_exceptions(program, project.domain_package)
    project.files.extend(exception_files)
    project.error_codes = registry.to_dict()

    for module in program.modules:
        project.files.extend(d.file for d in dtos_for(module, project.domain_package))
        service = generate_service(module, project.app_package, project.infra_package,
                                   project.domain_package)
        project.files.append(service.file)
        project.untranslated.extend(service.untranslated)
        project.unknown_names.extend(service.unknown_names)
        repository = generate_repository(module, project.infra_package, project.domain_package)
        project.files.append(repository.file)
        project.unsupported_sql.extend(repository.unsupported)
        project.planned_sql.extend(repository.planned)
    return project


def _remove_stale(source_root: Path, written: set[Path]) -> list[Path]:
    """Delete generated .java files this run did not produce, and any directory left empty."""
    if not source_root.is_dir():
        return []
    removed = []
    for path in sorted(source_root.rglob("*.java")):
        if path.resolve() not in written:
            path.unlink()
            removed.append(path)
    for directory in sorted(source_root.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return removed


def write(project: GeneratedProject, decisions: dict[str, Decision] | None = None) -> list[Path]:
    written: list[Path] = []
    source_root = project.root / SOURCE_ROOT
    for file in project.files:
        path = source_root / file.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file.render(), encoding="utf-8")
        written.append(path)

    # A file this run did not write is from an older one. Nothing edits `generated/` by hand, so leaving it
    # there can only mislead: a renamed class leaves its old file behind and javac compiles both, failing on
    # the stale one for a reason that has nothing to do with the current output (found in P4-5).
    _remove_stale(source_root, {p.resolve() for p in written})

    report = project.root / "generation-report.json"
    payload = {"summary": project.summary(), "errorCodes": project.error_codes}
    if decisions is not None:
        payload["verdicts"] = {
            routine: {"verdict": decision.verdict, "ruleVerdict": decision.rule_verdict,
                      "confidence": round(decision.confidence.value, 4),
                      "factors": decision.confidence.as_dict(),
                      "reasons": decision.reasons}
            for routine, decision in sorted(decisions.items())}
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    written.append(report)
    return written
