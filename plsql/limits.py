"""移行の決定のうち、**生成器が推測してはならないもの**を routine ごとに記録する config。

いまのところ 3 つある:

* **走査行数の上限**（P4-5 の続き、2026-09-17 の決定）
* **行ロックを落として楽観制御へ移すと決めた routine**（#9 / 2026-09-18 の決定）
* **トランザクション境界を 1 反復 = 1 トランザクションに割ると決めた routine**（#3 / #24 / #14）
* **動的 SQL が受け付けてよい表名**（2026-09-19 の決定）

どちらも「決めた人がいるときだけ、決めたと書ける」という同じ形である。書いていない routine に
既定の答えを当てると、**誰も決めていないことが決まったように見える**。



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
class RowLocks:
    """行ロックを落として**楽観制御 + 呼び出し側の再試行**へ移すと決めた routine と、その理由（#9）。

    既定は「決めていない」であり、決めていない routine の書き込みは**拒否したままにする**
    （`docs/plsql-transaction-patterns.md` §A）。ロックこそがその読み書きを安全にしていたので、
    落ちた以上、黙って書き換えを進めてはならない。

    記録された routine では、読んだ値を使う式の先行計算（P4-4）を行う。安全なのは**同じ
    トランザクションの中で読んで書く**からで、衝突は Consensus Commit が弾く（P3-4 で実測）。
    弾かれたものを再試行するのは呼び出し側の責務である（計画 §9）。

    **routine ごとに書く**のは、決めることが routine ごとに違うためである——その呼び出し側が
    再試行するのか、その操作が冪等か、業務例外と衝突を区別できるか。
    """

    optimistic: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "RowLocks":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        section = (data.get("rowLocks") or {}).get("optimistic") or {}
        return cls(optimistic={str(k): str(v).strip() for k, v in section.items()}, source=str(file))

    def decided(self, routine: str) -> bool:
        return routine in self.optimistic

    def why(self, routine: str) -> str | None:
        return self.optimistic.get(routine)


@dataclass
class Boundaries:
    """トランザクション境界を **1 反復 = 1 トランザクション**に割ると決めた routine と、その理由。

    決定は #3（`docs/plsql-transaction-patterns.md` §E / §F / §G）、実装は #24 / #14 である。
    記録された routine は、1 つの method ではなく**トランザクション単位に割った部品**として出る:

        <routine>Start / Targets / One / Failed / Done / FailedBatch

    **生成コードはループを持たない。** 回すのは呼び出し側で、生成コードには推奨の回し方が
    コメントとして付く（#24 の決定、2026-09-18）。ループを持たせると、1 反復ごとに境界へ
    踏み込むことになり、計画 §9 の既定（生成コードは begin も commit もしない）と食い違う。

    既定は「決めていない」である。書いていない routine は、いままでどおり 1 つの method として
    出て、`COMMIT` や `SAVEPOINT` のところで止まる——**止まっているのが正しい**。境界をどこに
    引くかは業務の設計であって、生成器が推測してよいものではない。
    """

    per_iteration: dict[str, str] = field(default_factory=dict)
    # 自分だけで 1 つのトランザクションになる routine（自律トランザクション / #3 §G）。
    # 呼び出し側のトランザクションとは**別に**回す——同じ中で呼ぶと、親の rollback で一緒に消える
    separate: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "Boundaries":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        transactions = data.get("transactions") or {}
        section = transactions.get("perIteration") or {}
        separate = transactions.get("separate") or {}
        both = sorted(set(section) & set(separate))
        if both:
            raise ValueError(f"{both} が perIteration と separate の両方にある。境界の形はどちらか一方である")
        return cls(per_iteration={str(k): str(v).strip() for k, v in section.items()},
                   separate={str(k): str(v).strip() for k, v in separate.items()}, source=str(file))

    def decided(self, routine: str) -> bool:
        return routine in self.per_iteration or routine in self.separate

    def why(self, routine: str) -> str | None:
        return self.per_iteration.get(routine) or self.separate.get(routine)


@dataclass
class DynamicTables:
    """表名が実行時に決まる動的 SQL の、**受け付けてよい表名**（2026-09-19 の決定）。

    `'DELETE FROM ' || p_table_name || ...` は走りうる文が数えられない。数えられるようにするのは
    **人が表名を決めたとき**だけで、書いていない routine は今までどおり拒否する。一覧に無い表名が
    渡されたら、生成コードは実行時に拒否する——Oracle ならどの表でも走ったが、それを許すことこそ
    移行で塞ぎたい穴である。
    """

    allowed: dict[str, list[str]] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "DynamicTables":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        section = data.get("dynamicTables") or {}
        return cls(allowed={str(k): [str(t) for t in (v or [])] for k, v in section.items()},
                   source=str(file))

    def for_routine(self, routine: str) -> list[str]:
        return list(self.allowed.get(routine, []))


@dataclass
class Limits:
    """走査行数の上限。既定 1 つと、routine ごとの上書き。"""

    scan_rows: int = DEFAULT_SCAN_ROWS
    by_routine: dict[str, int] = field(default_factory=dict)
    # 「上限では守らないと決めた」routine と、その理由。値の代わりに理由を書く場所であって、
    # 書き忘れとは別物である——`--limits-strict` はこの 2 つを区別する（#19 / 2026-09-18）
    not_limited: dict[str, str] = field(default_factory=dict)
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
        not_limited = {str(k): str(v) for k, v in (scan.get("notLimited") or {}).items()}
        overlap = sorted(set(by_routine) & set(not_limited))
        if overlap:
            raise ValueError(f"{overlap} が routines と notLimited の両方にある。"
                             f"上限を置くか置かないかは、どちらか一方である")
        return cls(scan_rows=_positive(scan.get("default", DEFAULT_SCAN_ROWS), "default"),
                   by_routine=by_routine, not_limited=not_limited, source=str(file))

    def for_routine(self, routine: str) -> int:
        return self.by_routine.get(routine, self.scan_rows)

    def decided(self, routine: str) -> bool:
        """その routine の上限について**誰かが決めたか**。既定に落ちたものは決まっていない。

        決めた形は 2 つある: 値を書く（`routines`）か、上限では守らないと決めて理由を書く
        （`notLimited`）。既定値は「決めていない」という意味で置いてある（DEFAULT_SCAN_ROWS）。
        その事実は生成コードのコメントには出るが、合否には出ていなかった——読んだ人だけが
        気づける状態は、判定に入っていないのと同じである（#19 / 2026-09-18 の決定）。

        理由をコメントではなく **データ**に書かせているのも同じ理由による。コメントは読み手への
        説明であって、決定の記録ではない。
        """
        return routine in self.by_routine or routine in self.not_limited

    def explain(self, routine: str) -> str:
        """その上限がどこから来たか。生成コードのコメントに入れる。"""
        if routine in self.by_routine:
            return f"{self.source or 'limits'} で {routine} に指定された値"
        if routine in self.not_limited:
            # 上限を置かないと決めた routine でも、既定値の網は外さない。決めたのは「この値で守る」
            # ことをやめたという話で、メモリを使い切ってよいという話ではない
            return (f"既定値。{routine} は上限では守らないと決めてある"
                    f"（{self.not_limited[routine]}）ので、これは暫定の網である")
        return f"既定値（{self.source or '組み込み'}）。この routine 固有の上限は決められていない"


def _positive(value, where) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError(f"scanRows.{where}: 正の整数でなければならない（{value!r}）")
    return number


@dataclass
class DbLinks:
    """DB link の行き先（2026-09-20 の決定）。

    `orders@warehouse_link` は別のデータベースの表で、Oracle はそれを分散トランザクションで書く。移行先で同じ意味を
    保つ形は 1 つ: **その表も ScalarDB の管理下に置き、別の namespace として同じトランザクションで書く**。複数の
    データベースにまたがるトランザクションは ScalarDB がそのためにあるもので、原子性は移行元と変わらない。

    行き先を決められるのは人だけである——link の名前からは、相手の表が ScalarDB の下に来るのかどうかは分からない。
    書いていない link は今までどおり何もしない（`LINK-001` は未決定のまま）。outbox などの結果整合へ変えるのは
    意味を変えるので、移行とは別の仕様変更として扱う。
    """

    namespaces: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "DbLinks":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        namespaces, reasons = {}, {}
        for link, entry in (data.get("dbLinks") or {}).items():
            if not isinstance(entry, dict) or not entry.get("namespace"):
                raise ValueError(f"dbLinks.{link}: `namespace` が要る（その link の表を置く ScalarDB の namespace）")
            namespaces[str(link).lower()] = str(entry["namespace"])
            reasons[str(link).lower()] = str(entry.get("reason") or "").strip()
        return cls(namespaces=namespaces, reasons=reasons, source=str(file))

    def namespace(self, link: str) -> str | None:
        return self.namespaces.get(link.lower())

    def why(self, link: str) -> str | None:
        return self.reasons.get(link.lower())
