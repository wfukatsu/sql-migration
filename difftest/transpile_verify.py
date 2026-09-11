#!/usr/bin/env python3
"""sql-transpile スキルの実行検証。

テスト用 SQL (skills/sql-transpile/examples/<source>.sql) の各文を、
  - 変換元のデータベースでそのまま実行した結果を「正解」とし、
  - 変換先のデータベースで「スキルの変換結果」と「素の sqlglot.transpile の結果」を実行して
結果集合を突き合わせる。スキルの判定 (OK / WARN / ERROR) が実際の動作と合っているかを見る。

判定（文 × 変換先ごと）:
  正しく変換   スキルが OK。変換後 SQL の結果が変換元と一致
  見逃し       スキルが OK。だが変換後 SQL が失敗するか結果が違う          ← スキルの不具合
  警告が的中   スキルが WARN。変換後 SQL が失敗するか結果が違う
  警告は杞憂   スキルが WARN。変換後 SQL の結果は一致
  正しく拒否   スキルが ERROR。素の変換も失敗するか結果が違う
  過剰な拒否   スキルが ERROR。だが素の変換は結果まで一致する

テスト用 SQL の注釈（文の直前のコメント行）:
  -- @tests          最初のテスト文に付ける。これより前は表とデータの準備
  -- @note: 説明      レポートに出す説明
  -- @check: SELECT  DML の後に実行して結果を比べる問合せ（変換元の方言で書く）

接続先（difftest の Docker Compose と同じ）:
  Oracle     localhost:1521/FREEPDB1   (docker compose --profile oracle up -d source-oracle)
  PostgreSQL localhost:15432/source    スキーマ transpile_verify を作り直して使う
  MySQL      localhost:13306/verify    使い捨てコンテナ (README 参照)
  DuckDB     プロセス内のインメモリ

使い方 (リポジトリルートから):
  .venv/bin/python difftest/transpile_verify.py                    # 全ペア
  .venv/bin/python difftest/transpile_verify.py --source oracle    # 変換元を絞る
  .venv/bin/python difftest/transpile_verify.py --pair oracle:postgres
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import json
import logging
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "sql-transpile"
sys.path.insert(0, str(SKILL / "scripts"))
# scalardb_migrate は import しない。同梱コピーと "scalardb" 方言の登録が衝突するため
import generic  # noqa: E402
from _scalardb.converter import _split_statements  # noqa: E402

logging.getLogger("sqlglot").setLevel(logging.CRITICAL)

EXAMPLES = SKILL / "examples"
TARGETS = {"oracle": ["postgres", "mysql", "duckdb"],
           "postgres": ["oracle", "mysql", "duckdb"],
           "mysql": ["oracle", "postgres", "duckdb"]}
CLASSES = ["正しく変換", "警告が的中", "正しく拒否", "警告は杞憂", "過剰な拒否", "見逃し"]


# ---------------------------------------------------------------------------------------------
# データベース
# ---------------------------------------------------------------------------------------------
def first_line(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:160]


class DB:
    dialect = ""

    def begin(self): pass
    def commit(self): self.con.commit()
    def rollback(self):
        try:
            self.con.rollback()
        except Exception:  # noqa: BLE001
            pass

    def execute(self, sql: str):
        cur = self.con.cursor()
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()] if cur.description else None

    def undo_ddl(self, sql: str):
        """変換先で実行した DDL を取り消す（トランザクションで戻せない方言用）。"""


class Oracle(DB):
    dialect = "oracle"

    def __init__(self):
        import oracledb
        self.con = oracledb.connect(user="source", password="source", dsn="localhost:1521/FREEPDB1")

    def reset(self, tables, sequences):
        cur = self.con.cursor()
        for t in tables:
            try: cur.execute(f"DROP TABLE {t} CASCADE CONSTRAINTS PURGE")
            except Exception: pass  # noqa: E701
        for s in sequences:
            try: cur.execute(f"DROP SEQUENCE {s}")
            except Exception: pass  # noqa: E701

    def undo_ddl(self, sql):
        name, kind = created_object(sql, self.dialect)
        if name:
            self.reset([name] if kind == "TABLE" else [], [name] if kind == "SEQUENCE" else [])


class Postgres(DB):
    dialect = "postgres"
    SCHEMA = "transpile_verify"

    def __init__(self):
        import psycopg
        self.con = psycopg.connect("postgresql://postgres:postgres@localhost:15432/source")

    def reset(self, tables, sequences):
        cur = self.con.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
        cur.execute(f"CREATE SCHEMA {self.SCHEMA}")
        cur.execute(f"SET search_path TO {self.SCHEMA}")
        self.con.commit()
    # DDL もトランザクションで戻るので undo_ddl は不要


class MySQL(DB):
    dialect = "mysql"

    def __init__(self):
        import pymysql
        self.con = pymysql.connect(host="127.0.0.1", port=13306, user="root", password="verify",
                                   database="verify", autocommit=False)

    def reset(self, tables, sequences):
        cur = self.con.cursor()
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for t in tables:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        self.con.commit()

    def undo_ddl(self, sql):
        name, kind = created_object(sql, self.dialect)
        if name and kind == "TABLE":
            self.reset([name], [])


class DuckDB(DB):
    dialect = "duckdb"

    def __init__(self):
        import duckdb
        self._duckdb = duckdb
        self.con = duckdb.connect(":memory:")
        self.in_txn = False

    def reset(self, tables, sequences):
        self.con.close()
        self.con = self._duckdb.connect(":memory:")
        self.in_txn = False

    def begin(self):
        self.con.execute("BEGIN TRANSACTION")
        self.in_txn = True

    def commit(self):
        if self.in_txn:
            self.con.execute("COMMIT")
            self.in_txn = False

    def rollback(self):
        if self.in_txn:
            try: self.con.execute("ROLLBACK")
            except Exception: pass  # noqa: E701
            self.in_txn = False

    def execute(self, sql):
        res = self.con.execute(sql)
        return [tuple(r) for r in res.fetchall()] if res.description else None


FACTORIES = {"oracle": Oracle, "postgres": Postgres, "mysql": MySQL, "duckdb": DuckDB}


def created_object(sql: str, dialect: str) -> tuple[str | None, str]:
    try:
        node = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return None, ""
    if isinstance(node, exp.Create):
        t = node.find(exp.Table)
        return (t.name if t else None), (node.args.get("kind") or "").upper()
    return None, ""


# ---------------------------------------------------------------------------------------------
# テスト用 SQL の読み込み
# ---------------------------------------------------------------------------------------------
@dataclass
class Stmt:
    index: int
    text: str           # 注釈コメントを含む原文（利用者がスキルに渡す形）
    body: str           # 注釈を除いた実行可能な SQL
    note: str
    check: str | None
    setup: bool
    kind: str           # QUERY | DML | DDL


def parse_corpus(path: Path, dialect: str) -> list[Stmt]:
    out, in_tests = [], False
    for i, text in enumerate(_split_statements(path.read_text(encoding="utf-8"), dialect), start=1):
        lines = text.strip().splitlines()
        comments = [ln.strip() for ln in lines if ln.strip().startswith("--")]
        body = "\n".join(ln for ln in lines if not ln.strip().startswith("--")).strip().rstrip(";").strip()
        if not body:
            continue
        if any(re.fullmatch(r"--\s*@tests", c) for c in comments):
            in_tests = True
        note = next((re.sub(r"^--\s*@note:\s*", "", c) for c in comments if re.match(r"--\s*@note:", c)), "")
        check = next((re.sub(r"^--\s*@check:\s*", "", c) for c in comments if re.match(r"--\s*@check:", c)), None)
        out.append(Stmt(i, text.strip(), body, note, check, not in_tests, statement_kind(body, dialect)))
    return out


def statement_kind(sql: str, dialect: str) -> str:
    try:
        node = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return "QUERY"
    if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        return "DML"
    if isinstance(node, (exp.Create, exp.Drop, exp.Alter)):
        return "DDL"
    return "QUERY"


def is_ordered(sql: str, dialect: str) -> bool:
    try:
        node = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return False
    return not isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge)) and bool(node.args.get("order"))


# ---------------------------------------------------------------------------------------------
# 実行と比較
# ---------------------------------------------------------------------------------------------
def norm(v):
    """Oracle / PostgreSQL / MySQL / DuckDB の値を比べられる形にそろえる。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, decimal.Decimal):
        v = float(v)
    if isinstance(v, float):
        v = round(v, 6)
        return int(v) if v.is_integer() else v
    if isinstance(v, datetime.datetime):
        return v.date().isoformat() if v.time() == datetime.time(0) else v.isoformat(sep=" ")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, str):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?", v.strip())
        if m:   # 日時を文字列で返す方言がある（MySQL の DATE_ADD など）
            return m.group(1) if m.group(2) == "00:00:00" else f"{m.group(1)} {m.group(2)}"
        return v
    if v is None or isinstance(v, int):
        return v
    return str(v)


