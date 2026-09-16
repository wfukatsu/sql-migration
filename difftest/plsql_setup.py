"""P3-1: convert every scenario's setup SQL to ScalarDB SQL, once, so the Java harness does not have to.

The scenarios (`fixtures/plsql/scenarios/*.yaml`) describe their starting rows in Oracle SQL, because Oracle is
what P0-5 measured. ScalarDB SQL does not take `DATE '2025-04-01'` or `SYSDATE`, so those statements have to be
converted before the ScalarDB side can seed the same rows.

The conversion runs through `scalardb_migrate` -- the converter this repository already ships -- rather than
through a hand-written rewrite in the harness. Two reasons. A hand-written rewrite would be a second
transcription of the fixture, free to drift from the one Oracle ran, and then a difference between the two
captures would mean the transcriptions disagreed rather than the databases. And the converter is the thing the
migration actually depends on: if it cannot produce the setup rows, that is a finding about the converter, and
it is recorded here as one instead of being worked around.

    python difftest/plsql_setup.py            # -> difftest/work/plsql-setup.json

Output: {"scenarios": {<name>: {"setup": [<scalardb sql>, ...]}}, "unconvertible": {<name>: <reason>}}.
A scenario whose setup will not convert is listed in "unconvertible" and left out of "scenarios"; the Java
harness reports it as unrunnable rather than seeding something Oracle never saw.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalardb_migrate.converter import convert_script  # noqa: E402
from scalardb_migrate.schema import SchemaRegistry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "fixtures" / "plsql" / "scenarios"
SCHEMA = ROOT / "fixtures" / "plsql" / "scalardb-schema.json"
OUT = ROOT / "difftest" / "work" / "plsql-setup.json"


def convert(statements: list[str], registry: SchemaRegistry) -> list[str]:
    """Every statement, converted. Raises when one of them cannot be, naming the statement."""
    out = []
    for statement in statements:
        text = statement.strip().rstrip(";")
        results, _ = convert_script(text + ";", "oracle", registry, {}, decompose=False)
        for result in results:
            if result.status == "ERROR" or not result.converted:
                reasons = "; ".join(f"{i.code}: {i.message}" for i in result.issues if i.severity == "ERROR")
                raise ValueError(f"{text!r} does not convert ({reasons or result.status})")
            out.extend(result.converted)
    return out


def main() -> int:
    registry = SchemaRegistry.from_schema_loader_json(str(SCHEMA))
    scenarios, unconvertible = {}, {}
    for path in sorted(SCENARIOS.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            scenarios[spec["name"]] = {"setup": convert(spec.get("setup") or [], registry)}
        except ValueError as e:
            unconvertible[spec["name"]] = str(e)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"scenarios": scenarios, "unconvertible": unconvertible},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(scenarios)} scenario(s) converted, {len(unconvertible)} not -> {OUT}")
    for name, reason in sorted(unconvertible.items()):
        print(f"  {name:<34} {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
