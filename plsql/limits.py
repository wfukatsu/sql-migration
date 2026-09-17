"""P4-5 の続き: 走査行数の上限。既定を config で、routine ごとに個別指定できるようにする（2026-09-17 の決定）。

Oracle の cursor は 1 行ずつ取るので 1000 万行でも動いた。ScalarDB には跨トランザクションの cursor が無く、
生成コードは行を先に読むので、**動く行数はメモリで決まる**。上限を決めずに移行すると本番で初めて分かる。

上限は業務ごとに違うので 1 つの値では決められない。既定を config に置き、routine 単位で上書きできる:

    # limits.yaml
    scanRows:
      default: 10000
      routines:
        pkg_order_report.mark_reviewed: 1000    # 1 日分の受注しか回らない
        prc_purge_audit: 100000                 # 夜間バッチ。多くても落ちない方が良い

超えたときは**例外で止まる**。OOM で落ちるより、限度を超えたと言って止まる方が良い——どちらも止まるが、
後者だけが理由を言う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 決めずに移行するよりは、控えめな既定を置いて超えたら止める方が良い。この値そのものに根拠は無く、
# 「業務ごとに決めるべきもの」であることを忘れないための出発点である。
DEFAULT_SCAN_ROWS = 10_000


@dataclass
class Limits:
    """走査行数の上限。既定 1 つと、routine ごとの上書き。"""

    scan_rows: int = DEFAULT_SCAN_ROWS
    by_routine: dict[str, int] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "Limits":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い。--limits を外すか、ファイルを作る")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        scan = data.get("scanRows") or {}
        by_routine = {str(k): _positive(v, k) for k, v in (scan.get("routines") or {}).items()}
        return cls(scan_rows=_positive(scan.get("default", DEFAULT_SCAN_ROWS), "default"),
                   by_routine=by_routine, source=str(file))

    def for_routine(self, routine: str) -> int:
        return self.by_routine.get(routine, self.scan_rows)

    def explain(self, routine: str) -> str:
        """その上限がどこから来たか。生成コードのコメントに入れる。"""
        if routine in self.by_routine:
            return f"{self.source or 'limits'} で {routine} に指定された値"
        return f"既定値（{self.source or '組み込み'}）。この routine 固有の上限は決められていない"


def _positive(value, where) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError(f"scanRows.{where}: 正の整数でなければならない（{value!r}）")
    return number
