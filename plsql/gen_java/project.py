"""P2-5..P2-7: write the generated sources out as a project tree.

The layout follows the plan's §9: application services, domain types, infrastructure repositories, and the
report beside them. Generated files go in one place that nobody edits by hand -- the plan's risk table says a
direct edit is how a project stops being regenerable, so keeping them apart is not tidiness, it is the mechanism.
"""

from __future__ import annotations

import json
import sys
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
    # 走査行数の上限が決められていない routine（#19）
    undecided_limits: list[str] = field(default_factory=list)
    unknown_names: list[str] = field(default_factory=list)
    error_codes: dict = field(default_factory=dict)
    # the program this was generated from, so a check made after the fact -- `verify`, which attributes a
    # javac error to a routine (#21) -- can get back from a Java method name to the PL/SQL it came from
    program: "M.Program | None" = None
    # ScalarDB SQL では走らない文の実行計画（P2-4 / P4-6）。**生成物と一緒に書き出す**——
    # repository はこれを classpath から読む。書き出していなかったので、計画を使う経路は
    # 実行時に「plan not found」で落ちていた（走査ループを計画で回すまで誰も通らなかった）
    plans: dict = field(default_factory=dict)
    # #12 §0 の決定 3: 直接の書き込みを権限で禁じる表（B / D 型の trigger が掛かる表）。namespace つき
    restricted: list[str] = field(default_factory=list)
    # A-3: 照合の控えの表を作る namespace（照合を生成したときだけ）
    baseline_namespace: str | None = None

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
             decisions: dict[str, Decision] | None = None,
             plans: dict | None = None, checks: list | None = None,
             types: dict | None = None, namespaces: dict | None = None) -> GeneratedProject:
    project = GeneratedProject(root=Path(root), base_package=base_package, program=program,
                               plans=dict(plans or {}))
    if checks:
        # #12 §0: 移行先の trigger を通らなかった書き込みを見つける照合（決定 1a / 2b / 4）
        from .checks import generate as generate_checks

        file = generate_checks(checks, project.app_package, project.domain_package, types or {})
        if file is not None:
            project.files.append(file)
        from .checks import generate_job

        job = generate_job(checks, project.app_package)
        if job is not None:
            project.files.append(job)
        namespace = next((n for n in (namespaces or {}).values() if n), None)
        project.baseline_namespace = namespace
        # 決定 3: B / D の表は直接の書き込みを権限で禁じる
        project.restricted = sorted({f"{(namespaces or {}).get(c.table) or ''}.{c.table}".lstrip(".")
                                     for c in checks if c.kind in ("B", "D")})

    exception_files, registry = generate_exceptions(program, project.domain_package)
    project.files.extend(exception_files)
    project.error_codes = registry.to_dict()

    # #12: trigger の本体は別の module にある。呼ぶ側がその routine を見られるようにする
    from .service import _PROGRAM

    _PROGRAM.set(program)
    for module in program.modules:
        project.files.extend(d.file for d in dtos_for(module, project.domain_package))
        service = generate_service(module, project.app_package, project.infra_package,
                                   project.domain_package, program)
        project.files.append(service.file)
        project.untranslated.extend(service.untranslated)
        project.unknown_names.extend(service.unknown_names)
        repository = generate_repository(module, project.infra_package, project.domain_package)
        project.files.append(repository.file)
        project.unsupported_sql.extend(repository.unsupported)
        project.planned_sql.extend(repository.planned)
        project.undecided_limits.extend(repository.undecided_limits)
    return project


def _is_regenerable(path: Path) -> bool:
    """Whether the file says the generator owns it. A handed-over file says the opposite ("now maintained by
    hand"), and a file with no banner was never ours."""
    from .emit import HEADER

    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().strip() == HEADER.splitlines()[0]
    except (OSError, UnicodeDecodeError):
        return False