@dataclass
class Run:
    ok: bool
    rows: list | None = None
    error: str = ""


def run(db: DB, sql: str, check: str | None, kind: str, keep_ddl: bool) -> Run:
    """1 文を実行する。DML は変更後に check を実行して結果を取り、最後に必ず巻き戻す。"""
    db.begin()
    try:
        rows = db.execute(sql)
        if check is not None:
            rows = db.execute(check)
        return Run(True, rows)
    except Exception as e:  # noqa: BLE001
        return Run(False, error=first_line(e))
    finally:
        if kind == "DDL" and keep_ddl:
            try:
                db.commit()   # 変換元の DDL は後続の文が使う（例: CREATE SEQUENCE の後の nextval）
            except Exception:  # noqa: BLE001
                db.rollback()
        else:
            db.rollback()
        if kind == "DDL" and not keep_ddl:
            db.undo_ddl(sql)


def same(a: Run, b: Run, ordered: bool) -> tuple[bool, str]:
    ra = [tuple(norm(x) for x in r) for r in (a.rows or [])]
    rb = [tuple(norm(x) for x in r) for r in (b.rows or [])]
    if not ordered:
        ra, rb = sorted(ra, key=repr), sorted(rb, key=repr)
    if ra == rb:
        return True, ""
    if len(ra) != len(rb):
        return False, f"行数が違う: 変換元 {len(ra)} 行 / 変換先 {len(rb)} 行"
    i = next(i for i, (x, y) in enumerate(zip(ra, rb)) if x != y)
    return False, f"{i + 1} 行目: 変換元 {ra[i]} / 変換先 {rb[i]}"


