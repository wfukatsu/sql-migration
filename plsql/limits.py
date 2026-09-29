"""移行の決定のうち、**生成器が推測してはならないもの**を routine ごとに記録する config。

いまのところ 3 つある:

* **走査行数の上限**（P4-5 の続き、2026-09-17 の決定）
* **行ロックを落として楽観制御へ移すと決めた routine**（#9 / 2026-09-18 の決定）
* **トランザクション境界を 1 反復 = 1 トランザクションに割ると決めた routine**（#3 / #24 / #14）
* **動的 SQL が受け付けてよい表名**（2026-09-19 の決定）
* **動的 SQL の連結する箇所（穴）ごとに受け付けてよい値**（#165、2026-09-30 の決定。`dynamicSql:`）
* **移行元のセッションの NLS**（#157。`nls:`、project に 1 つ。`NlsSettings`）

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

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 決めずに移行するよりは、控えめな既定を置いて超えたら止める方が良い。この値そのものに根拠は無く、
# 「業務ごとに決めるべきもの」であることを忘れないための出発点である。
DEFAULT_SCAN_ROWS = 10_000


@dataclass
class RowLocks:
    """行ロックを落として**楽観制御 + 呼び出し側の再試行**へ移すと決めた routine と、その理由（#9）。

    既定は「決めていない」である。決めていない routine で**断られるのは、書き換えないと ScalarDB に渡せない
    文だけ**である: 列を読む式で書く文（`SET c = c + :x`。読んでから書く形への書き換え `rmw.rewrite` をしない）、
    MERGE（`merge.rewrite` で割らない）、行ロックを持つ routine では PL/SQL の式を先に計算して値で渡す文
    （P4-4 の先行計算 `lift` をしない）。値や変数をそのまま書く文、キーで読む文はそのまま通る
    （`docs/plsql-migration/plsql-transaction-patterns.md` §A）。ロックこそがその読み書きを安全にしていたので、
    落ちた以上、黙って書き換えを進めてはならない。

    記録された routine では、これらの書き換えと先行計算を行う。安全なのは**同じ
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

    決定は #3（`docs/plsql-migration/plsql-transaction-patterns.md` §E / §F / §G）、実装は #24 / #14 である。
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
    # routine の中の COMMIT / ROLLBACK / SAVEPOINT を**呼び出し側の境界に移す**と決めた routine（2026-09-25、
    # samples/oracle-samples）。生成コードはその文を出さず、commit も rollback も呼び出し側が行う。
    # 途中の ROLLBACK が戻していた分は、呼び出し側が戻さないかぎり残る——意味が変わる決定である
    caller: dict[str, str] = field(default_factory=dict)
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
        caller = transactions.get("callerBoundary") or {}
        names = [set(section), set(separate), set(caller)]
        both = sorted((names[0] & names[1]) | (names[0] & names[2]) | (names[1] & names[2]))
        if both:
            raise ValueError(f"{both} が perIteration / separate / callerBoundary の 2 つ以上にある。境界の形は 1 つである")
        return cls(per_iteration={str(k): str(v).strip() for k, v in section.items()},
                   separate={str(k): str(v).strip() for k, v in separate.items()},
                   caller={str(k): str(v).strip() for k, v in caller.items()}, source=str(file))

    def decided(self, routine: str) -> bool:
        return routine in self.per_iteration or routine in self.separate or routine in self.caller

    def why(self, routine: str) -> str | None:
        return self.per_iteration.get(routine) or self.separate.get(routine) or self.caller.get(routine)

    def where(self, routine: str) -> str:
        if routine in self.per_iteration:
            return "perIteration"
        return "separate" if routine in self.separate else "callerBoundary"


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


# 穴ごとの一覧を掛け合わせた数の上限（#165）。1 つの文から生成する静的な文の数で、ORDER BY の列 8 つと方向 2 つ
# （16 通り）に、もう 1 つ 4 通りの穴が付いても収まる。これを超える文は、人が 1 つずつ見られる数ではないので、
# 展開せずに理由を言って断る
MAX_HOLE_COMBINATIONS = 64

