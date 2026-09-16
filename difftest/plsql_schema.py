"""P3-1: derive the money-variant ScalarDB schema from the Oracle DDL.

ScalarDB has no DECIMAL type, so an Oracle `NUMBER(12,2)` has to become something else, and there are two
defensible answers. `scalardb_migrate` maps it to DOUBLE and notes in the same breath that money is better kept
as a scaled integer in BIGINT. The PoC measures both rather than asserting which is right, so this writes the
second schema from the first.

What comes from where matters. Which columns are decimal is read out of `fixtures/plsql/src/schema.sql` through
the converter's own type mapping -- not from a list kept here, which would be a second copy of the DDL free to
drift from it. The key design (partition keys, clustering keys, secondary indexes) is read out of the existing
`fixtures/plsql/scalardb-schema.json`, because that was a deliberate decision recorded in KEY-DESIGN.md and is
not derivable from Oracle.

    python difftest/plsql_schema.py --variant double --namespace plsqlpoc_dbl \\
        --out difftest/work/plsql-schema-double.json

Variants:
  scaled  columns stay BIGINT and hold the value times 10^scale (the schema shipped in fixtures/)
  double  columns become DOUBLE, which is what the converter emits by default
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalardb_migrate.types import map_type  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DDL = ROOT / "fixtures" / "plsql" / "src" / "schema.sql"
BASE = ROOT / "fixtures" / "plsql" / "scalardb-schema.json"


def decimal_columns(ddl: Path) -> dict[str, dict[str, int]]:
    """{table: {column: scale}} for every Oracle column with a fractional part."""
    out: dict[str, dict[str, int]] = {}
    for statement in sqlglot.parse(ddl.read_text(encoding="utf-8"), read="oracle"):
        if not isinstance(statement, exp.Create) or not isinstance(statement.this, exp.Schema):
            continue
        table = statement.this.this.name.lower()
        for cd in statement.this.expressions:
            if not isinstance(cd, exp.ColumnDef) or cd.kind is None:
                continue
            params = [int(p.name) for p in cd.kind.expressions if isinstance(p, exp.DataTypeParam)]
            scale = params[1] if len(params) > 1 else 0
            # the converter decides what counts as decimal; asking it keeps one definition, not two
            if scale > 0 and map_type(cd.kind, "oracle").scalardb_type == "DOUBLE":
                out.setdefault(table, {})[cd.name.lower()] = scale
    return out


def build(variant: str, namespace: str) -> dict:
    decimals = decimal_columns(DDL)
    base = json.loads(BASE.read_text(encoding="utf-8"))
    out = {}
    for qualified, definition in base.items():
        table = qualified.split(".", 1)[1]
        definition = json.loads(json.dumps(definition))  # the base file is not ours to mutate
        if variant == "double":
            for column in decimals.get(table, {}):
                if column in definition["columns"]:
                    definition["columns"][column] = "DOUBLE"
        out[f"{namespace}.{table}"] = definition
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=["scaled", "double"], required=True)
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    schema = build(args.variant, args.namespace)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")

    decimals = decimal_columns(DDL)
    total = sum(len(c) for c in decimals.values())
    print(f"{args.variant} schema for namespace {args.namespace} -> {out}")
    print(f"  {total} decimal column(s) across {len(decimals)} table(s): "
          + ", ".join(f"{t}.{c}(x10^{s})" for t, cs in sorted(decimals.items()) for c, s in sorted(cs.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