def state(src: Run, tgt: Run, ordered: bool) -> tuple[str, str]:
    if not tgt.ok:
        return "FAIL", tgt.error
    ok, why = same(src, tgt, ordered)
    return ("MATCH", "") if ok else ("MISMATCH", why)


def classify(verdict: str, skill: str | None, raw: str) -> str:
    if verdict == "OK":
        return "正しく変換" if skill == "MATCH" else "見逃し"
    if verdict == "WARN":
        return "警告は杞憂" if skill == "MATCH" else "警告が的中"
    return "過剰な拒否" if raw == "MATCH" else "正しく拒否"


# ---------------------------------------------------------------------------------------------
# 1 ペアの検証
# ---------------------------------------------------------------------------------------------
@dataclass
class Case:
    index: int
    note: str
    sql: str
    verdict: str
    codes: list[str]
    skill_sql: str
    raw_sql: str
    skill_state: str | None
    skill_detail: str
    raw_state: str
    raw_detail: str
    cls: str
    comment_verdict: str = ""   # 注釈コメントを付けたままスキルに渡したときの判定
    kind: str = ""              # QUERY | DML | DDL


@dataclass
class PairResult:
    source: str
    target: str
    setup_problems: list[str] = field(default_factory=list)
    setup_conversion: list[str] = field(default_factory=list)   # スキルで変換した準備の文の失敗
    source_errors: list[str] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)


def convert_skill(sql: str, src: str, tgt: str):
    r = generic.convert_statement(sql, src, tgt)
    return r.status, (r.converted[0] if r.converted else ""), sorted({i.code for i in r.issues if i.severity != "INFO"})


def convert_raw(sql: str, src: str, tgt: str) -> str:
    try:
        return sqlglot.transpile(sql, read=src, write=tgt)[0]
    except Exception:  # noqa: BLE001
        return ""