_HOLE_NAME = re.compile(r"^[A-Za-z][\w$#]*$")


@dataclass
class DynamicSqlHoles:
    """動的 SQL の**連結する箇所（穴）ごと**に、受け付けてよい値を並べた一覧（#165、2026-09-30 の決定）。

        dynamicSql:
          pkg_report.list_orders:
            holes:
              p_sort_col: [order_id, ordered_at]    # ORDER BY の列
              p_sort_dir: [ASC, DESC]               # 並べる向き
            reason: 画面の並べ替えは列 2 つと向き 2 つだけ（2026-09-30、業務担当が確認）

    穴の名前は、文に連結している**変数（引数か局所変数）の名前**である。`DBMS_ASSERT.SIMPLE_SQL_NAME(p_col)` の
    ように包んでいれば中の `p_col` を書く。大文字小文字は区別しない。

    文の穴が**すべて**一覧を持つときだけ、値の組み合わせごとに静的な文を生成する（上限 `MAX_HOLE_COMBINATIONS`）。
    1 つでも一覧の無い穴があれば、その文は展開せず REDESIGN のまま未決定で、どの穴に一覧が無いかを言う。
    穴が 1 つの文は、いままでどおり `dynamicTables` でも決められる。
    """

    holes: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "DynamicSqlHoles":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        section = data.get("dynamicSql") or {}
        if not isinstance(section, dict):
            raise ValueError("dynamicSql は対応（routine id: {holes, reason}）で書く")
        holes: dict[str, dict[str, list[str]]] = {}
        reasons: dict[str, str] = {}
        for routine, entry in section.items():
            where = f"dynamicSql.{routine}"
            if not isinstance(entry, dict):
                raise ValueError(f"{where} は holes と reason を持つ対応で書く")
            extra = sorted(str(k) for k in entry if k not in ("holes", "reason"))
            if extra:
                raise ValueError(f"{where} に知らないキー {extra}。書けるのは holes と reason")
            why = " ".join(str(entry.get("reason") or "").split())
            if not why:
                raise ValueError(f"{where} に理由（reason）が無い。誰がなぜその値だけを受け付けると決めたかを書く")
            listed = entry.get("holes")
            if not isinstance(listed, dict) or not listed:
                raise ValueError(f"{where}.holes に穴ごとの一覧が無い（`holes: {{p_sort_col: [order_id, ordered_at]}}`）")
            by_hole: dict[str, list[str]] = {}
            for hole, values in listed.items():
                name = str(hole).strip()
                if not _HOLE_NAME.match(name):
                    raise ValueError(f"{where}.holes.{hole}: 穴の名前は連結している変数の名前で書く"
                                     f"（DBMS_ASSERT で包んでいれば中の変数名）")
                if name.lower() in by_hole:
                    raise ValueError(f"{where}.holes.{hole} が 2 度ある（大文字小文字は区別しない）")
                if not isinstance(values, list) or not values:
                    raise ValueError(f"{where}.holes.{hole} は受け付ける値の並び（[a, b]）で書く。空の並びは何も"
                                     f"受け付けないので、決定にならない")
                texts = []
                for value in values:
                    if value is None or isinstance(value, (bool, dict, list)):
                        raise ValueError(f"{where}.holes.{hole}: 値 {value!r} は文字列か数で書く")
                    texts.append(str(value))
                by_hole[name.lower()] = list(dict.fromkeys(texts))
            holes[str(routine)] = by_hole
            reasons[str(routine)] = why
        return cls(holes=holes, reasons=reasons, source=str(file))

    def for_routine(self, routine: str) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.holes.get(routine, {}).items()}

    def why(self, routine: str) -> str | None:
        return self.reasons.get(routine)


