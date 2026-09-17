"""#21: does the generated Java compile?

`AUTO` means "this routine may be generated unattended". The generator decides that from the IR -- every
statement translated, every SQL statement accepted by ScalarDB -- and then hands the answer over without ever
checking the one thing a compiler checks: that the names the method body reads are the names its signature
provides. Two defects of exactly that shape reached review (MR !53) and both were reported as `AUTO` with
`untranslated statements 0`, because the gate had no way to see them.

Type checking lives outside this codebase, in `runtime-java`'s Gradle build, which is the only thing that
knows the classpath the generated code compiles against. So the check is a subprocess, and the work here is
the part a subprocess cannot do: point each javac error at the routine it came from, so the answer is
"`pkg_x.y` does not compile" rather than a file and a line number nobody can act on.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRADLE_PROJECT = ROOT / "runtime-java"

# javac's own format, which Gradle passes through unchanged: `/path/File.java:12: error: cannot find symbol`
ERROR = re.compile(r"^(?P<file>.+\.java):(?P<line>\d+):\s*(?:error|エラー):\s*(?P<message>.*)$")
# a method of the generated class: one indent level in, and a name followed by its parameter list
METHOD = re.compile(r"^ {4}(?:public|private|protected)\s+[\w<>\[\], .]+?\s+(?P<name>\w+)\s*\(")


@dataclass
class CompileError:
    file: str
    line: int
    message: str
    routine: str | None = None

    def __str__(self) -> str:
        where = self.routine or f"{self.file}:{self.line}"
        return f"{where}: {self.message}"


@dataclass
class CompileReport:
    ran: bool
    errors: list[CompileError] = field(default_factory=list)
    unavailable: str | None = None      # why the check could not run, when it could not
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.ran and not self.errors

    def routines(self) -> list[str]:
        return sorted({e.routine for e in self.errors if e.routine})


def verify(out_dir: str | Path, project: "GeneratedProject | None" = None,
           gradle_project: Path = GRADLE_PROJECT, timeout: int = 900) -> CompileReport:
    """Compile the tree just written, and attribute what javac says to the routines it came from.

    Not being able to run is not a pass. A missing Gradle or JVM comes back as `unavailable`, and the caller
    that asked for the check fails on it -- an unverifiable answer reported as a verified one is the failure
    mode this whole check exists to remove.
    """
    gradle = shutil.which("gradle")
    if gradle is None:
        return CompileReport(ran=False, unavailable="gradle is not on PATH")
    if not gradle_project.is_dir():
        return CompileReport(ran=False, unavailable=f"no Gradle project at {gradle_project}")
    command = [gradle, "compileJava", "--console=plain", "-q",
               f"-Pplsql.generatedDir={Path(out_dir).resolve()}", "--rerun-tasks"]
    try:
        finished = subprocess.run(command, cwd=str(gradle_project), capture_output=True, text=True,
                                  timeout=timeout)
    except (OSError, subprocess.SubprocessError) as problem:
        return CompileReport(ran=False, unavailable=f"gradle could not be run: {problem}")
    output = finished.stdout + finished.stderr
    errors = _errors(output)
    if finished.returncode != 0 and not errors:
        # the build failed for a reason that is not the generated code -- a missing dependency, no network on
        # a first run. Reporting that as "the routines do not compile" would name the wrong culprit.
        return CompileReport(ran=False, unavailable="gradle compileJava failed without reporting a javac "
                                                    "error; the build itself is broken", output=output)
    if project is not None:
        _attribute(errors, project)
    return CompileReport(ran=True, errors=errors, output=output)


def _errors(output: str) -> list[CompileError]:
    """Every javac error in the build output, once. Gradle repeats the compiler's output on both streams,
    and the same error printed twice reads as two defects."""
    found: list[CompileError] = []
    seen: set[tuple[str, int, str]] = set()
    for line in output.splitlines():
        match = ERROR.match(line.strip())
        if match is None:
            continue
        key = (match.group("file"), int(match.group("line")), match.group("message").strip())
        if key in seen:
            continue
        seen.add(key)
        found.append(CompileError(file=key[0], line=key[1], message=key[2]))
    return found


def _attribute(errors: list[CompileError], project: "GeneratedProject") -> None:
    """Name the routine each error sits in, by the method that encloses its line."""
    from .gen_java.types import java_class_name, java_name

    owners: dict[str, list[tuple[str, str]]] = {}     # file name -> [(java method prefix, routine id)]
    for module in (project.program.modules if project.program is not None else []):
        for klass in (f"{java_class_name(module.name)}Service.java",
                      f"{java_class_name(module.name)}Repository.java"):
            owners[klass] = [(java_name(r.name), r.id) for r in module.routines]
    spans = {Path(f.path).name: _methods(f.render()) for f in project.files}
    for error in errors:
        name = Path(error.file).name
        method = _enclosing(spans.get(name) or [], error.line)
        if method is None:
            continue
        # longest prefix wins: `order_total` and `order_total_line` both prefix `orderTotalLineStmt1`
        candidates = [(prefix, routine) for prefix, routine in owners.get(name, [])
                      if method == prefix or method.startswith(prefix)]
        if candidates:
            error.routine = max(candidates, key=lambda c: len(c[0]))[1]


def _methods(text: str) -> list[tuple[int, str]]:
    return [(number, match.group("name"))
            for number, line in enumerate(text.splitlines(), start=1)
            if (match := METHOD.match(line))]


def _enclosing(methods: list[tuple[int, str]], line: int) -> str | None:
    found = [name for number, name in methods if number <= line]
    return found[-1] if found else None
