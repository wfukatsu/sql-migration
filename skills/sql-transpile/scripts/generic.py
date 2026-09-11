"""任意の SQLGlot 方言どうしの変換パス。

素の ``sqlglot.transpile()`` は、変換できない構文を黙って出力へ通すことがある。
Oracle → PostgreSQL の実測例:

    WHERE ROWNUM <= 5                 -> WHERE ROWNUM <= 5          (そのまま通る)
    WHERE e.deptno = d.deptno(+)      -> WHERE e.deptno = d.deptno  (外部結合が内部結合に化ける)
    CONNECT BY PRIOR empno = mgr      -> CONNECT BY PRIOR empno = mgr
    VALUES (emp_seq.NEXTVAL)          -> VALUES (emp_seq.NEXTVAL)

``(+)`` は ``unsupported_level=ErrorLevel.RAISE`` で検出できるが、残りは SQLGlot が
「未対応」と認識していないため、どのエラーレベルでも引っかからない。

このモジュールは 4 段で処理する。

1. 前処理  : SQLGlot が直さない構文を AST 上で直す（``(+)`` → LEFT JOIN、ROWNUM → LIMIT）
2. 生成    : ErrorLevel.RAISE で生成し、SQLGlot が知っている非対応を例外にする
3. 残存検査: 出力に残った危険構文を拾う（構文マーカー照合 + 未知関数の検出）
4. 往復検証: 生成結果が Target 方言でパースできることを確かめる
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError, UnsupportedError
from sqlglot.transforms import eliminate_join_marks

from _scalardb.converter import Issue, Result, _bad_join_mark_rewrite, _flatten, _split_statements, _unparen


# --------------------------------------------------------------------------------------
# 残存構文テーブル
#
# 「前処理を通したあとも出力に残っていたら、Target では動かない（か意味が変わる）」もの。
# key は Source 方言。値は (正規表現, コード, 説明, severity)。
# 詳細と書き換え方は references/dialect-notes.md にある。
# --------------------------------------------------------------------------------------
RESIDUAL: dict[str, list[tuple[str, str, str, str]]] = {
    "oracle": [
        (r"\bROWNUM\b", "ROWNUM", "Oracle の疑似列。LIMIT / FETCH FIRST に書き換える", "ERROR"),
        (r"\bROWID\b", "ROWID", "Oracle の疑似列。主キーで代替する", "ERROR"),
        (r"\bCONNECT\s+BY\b", "CONNECT_BY", "階層問合せ。再帰 CTE (WITH RECURSIVE) に書き換える", "ERROR"),
        (r"\bSTART\s+WITH\b", "CONNECT_BY", "階層問合せの起点。再帰 CTE に書き換える", "ERROR"),
        (r"\bSYS_CONNECT_BY_PATH\b", "CONNECT_BY", "階層問合せ専用関数。再帰 CTE で経路を組み立てる", "ERROR"),
        (r"\.\s*NEXTVAL\b", "SEQUENCE", "シーケンス。Target の採番機能かアプリケーション側で採番する", "ERROR"),
        (r"\.\s*CURRVAL\b", "SEQUENCE", "シーケンス。直前の採番値をアプリケーション側で保持する", "ERROR"),
        (r"\bKEEP\s*\(\s*DENSE_RANK\b", "KEEP", "Oracle 固有の集約修飾。ウィンドウ関数に書き換える", "ERROR"),
        (r"\bPIVOT\b", "PIVOT", "行列変換。CASE 式に展開する", "ERROR"),
        (r"\bUNPIVOT\b", "UNPIVOT", "列行変換。UNION ALL に展開する", "ERROR"),
        (r"\bDBMS_\w+", "PLSQL", "PL/SQL パッケージ。アプリケーション側の実装に置き換える", "ERROR"),
        (r"/\*\+", "HINT", "オプティマイザヒント。Target には効かないので削除してよい", "WARN"),
    ],
    "postgres": [
        (r"\bnextval\s*\(", "SEQUENCE", "シーケンス。Target の採番機能に置き換える", "ERROR"),
        (r"::\s*regclass\b", "PG_CATALOG", "PostgreSQL 固有のキャスト。Target には存在しない", "ERROR"),
        (r"\bDISTINCT\s+ON\b", "DISTINCT_ON", "PostgreSQL 固有。ウィンドウ関数で先頭行を取る形に書き換える", "ERROR"),
    ],
    "mysql": [
        (r"\bAUTO_INCREMENT\b", "AUTO_INC", "MySQL 固有の採番。Target の採番機能に置き換える", "ERROR"),
        (r"\bON\s+DUPLICATE\s+KEY\b", "UPSERT", "MySQL 固有の upsert。Target の構文に書き換える", "ERROR"),
        (r"\bSTRAIGHT_JOIN\b", "HINT", "MySQL 固有の結合ヒント。Target には効かない", "WARN"),
    ],
}

# 方言に依らず危険なもの
RESIDUAL_ANY: list[tuple[str, str, str, str]] = [
    (r"\bPRIOR\b", "CONNECT_BY", "階層問合せの参照。再帰 CTE に書き換える", "ERROR"),
]

# 変換ではどうにもならず、Target 側で作り直す必要がある文
NON_PORTABLE_DDL = {
    exp.Create: {"SEQUENCE": ("SEQUENCE", "シーケンスは方言ごとに扱いが異なる。Target の採番機能を確認する"),
                 "TRIGGER": ("TRIGGER", "トリガーは方言差が大きい。Target の構文で書き直す"),
                 "PROCEDURE": ("PROCEDURE", "ストアドプロシージャは移植不可。手続きは Target の言語で書き直す"),
                 "FUNCTION": ("FUNCTION", "ユーザー定義関数は移植不可。Target の言語で書き直す")},
}


def _preprocess(node: exp.Expression, source: str, issues: list[Issue]) -> exp.Expression:
    """SQLGlot が直してくれない構文を AST 上で直す。issues に経過を記録する。"""
    if source != "oracle":
        return node

    # Oracle 外部結合 (+) -> LEFT JOIN。SQLGlot 単体だと内部結合に化けて結果が静かに変わる
    if any(c.args.get("join_mark") for c in node.find_all(exp.Column)):
        bad = _bad_join_mark_rewrite(node)
        if bad:
            issues.append(Issue("ERROR", "ORACLE_JOIN_MARK",
                                f"(+) が FROM 側のテーブル {bad} に付いており、LEFT JOIN に書き換えられない。"
                                "結合の向きを入れ替えて書き直す"))
            return node
        node = eliminate_join_marks(node)
        issues.append(Issue("INFO", "ORACLE_JOIN_MARK", "Oracle 外部結合 (+) を LEFT JOIN に書き換えた"))

    node = _rownum_to_limit(node, issues)
    return node


def _rownum_to_limit(node: exp.Expression, issues: list[Issue]) -> exp.Expression:
    """WHERE の ``ROWNUM <= n`` を LIMIT n にする。

    Oracle は ORDER BY より前に ROWNUM を適用するため、ORDER BY を伴う文では
    意味が変わりうる。書き換えたうえで WARN を出す。
    """
    for sel in node.find_all(exp.Select):
        where = sel.args.get("where")
        if where is None:
            continue
        keep = []
        for leaf in _flatten(where.this, exp.And):
            u = _unparen(leaf)
            if (isinstance(u, (exp.LTE, exp.LT)) and isinstance(u.this, exp.Column)
                    and u.this.name.upper() == "ROWNUM" and isinstance(u.expression, exp.Literal)
                    and not sel.args.get("limit")):
                n = int(u.expression.name) - (1 if isinstance(u, exp.LT) else 0)
                sel.set("limit", exp.Limit(expression=exp.Literal.number(n)))
                sev = "WARN" if sel.args.get("order") else "INFO"
                issues.append(Issue(sev, "ROWNUM",
                                    f"ROWNUM <= {n} を LIMIT {n} に書き換えた。"
                                    + ("Oracle は ORDER BY より前に ROWNUM を適用するため件数が変わりうる"
                                       if sev == "WARN" else "")))
            else:
                keep.append(leaf)
        sel.set("where", exp.Where(this=exp.and_(*keep)) if keep else None)
    return node


def _check_residual(sql: str, source: str, issues: list[Issue]) -> None:
    """生成結果に残っている危険構文を拾う（このモジュールの核）。"""
    # 文字列リテラルの中身は誤検出のもとなので落としてから照合する
    stripped = re.sub(r"'(?:[^']|'')*'", "''", sql)
    reported = set()   # CONNECT BY / START WITH / PRIOR は同じ問題なので、コードごとに 1 件にまとめる
    for pattern, code, note, severity in RESIDUAL.get(source, []) + RESIDUAL_ANY:
        if code in reported:
            continue
        m = re.search(pattern, stripped, re.I)
        if m:
            reported.add(code)
            issues.append(Issue(severity, code, f"{m.group(0).strip()} が変換されずに残っている。{note}"))


def _check_unknown_functions(sql: str, target: str, issues: list[Issue]) -> None:
    """生成 SQL を Target 方言で読み直し、Target がモデル化していない関数を拾う。

    構文マーカーの表は「既知のもの」しか拾えない。こちらは方言に依らず効く汎用の網。
    """
    try:
        reparsed = sqlglot.parse_one(sql, read=target)
    except ParseError:
        return  # 往復検証側で ERROR になる
    seen = set()
    for fn in reparsed.find_all(exp.Anonymous):
        name = fn.name.upper()
        if name in seen:
            continue
        seen.add(name)
        issues.append(Issue("WARN", "UNKNOWN_FUNC",
                            f"関数 {name}() を {target} が解釈できない。"
                            f"{target} に同等の関数があるか確認する"))


def _check_roundtrip(sql: str, target: str, issues: list[Issue]) -> None:
    """生成結果が Target 方言でパースできることを確かめる。"""
    try:
        sqlglot.parse_one(sql, read=target)
    except ParseError as e:
        issues.append(Issue("ERROR", "ROUNDTRIP",
                            f"生成した SQL を {target} として読み直せない: {str(e).splitlines()[0][:120]}"))


def _check_non_portable(node: exp.Expression, issues: list[Issue]) -> None:
    """変換では埋められない DDL（シーケンス・トリガー・プロシージャ）を先に弾く。"""
    if isinstance(node, exp.Create):
        kind = (node.args.get("kind") or "").upper()
        table = NON_PORTABLE_DDL.get(exp.Create, {})
        if kind in table:
            code, note = table[kind]
            issues.append(Issue("ERROR", code, note))


# 方言をまたいでも名前が変わらない、移植性の高い関数。
# これ以外の関数が「元の名前のまま」出力に残っていたら、Target に同名の関数があるか要確認とする。
PORTABLE_FUNCS = {
    "COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NULLIF", "UPPER", "LOWER", "ABS", "ROUND",
    "CAST", "TRIM", "LTRIM", "RTRIM", "LENGTH", "SUBSTRING", "SUBSTR", "REPLACE", "CONCAT", "MOD",
    "POWER", "FLOOR", "CEIL", "CEILING", "SQRT", "EXP", "LN", "SIGN", "GREATEST", "LEAST",
    "CURRENT_DATE", "CURRENT_TIMESTAMP", "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD",
    "FIRST_VALUE", "LAST_VALUE", "NTILE", "EXISTS",
}


def _source_functions(node: exp.Expression) -> set[str]:
    """元 SQL に出てくる関数名（SQLGlot 上の正規名）を集める。"""
    names = set()
    for fn in node.find_all(exp.Func):
        name = fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()
        if name:
            names.add(name.upper())
    return names


def _check_function_portability(sql: str, src_funcs: set[str], target: str, issues: list[Issue]) -> None:
    """元 SQL 固有の関数名が、書き換えられずに出力へ残っていないかを見る。

    SQLGlot は MONTHS_BETWEEN や INITCAP のような関数を方言非依存の型付きノードとして扱う。
    そのため Target で読み直しても未知関数 (Anonymous) にはならず、Target にその関数が
    実在しなくても素通りする。NVL -> COALESCE のように書き換えられた関数は安全とみなし、
    名前がそのまま残ったものだけを「要確認」として WARN にする。

    Target が同名関数を持っている場合 (例: PostgreSQL の INITCAP) も WARN になるため、
    これは誤検出を許容する網である。レポートでは「要確認」と表現する。
    """
    stripped = re.sub(r"'(?:[^']|'')*'", "''", sql)
    for name in sorted(src_funcs - PORTABLE_FUNCS):
        if re.search(rf"\b{re.escape(name)}\s*\(", stripped, re.I):
            issues.append(Issue("WARN", "FUNC_PORTABILITY",
                                f"関数 {name}() が元の名前のまま残っている。"
                                f"{target} に同名・同じ意味の関数があるか確認する"))


def convert_statement(stmt: str, source: str, target: str) -> Result:
    """1 文を変換する。"""
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
    src_funcs = _source_functions(node)   # 前処理で AST が変わる前に集める
    _check_non_portable(node, issues)
    node = _preprocess(node, source, issues)

    converted: list[str] = []
    if not any(i.severity == "ERROR" for i in issues):
        try:
            out = node.sql(dialect=target, unsupported_level=ErrorLevel.RAISE, pretty=False)
            converted = [out]
            _check_residual(out, source, issues)
            _check_unknown_functions(out, target, issues)
            if source != target:   # 同一方言への変換では関数名が残るのが正常
                _check_function_portability(out, src_funcs, target, issues)
            _check_roundtrip(out, target, issues)
        except UnsupportedError as e:
            issues.append(Issue("ERROR", "UNSUPPORTED", str(e).splitlines()[0][:200]))
        except Exception as e:  # noqa: BLE001  生成器はまれに予期しない例外を投げる
            issues.append(Issue("ERROR", "GENERATE", f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"))

    if any(i.severity == "ERROR" for i in issues):
        status, converted = "ERROR", []
    elif any(i.severity == "WARN" for i in issues):
        status = "WARN"
    else:
        status = "OK"
    return Result(index=0, source_sql=src, kind=kind, status=status, converted=converted, issues=issues)


def convert_script(text: str, source: str, target: str) -> list[Result]:
    """スクリプト全体を変換する。文の分割は本体と同じトークナイザ方式を使う。"""
    results = []
    for i, stmt in enumerate(_split_statements(text, source), start=1):
        r = convert_statement(stmt, source, target)
        r.index = i
        results.append(r)
    return results