def verify_pair(src_db: DB, tgt_db: DB, stmts: list[Stmt]) -> PairResult:
    src, tgt = src_db.dialect, tgt_db.dialect
    res = PairResult(src, tgt)
    tables = sorted({n for s in stmts if s.kind == "DDL"
                     for n, k in [created_object(s.body, src)] if n and k == "TABLE"})
    seqs = sorted({n for s in stmts if s.kind == "DDL"
                   for n, k in [created_object(s.body, src)] if n and k == "SEQUENCE"})
    src_db.reset(tables, seqs)
    tgt_db.reset(tables, seqs)

    # 準備（変換元）: そのまま実行する
    for s in (x for x in stmts if x.setup):
        try:
            src_db.execute(s.body); src_db.commit()  # noqa: E702
        except Exception as e:  # noqa: BLE001
            src_db.rollback()
            res.setup_problems.append(f"#{s.index} 変換元で失敗: {first_line(e)}")

    # 準備の変換そのものを測る: スキルで変換した DDL とデータ投入が変換先で通るか
    for s in (x for x in stmts if x.setup):
        _, sql, codes = convert_skill(s.body, src, tgt)
        if not sql:
            res.setup_conversion.append(f"#{s.index} スキルが変換できない ({', '.join(codes)})")
            continue
        try:
            tgt_db.execute(sql); tgt_db.commit()  # noqa: E702
        except Exception as e:  # noqa: BLE001
            tgt_db.rollback()
            res.setup_conversion.append(f"#{s.index} 変換先で失敗: {first_line(e)} | {sql[:100]}")

    # 準備（変換先）: 自前のテスト用 SQL があれば、その準備部分で表とデータをそろえる。
    # 3 方言のテスト用 SQL は同じ表とデータなので、準備の変換の不具合がテスト文の評価に波及しない
    native = EXAMPLES / f"{tgt}.sql"
    if native.exists():
        tgt_db.reset(tables, seqs)
        for s in (x for x in parse_corpus(native, tgt) if x.setup):
            try:
                tgt_db.execute(s.body); tgt_db.commit()  # noqa: E702
            except Exception as e:  # noqa: BLE001
                tgt_db.rollback()
                res.setup_problems.append(f"#{s.index} 変換先の自前の準備で失敗: {first_line(e)}")

    for s in (x for x in stmts if not x.setup):
        ordered = is_ordered(s.check or s.body, src)
        truth = run(src_db, s.body, s.check, s.kind, keep_ddl=True)
        if not truth.ok:
            res.source_errors.append(f"#{s.index} {s.note}: {truth.error}")
            continue

        verdict, skill_sql, codes = convert_skill(s.body, src, tgt)
        raw_sql = convert_raw(s.body, src, tgt)
        raw_check = convert_raw(s.check, src, tgt) if s.check else None
        raw = run(tgt_db, raw_sql, raw_check, s.kind, keep_ddl=False) if raw_sql else Run(False, error="生成できない")
        raw_state, raw_detail = state(truth, raw, ordered)

        skill_state, skill_detail = None, ""
        if skill_sql:
            skill_check = None
            if s.check:
                _, skill_check, _ = convert_skill(s.check, src, tgt)
                skill_check = skill_check or raw_check
            skill_state, skill_detail = state(truth, run(tgt_db, skill_sql, skill_check, s.kind, keep_ddl=False), ordered)

        comment_verdict, _, _ = convert_skill(s.text, src, tgt)
        res.cases.append(Case(s.index, s.note, s.body, verdict, codes, skill_sql, raw_sql, skill_state,
                              skill_detail, raw_state, raw_detail, classify(verdict, skill_state, raw_state),
                              comment_verdict, kind=s.kind))
    return res


def scalardb_rates() -> dict[str, dict]:
    """ScalarDB を変換先にしたときの変換率（静的）。同梱コピーの登録衝突を避けて別プロセスで動かす。"""
    out = {}
    for src in TARGETS:
        p = subprocess.run([sys.executable, str(SKILL / "scripts" / "transpile.py"), str(EXAMPLES / f"{src}.sql"),
                            "--source", src, "--target", "scalardb"], capture_output=True, text=True, cwd=ROOT)
        kv = dict(ln.split("=", 1) for ln in p.stdout.strip().splitlines() if re.fullmatch(r"[A-Z]+=\S+", ln))
        out[src] = {"total": int(kv.get("TOTAL", 0)), "converted": int(kv.get("CONVERTED", 0)),
                    "rate": float(kv.get("RATE", 0))}
    return out


# ---------------------------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------------------------
def md_cell(s: str, n: int = 110) -> str:
    s = s.replace("\n", " ").replace("|", "\\|")
    return s[:n] + ("…" if len(s) > n else "")


