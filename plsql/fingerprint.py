"""What a piece of evidence was evidence *of*.

A comparison report says "this routine matched Oracle". It says nothing about which routine -- the source may
have been edited since, and the generator may have changed what it emits for the same source. Either way the
Java that ran is not the Java that would be generated now, and a verdict of AUTO resting on it is resting on
something else's test.

So the capture records two things, and the evidence only counts where both still hold:

* per routine, a hash of its PL/SQL source -- the lines its `source_range` covers
* one hash of the toolchain that turns that source into behaviour: the generator and everything feeding it, the
  SQL converter it calls, and the runtime helper the generated code runs on

The toolchain hash is deliberately coarse. A change to `expr.py` may leave most routines' output untouched, but
saying which ones would mean regenerating per variant at decision time; "nothing in the generator has changed
since this was measured" is cheap to check and cannot be wrong in the dangerous direction. Modules that only
report on results (review, kpi, report, cli, ...) are left out, or writing a report would invalidate the
evidence it reports on.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .ir import model as M

ROOT = Path(__file__).resolve().parent.parent
# modules that read results and produce none: they cannot change what a generated routine does
REPORTING = {"review.py", "kpi.py", "report.py", "cli.py", "remediate.py", "propose.py", "verify.py",
             "fingerprint.py", "corpus.py"}
TOOLCHAIN = (
    ("plsql", "*.py"), ("plsql/gen_java", "*.py"), ("plsql/ir", "*"), ("scalardb_migrate", "*.py"),
    ("runtime-java/src/main/java/com/scalar/migrate/plsql", "*.java"),
    ("runtime-java/src/main/java/com/scalar/migrate/appside", "*.java"),
)


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


def sources(program: M.Program, source_root: str | Path) -> dict[str, str]:
    """Routine id -> hash of the lines it was lowered from. Whitespace at line ends is not a change."""
    root = Path(source_root)
    lines: dict[str, list[str]] = {}
    out: dict[str, str] = {}
    for module in program.modules:
        for routine in module.routines:
            span = routine.source_range
            if span is None or not span.file:
                continue
            if span.file not in lines:
                found = root / span.file
                if not found.is_file():
                    found = next(iter(sorted(root.rglob(Path(span.file).name))), None)
                lines[span.file] = found.read_text(encoding="utf-8").splitlines() if found else []
            text = "\n".join(line.rstrip() for line in lines[span.file][span.start_line - 1:span.end_line])
            if text:
                out[routine.id] = _sha([text.encode()])
    return out


def of(program: M.Program, source_root: str | Path) -> dict:
    return {"toolchain": toolchain(), "sources": sources(program, source_root)}
