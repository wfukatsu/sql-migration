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

# javac's own format, which Gradle passes through unchanged: `/path/File.java:12: error: cannot find symbol`.
# Only the English word: the build pins the compiler's locale (`plsql.verify` in build.gradle) rather than
# have this enumerate languages -- the enumeration is what silently stopped covering things twice already.
ERROR = re.compile(r"^(?P<file>.+\.java):(?P<line>\d+):\s*error:\s*(?P<message>.*)$")
# any diagnostic, in any language. Used only to tell "javac said something this could not read" from "the
# build never got as far as javac", which are different culprits.
DIAGNOSTIC = re.compile(r"^.+\.java:\d+:\s*\S+:")
# `symbol: variable audit` and the like: javac indents what qualifies the line above it, in every language.
# It also echoes the offending source line with a caret under it, which says nothing the file and line do not
# already say, so the caret and the line it points at are dropped.
CONTINUATIONS = 4
CARET = re.compile(r"^\^+$")
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
    command = [gradle, "compileJava", "--console=plain", "-q", "-Pplsql.verify=1",
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
        # a first run. Reporting that as "the routines do not compile" would name the wrong culprit. A build
        # that did reach javac, in a language this could not read, is a third culprit again: the locale pin
        # did not take, and saying "the build is broken" would send the reader to the wrong place.
        unreadable = any(DIAGNOSTIC.match(line.strip()) for line in output.splitlines())
        return CompileReport(ran=False, output=output, unavailable=(
            "javac reported diagnostics this check could not read; the compiler's locale is not English, so "
            "`plsql.verify` did not pin it" if unreadable else
            "gradle compileJava failed without reporting a javac error; the build itself is broken"))
    if project is not None:
        _attribute(errors, project)
    return CompileReport(ran=True, errors=errors, output=output)


def _errors(output: str) -> list[CompileError]:
    """Every javac error in the build output, once, with the lines that say which name it is about.

    `cannot find symbol` on its own is not actionable -- `symbol: variable audit` is the whole answer, and
    javac writes it on the next line, indented. The indent is the only marker that does not change with the
    language, so that is what is read. Gradle repeats the compiler's output on both streams, so the same
    error printed twice is collapsed rather than counted as two defects.
    """
    found: list[CompileError] = []
    extras: dict[int, list[str]] = {}
    current: CompileError | None = None
    for raw in output.splitlines():
        line = raw.strip()
        match = ERROR.match(line)
        if match is not None:
            current = CompileError(file=match.group("file"), line=int(match.group("line")),
                                   message=match.group("message").strip())
            found.append(current)
            extras[id(current)] = []
            continue
        if current is not None and line and raw[:1].isspace() and len(extras[id(current)]) < CONTINUATIONS:
            extras[id(current)].append(line)
            continue
        current = None   # anything else ends the run; the next indented line is not this error's
    for error in found:
        for qualifier in _qualifiers(extras[id(error)]):
            error.message += f" / {qualifier}"
    seen: set[tuple[str, int, str]] = set()
    unique: list[CompileError] = []
    for error in found:
        key = (error.file, error.line, error.message)
        if key not in seen:
            seen.add(key)
            unique.append(error)
    return unique


def _qualifiers(lines: list[str]) -> list[str]:
    """The continuation lines worth keeping: everything but the echoed source and the caret under it."""
    keep = list(lines)
    for index in reversed(range(len(keep))):
        if CARET.match(keep[index]):
            del keep[index]
            if index - 1 >= 0:
                del keep[index - 1]   # the source line the caret pointed at
    return keep


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
