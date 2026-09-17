"""採番方式の決定（2026-09-17、計画 §9）を、移行元 DDL から導く。

ScalarDB に順序オブジェクトは無い。決めたのは**用途ごとに使い分ける**ことで、代理キーは hi/lo
（範囲を先に確保して配る。欠番は出るが衝突が激減する）、業務上意味のある番号は counters 表 + 再試行
（厳密な単調増加と欠番なし）である。

**どちらに寄せるかは、移行元の DDL が既に宣言している。**

```sql
CREATE SEQUENCE seq_order_id  START WITH 1000 INCREMENT BY 1 NOCACHE;   -- 欠番を許さない
CREATE SEQUENCE seq_audit_id  START WITH 1    INCREMENT BY 1 CACHE 100; -- 欠番を許す
```

`CACHE n` は「インスタンス停止時に未使用の n 個を捨ててよい」と書いてあるのと同じで、それは hi/lo が
持ち込む性質そのものである。`NOCACHE` はその逆で、欠番を避けたいという意思表示である。だから既定は
**DDL から導き、人が書くのは例外だけ**にする——設定ファイルを一から書かせると、元が何を言っていたかを
読まずに埋めることになる。

    policies = Policies.from_ddl("fixtures/plsql/src/schema.sql")
    policies["seq_audit_id"].scheme      # "hilo"
    policies["seq_order_id"].scheme      # "counter"

上書きは YAML で:

    sequences:
      seq_tx_id: {scheme: counter, reason: 在庫の追跡番号は欠番を許さないと監査要件で決まった}
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

HILO = "hilo"
COUNTER = "counter"
SCHEMES = {HILO, COUNTER}

# CREATE SEQUENCE の各要素。ORDER / CYCLE などは採番方式の選択に効かないので読まない
SEQUENCE = re.compile(
    r"CREATE\s+SEQUENCE\s+(?P<name>[\w$#.]+)"
    r"(?:.*?START\s+WITH\s+(?P<start>\d+))?"
    r"(?:.*?INCREMENT\s+BY\s+(?P<increment>\d+))?"
    r"(?:.*?(?P<nocache>NOCACHE)|.*?CACHE\s+(?P<cache>\d+))?"
    r"[^;]*;", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Policy:
    """1 つの sequence をどう採番するか、そしてなぜそう決まったか。"""

    name: str
    scheme: str
    block: int          # hilo のとき 1 回に確保する個数。counter では 1
    start: int
    increment: int
    reason: str

    def as_dict(self) -> dict:
        return {"name": self.name, "scheme": self.scheme, "block": self.block,
                "start": self.start, "increment": self.increment, "reason": self.reason}


class Policies(dict):
    """{sequence 名: Policy}。名前は小文字で引く。"""

    @classmethod
    def from_ddl(cls, ddl: str | Path, overrides: str | Path | None = None) -> "Policies":
        text = Path(ddl).read_text(encoding="utf-8")
        policies = cls()
        for match in SEQUENCE.finditer(text):
            name = match.group("name").rpartition(".")[2].lower()
            start = int(match.group("start") or 1)
            increment = int(match.group("increment") or 1)
            cache = match.group("cache")
            if match.group("nocache") or cache in (None, "0", "1"):
                policies[name] = Policy(name, COUNTER, 1, start, increment,
                                        "DDL が NOCACHE。欠番を避けたいという意思表示なので counters + 再試行")
            else:
                block = int(cache)
                policies[name] = Policy(name, HILO, block, start, increment,
                                        f"DDL が CACHE {block}。停止時に未使用分を捨ててよいと書いてあるので hi/lo")
        policies._apply(overrides)
        return policies

    def _apply(self, overrides: str | Path | None) -> None:
        if overrides is None or not Path(overrides).exists():
            return
        data = yaml.safe_load(Path(overrides).read_text(encoding="utf-8")) or {}
        for name, entry in (data.get("sequences") or {}).items():
            key = str(name).lower()
            scheme = str(entry.get("scheme", "")).lower()
            if scheme not in SCHEMES:
                raise ValueError(f"{name}: scheme は {sorted(SCHEMES)} のいずれか（{scheme!r}）")
            reason = entry.get("reason")
            if not reason:
                # DDL が言っていることを覆すのだから、理由が要る
                raise ValueError(f"{name}: DDL から導いた方式を上書きするには reason が要る")
            existing = self.get(key)
            self[key] = Policy(key, scheme, int(entry.get("block", 100)) if scheme == HILO else 1,
                               existing.start if existing else int(entry.get("start", 1)),
                               existing.increment if existing else 1,
                               f"上書き: {reason}")

    def of(self, name: str) -> Policy | None:
        return self.get(name.rpartition(".")[2].lower())

    def seed_rows(self) -> list[tuple[str, int]]:
        """counters 表に要る初期行。どちらの方式も同じ表を使う。"""
        return sorted((p.name, p.start) for p in self.values())
