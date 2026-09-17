#!/usr/bin/env python3
"""P2-11 / P3-2: the generated Java against what Oracle actually did, at two levels of evidence.

Compiling says nothing about meaning, so both stages compare behaviour against the P0-5 Oracle captures. They
differ in what they put underneath the generated code, and that difference is the point.

    .venv/bin/python difftest/plsql_diff.py              # both stages
    .venv/bin/python difftest/plsql_diff.py --early      # stage 1 only, no cluster needed
    .venv/bin/python difftest/plsql_diff.py --full       # stage 2 only

**Stage 1 (P2-11), H2.** What is under test is the generated Java: its control flow, its exceptions, its
arithmetic. H2 needs no licensed cluster, which is what makes this runnable on every change.

**Stage 2 (P3-2), a real ScalarDB Cluster.** H2 accepting something is not evidence that ScalarDB does -- it
accepted a `BigDecimal` bind that ScalarDB refuses outright, which is how 33 scenarios looked fine until they
met a cluster. This stage compares the captures P3-1 took, on every field the capture holds, for each money
convention. It needs captures to exist: `difftest/plsql_capture.py --variant scaled|double` takes them.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HARNESS = "com.scalar.migrate.plsql.GeneratedDiffTest"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", default=str(ROOT / "fixtures" / "plsql" / "src"))
    parser.add_argument("--out-dir", default=str(ROOT / "generated"))
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--early", action="store_true", help="stage 1 only (H2)")
    parser.add_argument("--full", action="store_true", help="stage 2 only (the ScalarDB captures)")
    parser.add_argument("--json", help="write stage 2's report here, for P3-5")
    args = parser.parse_args(argv)

    stages = (args.early, args.full)
    early, full = stages if any(stages) else (True, True)

    if full and not early:
        return _compare(args.json)

    if not args.skip_generate:
        print("generating...")
        generated = subprocess.run(
            [sys.executable, "-m", "plsql.generate", args.src, "--out-dir", args.out_dir],
            cwd=str(ROOT), capture_output=True, text=True)
        print(generated.stdout.strip())
        if generated.returncode != 0:
            print(generated.stderr.strip(), file=sys.stderr)
            return generated.returncode

    print(f"running {HARNESS} against the P0-5 captures...")
    finished = subprocess.run(
        ["gradle", "test", "-Dplsql.generated=1", "--tests", f"*{HARNESS.rsplit('.', 1)[-1]}*"],
        cwd=str(ROOT / "runtime-java"), capture_output=True, text=True)
    print(_summary(ROOT / "runtime-java" / "build" / "test-results" / "test"))
    if finished.returncode != 0:
        print(finished.stdout[-3000:], file=sys.stderr)
        return finished.returncode
    return _compare(args.json) if full else 0


def _compare(report: str | None) -> int:
    """Stage 2. Kept in its own module because it reads captures and runs nothing."""
    from difftest.plsql_compare import main as compare

    print("\ncomparing the ScalarDB captures with the P0-5 captures...")
    return compare(["--json", report] if report else [])


def _summary(results: Path) -> str:
    import xml.etree.ElementTree as ET

    for path in sorted(results.glob("TEST-*GeneratedDiffTest.xml")):
        root = ET.parse(path).getroot()
        failures = int(root.get("failures", 0)) + int(root.get("errors", 0))
        lines = [f"{root.get('tests')} comparisons, {failures} disagreeing with Oracle"]
        for case in root.iter("testcase"):
            for failure in list(case.iter("failure")) + list(case.iter("error")):
                lines.append(f"  {case.get('name')}: {(failure.get('message') or '')[:200]}")
        return "\n".join(lines)
    return "no results: did the harness run?"


if __name__ == "__main__":
    sys.exit(main())
