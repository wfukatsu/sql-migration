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

    python difftest/plsql_setup.py                                  # -> difftest/work/plsql-setup.json
    python difftest/plsql_setup.py --schema difftest/work/plsql-schema-double.json \\
        --out difftest/work/plsql-setup-double.json

The schema matters: it is what decides whether `100000.00` fits the column it lands in, and whether a date-only
literal has to be padded for a TIMESTAMP. So each money variant needs its own conversion.

Output: {"scenarios": {<name>: {"setup": [<scalardb sql>, ...]}}, "unconvertible": {<name>: <reason>}}.
A scenario whose setup will not convert is listed in "unconvertible" and left out of "scenarios"; the Java
harness reports it as unrunnable rather than seeding something Oracle never saw.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from difftest.plsql_schema import decimal_columns  # noqa: E402
from scalardb_migrate.converter import convert_script  # noqa: E402
from scalardb_migrate.schema import SchemaRegistry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "fixtures" / "plsql" / "scenarios"
SCHEMA = ROOT / "fixtures" / "plsql" / "scalardb-schema.json"
OUT = ROOT / "difftest" / "work" / "plsql-setup.json"


def scale_money(statement: str, scales: dict[str, dict[str, int]]) -> str:
    """Write a decimal literal as the scaled integer the column holds under the scaled-money convention.

    The generated repository does the same thing at the bind boundary (`Plsql.bind`), so the starting rows have
    to be written the same way or the routine would read dollars where it stored cents.

    A literal with more decimals than the column keeps is rounded half-up, because that is what Oracle does when
    a value is stored into a NUMBER(p,s) -- the corpus relies on it, inserting 1234.565 into a NUMBER(14,2).
    Refusing here would be stricter than the database being reproduced, which is a different kind of wrong.
    """
    tree = sqlglot.parse_one(statement, read="oracle")
    if not isinstance(tree, exp.Insert) or not isinstance(tree.this, exp.Schema):
        return statement
    table = tree.this.this.name.lower()
    columns = [c.name.lower() for c in tree.this.expressions]
    for column, scale in scales.get(table, {}).items():
        if column not in columns:
            continue
        index = columns.index(column)
        for tuple_ in (tree.expression.expressions if isinstance(tree.expression, exp.Values) else []):
            value = tuple_.expressions[index]
            if isinstance(value, exp.Literal) and value.is_number:
                scaled = Decimal(value.name).scaleb(scale).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                tuple_.expressions[index].replace(exp.Literal.number(int(scaled)))
    return tree.sql(dialect="oracle")


CLOCKS = {"SYSDATE", "SYSTIMESTAMP", "CURRENT_DATE", "CURRENT_TIMESTAMP", "LOCALTIMESTAMP"}


def pin_masked_clocks(statement: str, mask: dict[str, list[str]], pinned: str | None,
                      registry: SchemaRegistry | None = None) -> str:
    """時計を読む値のうち、**比べない列に入るもの**だけを、固定した時刻の literal にする（2026-09-19）。

    ScalarDB SQL は VALUES に関数を受け付けないので、`paid_at = SYSTIMESTAMP` の準備行は変換できず、
    `payment_paid_total` / `payment_last_paid_at` は比べられないままだった。

    置き換えてよいのは、その列をシナリオが**マスクしている**ときだけである。マスクされた列の値は
    比較に出ないので、どの時刻を入れても答えは変わらない。マスクされていない列の時計は置き換えない
    ——そこに入る値は比較に出るので、Oracle が読んだ時計とこちらの literal が違えば、差を作るのは
    この置き換えの方になる。そういう準備行は今までどおり変換できないと記録する。
    """
    if not pinned:
        return statement
    tree = sqlglot.parse_one(statement, read="oracle")
    if not isinstance(tree, exp.Insert) or not isinstance(tree.this, exp.Schema):
        return statement
    table = tree.this.this.name.lower()
    masked = {c.lower() for c in mask.get(table, [])}
    columns = [c.name.lower() for c in tree.this.expressions]
    changed = False
    for row in (tree.expression.expressions if isinstance(tree.expression, exp.Values) else []):
        for column, value in zip(columns, row.expressions):
            # 時計は版によって別々の節で来る（`Systimestamp` / `CurrentTimestamp(sysdate=True)` / 素の列名）。
            # 1 つの形だけを見ると、残りが黙って置き換わらない
            name = value.name.upper() if isinstance(value, exp.Column) else \
                ("SYSTIMESTAMP" if type(value).__name__ == "Systimestamp" else
                 "SYSDATE" if isinstance(value, exp.CurrentTimestamp) and value.args.get("sysdate") else
                 "CURRENT_TIMESTAMP" if isinstance(value, exp.CurrentTimestamp) else "")
            if name in CLOCKS and column in masked:
                # TIMESTAMPTZ 列は offset の無い文字列を読めない（ScalarDB が "could not be parsed" で
                # 拒否した）。値はマスクされているので、UTC として書けば足りる
                meta = registry.get(table) if registry is not None else None
                zoned = meta is not None and (meta.columns.get(column) or "").upper() == "TIMESTAMPTZ"
                value.replace(sqlglot.parse_one(f"TIMESTAMP '{pinned}{'Z' if zoned else ''}'", read="oracle"))
                changed = True
    return tree.sql(dialect="oracle") if changed else statement


