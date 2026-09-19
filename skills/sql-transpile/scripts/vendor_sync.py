#!/usr/bin/env python3
"""同梱した ScalarDB 変換モジュールと、リポジトリ本体との差分を管理する。

このスキルは scalardb_migrate/ を import せずに動くよう、その 7 モジュール（MODULES）を
scripts/_scalardb/ にコピー（vendoring）して持っている。スキルのディレクトリを別の場所へコピーしても
変換は動く（そのときは比べる本体が無いので、--check は何もせず 0 で終わる）。
本体を改善してもコピーには自動で反映されないため、乖離を「見える」状態にするのがこのツール。

使い方 (リポジトリルートから):
    .venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --check
    .venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --update

終了コード:
    0 = 同梱コピーは本体と一致、または比べる本体が無い (--check) / コピー完了 (--update)
    1 = 差分あり (--check)
    2 = 実行エラー (--update で本体が見つからない等)

出力の最終行は機械可読:
    VENDOR_DRIFT=0        （本体が無いときは VENDOR_DRIFT=n/a）
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_DIR.parent.parent
UPSTREAM = REPO_ROOT / "scalardb_migrate"
VENDORED = SKILL_DIR / "scripts" / "_scalardb"

MODULES = ("__init__.py", "appside.py", "converter.py", "decomposer.py", "dialect.py", "schema.py", "types.py")


def digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def compare() -> list[tuple[str, str, str]]:
    """(モジュール名, 本体のハッシュ, 同梱のハッシュ) を差分のあるものだけ返す。"""
    drift = []
    for name in MODULES:
        up, ven = digest(UPSTREAM / name), digest(VENDORED / name)
        if up != ven:
            drift.append((name, up or "(なし)", ven or "(なし)"))
    return drift


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同梱した ScalarDB 変換モジュールの鮮度を確認・更新する")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="本体との差分を検出する（変更はしない）")
    g.add_argument("--update", action="store_true", help="本体から同梱コピーを作り直す")
    args = ap.parse_args(argv)

    if not UPSTREAM.is_dir():
        if args.check:
            # リポジトリの外へコピーされたスキルには、比べる相手が無い。それは食い違いでも失敗でもない——
            # 同梱コピーだけで動く、というのがコピーを持っている理由である。以前は終了コード 2 で、
            # SKILL.md の手順どおりに動かすと「実行エラー」に見えた
            print(f"本体（{UPSTREAM}）がありません。同梱コピーだけで動いています。比べる相手が無いので確認は省きます。")
            print("VENDOR_DRIFT=n/a")
            return 0
        print(f"本体が見つかりません: {UPSTREAM}", file=sys.stderr)
        print("--update は sql-migration リポジトリ内のスキルでだけ使えます。", file=sys.stderr)
        return 2

    if args.update:
        VENDORED.mkdir(parents=True, exist_ok=True)
        for name in MODULES:
            src = UPSTREAM / name
            if not src.is_file():
                print(f"本体にありません: {src}", file=sys.stderr)
                return 2
            shutil.copy2(src, VENDORED / name)
            print(f"  更新: {name}")
        print(f"\n{len(MODULES)} モジュールを同梱コピーに反映しました。")
        print("VENDOR_DRIFT=0")
        return 0

    drift = compare()
    if not drift:
        print(f"同梱コピーは本体と一致しています（{len(MODULES)} モジュール）。")
        print("VENDOR_DRIFT=0")
        return 0

    print("同梱コピーが本体と食い違っています。\n")
    print(f"  {'モジュール':<18}{'本体':<14}{'同梱':<14}")
    for name, up, ven in drift:
        print(f"  {name:<18}{up:<14}{ven:<14}")
    print("\n本体の変更を取り込むには:")
    print("  .venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --update")
    print(f"VENDOR_DRIFT={len(drift)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