def render(results: list[PairResult], sdb: dict, skipped: list[str]) -> str:
    L = ["# sql-transpile 実行検証レポート", ""]
    if skipped:
        L += [f"- 実行できなかったペア: {', '.join(skipped)}", ""]
    L += ["## ペアごとの結果", "",
          "| 変換元 → 変換先 | 文数 | 変換率 | " + " | ".join(CLASSES) + " | 素の SQLGlot で結果が黙って変わる文 |",
          "|---|---|---|" + "---|" * len(CLASSES) + "---|"]
    for r in results:
        c = Counter(x.cls for x in r.cases)
        n = max(len(r.cases), 1)
        conv = sum(1 for x in r.cases if x.verdict in ("OK", "WARN"))
        silent = sum(1 for x in r.cases if x.raw_state == "MISMATCH")
        L.append(f"| {r.source} → {r.target} | {n} | {conv / n * 100:.1f}% | "
                 + " | ".join(str(c.get(k, 0)) for k in CLASSES) + f" | {silent} |")

    L += ["", "## ScalarDB を変換先にしたときの変換率（静的）", "", "| 変換元 | 文数 | 変換できた文 | 変換率 |", "|---|---|---|---|"]
    for src, v in sdb.items():
        L.append(f"| {src} | {v['total']} | {v['converted']} | {v['rate']}% |")

    for title, cls in (("見逃し（スキルが OK と判定したのに動かない）", "見逃し"),
                       ("過剰な拒否（スキルが ERROR にしたが素の変換で動く）", "過剰な拒否"),
                       ("警告が的中", "警告が的中")):
        rows = [(r, x) for r in results for x in r.cases if x.cls == cls]
        if not rows:
            continue
        L += ["", f"## {title}", "", "| 変換元 → 変換先 | # | 何を確かめる文か | 変換後 SQL | 起きたこと |", "|---|---|---|---|---|"]
        for r, x in rows:
            what = x.skill_detail if x.skill_state else x.raw_detail
            if cls == "過剰な拒否" and x.kind == "DDL":
                what = "DDL が成功しただけ。意味が保たれたか（採番の自動化など）は未確認"
            shown = x.skill_sql or x.raw_sql
            L.append(f"| {r.source} → {r.target} | {x.index} | {md_cell(x.note, 50)} | `{md_cell(shown, 90)}` | {md_cell(what, 90)} |")

    flips = [(r, x) for r in results for x in r.cases if x.comment_verdict != x.verdict]
    if flips:
        L += ["", "## コメントがあると判定が変わる文", "",
              "注釈コメントを付けたままスキルに渡したときと、外したときで判定が違った文。",
              "", "| 変換元 → 変換先 | # | 注釈なし | 注釈あり | 注釈 |", "|---|---|---|---|---|"]
        for r, x in flips:
            L.append(f"| {r.source} → {r.target} | {x.index} | {x.verdict} | {x.comment_verdict} | {md_cell(x.note, 60)} |")

    conv_fail = [(r, p) for r in results for p in r.setup_conversion]
    if conv_fail:
        L += ["", "## 準備の文の変換（スキルで変換した DDL とデータ投入）", "",
              "テスト文の評価とは別に、準備の文をスキルで変換して変換先で実行した結果。", ""]
        L += [f"- {r.source} → {r.target} {p}" for r, p in conv_fail]
    for r in results:
        if r.setup_problems or r.source_errors:
            L += ["", f"## 準備の問題: {r.source} → {r.target}", ""]
            L += [f"- {p}" for p in r.setup_problems + r.source_errors]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="sql-transpile スキルを実データベースで検証する")
    ap.add_argument("--source", choices=list(TARGETS))
    ap.add_argument("--pair", help="変換元:変換先 (例 oracle:postgres)")
    ap.add_argument("--out-dir", default=str(ROOT / "out" / "transpile-verify"))
    args = ap.parse_args(argv)

    pairs = [(s, t) for s in TARGETS for t in TARGETS[s]]
    if args.source:
        pairs = [p for p in pairs if p[0] == args.source]
    if args.pair:
        pairs = [tuple(args.pair.split(":", 1))]

    dbs, skipped = {}, []
    for name in sorted({x for p in pairs for x in p}):
        try:
            dbs[name] = FACTORIES[name]()
        except Exception as e:  # noqa: BLE001
            print(f"{name} に接続できない: {first_line(e)}", file=sys.stderr)
    results = []
    for src, tgt in pairs:
        if src not in dbs or tgt not in dbs:
            skipped.append(f"{src}→{tgt}")
            continue
        stmts = parse_corpus(EXAMPLES / f"{src}.sql", src)
        r = verify_pair(dbs[src], dbs[tgt], stmts)
        results.append(r)
        c = Counter(x.cls for x in r.cases)
        print(f"{src:>8} → {tgt:<8} {len(r.cases):>3} 文  " + "  ".join(f"{k}:{c.get(k, 0)}" for k in CLASSES)
              + (f"  [準備の問題 {len(r.setup_problems) + len(r.source_errors)}]" if r.setup_problems or r.source_errors else "")
              + (f"  [準備の変換の失敗 {len(r.setup_conversion)}]" if r.setup_conversion else ""))

    sdb = scalardb_rates()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render(results, sdb, skipped), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(
        [{"source": r.source, "target": r.target, "setup_problems": r.setup_problems,
          "setup_conversion": r.setup_conversion,
          "source_errors": r.source_errors, "cases": [c.__dict__ for c in r.cases]} for r in results],
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nレポート: {out / 'report.md'}")
    misses = sum(1 for r in results for x in r.cases if x.cls == "見逃し")
    print(f"MISSES={misses}")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