def convert(statements: list[str], registry: SchemaRegistry,
            scales: dict[str, dict[str, int]] | None = None,
            mask: dict[str, list[str]] | None = None, pinned: str | None = None) -> list[str]:
    """Every statement, converted. Raises when one of them cannot be, naming the statement."""
    out = []
    for statement in statements:
        text = statement.strip().rstrip(";")
        text = pin_masked_clocks(text, mask or {}, pinned, registry)
        if scales:
            text = scale_money(text, scales)
        results, _ = convert_script(text + ";", "oracle", registry, {}, decompose=False)
        for result in results:
            if result.status == "ERROR" or not result.converted:
                reasons = "; ".join(f"{i.code}: {i.message}" for i in result.issues if i.severity == "ERROR")
                raise ValueError(f"{text!r} does not convert ({reasons or result.status})")
            out.extend(result.converted)
    return out


BLOCK = re.compile(r"^\s*BEGIN\s+(?P<body>.*?)\s*END\s*;\s*$", re.IGNORECASE | re.DOTALL)
DML = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def direct(spec: dict, registry: SchemaRegistry, scales: dict[str, dict[str, int]] | None) -> dict | None:
    """ブロックが**素の DML だけ**のシナリオを、ScalarDB で同じ DML を直接走らせる形にする（2026-09-19）。

    trigger のシナリオ（`UPDATE products SET unit_price = :p_price ...`）は routine を呼ばない——
    **PL/SQL の外から表へ直接書く**経路である。移行先に trigger は無く、掛かるのは生成したコードが
    書くときだけなので（#12 §0）、この経路では trigger が掛からない。それを比べずに「比較できない」と
    しておくと、§0 が言っている穴が数字に出ない。同じ DML を ScalarDB へ直接流し、何が起きるかを
    そのまま記録する。

    bind ごとに、それが入る列の型と桁を付ける。ハーネスは生成コードと同じ `Plsql.bind` で渡す——
    金額列は scaled の規約で整数になっているからである。変換できない DML（キーを持たない INSERT
    など）は `refused` として理由を残し、ハーネスはそれを**結果として**記録する。
    """
    call = spec.get("call") or {}
    if call.get("kind") != "block":
        return None
    matched = BLOCK.match(call.get("body") or "")
    if matched is None:
        return None
    statements = [part.strip() for part in matched.group("body").split(";") if part.strip()]
    if not statements or not all(DML.match(part) for part in statements):
        return None
    from plsql.sqlbridge import bind_columns

    out = []
    for statement in statements:
        text = scale_money(statement, scales) if scales else statement
        results, _ = convert_script(text + ";", "oracle", registry, {}, decompose=False)
        result = results[-1]
        if result.status == "ERROR" or not result.converted:
            reasons = "; ".join(f"{i.code}: {i.message}" for i in result.issues if i.severity == "ERROR")
            return {"refused": f"{statement!r} does not convert ({reasons or result.status})"}
        tree = sqlglot.parse_one(statement, read="oracle")
        table = next((t.name.lower() for t in tree.find_all(exp.Table)), "")
        meta = registry.get(table)
        binds = []
        for name, column in bind_columns(tree).items():
            kind = (meta.columns.get(column) if meta else None) or ""
            scale = (scales or {}).get(table, {}).get(column, 0)
            binds.append({"name": name, "type": kind, "scale": scale})
        out.append({"sql": result.converted[0], "binds": binds})
    return {"statements": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema", default=str(SCHEMA), help="Schema Loader JSON the setup rows must fit")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--variant", choices=["scaled", "double"], default="scaled",
                    help="scaled writes money as an integer times 10^scale, matching Plsql.bind in the "
                         "generated repository; double writes it as it stands")
    args = ap.parse_args(argv)

    registry = SchemaRegistry.from_schema_loader_json(args.schema)
    scales = decimal_columns(ROOT / "fixtures" / "plsql" / "src" / "schema.sql") \
        if args.variant == "scaled" else None
    scenarios, unconvertible = {}, {}
    for path in sorted(SCENARIOS.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            scenarios[spec["name"]] = {"setup": convert(spec.get("setup") or [], registry, scales,
                                                        spec.get("mask") or {},
                                                        (spec.get("pinned") or {}).get("sysdate"))}
            straight = direct(spec, registry, scales)
            if straight is not None:
                scenarios[spec["name"]]["direct"] = straight
        except ValueError as e:
            unconvertible[spec["name"]] = str(e)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"scenarios": scenarios, "unconvertible": unconvertible},
                             ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(scenarios)} scenario(s) converted, {len(unconvertible)} not -> {out}")
    for name, reason in sorted(unconvertible.items()):
        print(f"  {name:<34} {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
