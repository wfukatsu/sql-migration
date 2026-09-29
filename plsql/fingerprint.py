"""What a piece of evidence was evidence *of*.

A comparison report says "this routine matched Oracle". It says nothing about which routine -- the source may
have been edited since, and the generator may have changed what it emits for the same source. Either way the
Java that ran is not the Java that would be generated now, and a verdict of AUTO resting on it is resting on
something else's test.

So the capture records two things, and the evidence only counts where both still hold:

* per routine, a hash of the PL/SQL source its behaviour depends on: its own lines, the rest of its package (the
  spec, the package's variables, constants, types and initialisation), every routine it calls directly or not and
  their packages, the packages it only names (`pkg_b.t_qty`, `pkg_b.c_rate`: a SUBTYPE or a constant, #148), the
  triggers, and the project's inputs (the Oracle DDL, the ScalarDB schema, limits.yaml). Only
  the routine's own lines were hashed before, so `c_rate CONSTANT NUMBER := 0.08` changed to 0.10 left a
  routine that used it verified (#96). Editing a routine it does not call still leaves it alone
* one hash of the toolchain that turns that source into behaviour: the generator and everything feeding it (the
  parser, the rules the decisions come from), the SQL converter it calls, and the runtime the generated code runs
  on, with the build file that fixes the ScalarDB version

The toolchain hash is deliberately coarse. A change to `expr.py` may leave most routines' output untouched, but
saying which ones would mean regenerating per variant at decision time; "nothing in the generator has changed
since this was measured" is cheap to check and cannot be wrong in the dangerous direction. Modules that only
report on results (review, kpi, report, cli, ...) are left out, or writing a report would invalidate the
evidence it reports on.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .ir import model as M

ROOT = Path(__file__).resolve().parent.parent
# modules that read results and produce none: they cannot change what a generated routine does
REPORTING = {"review.py", "kpi.py", "report.py", "cli.py", "remediate.py", "propose.py", "verify.py",
             "fingerprint.py", "corpus.py"}
TOOLCHAIN = (
    ("plsql", "*.py"), ("plsql/gen_java", "*.py"), ("plsql/ir", "*"), ("scalardb_migrate", "*.py"),
    ("plsql/rules", "*"), ("plsql/grammar", "*.g4"), ("plsql/grammar/generated", "*.py"),
    ("runtime-java/src/main/java/com/scalar/migrate/plsql", "*.java"),
    ("runtime-java/src/main/java/com/scalar/migrate/appside", "*.java"),
    # PlanRunner / Residual / CoreFetcher: the generated repositories run a planned statement through them (#96)
    ("runtime-java/src/main/java/com/scalar/migrate/runtime", "*.java"),
    ("runtime-java", "build.gradle"),
)
# what the generated code is generated from besides the PL/SQL, next to the source directory or one level up
PROJECT_INPUTS = (("", "schema.sql"), ("..", "scalardb-schema.json"), ("..", "limits.yaml"), ("", "limits.yaml"))
# what Oracle's side of a comparison (difftest/plsql_run.py) ran on: the PL/SQL and the DDL deployed with it. The
# ScalarDB schema and the decisions shape only the generated code, so changing them leaves an Oracle capture valid
ORACLE_INPUTS = (("", "schema.sql"), ("", "type_bodies.sql"))


def _sha(parts: list[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))   # length-prefixed: ("ab", "c") is not ("a", "bc")
        digest.update(part)
    return digest.hexdigest()


def toolchain(root: Path = ROOT) -> str:
    parts: list[bytes] = []
    for directory, pattern in TOOLCHAIN:
        for path in sorted((root / directory).glob(pattern)):
            if path.is_file() and path.name not in REPORTING and "__pycache__" not in path.parts:
                parts += [str(path.relative_to(root)).encode(), path.read_bytes()]
    return _sha(parts)


def sources(program: M.Program, source_root: str | Path, inputs=PROJECT_INPUTS) -> dict[str, str]:
    """Routine id -> hash of the source its behaviour depends on (see the module docstring). Whitespace at line
    ends is not a change.

    A routine whose lines cannot be found, or whose file name matches two files under the root, is left out, and
    left-out routines are stale (`review._stale`). The IR keeps a file's name, not its directory: `a/x.prc` and
    `b/x.prc` both hashed the first one found, and a routine whose range fell outside it was never checked (#96).
    """
    from .analysis import build_call_graph

    root = Path(source_root)
    files = _Files(root)
    module_of = {r.id: m for m in program.modules for r in m.routines}
    own = {r.id: files.text(r.source_range) for m in program.modules for r in m.routines}
    shared: dict[str, str | None] = {}
    for module in program.modules:
        # the package's own lines: its range, less the routines in it. A spec and a body are two modules of one name
        rest = files.text(module.source_range, without=[r.source_range for r in module.routines])
        if rest is None and module.source_range is not None:
            shared[module.name] = None
        elif shared.get(module.name, "") is not None:
            # the specification beside the body (`pkg.pks` next to `pkg.pkb`): the analysis merges its declarations
            # into the body's module, whose range is the body file only, so its SUBTYPEs and constants were not
            # hashed (#148 H2). A package whose spec is its own module is hashed through that module as before
            spec = files.spec(module.source_range)
            shared[module.name] = (shared.get(module.name) or "") + "\n" + (rest or "") + (f"\n{spec}" if spec else "")
    triggers = [own[r.id] for m in program.modules if m.module_kind == "trigger" for r in m.routines]
    common = [_project_inputs(root, inputs)] + sorted(t or "" for t in triggers)
    graph = build_call_graph(program)
    packages = sorted({m.name for m in program.modules if m.module_kind != "trigger" and m.name}, key=len,
                      reverse=True)
    named = re.compile(r"(?<![\w$#.])(" + "|".join(re.escape(n) for n in packages) + r")\s*\.", re.IGNORECASE) \
        if packages else None
    canonical = {n.lower(): n for n in packages}
    out: dict[str, str] = {}
    for routine_id, text in own.items():
        if not text:
            continue
        home = module_of[routine_id].name
        parts = [text, shared.get(home, "")]
        for callee in sorted(graph.reachable_from(routine_id) - {routine_id}):
            # an external callee is outside the program; nothing to hash. A trigger is in `common` already, and the
            # edge to it exists only when the analysis was given the ScalarDB schema: the capture analyses without
            # one and `plsql.cli` with one, and 9 routines of samples/oracle-samples read as stale
            if callee in own and module_of[callee].module_kind != "trigger":
                parts += [callee, own[callee], shared.get(module_of[callee].name, "")]
        # #148 H2: a package the routine only names -- `v pkg_b.t_qty;`, `pkg_b.c_rate`, `pkg_b.e_busy` -- without
        # calling it. Its SUBTYPE, constant or type is part of what the routine does (NUMBER(3) narrowed to NUMBER(1)
        # changed the generated `fitInt`), and only called packages were hashed. Followed through what the added
        # specifications name in turn. Over-including only makes evidence stale more often, the safe direction
        included = {home.lower()} | {module_of[c].name.lower() for c in graph.reachable_from(routine_id) if c in own}
        pending = [p for p in parts if p]
        while named is not None and pending:
            for found in named.finditer(pending.pop()):
                name = canonical[found.group(1).lower()]
                if name.lower() in included:
                    continue
                included.add(name.lower())
                parts += [name, shared.get(name, "")]
                if shared.get(name):
                    pending.append(shared[name])
        if any(part is None for part in parts):
            continue   # a file it depends on is missing or ambiguous: stale rather than half-hashed
        out[routine_id] = _sha([part.encode() for part in parts + common])
    return out


class _Files:
    """The lines of each source file the IR names, found under the root once."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.lines: dict[str, list[str] | None] = {}
        self.paths: dict[str, Path | None] = {}

    def _lines(self, name: str) -> list[str] | None:
        if name not in self.lines:
            found = self.root / name
            if not found.is_file():
                matches = sorted(self.root.rglob(Path(name).name))
                found = matches[0] if len(matches) == 1 else None   # none, or ambiguous: not guessed
            self.paths[name] = found
            self.lines[name] = found.read_text(encoding="utf-8").splitlines() if found else None
        return self.lines[name]

    def spec(self, span) -> str | None:
        """The whole of the specification file beside the body `span` is in, when there is one."""
        if span is None or not span.file or Path(span.file).suffix.lower() not in (".pkb", ".bdy"):
            return None
        self._lines(span.file)
        body = self.paths.get(span.file)
        if body is None:
            return None
        found = next((p for p in sorted(body.parent.iterdir())
                      if p.stem == body.stem and p.suffix.lower() in (".pks", ".spc")), None)
        return "\n".join(line.rstrip() for line in found.read_text(encoding="utf-8").splitlines()) if found else None

    def text(self, span, without=()) -> str | None:
        if span is None or not span.file:
            return None
        lines = self._lines(span.file)
        if lines is None:
            return None
        skipped = {n for inner in without if inner is not None and inner.file == span.file
                   for n in range(inner.start_line, inner.end_line + 1)}
        return "\n".join(line.rstrip() for n, line in enumerate(lines[span.start_line - 1:span.end_line],
                                                                 start=span.start_line) if n not in skipped)


def _project_inputs(root: Path, inputs=PROJECT_INPUTS) -> str:
    parts = []
    for directory, name in inputs:
        path = (root / directory / name).resolve()
        if path.is_file():
            parts.append(f"{directory}/{name}\n{path.read_text(encoding='utf-8')}")
    return "\n".join(parts)


def of(program: M.Program, source_root: str | Path) -> dict:
    return {"toolchain": toolchain(), "sources": sources(program, source_root)}
