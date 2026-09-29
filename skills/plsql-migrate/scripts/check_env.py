#!/usr/bin/env python3
"""Step 0: このスキルが動かす PL/SQL の道具（plsql/）が動く Python かを確かめる。引数は取らない。

allowed-tools に `python -c *` を置くと、確認なしで任意のコードが動く（#152、セキュリティのレビュー M1）。
環境の確認はこの決まったスクリプトだけにして、そのパスだけを許す。

    .venv/bin/python skills/plsql-migrate/scripts/check_env.py

終了コード: 0 = 動く、3 = 依存か plsql/ を読み込めない
"""

from __future__ import annotations

import importlib
import platform
import sys
from pathlib import Path

# spec_facts.py と同じく、リポジトリのルート（skills/<名前>/scripts の 3 つ上）から plsql を読む
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 構文解析（antlr4）、SQL の変換（sqlglot）、limits.yaml（yaml）、IR の検査（jsonschema）、PL/SQL の道具（plsql）
MODULES = ("sqlglot", "antlr4", "yaml", "jsonschema", "plsql")


def main() -> int:
    print("python", platform.python_version())
    if sys.version_info < (3, 10):
        print("Python 3.10 以上が要ります", file=sys.stderr)
        return 3
    missing = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except ImportError as error:
            missing.append(f"{name}: {error}")
    if missing:
        print("依存を読み込めません: " + "; ".join(missing), file=sys.stderr)
        return 3
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
