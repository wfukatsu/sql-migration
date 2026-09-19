"""任意の SQLGlot 方言どうしの変換パス。

素の ``sqlglot.transpile()`` は、変換先に無い構文を黙って出力へ通すことがある。
Oracle から PostgreSQL への実測例:

    WHERE ROWNUM <= 5                 -> WHERE ROWNUM <= 5          (そのまま通る)
    WHERE e.deptno = d.deptno(+)      -> WHERE e.deptno = d.deptno  (外部結合が内部結合に化ける)
    CONNECT BY PRIOR empno = mgr      -> そのまま通る
    VALUES (emp_seq.NEXTVAL)          -> そのまま通る

このモジュールは 1 文を次の順で処理する。

1. 解析         Source 方言で構文木にする
2. 変換元の検査 変換先に持ち込めない構文を構文木で拾う。コメントや文字列リテラルには反応しない
3. 前処理       SQLGlot が直さない構文を構文木の上で直す。外部結合の記号、ROWNUM、再帰 CTE、
                FROM dual、日付リテラル、整数除算、INTERVAL、DISTINCT ON、TRUNC の単位など
4. 生成         ErrorLevel.RAISE で生成し、SQLGlot が知っている非対応を例外にする
5. 変換先の検査 変換先に無い構文と、変換先の組み込み関数一覧に無い関数を拾う
6. 往復検証     生成結果が Target 方言として読み直せることを確かめる

変換先の能力は PostgreSQL 16、Oracle Database 23ai、MySQL 8.4、DuckDB 1.5 で実測して決めた。
それ以外の方言は公開文書に基づく。詳しくは references/dialect-notes.md。
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ErrorLevel, ParseError, TokenError, UnsupportedError
from sqlglot.tokens import TokenType
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.transforms import eliminate_distinct_on, eliminate_join_marks

from _scalardb.converter import Issue, Result, _bad_join_mark_rewrite, _flatten, _split_statements, _unparen

CATALOG_DIR = Path(__file__).resolve().parent / "catalogs"
FROM_KEY = "from_" if "from_" in exp.Select.arg_types else "from"

# ---------------------------------------------------------------------------------------------
# 変換先の能力
# ---------------------------------------------------------------------------------------------
# 能力の判定をする方言。これ以外の変換先では、能力が分からないので構文の有無で警告しない
KNOWN_TARGETS = {"postgres", "oracle", "mysql", "duckdb", "tsql", "sqlite", "snowflake", "bigquery"}
SEQUENCE_TARGETS = {"postgres", "oracle", "duckdb", "tsql", "snowflake"}      # CREATE SEQUENCE
SEQ_FUNC_TARGETS = {"postgres", "duckdb"}                                    # nextval('seq')
CONNECT_BY_TARGETS = {"oracle", "snowflake"}
CONNECT_BY_REWRITE_TARGETS = {"duckdb"}   # SQLGlot の生成器が単純な形だけ再帰 CTE に書き換える（実行して確認）
MERGE_TARGETS = {"postgres", "oracle", "duckdb", "tsql", "snowflake", "bigquery"}
MERGE_UNQUALIFIED_SET = {"postgres", "duckdb", "sqlite"}                     # UPDATE SET に表名修飾を書けない
ON_CONFLICT_TARGETS = {"postgres", "duckdb", "sqlite"}
RETURNING_TARGETS = {"postgres", "duckdb", "sqlite"}
SERIAL_TARGETS = {"postgres", "mysql"}                                       # MySQL は SERIAL を型の別名として持つ
AGG_FILTER_TARGETS = {"postgres", "duckdb", "sqlite", "oracle"}             # Oracle は 23ai で受け付けることを確認
CASE_SENSITIVE_TARGETS = {"postgres", "oracle", "duckdb", "sqlite", "snowflake"}
UPPER_FOLDING = {"oracle", "snowflake"}      # 引用符の無い識別子を大文字に畳む
LOWER_FOLDING = {"postgres", "redshift"}     # 引用符の無い識別子を小文字に畳む
CASELESS_QUOTE_SOURCES = {"mysql", "tsql"}   # 引用符が大文字小文字の区別に意味を持たない（バッククォート、角括弧）

# 引用符を外すと予約語として解釈されてしまう語。SQLGlot は PostgreSQL と Oracle の予約語を持たないので、
# Oracle の SQL 予約語と PostgreSQL の予約キーワードをここに持つ。
RESERVED = frozenset("""
ACCESS ADD ALL ALTER ANALYSE ANALYZE AND ANY ARRAY AS ASC ASYMMETRIC AUDIT AUTHORIZATION BETWEEN BINARY BOTH BY
CASE CAST CHAR CHECK CLUSTER COLLATE COLLATION COLUMN COLUMN_VALUE COMMENT COMPRESS CONCURRENTLY CONNECT CONSTRAINT
CREATE CROSS CURRENT CURRENT_CATALOG CURRENT_DATE CURRENT_ROLE CURRENT_SCHEMA CURRENT_TIME CURRENT_TIMESTAMP
CURRENT_USER DATE DECIMAL DEFAULT DEFERRABLE DELETE DESC DISTINCT DO DROP ELSE END EXCEPT EXCLUSIVE EXISTS FALSE
FETCH FILE FLOAT FOR FOREIGN FREEZE FROM FULL GRANT GROUP HAVING IDENTIFIED ILIKE IMMEDIATE IN INCREMENT INDEX
INITIAL INITIALLY INNER INSERT INTEGER INTERSECT INTO IS ISNULL JOIN LATERAL LEADING LEFT LEVEL LIKE LIMIT
LOCALTIME LOCALTIMESTAMP LOCK LONG MAXEXTENTS MINUS MLSLABEL MODE MODIFY NATURAL NESTED_TABLE_ID NOAUDIT
NOCOMPRESS NOT NOTNULL NOWAIT NULL NUMBER OF OFFLINE OFFSET ON ONLINE ONLY OPTION OR ORDER OUTER OVERLAPS PCTFREE
PLACING PRIMARY PRIOR PUBLIC RAW REFERENCES RENAME RESOURCE RETURNING REVOKE RIGHT ROW ROWID ROWNUM ROWS SELECT
SESSION SESSION_USER SET SHARE SIMILAR SIZE SMALLINT SOME START SUCCESSFUL SYMMETRIC SYNONYM SYSDATE SYSTEM_USER
TABLE TABLESAMPLE THEN TO TRAILING TRIGGER TRUE UID UNION UNIQUE UPDATE USER USING VALIDATE VALUES VARCHAR
VARCHAR2 VARIADIC VERBOSE VIEW WHEN WHENEVER WHERE WINDOW WITH
""".split())
SIMPLE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$#]*")

# Oracle の TRUNC(date, 書式) の書式から、標準の単位名への写像
TRUNC_UNITS = {"MM": "MONTH", "MON": "MONTH", "MONTH": "MONTH", "RM": "MONTH", "YYYY": "YEAR", "YEAR": "YEAR",
               "YY": "YEAR", "SYYYY": "YEAR", "Y": "YEAR", "DD": "DAY", "DDD": "DAY", "J": "DAY", "HH": "HOUR",
               "HH12": "HOUR", "HH24": "HOUR", "MI": "MINUTE", "Q": "QUARTER", "IW": "WEEK"}
INTERVAL_PLURALS = {"YEARS", "MONTHS", "WEEKS", "DAYS", "HOURS", "MINUTES", "SECONDS"}

# 関数一覧に載らない、構文として扱われる関数
KEYWORD_FUNCS = {"CAST", "TRY_CAST", "EXTRACT", "TRIM", "POSITION", "SUBSTRING", "OVERLAY", "CONVERT", "COALESCE",
                 "NULLIF", "EXISTS", "CASE", "INTERVAL", "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP",
                 "LOCALTIME", "LOCALTIMESTAMP", "ANY", "ALL", "SOME", "ROW", "ARRAY", "VALUES", "GREATEST", "LEAST",
                 # 字句としては名前と括弧に見える構文: ON CONFLICT(col)、Oracle の WITHIN GROUP (ORDER BY ...)
                 "CONFLICT", "GROUP", "WITHIN"}
# この語の直後の「名前 (」は関数呼び出しではなく、索引・制約・表の名前
NAME_BEFORE_PAREN = {"INDEX", "KEY", "UNIQUE", "CONSTRAINT", "REFERENCES", "INTO", "TABLE"}
# 関数一覧の無い変換先で使う予備の判定: 方言をまたいでも名前が変わらない関数
PORTABLE_FUNCS = {"COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NULLIF", "UPPER", "LOWER", "ABS", "ROUND",
                  "CAST", "TRIM", "LTRIM", "RTRIM", "LENGTH", "SUBSTRING", "SUBSTR", "REPLACE", "CONCAT", "MOD",
                  "POWER", "FLOOR", "CEIL", "CEILING", "SQRT", "EXP", "LN", "SIGN", "GREATEST", "LEAST", "EXTRACT",
                  "CURRENT_DATE", "CURRENT_TIMESTAMP", "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD",
                  "FIRST_VALUE", "LAST_VALUE", "NTILE", "EXISTS"}

DATE_LITERAL = re.compile(r"\d{4}-\d{2}-\d{2}")
TIMESTAMP_LITERAL = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
LETTERS = re.compile(r"[A-Za-z]")


# FETCH FIRST n ROWS WITH TIES をそのまま書ける変換先
WITH_TIES_TARGETS = {"oracle", "postgres", "tsql", "scalardb"}


def _add(issues: list[Issue], severity: str, code: str, message: str) -> None:
    """同じ重要度とコードの指摘は 1 件にまとめる。"""
    if not any(i.severity == severity and i.code == code for i in issues):
        issues.append(Issue(severity, code, message))


def _generator(dialect: str):
    return Dialect.get_or_raise(dialect).generator_class


# ---------------------------------------------------------------------------------------------
# 2. 変換元の検査（構文木で判定するので、コメント・文字列リテラル・引用符付きの識別子に反応しない）
# ---------------------------------------------------------------------------------------------
def _simple_connect_by(node: exp.Expression) -> bool:
    """SQLGlot の再帰 CTE への書き換えが正しく動く形か。

    DuckDB で実行して確かめた。SYS_CONNECT_BY_PATH・CONNECT_BY_ISLEAF・ORDER SIBLINGS BY・結合・FROM dual は
    失敗し、NOCYCLE は循環を検出しない。WHERE は Oracle が階層を作った後に適用するので、意味が保たれるか確かめられていない。
    """
    connects = list(node.find_all(exp.Connect))
    if len(connects) != 1:
        return False
    sel = connects[0].parent
    if not isinstance(sel, exp.Select) or connects[0].args.get("nocycle"):
        return False
    if sel.args.get("joins") or sel.args.get("where"):
        return False
    f = sel.args.get(FROM_KEY)
    if f is None or not isinstance(f.this, exp.Table) or f.this.name.upper() == "DUAL":
        return False
    order = sel.args.get("order")
    if order is not None and order.args.get("siblings"):
        return False
    if any(fn.name.upper() == "SYS_CONNECT_BY_PATH" for fn in node.find_all(exp.Anonymous)):
        return False
    return not any(c.name.upper() in ("CONNECT_BY_ISLEAF", "CONNECT_BY_ISCYCLE") for c in node.find_all(exp.Column))


def _check_source(node: exp.Expression, source: str, target: str, issues: list[Issue]) -> None:
    if node.find(exp.Connect) is not None and target not in CONNECT_BY_TARGETS:
        if target in CONNECT_BY_REWRITE_TARGETS and _simple_connect_by(node):
            _add(issues, "INFO", "CONNECT_BY", "階層問合せを SQLGlot が再帰 CTE に書き換えた（1 表の START WITH と CONNECT BY PRIOR だけの形）")
        else:
            _add(issues, "ERROR", "CONNECT_BY", "階層問合せ（CONNECT BY）。再帰 CTE に書き換える")

    # WITH TIES は n 番目と同順位の行をすべて返す。FETCH 句を持たない方言へは LIMIT n になり、同順位の行が黙って落ちる
    if target not in WITH_TIES_TARGETS:
        for fetch in node.find_all(exp.Fetch):
            options = fetch.args.get("limit_options")
            if options is not None and options.args.get("with_ties"):
                _add(issues, "ERROR", "WITH_TIES",
                     "FETCH ... WITH TIES。LIMIT n に置き換えると n 番目と同順位の行が落ちる。"
                     "RANK() OVER (ORDER BY ...) <= n で絞る形に書き直す")

    if source == "oracle" and target != "oracle":
        for col in node.find_all(exp.Column):
            if isinstance(col.this, exp.Identifier) and col.this.quoted:
                continue
            name = col.name.upper()
            if name == "ROWID" and not col.table:
                _add(issues, "ERROR", "ROWID", "Oracle の疑似列 ROWID。主キーで行を特定する形に書き換える")
            elif name in ("NEXTVAL", "CURRVAL") and col.table:
                _add(issues, "ERROR", "SEQUENCE",
                     f"シーケンス {col.table}.{name}。変換先の採番機能か、アプリケーション側の採番に置き換える")
        for w in node.find_all(exp.Window):
            if str(w.args.get("over") or "").upper() == "KEEP":
                _add(issues, "ERROR", "KEEP", "Oracle 固有の集約修飾 KEEP。ウィンドウ関数に書き換える")
        for ident in node.find_all(exp.Identifier):
            if ident.name.upper().startswith("DBMS_"):
                _add(issues, "ERROR", "PLSQL", "PL/SQL パッケージの呼び出し。アプリケーション側の実装に置き換える")

    if source == "postgres" and target != "postgres":
        for fn in node.find_all(exp.Anonymous):
            if fn.name.upper() not in ("NEXTVAL", "CURRVAL", "SETVAL"):
                continue
            if target not in SEQ_FUNC_TARGETS:
                _add(issues, "ERROR", "SEQUENCE", f"シーケンス関数 {fn.name}()。変換先の採番機能に置き換える")
            else:
                _add(issues, "WARN", "SEQUENCE",
                     f"シーケンス関数 {fn.name}()。変換先に同じ名前のシーケンスが要る。"
                     "PostgreSQL の SERIAL 列が暗黙に作るシーケンスは、変換先には作られない")
        for cast in node.find_all(exp.Cast):
            if cast.to.sql().upper() in ("REGCLASS", "REGTYPE", "REGPROC", "OID"):
                _add(issues, "ERROR", "PG_CATALOG", "PostgreSQL のシステムカタログ型へのキャスト。変換先には無い")

    if isinstance(node, exp.Create):
        kind = (node.args.get("kind") or "").upper()
        if kind == "SEQUENCE" and target in KNOWN_TARGETS and target not in SEQUENCE_TARGETS:
            _add(issues, "ERROR", "SEQUENCE", f"{target} にはシーケンスが無い。変換先の採番機能に置き換える")
        elif kind in ("TRIGGER", "PROCEDURE", "FUNCTION"):
            _add(issues, "ERROR", kind, f"{kind} は方言差が大きく機械変換の対象外。変換先の手続き言語で書き直す")


# ---------------------------------------------------------------------------------------------
# 3. 前処理
# ---------------------------------------------------------------------------------------------
def _put_preserved_table_first(sel: exp.Select) -> None:
    """外部結合の記号が FROM の先頭の表に付いていたら、記号の付かない表を先頭に入れ替える。

    SQLGlot の書き換えは FROM の先頭を保持される側とみなすので、そのままでは表が重複した壊れた SQL になる。
    """
    f = sel.args.get(FROM_KEY)
    joins = sel.args.get("joins") or []
    if f is None or not isinstance(f.this, exp.Table) or not joins:
        return
    marked = {c.table.lower() for c in sel.find_all(exp.Column) if c.args.get("join_mark") and c.table}
    if f.this.alias_or_name.lower() not in marked:
        return
    for j in joins:
        if isinstance(j.this, exp.Table) and not j.args.get("on") and not j.args.get("kind") \
                and not j.args.get("side") and j.this.alias_or_name.lower() not in marked:
            from_table, join_table = f.this.copy(), j.this.copy()
            f.set("this", join_table)
            j.set("this", from_table)
            return


def _fix_join_marks(node: exp.Expression, issues: list[Issue]) -> exp.Expression:
    if not any(c.args.get("join_mark") for c in node.find_all(exp.Column)):
        return node
    for sel in list(node.find_all(exp.Select)):
        _put_preserved_table_first(sel)
    try:
        rewritten = eliminate_join_marks(node.copy())
    except Exception:  # noqa: BLE001  SQLGlot は保持される側の表を決められないと AssertionError を投げる
        rewritten = None
    # 書き換えの後で検査する（書き換えが残す重複した表や CROSS JOIN を探す検査なので、前に呼んでも意味が無い）
    if rewritten is None or _bad_join_mark_rewrite(rewritten):
        _add(issues, "ERROR", "ORACLE_JOIN_MARK",
             "外部結合の記号 (+) を LEFT JOIN に書き換えられない。記号の向きが混ざっている。明示的な JOIN で書き直す")
        return node
    _add(issues, "INFO", "ORACLE_JOIN_MARK", "Oracle 外部結合 (+) を LEFT JOIN に書き換えた")
    return rewritten


def _limits_output_not_input(sel: exp.Select) -> str | None:
    """LIMIT に置き換えると意味が変わる SELECT の形。変わらなければ None。

    ROWNUM は**入力の行**を数える。LIMIT は**出力の行**を数える。集約や DISTINCT を挟むと両者は別物になる:
    ``SELECT COUNT(*) FROM emp WHERE ROWNUM <= 5`` は 5 を返すが、``... LIMIT 5`` は全件を数える。
    """
    if sel.args.get("distinct"):
        return "DISTINCT"
    if sel.args.get("group"):
        return "GROUP BY"
    if sel.args.get("having"):
        return "HAVING"
    for e in sel.expressions:
        if e.find(exp.AggFunc):
            return "集約関数"
        if e.find(exp.Window):
            return "ウィンドウ関数"
    return None


def _rownum_to_limit(node: exp.Expression, issues: list[Issue]) -> exp.Expression:
    """WHERE の ``ROWNUM <= n`` を LIMIT n にする。

    Oracle は ORDER BY より前に ROWNUM を適用するため、同じ SELECT に ORDER BY があると意味が変わりうる。
    集約・DISTINCT・GROUP BY・ウィンドウ関数のある SELECT では書き換えない（件数を数える対象が変わる）。
    """
    for sel in list(node.find_all(exp.Select)):
        where = sel.args.get("where")
        if where is None:
            continue
        keep = []
        for leaf in _flatten(where.this, exp.And):
            u = _unparen(leaf)
            if not (isinstance(u, (exp.LTE, exp.LT)) and isinstance(u.this, exp.Column)
                    and u.this.name.upper() == "ROWNUM" and isinstance(u.expression, exp.Literal)
                    and not sel.args.get("limit")):
                keep.append(leaf)
                continue
            if not u.expression.is_int:
                _add(issues, "ERROR", "ROWNUM", f"ROWNUM の上限 {u.expression.sql()} が整数でない。LIMIT に書き換えられない")
                keep.append(leaf)
                continue
            shape = _limits_output_not_input(sel)
            if shape is not None:
                _add(issues, "ERROR", "ROWNUM",
                     f"{shape} のある SELECT の ROWNUM は LIMIT に書き換えられない。ROWNUM は入力の行を、LIMIT は"
                     "出力の行を数える。先に絞る副問い合わせ（FROM (SELECT ... LIMIT n)）に書き直す")
                keep.append(leaf)
                continue
            n = int(u.expression.name) - (1 if isinstance(u, exp.LT) else 0)
            sel.set("limit", exp.Limit(expression=exp.Literal.number(n)))
            if sel.args.get("order"):
                _add(issues, "WARN", "ROWNUM", f"ROWNUM <= {n} を LIMIT {n} に書き換えた。"
                                               "Oracle は ORDER BY より前に ROWNUM を適用するため件数が変わりうる")
            elif sel.args.get("locks"):
                _add(issues, "WARN", "ROWNUM", f"ROWNUM <= {n} を LIMIT {n} に書き換えた。FOR UPDATE と LIMIT の"
                                               "組み合わせは、ロックされる行が方言によって異なる")
            else:
                _add(issues, "INFO", "ROWNUM", f"ROWNUM <= {n} を LIMIT {n} に書き換えた")
        sel.set("where", exp.Where(this=exp.and_(*keep)) if keep else None)
        if sel.args.get("limit") and isinstance(sel.parent, exp.SetOperation):
            # UNION の枝に付く LIMIT は括弧が要る。括弧が無いと PostgreSQL は構文エラー、MySQL は全体の LIMIT と読む
            sel.replace(exp.Subquery(this=sel.copy()))
    return node


def _fix_trunc_unit(node: exp.Expression) -> None:
    """Oracle の TRUNC(date, 'MM') の書式を標準の単位名にする。

    SQLGlot は書式をそのまま渡し、PostgreSQL と DuckDB は受け付けず、MySQL では日単位の切り捨てに化ける。
    """
    for dt in node.find_all(exp.DateTrunc, exp.TimestampTrunc):
        unit = dt.args.get("unit")
        name = (unit.name if unit is not None else "DD").upper()
        if name in TRUNC_UNITS:
            dt.set("unit", exp.Literal.string(TRUNC_UNITS[name]))


def _fix_recursive_cte(node: exp.Expression, target: str) -> None:
    """再帰 CTE の RECURSIVE を変換先に合わせる。

    Oracle は RECURSIVE を書かず、PostgreSQL・MySQL・DuckDB は必須。Oracle は逆に RECURSIVE を拒み、
    再帰 CTE に列名の並びを要求する。
    """
    keyword_required = getattr(_generator(target), "CTE_RECURSIVE_KEYWORD_REQUIRED", True)
    for w in node.find_all(exp.With):
        self_referencing = [c for c in w.expressions
                            if any(t.name.lower() == c.alias.lower() for t in c.this.find_all(exp.Table))]
        if target == "oracle" or not keyword_required:
            w.set("recursive", None)
            if target == "oracle":
                for c in self_referencing:
                    alias = c.args.get("alias")
                    if alias is not None and not alias.columns:
                        first = c.this
                        while isinstance(first, exp.SetOperation):
                            first = first.left
                        alias.set("columns", [exp.to_identifier(p.alias_or_name) for p in first.expressions])
        elif self_referencing:
            w.set("recursive", True)


def _fix_dual(node: exp.Expression, target: str) -> None:
    """FROM dual を落とす。どの変換先も FROM の無い SELECT を受け付ける。

    MySQL は DUAL を持つが、SQLGlot が引用符で囲むので表として解決されずに失敗する。
    """
    if target == "oracle":
        return
    for sel in node.find_all(exp.Select):
        f = sel.args.get(FROM_KEY)
        if f is not None and isinstance(f.this, exp.Table) and f.this.name.upper() == "DUAL" \
                and not f.this.db and not sel.args.get("joins"):
            sel.set(FROM_KEY, None)


def _fix_hints(node: exp.Expression, target: str, issues: list[Issue]) -> None:
    """実行計画だけに効き、結果を変えないヒントを外す。"""
    for sel in node.find_all(exp.Select):
        if sel.args.get("hint") is not None:
            sel.set("hint", None)
            _add(issues, "INFO", "HINT", "オプティマイザヒントを外した。変換先では効かず、結果は変わらない")
    if target != "mysql":
        for j in node.find_all(exp.Join):
            if str(j.args.get("kind") or "").upper() == "STRAIGHT_JOIN":
                j.set("kind", None)
                _add(issues, "INFO", "HINT", "STRAIGHT_JOIN を通常の結合にした。結合順を強制するだけで結果は変わらない")


def _fix_rollup(node: exp.Expression, target: str) -> None:
    """MySQL の GROUP BY a WITH ROLLUP を、標準の GROUP BY ROLLUP (a) にする。"""
    if target == "mysql":
        return
    for g in node.find_all(exp.Group):
        rollups = g.args.get("rollup") or []
        if rollups and g.expressions and all(isinstance(r, exp.Rollup) and not r.expressions for r in rollups):
            columns = [e.copy() for e in g.expressions]
            g.set("expressions", [])
            g.set("rollup", [exp.Rollup(expressions=columns)])


def _fix_derived_alias(node: exp.Expression) -> None:
    """別名の無い派生表に別名を付ける。MySQL と SQL Server は派生表に別名を要求する。"""
    used = {t.alias_or_name.lower() for t in node.find_all(exp.Table)}
    n = 0
    for sub in node.find_all(exp.Subquery):
        if isinstance(sub.parent, (exp.From, exp.Join)) and not sub.alias:
            n += 1
            while f"subq{n}" in used:
                n += 1
            sub.set("alias", exp.TableAlias(this=exp.to_identifier(f"subq{n}")))


def _fix_merge_set_targets(node: exp.Expression, target: str) -> None:
    """MERGE の UPDATE SET から表名修飾を外す。PostgreSQL と DuckDB は修飾を受け付けない。"""
    if target not in MERGE_UNQUALIFIED_SET:
        return
    for merge in node.find_all(exp.Merge):
        for upd in merge.find_all(exp.Update):
            for eq in upd.expressions:
                if isinstance(eq, exp.EQ) and isinstance(eq.this, exp.Column) and eq.this.table:
                    eq.this.set("table", None)


def _fix_auto_increment(node: exp.Expression, target: str) -> None:
    """MySQL の AUTO_INCREMENT を Oracle の IDENTITY 列にする。SQLGlot は Oracle 向けにそのまま出力する。"""
    if target != "oracle":
        return
    for c in list(node.find_all(exp.AutoIncrementColumnConstraint)):
        c.replace(exp.GeneratedAsIdentityColumnConstraint(this=False))


def _fix_intervals(node: exp.Expression, target: str) -> None:
    """Oracle 向けに INTERVAL を直す。

    SQLGlot は PostgreSQL の '6 months' を INTERVAL '6' MONTHS と複数形のまま出し、Oracle は拒む。
    単数形に直すだけでは月末の日付で失敗するので、月と年の加算は ADD_MONTHS にする。
    """
    if target != "oracle":
        return
    for iv in list(node.find_all(exp.Interval)):
        unit = iv.args.get("unit")
        if unit is None:
            continue
        name = unit.name.upper()
        if name in INTERVAL_PLURALS:
            name = name[:-1]
            iv.set("unit", exp.var(name))
        if name in ("MONTH", "YEAR") and isinstance(iv.parent, exp.Add) and isinstance(iv.this, exp.Literal) \
                and iv.this.name.lstrip("-").isdigit():
            months = int(iv.this.name) * (12 if name == "YEAR" else 1)
            add = iv.parent
            other = add.this if add.expression is iv else add.expression
            add.replace(exp.func("ADD_MONTHS", other.copy(), exp.Literal.number(months)))


def _fix_distinct_on(node: exp.Expression, target: str) -> exp.Expression:
    """Oracle 向けの DISTINCT ON の書き換え。

    SQLGlot の書き換えはアンダースコアで始まる別名を作り、Oracle の生成器はそれを引用符で囲まないので失敗する。
    """
    if target != "oracle":
        return node
    if not any(isinstance(s.args.get("distinct"), exp.Distinct) and s.args["distinct"].args.get("on")
               for s in node.find_all(exp.Select)):
        return node
    node = node.transform(eliminate_distinct_on)
    for ident in node.find_all(exp.Identifier):
        if ident.name.startswith("_"):
            ident.set("this", "x" + ident.name)
    return node


def _fix_datediff_for_oracle(node: exp.Expression, target: str) -> None:
    """DATEDIFF を Oracle の日付の引き算にする。Oracle に DATEDIFF は無い。"""
    if target != "oracle":
        return
    def day(e: exp.Expression) -> exp.Expression:
        if isinstance(e, exp.Literal) and e.is_string and DATE_LITERAL.fullmatch(e.name):
            return exp.StrToDate(this=e.copy(), format=exp.Literal.string("%Y-%m-%d"))
        if isinstance(e, exp.Literal) and e.is_string and TIMESTAMP_LITERAL.fullmatch(e.name):
            e = exp.StrToTime(this=e.copy(), format=exp.Literal.string("%Y-%m-%d %H:%M:%S"))
        return exp.Anonymous(this="TRUNC", expressions=[e.copy()])   # MySQL の DATEDIFF は時刻を無視する

    for dd in list(node.find_all(exp.DateDiff)):
        unit = dd.args.get("unit")
        if unit is None or unit.name.upper() in ("DAY", "DAYS", "D"):
            dd.replace(exp.Paren(this=exp.Sub(this=day(dd.this), expression=day(dd.expression))))


def _is_type(e: exp.Expression, *types) -> bool:
    return e.type is not None and e.type.is_type(*types)


def _is_unknown(e: exp.Expression) -> bool:
    return e.type is None or e.type.this == exp.DataType.Type.UNKNOWN


def _integer_division(a: exp.Expression, b: exp.Expression, target: str) -> exp.Expression | None:
    """整数どうしの除算を、変換先で小数部を切り捨てる形にする。

    SQLGlot の模倣は CAST で、変換先では四捨五入になるため使わない（7499 / 4 が 1875 になる）。
    """
    div = exp.Div(this=a, expression=b)
    if target == "oracle" or target == "snowflake":
        return exp.func("TRUNC", div)
    if target == "mysql":
        return exp.func("TRUNCATE", div, exp.Literal.number(0))
    if target == "duckdb":
        return exp.IntDiv(this=a, expression=b)
    if target == "bigquery":
        return exp.Anonymous(this="DIV", expressions=[a, b])
    return None


def _typed_arithmetic(node: exp.Expression, source: str, target: str, schema: dict | None,
                      issues: list[Issue]) -> None:
    """型に依存する意味の差を、表定義から付けた型で埋める。

    - 整数どうしの除算: 変換元 (PostgreSQL など) は切り捨て、変換先 (Oracle・MySQL・DuckDB) は小数を返す
    - 日付どうしの引き算: MySQL だけは日数にならない
    型付けには列の解決が要り、列の解決は構文木を書き換えるので、型は複製した構文木で判定し、
    書き換えは元の構文木に施す。除算と引き算は複製と元とで同じ順に現れる。
    """
    if isinstance(node, exp.Create):
        return
    fix_division = Dialect.get_or_raise(source).TYPED_DIVISION and not Dialect.get_or_raise(target).TYPED_DIVISION
    divisions = list(node.find_all(exp.Div)) if fix_division else []
    subtractions = list(node.find_all(exp.Sub)) if target == "mysql" else []
    if not divisions and not subtractions:
        return
    typed = None
    try:
        typed = annotate_types(qualify(node.copy(), schema=schema or None, dialect=source,
                                       validate_qualify_columns=False, identify=False),
                               schema=schema or None, dialect=source)
    except Exception:  # noqa: BLE001  型が付かなければ判定できないものとして扱う
        typed = None

    if divisions:
        typed_divs = list(typed.find_all(exp.Div)) if typed is not None else []
        pairs = list(zip(divisions, typed_divs)) if len(typed_divs) == len(divisions) else [(d, None) for d in divisions]
        unknown = False
        for d, t in pairs:
            if t is not None and _is_type(t.left, *exp.DataType.INTEGER_TYPES) \
                    and _is_type(t.right, *exp.DataType.INTEGER_TYPES):
                replacement = _integer_division(d.left.copy(), d.right.copy(), target)
                if replacement is None:
                    unknown = True
                else:
                    d.replace(replacement)
                    _add(issues, "INFO", "DIVISION", f"整数どうしの除算を {target} で切り捨てになる形に書き換えた")
            elif t is None or _is_unknown(t.left) or _is_unknown(t.right):
                unknown = True
        if unknown:
            _add(issues, "WARN", "DIVISION",
                 f"型の分からない除算がある。{source} の整数どうしの除算は小数部を切り捨てるが、{target} は小数を返す。"
                 "表定義（スクリプト内の CREATE TABLE か --schema）があれば自動で書き換える")

    if subtractions:
        typed_subs = list(typed.find_all(exp.Sub)) if typed is not None else []
        if len(typed_subs) == len(subtractions):
            for s, t in zip(subtractions, typed_subs):
                dates = [_is_type(x, exp.DataType.Type.DATE) for x in (t.left, t.right)]
                if all(dates):
                    s.replace(exp.DateDiff(this=s.left.copy(), expression=s.right.copy()))
                    _add(issues, "INFO", "DATE_ARITH", "日付どうしの引き算を MySQL の DATEDIFF に書き換えた")
                elif any(dates) and (_is_unknown(t.left) or _is_unknown(t.right)):
                    _add(issues, "WARN", "DATE_ARITH",
                         "日付の引き算がある。MySQL で日付どうしを引いても日数にならない。日数なら DATEDIFF を使う")


def _fix_intdiv(node: exp.Expression, target: str) -> None:
    """MySQL の DIV のような整数除算を Oracle の TRUNC にする。SQLGlot の CAST は四捨五入になる。"""
    if target != "oracle":
        return
    for d in list(node.find_all(exp.IntDiv)):
        d.replace(exp.func("TRUNC", exp.Div(this=d.this.copy(), expression=d.expression.copy())))


def _fix_date_literals(node: exp.Expression, target: str) -> None:
    """日付と日時の文字列の CAST を、書式を明示した TO_DATE / TO_TIMESTAMP にする。

    PostgreSQL と MySQL の日付リテラルは SQLGlot 内部で CAST になり、Oracle の CAST は既定の日付書式に依存して失敗する。
    """
    if target != "oracle":
        return
    for c in list(node.find_all(exp.Cast)):
        if not (isinstance(c.this, exp.Literal) and c.this.is_string):
            continue
        if c.to.this == exp.DataType.Type.DATE and DATE_LITERAL.fullmatch(c.this.name):
            c.replace(exp.StrToDate(this=c.this.copy(), format=exp.Literal.string("%Y-%m-%d")))
        elif c.to.this == exp.DataType.Type.TIMESTAMP and TIMESTAMP_LITERAL.fullmatch(c.this.name):
            c.replace(exp.StrToTime(this=c.this.copy(), format=exp.Literal.string("%Y-%m-%d %H:%M:%S")))


def _fold(name: str, target: str) -> str:
    if target in UPPER_FOLDING:
        return name.upper()
    if target in LOWER_FOLDING:
        return name.lower()
    return name


def _fix_caseless_quotes(node: exp.Expression, source: str, target: str) -> None:
    """MySQL のバッククォートや SQL Server の角括弧で囲んだ識別子を、変換先の流儀にそろえる。

    これらの引用符は大文字小文字の区別に意味を持たないが、そのまま二重引用符にすると Oracle や
    PostgreSQL では大文字小文字を区別する名前になり、表が見つからなくなる。
    予約語でなければ引用符を外し、予約語なら引用符を残して変換先の大文字小文字に畳む。
    """
    if source not in CASELESS_QUOTE_SOURCES:
        return
    reserved = RESERVED | {k.upper() for k in getattr(_generator(target), "RESERVED_KEYWORDS", ())}
    for ident in node.find_all(exp.Identifier):
        if not ident.quoted or not SIMPLE_IDENT.fullmatch(ident.name):
            continue
        if ident.name.upper() in reserved:
            ident.set("this", _fold(ident.name, target))
        else:
            ident.set("quoted", False)


def _collation(node: exp.Expression, source: str, target: str, issues: list[Issue], rewrite: bool) -> exp.Expression:
    """MySQL の既定の照合順序は大文字小文字を区別しないが、多くの変換先は区別する。

    照合順序は列ごとに決まるので SQL の文面だけでは完全には直せない。既定では警告にとどめ、
    利用者が選んだときだけ大文字小文字を区別しない形に書き換える。
    """
    if source != "mysql" or target not in CASE_SENSITIVE_TARGETS:
        return node
    hits = 0
    for cmp in list(node.find_all(exp.Like, exp.EQ, exp.NEQ, exp.In)):
        if isinstance(cmp, exp.In):
            lits = cmp.expressions
            if not (isinstance(cmp.this, exp.Column) and lits
                    and all(isinstance(x, exp.Literal) and x.is_string for x in lits)
                    and any(LETTERS.search(x.name) for x in lits)):
                continue
            hits += 1
            if rewrite:
                cmp.set("this", exp.Lower(this=cmp.this.copy()))
                cmp.set("expressions", [exp.Lower(this=x.copy()) for x in lits])
            continue
        col, lit = cmp.this, cmp.expression
        if isinstance(col, exp.Literal):
            col, lit = lit, col
        if not (isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string and LETTERS.search(lit.name)):
            continue
        hits += 1
        if rewrite:
            if isinstance(cmp, exp.Like):
                cmp.replace(exp.ILike(this=cmp.this.copy(), expression=cmp.expression.copy()))
            else:
                cmp.set("this", exp.Lower(this=cmp.this.copy()))
                cmp.set("expression", exp.Lower(this=cmp.expression.copy()))
    if hits and rewrite:
        _add(issues, "INFO", "COLLATION", "文字列の比較を、大文字小文字を区別しない形に書き換えた。索引が使われなくなることがある")
    elif hits:
        _add(issues, "WARN", "COLLATION",
             f"MySQL の既定の照合順序は大文字小文字を区別しないが、{target} は区別するので結果が変わりうる。"
             "--mysql-case-insensitive を付けると大文字小文字を区別しない形に書き換える")
    return node


def _preprocess(node: exp.Expression, source: str, target: str, issues: list[Issue], schema: dict | None,
                case_insensitive: bool) -> exp.Expression:
    if source == "oracle":
        node = _fix_join_marks(node, issues)
        if any(i.severity == "ERROR" for i in issues):
            return node
        node = _rownum_to_limit(node, issues)
        _fix_trunc_unit(node)
    _fix_recursive_cte(node, target)
    _fix_dual(node, target)
    _fix_hints(node, target, issues)
    _fix_rollup(node, target)
    node = _fix_distinct_on(node, target)
    _fix_derived_alias(node)
    _fix_merge_set_targets(node, target)
    _fix_auto_increment(node, target)
    _fix_intervals(node, target)
    _fix_datediff_for_oracle(node, target)
    _typed_arithmetic(node, source, target, schema, issues)
    _fix_intdiv(node, target)
    _fix_date_literals(node, target)
    _fix_caseless_quotes(node, source, target)
    node = _collation(node, source, target, issues, case_insensitive)
    return node


def _check_rownum_left(node: exp.Expression, source: str, target: str, issues: list[Issue]) -> None:
    """LIMIT に書き換えられなかった ROWNUM が残っていないかを見る。"""
    if source != "oracle" or target == "oracle":
        return
    for col in node.find_all(exp.Column):
        if col.name.upper() == "ROWNUM" and not (isinstance(col.this, exp.Identifier) and col.this.quoted):
            _add(issues, "ERROR", "ROWNUM",
                 "ROWNUM が LIMIT に書き換えられない形で残っている。ROW_NUMBER() OVER (...) で書き直す")
            return


# ---------------------------------------------------------------------------------------------
# 5. 変換先の検査
# ---------------------------------------------------------------------------------------------
def _strip_literals(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _check_target(node: exp.Expression, check_sql: str, target: str, issues: list[Issue]) -> None:
    """変換先が持っていない構文を拾う。node は前処理後の構文木、check_sql はコメントを除いた出力。"""
    if target not in KNOWN_TARGETS:
        return
    up = _strip_literals(check_sql).upper()

    def refuse(code: str, message: str) -> None:
        _add(issues, "ERROR", code, message)

    if re.search(r"\bCONNECT\s+BY\b", up) and target not in CONNECT_BY_TARGETS:
        refuse("CONNECT_BY", "階層問合せ（CONNECT BY）が書き換えられずに残っている。再帰 CTE に書き換える")
    if re.match(r"\s*MERGE\b", up) and target not in MERGE_TARGETS:
        refuse("MERGE", f"{target} には MERGE が無い。存在確認と INSERT / UPDATE を 1 トランザクションで行う")
    if "WITH ROLLUP" in up and target != "mysql":
        refuse("WITH_ROLLUP", "MySQL 固有の WITH ROLLUP。GROUP BY ROLLUP (...) に書き換える")
    if isinstance(node, exp.Update) and isinstance(node.this, exp.Table) and node.this.args.get("joins") \
            and target != "mysql":
        refuse("UPDATE_JOIN", "MySQL 固有の結合つき UPDATE。UPDATE ... FROM か相関サブクエリに書き換える")
    if isinstance(node, (exp.Update, exp.Delete)) and (node.args.get("limit") or node.args.get("order")) \
            and target != "mysql":
        refuse("UPDATE_LIMIT", "ORDER BY や LIMIT つきの UPDATE / DELETE。主キーで対象行を絞る形に書き換える")
    if re.search(r"\bON\s+CONFLICT\b", up) and target not in ON_CONFLICT_TARGETS:
        refuse("ON_CONFLICT", f"{target} には ON CONFLICT が無い。変換先の upsert 構文に書き換える")
    if re.search(r"\bAUTO_INCREMENT\b", up) and target != "mysql":
        refuse("AUTO_INC", f"{target} に AUTO_INCREMENT は無い。変換先の採番機能 (IDENTITY など) に置き換える")
    if re.search(r"\bON\s+DUPLICATE\s+KEY\b", up) and target != "mysql":
        refuse("ON_DUPLICATE_KEY", "MySQL 固有の ON DUPLICATE KEY UPDATE。変換先の upsert 構文に書き換える")
    if re.search(r"\bRETURNING\b", up) and target not in RETURNING_TARGETS:
        refuse("RETURNING", f"{target} では RETURNING で結果を返せない。変更後に SELECT で読み直す")
    if re.search(r"\b(BIG|SMALL)?SERIAL\b", up) and target not in SERIAL_TARGETS:
        refuse("SERIAL", f"{target} に SERIAL 型は無い。変換先の採番機能 (IDENTITY など) に置き換える")
    if re.search(r"\)\s*FILTER\s*\(\s*WHERE\b", up) and target not in AGG_FILTER_TARGETS:
        refuse("AGG_FILTER", f"{target} には集約の FILTER 句が無い。SUM(CASE WHEN ... THEN 1 END) の形に書き換える")
    if target == "mysql" and isinstance(node, exp.Update) and isinstance(node.this, exp.Table):
        name = node.this.name.lower()
        for sub in node.find_all(exp.Subquery):
            if any(t.name.lower() == name for t in sub.find_all(exp.Table)):
                refuse("UPDATE_SELF_SUBQUERY", "MySQL は更新対象の表をサブクエリで読めない。サブクエリを派生表で包むか、先に読み取る")
                break
    for sel in node.find_all(exp.Select):
        f = sel.args.get(FROM_KEY)
        tables = ([f.this] if f is not None else []) + [j.this for j in sel.args.get("joins") or []]
        names = [t.alias_or_name.lower() for t in tables if isinstance(t, exp.Table)]
        if len(names) != len(set(names)):
            refuse("DUPLICATE_ALIAS", "同じ表の別名が 1 つの FROM に重複している。変換の結果が壊れている")
            break


@functools.lru_cache(maxsize=None)
def _catalog(target: str) -> frozenset[str] | None:
    path = CATALOG_DIR / f"{target}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(name.strip().upper() for name in data["functions"])


def _source_functions(node: exp.Expression) -> set[str]:
    names = set()
    for fn in node.find_all(exp.Func):
        name = fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()
        if name:
            names.add(name.upper())
    return names


def _called_names(sql: str, target: str, node: exp.Expression) -> set[str] | None:
    """出力に関数呼び出しとして書かれた名前を、変換先の字句解析で拾う。

    構文木を読み直して表示し直すと別の綴りになることがある（MySQL の TRUNC が TRUNCATE になるなど）ので、
    出力の字句をそのまま見る。表名・別名・CTE 名・型名の直後の括弧は関数呼び出しではないので除く。
    """
    if isinstance(node, exp.Command):   # SQLGlot が解析できずに文面のまま持っている文。表名と関数を区別できない
        return set()
    try:
        tokens = Dialect.get_or_raise(target).tokenize(sql)
    except Exception:  # noqa: BLE001
        return None
    not_functions = ({t.name.upper() for t in node.find_all(exp.Table)}
                     | {a.name.upper() for a in node.find_all(exp.TableAlias)}
                     | {t.value.upper() for t in exp.DataType.Type} | KEYWORD_FUNCS)
    names = set()
    for i, tok in enumerate(tokens[:-1]):
        if tok.token_type != TokenType.VAR or tokens[i + 1].token_type != TokenType.L_PAREN:
            continue
        if i > 0 and tokens[i - 1].token_type == TokenType.DOT:   # スキーマやパッケージで修飾された呼び出し
            continue
        if i > 0 and tokens[i - 1].text.upper() in NAME_BEFORE_PAREN:   # INDEX idx (col)、INSERT INTO t (col) など
            continue
        if tok.text.upper() not in not_functions:
            names.add(tok.text.upper())
    return names


def _check_functions(node: exp.Expression, check_sql: str, target: str, src_funcs: set[str],
                     issues: list[Issue]) -> None:
    """変換先に無い関数を拾う。

    変換先の組み込み関数一覧があれば、出力に書かれた関数名が一覧にあるかで判定する。一覧が無ければ、
    変換元固有の関数名が書き換えられずに残っているかで判定する（誤検出を許容する予備の判定）。
    利用者定義の関数がありうるので ERROR にはせず WARN にとどめる。
    """
    catalog = _catalog(target)
    called = _called_names(check_sql, target, node) if catalog is not None else None
    if called is None:
        stripped = _strip_literals(check_sql)
        left = [n for n in sorted(src_funcs - PORTABLE_FUNCS) if re.search(rf"\b{re.escape(n)}\s*\(", stripped, re.I)]
        if left:
            _add(issues, "WARN", "FUNC_PORTABILITY",
                 f"関数 {', '.join(n + '()' for n in left)} が元の名前のまま残っている。"
                 f"{target} に同名・同じ意味の関数があるか確認する")
        try:   # 変換先の方言で読み直して、SQLGlot が関数として知らない名前を拾う
            unknown = sorted({f.name.upper() for f in sqlglot.parse_one(check_sql, read=target).find_all(exp.Anonymous)})
        except ParseError:
            unknown = []
        if unknown:
            _add(issues, "WARN", "UNKNOWN_FUNC",
                 f"関数 {', '.join(n + '()' for n in unknown)} を {target} の関数として認識できない。同等の関数があるか確認する")
        return
    missing = sorted(called - catalog)
    if missing:
        _add(issues, "WARN", "FUNC_PORTABILITY",
             f"関数 {', '.join(n + '()' for n in missing)} は {target} の組み込み関数一覧に無い。"
             "同じ意味の関数に書き換えるか、利用者定義の関数か確認する")


def _check_roundtrip(sql: str, target: str, issues: list[Issue]) -> None:
    try:
        sqlglot.parse_one(sql, read=target)
    except ParseError as e:
        _add(issues, "ERROR", "ROUNDTRIP", f"生成した SQL を {target} として読み直せない: {str(e).splitlines()[0][:120]}")


# ---------------------------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------------------------
def convert_statement(stmt: str, source: str, target: str, schema: dict | None = None,
                      case_insensitive: bool = False) -> Result:
    """1 文を変換する。schema は {表名: {列名: 型}}。型に依存する書き換えに使う。"""
    issues: list[Issue] = []
    src = stmt.strip()
    try:
        node = sqlglot.parse_one(src, read=source)
    except ParseError as e:
        return Result(index=0, source_sql=src, kind="PARSE_ERROR", status="ERROR",
                      issues=[Issue("ERROR", "PARSE", str(e).splitlines()[0][:200])])
    if node is None:
        return Result(index=0, source_sql=src, kind="UNKNOWN", status="ERROR",
                      issues=[Issue("ERROR", "PARSE", "空の文として解析された")])
    kind = type(node).__name__.upper()

    converted: list[str] = []
    if source == target:   # 同じ方言への変換は整形し直すだけ
        out = node.sql(dialect=target)
        converted = [out]
        _check_roundtrip(out, target, issues)
    else:
        src_funcs = _source_functions(node)   # 前処理で構文木が変わる前に集める
        _check_source(node, source, target, issues)
        if not any(i.severity == "ERROR" for i in issues):
            node = _preprocess(node, source, target, issues, schema, case_insensitive)
            _check_rownum_left(node, source, target, issues)
        if not any(i.severity == "ERROR" for i in issues):
            try:
                out = node.sql(dialect=target, unsupported_level=ErrorLevel.RAISE, pretty=False)
                check_sql = node.sql(dialect=target, comments=False)   # 検査はコメントを除いた文面で行う
                converted = [out]
                _check_target(node, check_sql, target, issues)
                _check_functions(node, check_sql, target, src_funcs, issues)
                _check_roundtrip(out, target, issues)
            except UnsupportedError as e:
                _add(issues, "ERROR", "UNSUPPORTED", str(e).splitlines()[0][:200])
            except Exception as e:  # noqa: BLE001  生成器はまれに予期しない例外を投げる
                _add(issues, "ERROR", "GENERATE", f"{type(e).__name__}: {str(e).splitlines()[0][:180]}")

    if any(i.severity == "ERROR" for i in issues):
        status, converted = "ERROR", []
    elif any(i.severity == "WARN" for i in issues):
        status = "WARN"
    else:
        status = "OK"
    return Result(index=0, source_sql=src, kind=kind, status=status, converted=converted, issues=issues)


def schema_from_ddl(statements: list[str], dialect: str) -> dict:
    """スクリプト内の CREATE TABLE から {表名: {列名: 型}} を作る。"""
    schema: dict[str, dict[str, str]] = {}
    for stmt in statements:
        try:
            node = sqlglot.parse_one(stmt, read=dialect)
        except ParseError:
            continue
        if not (isinstance(node, exp.Create) and str(node.args.get("kind") or "").upper() == "TABLE"
                and isinstance(node.this, exp.Schema) and isinstance(node.this.this, exp.Table)):
            continue
        columns = {cd.name: cd.args["kind"].sql(dialect=dialect)
                   for cd in node.this.expressions if isinstance(cd, exp.ColumnDef) and cd.args.get("kind")}
        if columns:
            schema[node.this.this.name] = columns
    return schema


def convert_script(text: str, source: str, target: str, schema: dict | None = None,
                   case_insensitive: bool = False) -> list[Result]:
    """スクリプト全体を変換する。スクリプト内の CREATE TABLE と schema を合わせて型の判定に使う。"""
    try:
        statements = _split_statements(text, source)
    except TokenError as e:
        # 文の切れ目が決められない（たいていは閉じていない文字列）。1 文ずつには変換できないので、どこで
        # つまずいたかを 1 件の ERROR として返す——トレースバックで終わると、レポートが何も残らない
        return [Result(index=1, source_sql=text.strip()[:2000], kind="TOKEN_ERROR", status="ERROR",
                       issues=[Issue("ERROR", "TOKENIZE", f"スクリプトを文に分けられない: {str(e).splitlines()[0][:200]}")])]
    merged = dict(schema or {})
    merged.update(schema_from_ddl(statements, source))
    results = []
    for i, stmt in enumerate(statements, start=1):
        try:
            r = convert_statement(stmt, source, target, merged or None, case_insensitive)
        except Exception as e:  # noqa: BLE001  1 文の想定外で、残りの文の変換まで止めない
            r = Result(index=i, source_sql=stmt.strip(), kind="INTERNAL_ERROR", status="ERROR",
                       issues=[Issue("ERROR", "INTERNAL",
                                     f"この文の変換中に変換器が失敗した（{type(e).__name__}: "
                                     f"{(str(e).splitlines() or ['メッセージなし'])[0][:160]}）。"
                                     f"残りの文は変換した。文を添えて報告してほしい")])
        r.index = i
        results.append(r)
    return results
