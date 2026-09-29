#!/usr/bin/env python3
"""Step 0: このスキルのスクリプトが動く Python か（sqlglot を読めるか）を確かめる。引数は取らない。

allowed-tools に `python -c *` を置くと、確認なしで任意のコードが動く（#152、セキュリティのレビュー M1）。
環境の確認はこの決まったスクリプトだけにして、そのパスだけを許す。

    .venv/bin/python skills/sql-transpile/scripts/check_env.py

終了コード: 0 = 動く、3 = 依存を読み込めない（transpile.py の 3 と同じ）
"""

from __future__ import annotations

import platform
import sys


def main() -> int:
    print("python", platform.python_version())
    if sys.version_info < (3, 10):
        print("Python 3.10 以上が要ります", file=sys.stderr)
        return 3
    try:
        import sqlglot
    except ImportError as error:
        print(f"依存を読み込めません: {error}", file=sys.stderr)
        return 3
    print("sqlglot", sqlglot.__version__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