def _remove_stale(source_root: Path, written: set[Path]) -> list[Path]:
    """Delete generated .java files this run did not produce, and any directory left empty.

    Only files that carry the generator's own "Do not edit" banner. It used to delete every other `.java` under
    the source root, so `--out-dir` pointed at a real project -- or at a tree after `--handover` -- lost its
    hand-written classes.
    """
    if not source_root.is_dir():
        return []
    removed = []
    for path in sorted(source_root.rglob("*.java")):
        if path.resolve() in written:
            continue
        if not _is_regenerable(path):
            print(f"  kept {path}: not written by this run and not marked as generated", file=sys.stderr)
            continue
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

    plans = source_root.parent / "resources" / "plans"
    if project.plans:
        plans.mkdir(parents=True, exist_ok=True)
    for statement_id, plan in sorted(project.plans.items()):
        path = plans / f"{statement_id}.plan.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        written.append(path)
    if plans.is_dir():
        # 前の実行が書いた計画は、今回の生成物のものではない。残すと classpath に古い計画が並ぶ
        for stale in sorted(plans.glob("*.plan.json")):
            if stale.resolve() not in {p.resolve() for p in written}:
                stale.unlink()

    if project.baseline_namespace is not None or any(f.name == "TriggerChecks" for f in project.files):
        ddl = project.root / "db" / "trigger-check-baseline.sql"
        ddl.parent.mkdir(parents=True, exist_ok=True)
        table = f"{project.baseline_namespace}.trigger_check_baseline" if project.baseline_namespace \
            else "trigger_check_baseline"
        ddl.write_text(
            "-- #12 §0 / A-3（2026-09-19）: 照合の控えの表。監査行が削除（prc_purge_audit）で消えても、\n"
            "-- 最後に監査した値をここに残す。残さないと、しばらく変わっていない行が照合から外れる。\n"
            "-- 移行で足す表であり、Oracle 側には無い。照合ジョブ（TriggerCheckJob.daily）が更新する。\n\n"
            f"CREATE TABLE IF NOT EXISTS {table} (\n"
            "  trigger_name TEXT,\n  key_value TEXT,\n  last_value TEXT,\n  last_at TIMESTAMP,\n"
            "  PRIMARY KEY (trigger_name, key_value)\n);\n", encoding="utf-8")
        written.append(ddl)

    if project.restricted:
        grants = project.root / "db" / "restrict-direct-writes.sql"
        grants.parent.mkdir(parents=True, exist_ok=True)
        grants.write_text(_restrict(project.restricted), encoding="utf-8")
        written.append(grants)

    report = project.root / "generation-report.json"
    payload = {"summary": project.summary(), "errorCodes": project.error_codes}
    if decisions is not None:
        payload["verdicts"] = {
            routine: {"verdict": decision.verdict, "ruleVerdict": decision.rule_verdict,
                      "confidence": round(decision.confidence.value, 4),
                      "factors": decision.confidence.as_dict(),
                      "reasons": decision.reasons}
            for routine, decision in sorted(decisions.items())}
    diagnostics = _diagnostic_codes(project.program)
    if diagnostics:
        payload["diagnostics"] = diagnostics
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    written.append(report)
    return written


def _diagnostic_codes(program: M.Program | None) -> dict[str, list[str]]:
    """routine ごとの診断コード（2026-09-19、PL/SQL 変換スキル）。

    `OPTIMISTIC` / `MERGE_SPLIT` / `TRIGGER_CALL` のような診断は、生成コードの外で決めること
    （`docs/plsql-migration/plsql-decisions-outside-generator.md` §0.1）の目印である。IR の中にしか無いと、生成物だけを
    見る人やスキルには、どの項目を確かめればよいかが分からない。
    """
    if program is None:
        return {}
    from ..lower import _walk

    out: dict[str, list[str]] = {}
    for module in program.modules:
        for routine in module.routines:
            codes = {d.code for d in routine.diagnostics}
            for statement in _walk(routine.body):
                codes.update(d.code for d in statement.diagnostics)
            for handler in routine.exception_handlers:
                for statement in _walk(handler.body):
                    codes.update(d.code for d in statement.diagnostics)
            if codes:
                out[routine.id] = sorted(codes)
    return dict(sorted(out.items()))


def _restrict(tables: list[str]) -> str:
    """#12 §0 の決定 3: B / D 型の trigger が掛かる表への、直接の書き込みを禁じる（雛形）。

    移行先では trigger が掛かるのは生成したコードが書くときだけで、他の経路から書かれると検証
    （拒否）を免れる。被害が残るのはこの型だけなので、ここだけ権限で塞ぐ。**誰に禁じるかは運用が
    決める**——`<other_user>` を、アプリ以外で書き込み権限を持つ利用者に置き換えて流す。
    """
    lines = ["-- #12 §0 の決定 3（2026-09-19）: B / D 型の trigger が掛かる表への直接の書き込みを禁じる。",
             "-- 移行先の trigger は生成したコードが書くときにしか掛からないので、他の経路から書かれると",
             "-- 検証（拒否）を免れ、不正な値がそのまま入る。<other_user> を、アプリ以外で書き込み権限を",
             "-- 持つ利用者に置き換えて流すこと。ScalarDB Cluster の認証・認可が有効である必要がある。",
             ""]
    for table in tables:
        lines.append(f"REVOKE INSERT, UPDATE, DELETE ON {table} FROM <other_user>;")
    return "\n".join(lines) + "\n"