def conditional_compilation(path: str | Path | None) -> None:
    """#118: the source database's PLSQL_CCFLAGS and version, for the `$IF` the preprocessor resolves.

        conditionalCompilation:
          flags: {logger_debug: false, no_op: false}
          dbVersion: "19.0"

    Without the section a flag nobody declared is NULL, as in Oracle, and the version is 19.0.
    """
    from .conditional import Settings, configure

    data = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}) if path else {}
    section = data.get("conditionalCompilation") or {}
    unknown = set(section) - {"flags", "dbVersion"}
    if unknown:
        raise ValueError(f"{path}: conditionalCompilation に知らないキー {sorted(unknown)}")
    flags = {str(k).lower(): (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
             for k, v in (section.get("flags") or {}).items()}
    major, _, minor = str(section.get("dbVersion", "19.0")).partition(".")
    configure(Settings(flags=flags, version=(int(major), int(minor or 0))))


@dataclass
class DynamicDdl:
    """routine の中の DDL（`EXECUTE IMMEDIATE 'CREATE TABLE ...'`）を移行先で実行しないと決めた routine（#52）。

    ScalarDB はトランザクションの中で DDL を流さず、スキーマは Schema Loader が持つ。作ってすぐ消す一時表の
    ように、データに何も残さない DDL なら省いても意味は変わらない——それを確かめて決めるのは人である。書いて
    いない routine の DDL は、生成器が理由つきで断る。
    """

    omit: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "DynamicDdl":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        section = (data.get("ddl") or {}).get("omit") or {}
        for routine, why in section.items():
            if not str(why or "").strip():
                raise ValueError(f"{file}: ddl.omit.{routine} には理由が要る")
        return cls(omit={str(k): " ".join(str(v).split()) for k, v in section.items()}, source=str(file))

    def why(self, routine: str) -> str | None:
        return self.omit.get(routine)


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


@dataclass
class PackageState:
    """Package variables the caller carries (#46 / 2026-09-25): `packageState.carried.<package>: <reason>`.

    A package variable is session state. The generated Service is a singleton, so a field would be shared by
    every caller -- a different meaning. The one mechanical answer that keeps the meaning is to hand the
    variables to the caller: every routine of the package that reads or writes one (directly, or through a
    routine that does) takes it as an IN OUT argument and returns it in its result record. The caller keeps
    the value between calls, which is what the session did. Recorded per package, with the reason.
    """

    carried: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "PackageState":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        section = (data.get("packageState") or {}).get("carried") or {}
        return cls(carried={str(k).lower(): str(v).strip() for k, v in section.items()}, source=str(file))

    def decided(self, package: str) -> bool:
        return package.lower() in self.carried

    def why(self, package: str) -> str | None:
        return self.carried.get(package.lower())


@dataclass
class Constraints:
    """Tables whose CHECK / FOREIGN KEY constraints the writer guards (#50, 2026-09-25, the user's decision:
    "表ごとに決めて guard を生成"): `constraints.enforce.<table>: <reason>`.

    ScalarDB has neither constraint, so a write Oracle refused (ORA-02290 / ORA-02291) goes through. For a table
    recorded here the generated code evaluates each CHECK on the values it is about to write and reads the parent
    row of each FOREIGN KEY before writing, raising the Oracle error code when the constraint would have fired.
    A table not recorded is left to the application, and the write says so (`CONSTRAINT_UNDECIDED`). Per table,
    because that is where the constraint lives and where the question "who validates this now" is answered.
    """

    enforce: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "Constraints":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        section = (data.get("constraints") or {}).get("enforce") or {}
        return cls(enforce={str(k).lower(): str(v).strip() for k, v in section.items()}, source=str(file))

    def decided(self, table: str) -> bool:
        return table.lower() in self.enforce

    def why(self, table: str) -> str | None:
        return self.enforce.get(table.lower())


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


@dataclass
class NlsSettings:
    """The NLS settings of the source database's sessions (#157): `nls:` in limits.yaml, one for the project.

        nls:
          reason: ログオン trigger とクライアントの NLS_LANG を確認した（AMERICAN_AMERICA.AL32UTF8）
          dateLanguage: AMERICAN          # MON / MONTH / DAY / DY / AM / AD の言語。AMERICAN / ENGLISH / JAPANESE
          territory: AMERICA              # D（週の始まり）、DS / DL / TS、下の既定。AMERICA / JAPAN
          dateFormat: DD-MON-RR           # 書かなければ地域の既定
          timestampFormat: DD-MON-RR HH.MI.SSXFF AM
          timestampTzFormat: DD-MON-RR HH.MI.SSXFF AM TZR
          numericCharacters: ".,"         # D と G
          currency: "$"                   # L
          isoCurrency: AMERICA            # C（地域の名前で書く。AMERICA は USD）
          dualCurrency: "$"               # U

    Only `reason` is required: what is left out is what ALTER SESSION SET NLS_TERRITORY sets, as in Oracle. With the
    section the generated code writes and reads text under these settings (`Plsql.useNls`), and SEM-008 / SEM-012 no
    longer hold a routine at REVIEW for what they decide. Without it the runtime writes Oracle's defaults (AMERICAN /
    AMERICA) and the rules keep asking -- a match on the comparison database says nothing about a source whose
    sessions set them otherwise.
    """

    reason: str | None = None
    date_language: str = "AMERICAN"
    territory: str = "AMERICA"
    date_format: str | None = None
    timestamp_format: str | None = None
    timestamp_tz_format: str | None = None
    numeric_characters: str | None = None
    currency: str | None = None
    iso_currency: str | None = None
    dual_currency: str | None = None
    source: str | None = None

    KEYS = {"reason": "reason", "dateLanguage": "date_language", "territory": "territory",
            "dateFormat": "date_format", "timestampFormat": "timestamp_format",
            "timestampTzFormat": "timestamp_tz_format", "numericCharacters": "numeric_characters",
            "currency": "currency", "isoCurrency": "iso_currency", "dualCurrency": "dual_currency"}
    # what the runtime knows (`Nls`): the names and formats measured on Oracle 26ai
    LANGUAGES = ("AMERICAN", "ENGLISH", "JAPANESE")
    TERRITORIES = ("AMERICA", "JAPAN")
    ISO_CURRENCIES = ("AMERICA", "JAPAN", "GERMANY", "FRANCE", "ITALY", "SPAIN", "UNITED KINGDOM", "CHINA", "KOREA",
                      "CANADA")

    @classmethod
    def load(cls, path: str | Path | None) -> "NlsSettings":
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            raise FileNotFoundError(f"{file} が無い")
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        section = data.get("nls")
        if not section:
            return cls()
        values = {cls.KEYS[k]: (str(v).strip() if k in ("reason", "dateLanguage", "territory", "isoCurrency")
                                else str(v))
                  for k, v in section.items() if k in cls.KEYS and v is not None}
        for name in ("date_language", "territory", "iso_currency"):
            if name in values:
                values[name] = values[name].upper()
        return cls(**values, source=str(file))

    @property
    def decided(self) -> bool:
        return bool(self.reason)

    def problems(self) -> list[str]:
        """What the runtime would refuse, or read otherwise than the person meant. Empty when nothing is wrong."""
        from .formats import check_date

        out = []
        if not self.reason:
            out.append("nls.reason が無い。移行元のセッションの設定をどう確かめたかを書く")
        if self.date_language not in self.LANGUAGES:
            out.append(f"nls.dateLanguage {self.date_language!r} はランタイムが知らない（書けるのは {', '.join(self.LANGUAGES)}）")
        if self.territory not in self.TERRITORIES:
            out.append(f"nls.territory {self.territory!r} はランタイムが知らない（書けるのは {', '.join(self.TERRITORIES)}）")
        if self.iso_currency is not None and self.iso_currency not in self.ISO_CURRENCIES:
            out.append(f"nls.isoCurrency {self.iso_currency!r} は地域の名前で書く（{', '.join(self.ISO_CURRENCIES)}）")
        characters = self.numeric_characters
        if characters is not None and (len(characters) != 2 or characters[0] == characters[1]
                                       or any(c.isdigit() or c in "+-<>" for c in characters)):
            out.append(f"nls.numericCharacters {characters!r} は小数点と桁区切りの 2 文字（違う文字で、数字でも符号でもない）")
        for key, value in (("currency", self.currency), ("dualCurrency", self.dual_currency)):
            if value is not None and not 1 <= len(value) <= 10:
                out.append(f"nls.{key} は 1〜10 文字")
        for key, value in (("dateFormat", self.date_format), ("timestampFormat", self.timestamp_format),
                           ("timestampTzFormat", self.timestamp_tz_format)):
            if value is None:
                continue
            check = check_date(value)
            if check.state != "ok":
                out.append(f"nls.{key} {value!r} は日付の書式として読めない（{check.detail}）")
            elif key == "dateFormat" and check.elements & {"FF", "X", "TZH", "TZM", "TZR", "TZD"}:
                out.append(f"nls.dateFormat {value!r} に DATE が持たない要素がある（FF / X / TZ*）")
        return out

    def java(self) -> str:
        """The `Nls` the generated code hands to `Plsql.useNls`."""
        out = f"Nls.of({_java_string(self.date_language)}, {_java_string(self.territory)})"
        for method, value in (("withDateFormat", self.date_format), ("withTimestampFormat", self.timestamp_format),
                              ("withTimestampTzFormat", self.timestamp_tz_format),
                              ("withNumericCharacters", self.numeric_characters), ("withCurrency", self.currency),
                              ("withIsoCurrency", self.iso_currency), ("withDualCurrency", self.dual_currency)):
            if value is not None:
                out += f".{method}({_java_string(value)})"
        return out


def _java_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- checking the file (#145) -------------------------------------------------------------------------------
#
# Each loader above reads its own section and ignores the rest, so a misspelt section (`rowlocks:`), a routine id
# that is not in the source (`pkg_shop.reserv`) or a decision with no reason (`pkg_shop.reserve:` -> the string
# "None") all went through with exit 0: the decision looked recorded and changed nothing. `validate` runs once,
# before the loaders, and names what is wrong; `unknown_routines` needs the parsed program and runs after the parse.

class LimitsError(ValueError):
    """limits.yaml cannot be used as it is. The message says where and why, for the person who wrote it."""


# section -> its keys (None: the keys are names the user chooses -- routine ids, link names)
SECTIONS: dict[str, set[str] | None] = {
    "scanRows": {"default", "routines", "notLimited"},
    "rowLocks": {"optimistic"},
    "transactions": {"perIteration", "separate", "callerBoundary"},
    "dynamicTables": None,
    "dynamicSql": None,
    "ddl": {"omit"},
    "packageState": {"carried"},
    "constraints": {"enforce"},
    "dbLinks": None,
    "conditionalCompilation": {"flags", "dbVersion"},
    "nls": set(NlsSettings.KEYS),
}
# the places whose value is the reason for the decision: an empty one is a decision nobody explained
REASONED = [("scanRows", "notLimited"), ("rowLocks", "optimistic"), ("transactions", "perIteration"),
            ("transactions", "separate"), ("transactions", "callerBoundary"), ("ddl", "omit"),
            ("packageState", "carried"), ("constraints", "enforce")]
# the places keyed by routine id
ROUTINE_KEYED = [("scanRows", "routines"), ("scanRows", "notLimited"), ("rowLocks", "optimistic"),
                 ("transactions", "perIteration"), ("transactions", "separate"),
                 ("transactions", "callerBoundary"), ("ddl", "omit"), ("dynamicTables", None),
                 ("dynamicSql", None)]


def _read(path: str | Path) -> dict:
    file = Path(path)
    if not file.exists():
        raise LimitsError(f"{file} が無い。まだ決定が無ければ --limits を外して回す（決めたら作って渡す）")
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" {mark.line + 1} 行目" if mark is not None else ""
        raise LimitsError(f"{file}{where}: YAML として読めない（{getattr(error, 'problem', None) or error}）") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise LimitsError(f"{file}: 最上位は節の名前の対応（scanRows: … など）でなければならない")
    return data


def validate(path: str | Path) -> None:
    """Refuse a limits.yaml the generator would misread, with the reason. Raises LimitsError."""
    file = Path(path)
    data = _read(file)
    unknown = sorted(str(k) for k in data if k not in SECTIONS)
    if unknown:
        hints = [f"{k}（{_near(k, SECTIONS)} のこと？）" if _near(k, SECTIONS) else k for k in unknown]
        raise LimitsError(f"{file}: 知らない節 {', '.join(hints)}。生成器が読むのは {', '.join(SECTIONS)} だけで、"
                          f"ほかの節は黙って無視される")
    for section, keys in SECTIONS.items():
        value = data.get(section)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise LimitsError(f"{file}: {section} は対応（キー: 値）で書く")
        if keys is None:
            continue
        extra = sorted(str(k) for k in value if k not in keys)
        if extra:
            raise LimitsError(f"{file}: {section} に知らないキー {extra}。書けるのは {sorted(keys)}")
        if section == "nls":
            continue   # values, not mappings: checked below
        for key in keys - {"default", "dbVersion"}:
            if value.get(key) is not None and not isinstance(value[key], dict):
                raise LimitsError(f"{file}: {section}.{key} は対応（id: 値）で書く")
    for section, key in REASONED:
        for name, why in ((data.get(section) or {}).get(key) or {}).items():
            if not str(why if why is not None else "").strip():
                raise LimitsError(f"{file}: {section}.{key}.{name} に理由が無い。決定の理由を値として書く"
                                  f"（`{name}: <誰がなぜ決めたか>`）")
    # the loaders' own checks (numbers, overlaps, dbLinks' namespace), turned into the same kind of error
    try:
        Limits.load(file)
        Boundaries.load(file)
        DbLinks.load(file)
        DynamicDdl.load(file)
        DynamicSqlHoles.load(file)
    except (ValueError, TypeError) as error:
        raise LimitsError(f"{file}: {error}") from None
    if data.get("nls") is not None:
        problems = NlsSettings.load(file).problems()
        if problems:
            raise LimitsError(f"{file}: " + "。".join(problems))
    cc = data.get("conditionalCompilation") or {}
    major, _, minor = str(cc.get("dbVersion", "19.0")).partition(".")
    if not major.isdigit() or (minor and not minor.isdigit()):
        raise LimitsError(f"{file}: conditionalCompilation.dbVersion は \"19.0\" の形で書く（{cc.get('dbVersion')!r}）")


def unknown_routines(path: str | Path, routine_ids) -> list[str]:
    """Routine ids limits.yaml decides for that the source does not have, each with the nearest real id.

    Such a decision changes nothing: the id is compared as written. It is usually a typo or a routine renamed
    since, and the routine it meant is left undecided without anybody noticing.
    """
    data = _read(path)
    known = set(routine_ids)
    out = []
    for section, key in ROUTINE_KEYED:
        entries = data.get(section) or {}
        if key is not None:
            entries = entries.get(key) or {}
        where = f"{section}.{key}" if key else section
        for name in entries:
            if str(name) not in known:
                near = _near(str(name), known)
                out.append(f"{where}.{name}" + (f"（{near} のこと？）" if near else ""))
    return out


def _near(word: str, candidates) -> str | None:
    from difflib import get_close_matches

    lowered = {str(c).lower(): str(c) for c in candidates}
    found = get_close_matches(str(word).lower(), list(lowered), n=1, cutoff=0.75)
    return lowered[found[0]] if found else None
