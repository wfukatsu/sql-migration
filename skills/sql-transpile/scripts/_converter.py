"""変換器 scalardb_migrate を、スキルの置かれた場所に合わせて読めるようにする（#159 M1、2026-09-30）。

- リポジトリ（開発用のチェックアウト、プラグインとして入れたもの）の中では、リポジトリのルートの
  scalardb_migrate/ をそのまま読む。コピーは持たない。
- 公開の GitHub の履歴では、bin/publish-github がこのディレクトリに scalardb_migrate/ の 7 モジュールのコピー
  _scalardb/ を置く。スキルのディレクトリだけを別の場所へ写したときは、それを scalardb_migrate という名前で読む。

どちらでも import するのは scalardb_migrate.* なので、同じプロセスに変換器が 2 つ入ることはない（SQLGlot の
方言表の "scalardb" の登録が衝突しない）。スキルのスクリプトは、scalardb_migrate を import する前にこれを import する。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPOSITORY = SCRIPTS.parents[2]            # <root>/skills/sql-transpile/scripts → <root>
COPY = SCRIPTS / "_scalardb"               # 公開した履歴にだけある


def _load() -> None:
    if "scalardb_migrate" in sys.modules:
        return
    if (REPOSITORY / "scalardb_migrate" / "__init__.py").is_file():
        if str(REPOSITORY) not in sys.path:
            sys.path.append(str(REPOSITORY))
        return
    if (COPY / "__init__.py").is_file():
        # コピーを本体の名前で読む。中のモジュールは相対 import なので、scalardb_migrate.converter などとして読まれる
        spec = importlib.util.spec_from_file_location("scalardb_migrate", COPY / "__init__.py",
                                                      submodule_search_locations=[str(COPY)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["scalardb_migrate"] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules["scalardb_migrate"]
            raise
    # どちらも無ければ何もしない。scalardb_migrate の import が ImportError になり、transpile.py がそれを伝える


_load()
