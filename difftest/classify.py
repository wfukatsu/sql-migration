"""Classify write / DDL / PL-SQL statements with the converter only (no database) and write JSON for report.py.

  .venv/bin/python difftest/classify.py difftest/cases/oracle-features-write.sql --dialect oracle --json-out out/oracle-features.write.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scalardb_migrate.converter import convert_script  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_file")
    ap.add_argument("--dialect", required=True, choices=["oracle", "postgres", "mysql"])
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args()
    results, _ = convert_script(Path(args.case_file).read_text(encoding="utf-8"), args.dialect)
    out = []
    for r in results:
        m = re.search(r"--\s*@feature:\s*(.+)", r.source_sql)
        if not m:
            continue
        body = "\n".join(l for l in r.source_sql.splitlines() if not l.strip().startswith("--")).strip()
        out.append({"index": r.index, "feature": m.group(1).strip(), "sql": body, "kind": r.kind,
                    "convert_status": r.status, "converted": r.converted,
                    "issues": [(i.severity, i.code, i.message) for i in r.issues if i.severity != "INFO"]})
        print(f"{r.status:<8} {m.group(1).strip()}")
    Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
