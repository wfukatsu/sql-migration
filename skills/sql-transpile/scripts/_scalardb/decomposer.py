"""Decompose a read-only statement that ScalarDB SQL cannot execute into

  * fetch specs  : per base table, the largest predicate ScalarDB can evaluate (col op literal, AND/OR of them)
                   plus the columns the statement needs   -> executed through ScalarDB (SQL or Core API)
  * residual SQL : the original statement, run in-process on the fetched rows
                   - java  : H2 in compatibility mode (MODE=Oracle|PostgreSQL|MySQL), original SQL as-is
                             (plus a few static rewrites for functions H2 lacks)
                   - python: sqlglot transpile to SQLite (reference implementation / tests)

The result is a JSON-serialisable Plan (see docs/app-side-processing-plan.md §3.1).
"""

from __future__ import annotations

import functools
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize import normalize
from sqlglot.transforms import eliminate_join_marks

from .schema import SchemaRegistry, TableMeta

DEFAULT_ROW_LIMIT = 10_000
H2_MODE = {"oracle": "Oracle", "postgres": "PostgreSQL", "mysql": "MySQL"}
COMPARE_OPS = {exp.EQ: "=", exp.NEQ: "<>", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}
FLIP_OPS = {"=": "=", "<>": "<>", ">": "<", "<": ">", ">=": "<=", "<=": ">="}

# ScalarDB type -> H2 / sqlite column types
H2_TYPES = {"BOOLEAN": "BOOLEAN", "INT": "INT", "BIGINT": "BIGINT", "FLOAT": "REAL", "DOUBLE": "DOUBLE PRECISION",
            "TEXT": "VARCHAR", "BLOB": "BINARY VARYING", "DATE": "DATE", "TIME": "TIME", "TIMESTAMP": "TIMESTAMP",
            "TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE"}
SQLITE_TYPES = {"BOOLEAN": "INTEGER", "INT": "INTEGER", "BIGINT": "INTEGER", "FLOAT": "REAL", "DOUBLE": "REAL",
                "TEXT": "TEXT", "BLOB": "BLOB", "DATE": "TEXT", "TIME": "TEXT", "TIMESTAMP": "TEXT", "TIMESTAMPTZ": "TEXT"}


@dataclass
class Predicate:
    column: str
    op: str  # = <> > >= < <= LIKE NOT LIKE IS NULL IS NOT NULL BETWEEN
    value: Any = None  # literal, {"param": name}, or [low, high] for BETWEEN


@dataclass
class FetchSpec:
    table: str
    namespace: str | None
    alias: str
    columns: list[str] | None  # None => all columns (schema unknown)
    column_types: dict[str, str]  # ScalarDB types when known
    predicates: list[Predicate | list[Predicate]]  # AND list; a nested list is an OR group
    scalardb_sql: str
    access_path: str
    max_rows: int = DEFAULT_ROW_LIMIT


@dataclass
class Plan:
    pattern: str
    source_dialect: str
    source_sql: str
    fetch: list[FetchSpec]
    residual: dict[str, dict[str, str]]
    write: dict | None
    guardrails: dict
    unresolved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class NotDecomposable(Exception):
    pass


# --------------------------------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _h2_target_dialect(name: str):
    """The source dialect, but rendering NULL ordering the way H2 needs it (H2 sorts NULLs first for ASC).

    sqlglot omits a NULLS FIRST / NULLS LAST clause when it matches the target dialect's own default, so rendering
    with the source dialect would silently drop the source engine's NULL ordering.
    """
    base = type(sqlglot.Dialect.get_or_raise(name))
    return type(f"H2{base.__name__}", (base,), {"NULL_ORDERING": "nulls_are_small"})


def _unparen(e: exp.Expression) -> exp.Expression:
    while isinstance(e, exp.Paren):
        e = e.this
    return e


def _flatten(e: exp.Expression, op: type) -> list[exp.Expression]:
    e = _unparen(e)
    if isinstance(e, op):
        return _flatten(e.this, op) + _flatten(e.expression, op)
    return [e]


def _literal_value(e: exp.Expression):
    """Python value for a literal / bind marker, or raise NotDecomposable."""
    e = _unparen(e)
    if isinstance(e, exp.Literal):
        if e.is_string:
            return e.name
        return float(e.name) if "." in e.name or "e" in e.name.lower() else int(e.name)
    if isinstance(e, exp.Neg) and isinstance(e.this, exp.Literal):
        v = _literal_value(e.this)
        return -v
    if isinstance(e, exp.Null):
        return None
    if isinstance(e, exp.Boolean):
        return bool(e.this)
    if isinstance(e, exp.Placeholder):
        return {"param": e.name or "?"}
    if isinstance(e, exp.Parameter):
        return {"param": e.name}
    if isinstance(e, exp.Cast) and isinstance(e.this, exp.Literal):  # DATE '...' style
        return e.this.name
    if isinstance(e, (exp.StrToDate, exp.StrToTime, exp.TsOrDsToDate)) and isinstance(e.this, exp.Literal):
        return e.this.name
    raise NotDecomposable(f"not a literal: {e.sql()}")


def _sql_value(v) -> str:
    if isinstance(v, dict):
        return f":{v['param']}" if v["param"] != "?" else "?"
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def _pred_sql(p: Predicate) -> str:
    if p.op in ("IS NULL", "IS NOT NULL"):
        return f"{p.column} {p.op}"
    if p.op == "BETWEEN":
        return f"{p.column} BETWEEN {_sql_value(p.value[0])} AND {_sql_value(p.value[1])}"
    return f"{p.column} {p.op} {_sql_value(p.value)}"


class Scope:
    """Column resolution for one SELECT scope: alias -> (table name, meta)."""

    def __init__(self, select: exp.Select, registry: SchemaRegistry, outer: "Scope | None" = None):
        self.select = select
        self.registry = registry
        self.outer = outer
        self.tables: dict[str, exp.Table] = {}
        self.outer_joined: set[str] = set()
        from_ = select.args.get("from_") or select.args.get("from")
        if from_ is not None and isinstance(from_.this, exp.Table):
            self._add(from_.this)
        for j in select.args.get("joins") or []:
            if isinstance(j.this, exp.Table):
                self._add(j.this)
                # sqlglot puts LEFT/RIGHT in `side`, but eliminate_join_marks() (Oracle (+)) puts it in `kind`
                side = (j.args.get("side") or j.args.get("kind") or "").upper()
                if side in ("LEFT", "RIGHT", "FULL"):
                    self.outer_joined.add((j.this.alias or j.this.name).lower())
                    if side in ("RIGHT", "FULL"):
                        # the FROM side becomes nullable too
                        self.outer_joined.add((from_.this.alias or from_.this.name).lower())

    def _add(self, t: exp.Table) -> None:
        self.tables[(t.alias or t.name).lower()] = t

    def owner(self, col: exp.Column) -> str | None:
        """Alias of the table owning this column in this scope (or None if unknown / belongs to outer scope)."""
        q = (col.table or "").lower()
        if q:
            if q in self.tables:
                return q
            return None  # outer-scope reference or unknown alias
        if len(self.tables) == 1:
            return next(iter(self.tables))
        owners = [a for a, t in self.tables.items()
                  if (m := self.registry.get(t.name)) and col.name.lower() in {c.lower() for c in m.columns}]
        return owners[0] if len(owners) == 1 else None


class Decomposer:
    def __init__(self, dialect: str, registry: SchemaRegistry, row_limit: int = DEFAULT_ROW_LIMIT):
        self.dialect = dialect
        self.registry = registry
        self.row_limit = row_limit

    # -- entry point --------------------------------------------------------------------------------
    def decompose(self, node: exp.Expression, source_sql: str, error_codes: set[str]) -> Plan:
        if not isinstance(node, (exp.Select, exp.SetOperation)):
            raise NotDecomposable("only SELECT statements (incl. UNION / MINUS / INTERSECT / CTE) can be decomposed")
        for w in node.find_all(exp.Window):  # H2/sqlite handle windows; nothing to do but note
            break
        unresolved: list[str] = []
        notes: list[str] = []
        self._rewritten = False
        for c in node.find_all(exp.Column):
            if c.name.upper() in ("ROWID", "ROWSCN", "ORA_ROWSCN"):
                raise NotDecomposable(f"pseudo-column {c.name.upper()} cannot be fetched from ScalarDB")
        if any(c.args.get("join_mark") for c in node.find_all(exp.Column)):
            from .converter import _bad_join_mark_rewrite
            node = eliminate_join_marks(node.copy())
            if _bad_join_mark_rewrite(node):
                raise NotDecomposable("Oracle (+) outer join with the mark on the FROM-side table: rewrite as LEFT/RIGHT JOIN by hand")
            self._rewritten = True
            notes.append("Oracle (+) outer join rewritten as LEFT/RIGHT OUTER JOIN (H2 does not support (+))")
        cte_names = {c.alias.lower() for c in node.find_all(exp.CTE)}
        # collect per-table column usage and pushdown predicates, scope by scope
        # one fetch per base table: the H2 side holds a single copy of each table, so every scope / alias that
        # references the table contributes its columns, and predicates are kept only if all scopes agree
        specs: dict[str, FetchSpec] = {}
        columns_used: dict[str, set[str]] = {}
        preds_seen: dict[str, list] = {}
        global_alias: dict[str, str] = {}
        for t in node.find_all(exp.Table):
            if t.name.lower() not in cte_names and t.name.lower() != "dual":
                global_alias[(t.alias or t.name).lower()] = t.name.lower()
        for sel in node.find_all(exp.Select):
            scope = Scope(sel, self.registry)
            for alias, t in scope.tables.items():
                if t.name.lower() in cte_names or t.name.lower() == "dual":
                    continue
                key = t.name.lower()
                columns_used.setdefault(key, set())
                preds = self._pushdown(sel, scope, alias, t, notes)
                if key in specs:
                    if preds_seen[key] != [self._group_sql(g) for g in preds]:
                        if specs[key].predicates:
                            notes.append(f"{t.name}: referenced by several scopes with different predicates; fetching without predicate")
                        specs[key].predicates = []
                        preds_seen[key] = []
                    continue
                meta = self.registry.get(t.name)
                specs[key] = FetchSpec(table=t.name, namespace=t.db or (meta.namespace if meta else None), alias=alias,
                                       columns=None, column_types=dict(meta.columns) if meta else {},
                                       predicates=preds, scalardb_sql="", access_path="")
                preds_seen[key] = [self._group_sql(g) for g in preds]
            for col in sel.find_all(exp.Column):
                if col.find_ancestor(exp.Select) is not sel:
                    continue  # belongs to a nested scope
                owner = scope.owner(col)
                if owner is None:
                    key = global_alias.get((col.table or "").lower())  # correlated reference to an enclosing scope
                    if key is None:
                        continue
                else:
                    key = scope.tables[owner].name.lower()
                if key not in columns_used or col.name.upper() == "ROWNUM":  # ROWNUM is a pseudo-column
                    continue
                columns_used[key].add("*" if isinstance(col.this, exp.Star) else col.name)
            for p in sel.expressions:
                if isinstance(p, exp.Star):
                    for alias, t in scope.tables.items():
                        if t.name.lower() in columns_used:
                            columns_used[t.name.lower()].add("*")
        # build fetch specs
        fetch: list[FetchSpec] = []
        cross_partition = False
        for key, spec in specs.items():
            used = columns_used.get(key, set())
            meta = self.registry.get(spec.table)
            if meta is None:
                spec.columns = None
                notes.append(f"{spec.table}: schema unknown, fetching all columns; runtime must infer types")
            elif "*" in used:
                spec.columns = list(meta.columns)
            else:
                known = {c.lower(): c for c in meta.columns}
                wanted = {c.lower() for c in used} | {c.lower() for c in meta.primary_key}  # keys let H2 de-duplicate
                cols = [c for c in meta.columns if c.lower() in wanted]
                missing = [c for c in used if c.lower() not in known]
                if missing:
                    unresolved.append(f"{spec.table}: columns {missing} not in schema")
                spec.columns = cols
            spec.access_path = self._access_path(meta, spec.predicates)
            cross_partition |= spec.access_path == "CROSS_PARTITION"
            spec.max_rows = self.row_limit
            spec.scalardb_sql = self._fetch_sql(spec)
            fetch.append(spec)
        # merge duplicate fetches of the same table (same alias in different scopes)
        residual = self._residual(node, source_sql, unresolved, notes)
        pattern = self._pattern(error_codes, node)
        return Plan(pattern=pattern, source_dialect=self.dialect, source_sql=source_sql, fetch=fetch,
                    residual=residual, write=None,
                    guardrails={"requires_cross_partition_scan": cross_partition, "row_limit": self.row_limit},
                    unresolved=unresolved, notes=notes)

    # -- pushdown -----------------------------------------------------------------------------------
    def _pushdown(self, sel: exp.Select, scope: Scope, alias: str, table: exp.Table, notes: list[str]) -> list:
        where = sel.args.get("where")
        if where is None:
            return []
        cond = self._push_not(where.this.copy(), False)
        cond = self._expand_in(cond)
        try:
            cond = normalize(cond, dnf=False, max_distance=128)  # CNF: AND of ORs
        except Exception:  # noqa: BLE001
            return []
        out: list = []
        for conjunct in _flatten(cond, exp.And):
            group = []
            for leaf in _flatten(conjunct, exp.Or):
                p = self._leaf_predicate(_unparen(leaf), scope, alias)
                if p is None:
                    group = None
                    break
                if alias in scope.outer_joined and p.op in ("IS NULL", "IS NOT NULL"):
                    group = None  # NULL tests on an outer-joined side must see the join result
                    break
                group.append(p)
            if group is None:
                continue
            out.append(group[0] if len(group) == 1 else group)
        if out:
            notes.append(f"{table.name}: pushdown {' AND '.join(self._group_sql(g) for g in out)}")
        return out

    @staticmethod
    def _group_sql(g) -> str:
        return _pred_sql(g) if isinstance(g, Predicate) else "(" + " OR ".join(_pred_sql(p) for p in g) + ")"

    def _leaf_predicate(self, leaf: exp.Expression, scope: Scope, alias: str) -> Predicate | None:
        try:
            if isinstance(leaf, exp.Not):
                inner = _unparen(leaf.this)
                if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null) and isinstance(inner.this, exp.Column):
                    return self._own(inner.this, scope, alias, "IS NOT NULL")
                if isinstance(inner, exp.Like) and isinstance(inner.this, exp.Column):
                    return self._own(inner.this, scope, alias, "NOT LIKE", _literal_value(inner.expression))
                return None
            if isinstance(leaf, exp.Is) and isinstance(leaf.expression, exp.Null) and isinstance(leaf.this, exp.Column):
                return self._own(leaf.this, scope, alias, "IS NULL")
            if isinstance(leaf, exp.Like) and isinstance(leaf.this, exp.Column):
                return self._own(leaf.this, scope, alias, "LIKE", _literal_value(leaf.expression))
            if isinstance(leaf, exp.Between) and isinstance(leaf.this, exp.Column):
                return self._own(leaf.this, scope, alias, "BETWEEN",
                                 [_literal_value(leaf.args["low"]), _literal_value(leaf.args["high"])])
            if type(leaf) in COMPARE_OPS:
                op = COMPARE_OPS[type(leaf)]
                lhs, rhs = leaf.this, leaf.expression
                if isinstance(rhs, exp.Column) and not isinstance(lhs, exp.Column):
                    lhs, rhs, op = rhs, lhs, FLIP_OPS[op]
                if isinstance(lhs, exp.Column) and not isinstance(_unparen(rhs), exp.Column):
                    return self._own(lhs, scope, alias, op, _literal_value(rhs))
        except NotDecomposable:
            return None
        return None

    @staticmethod
    def _own(col: exp.Column, scope: Scope, alias: str, op: str, value=None) -> Predicate | None:
        if scope.owner(col) != alias or col.name.upper() == "ROWNUM":
            return None
        return Predicate(col.name, op, value)

    def _push_not(self, e: exp.Expression, negate: bool) -> exp.Expression:
        e = _unparen(e)
        if isinstance(e, exp.Not):
            return self._push_not(e.this, not negate)
        if isinstance(e, (exp.And, exp.Or)):
            l, r = self._push_not(e.this, negate), self._push_not(e.expression, negate)
            if negate:
                return exp.Or(this=l, expression=r) if isinstance(e, exp.And) else exp.And(this=l, expression=r)
            return type(e)(this=l, expression=r)
        if not negate:
            return e
        neg = {exp.EQ: exp.NEQ, exp.NEQ: exp.EQ, exp.GT: exp.LTE, exp.LTE: exp.GT, exp.LT: exp.GTE, exp.GTE: exp.LT}
        if type(e) in neg:
            return neg[type(e)](this=e.this, expression=e.expression)
        return exp.Not(this=e)

    def _expand_in(self, e: exp.Expression) -> exp.Expression:
        e = _unparen(e)
        if isinstance(e, (exp.And, exp.Or)):
            return type(e)(this=self._expand_in(e.this), expression=self._expand_in(e.expression))
        neg = isinstance(e, exp.Not) and isinstance(_unparen(e.this), exp.In)
        target = _unparen(e.this) if neg else e
        if isinstance(target, exp.In) and target.expressions and not target.args.get("query"):
            col = target.this
            if neg:
                parts = [exp.NEQ(this=col.copy(), expression=v) for v in target.expressions]
                return exp.and_(*parts) if len(parts) > 1 else parts[0]
            parts = [exp.EQ(this=col.copy(), expression=v) for v in target.expressions]
            return exp.or_(*parts) if len(parts) > 1 else parts[0]
        if isinstance(target, exp.In):
            return exp.Boolean(this=True)  # IN (subquery): cannot push down, treat as no-op for fetch
        return e

    # -- access path ---------------------------------------------------------------------------------
    @staticmethod
    def _access_path(meta: TableMeta | None, preds: list) -> str:
        if meta is None:
            return "UNKNOWN"
        eq = {p.column.lower() for p in preds if isinstance(p, Predicate) and p.op == "="}
        pk = [c.lower() for c in meta.primary_key]
        pkey = [c.lower() for c in meta.partition_key]
        if pk and all(c in eq for c in pk):
            return "GET"
        if pkey and all(c in eq for c in pkey):
            return "PARTITION_SCAN"
        if any(c.lower() in eq for c in meta.secondary_indexes):
            return "INDEX_SCAN"
        return "CROSS_PARTITION"

    @staticmethod
    def _fetch_sql(spec: FetchSpec) -> str:
        cols = ", ".join(spec.columns) if spec.columns else "*"
        name = f"{spec.namespace}.{spec.table}" if spec.namespace else spec.table
        where = " AND ".join(Decomposer._group_sql(g) for g in spec.predicates)
        return f"SELECT {cols} FROM {name}" + (f" WHERE {where}" if where else "")

    # -- residual -----------------------------------------------------------------------------------
    def _residual(self, node: exp.Expression, source_sql: str, unresolved: list[str], notes: list[str]) -> dict:
        java_sql = self._java_residual(node, source_sql, unresolved, notes)
        try:
            py_node = node.copy()
            py_node = self._rownum_to_limit(py_node)
            python_sql = py_node.sql(dialect="sqlite")
            for c in py_node.find_all(exp.Column):
                if c.name.upper() == "ROWNUM":
                    unresolved.append("python: ROWNUM in a form SQLite cannot express")
        except Exception as e:  # noqa: BLE001
            python_sql = ""
            unresolved.append(f"python: sqlite transpile failed: {e}")
        return {"java": {"engine": "h2", "mode": H2_MODE[self.dialect], "sql": java_sql},
                "python": {"engine": "sqlite3", "sql": python_sql}}

    _TRUNC_UNITS = {"MM": "MONTH", "MON": "MONTH", "MONTH": "MONTH", "RM": "MONTH", "YYYY": "YEAR", "YEAR": "YEAR",
                    "YY": "YEAR", "Y": "YEAR", "SYYYY": "YEAR", "DD": "DAY", "DDD": "DAY", "J": "DAY", "HH": "HOUR",
                    "HH12": "HOUR", "HH24": "HOUR", "MI": "MINUTE", "Q": "QUARTER", "IW": "WEEK", "WW": "WEEK"}

    def _is_datelike(self, e: exp.Expression) -> bool:
        e = _unparen(e)
        if isinstance(e, exp.Column):
            for meta in (self.registry.get(t.name) for t in e.find_ancestor(exp.Select, exp.SetOperation).find_all(exp.Table)) if e.find_ancestor(exp.Select, exp.SetOperation) else []:
                if meta and any(c.lower() == e.name.lower() and ty in ("DATE", "TIMESTAMP", "TIMESTAMPTZ") for c, ty in meta.columns.items()):
                    return True
            return False
        if isinstance(e, exp.Cast):
            return e.to.this in (exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.DATETIME)
        if isinstance(e, (exp.StrToDate, exp.StrToTime, exp.TsOrDsToDate, exp.DateTrunc, exp.CurrentDate, exp.CurrentTimestamp)) \
                or re.search(r"Date|Timestamp|TsOrDs", type(e).__name__):
            return True
        if isinstance(e, exp.Anonymous) and e.name.upper() in ("TRUNC", "ADD_MONTHS", "LAST_DAY", "NEXT_DAY", "SYSDATE", "DATE_TRUNC"):
            return True
        return False

    def _h2_rewrites(self, node: exp.Expression, notes: list[str], unresolved: list[str]) -> bool:
        """Rewrite Oracle constructs H2 Oracle mode lacks. Returns True if the AST changed."""
        changed = False
        for dt in list(node.find_all(exp.DateTrunc)):  # TRUNC(date, 'MM') -> DATE_TRUNC('MONTH', date)
            unit = (dt.args.get("unit").name if dt.args.get("unit") else "DD").upper()
            if unit in self._TRUNC_UNITS:
                dt.replace(exp.Anonymous(this="DATE_TRUNC", expressions=[exp.Literal.string(self._TRUNC_UNITS[unit]), dt.this]))
                changed = True
            else:
                unresolved.append(f"java: TRUNC(date, '{unit}') has no H2 equivalent")
        # NULL ordering differs per engine and H2's compatibility modes do not reproduce it: Oracle and PostgreSQL
        # sort NULLs last for ASC (first for DESC), MySQL and H2 sort them first for ASC. sqlglot keeps the source
        # default in the AST, so it is enough to render the residual SQL with a generator that treats NULLs as small
        # (H2's rule): every ordering that differs from it then comes out as an explicit NULLS FIRST / NULLS LAST.
        # Verified against Oracle Database 23ai: ORDER BY over a nullable column put the NULL group last, H2 first.
        for o in list(node.find_all(exp.Ordered)):
            if o.args.get("nulls_first") is None:
                o.set("nulls_first", (self.dialect == "mysql") != bool(o.args.get("desc")))
            changed = True
        for sub in list(node.find_all(exp.Sub)):  # date - date -> fractional days (Oracle) instead of an INTERVAL (H2)
            if self._is_datelike(sub.this) and self._is_datelike(sub.expression):
                sub.replace(exp.Anonymous(this="DAYS_BETWEEN", expressions=[sub.this, sub.expression]))
                changed = True
        if changed:
            notes.append("java: Oracle semantics made explicit for H2 (date functions, NULL ordering)")
        return changed

    def _java_residual(self, node: exp.Expression, source_sql: str, unresolved: list[str], notes: list[str]) -> str:
        work = node.copy()
        if self._h2_rewrites(work, notes, unresolved):
            self._rewritten = True
        sql = work.sql(dialect=_h2_target_dialect(self.dialect)) if self._rewritten else source_sql
        if self.dialect == "mysql":
            # H2 MySQL mode lacks DATE_FORMAT; FORMATDATETIME takes a Java pattern
            def repl(m):
                return f"FORMATDATETIME({m.group(1)}, '{_mysql_fmt_to_java(m.group(2))}')"
            new = re.sub(r"DATE_FORMAT\(\s*([^,]+?)\s*,\s*'([^']*)'\s*\)", repl, sql, flags=re.I)
            if new != sql:
                notes.append("java: DATE_FORMAT rewritten to FORMATDATETIME for H2")
                sql = new
        # strip schema qualifiers from table names: fetched tables live unqualified in H2
        for t in node.find_all(exp.Table):
            if t.db:
                sql = re.sub(rf"\b{re.escape(t.db)}\.{re.escape(t.name)}\b", t.name, sql)
        return sql.rstrip(";")

    @staticmethod
    def _rownum_to_limit(node: exp.Expression) -> exp.Expression:
        for sel in node.find_all(exp.Select):
            where = sel.args.get("where")
            if where is None:
                continue
            keep = []
            for leaf in _flatten(where.this, exp.And):
                u = _unparen(leaf)
                if isinstance(u, (exp.LTE, exp.LT)) and isinstance(u.this, exp.Column) and u.this.name.upper() == "ROWNUM" \
                        and isinstance(u.expression, exp.Literal) and not sel.args.get("limit"):
                    n = int(u.expression.name) - (1 if isinstance(u, exp.LT) else 0)
                    sel.set("limit", exp.Limit(expression=exp.Literal.number(n)))
                else:
                    keep.append(leaf)
            sel.set("where", exp.Where(this=exp.and_(*keep)) if keep else None)
        return node

    @staticmethod
    def _pattern(codes: set[str], node: exp.Expression) -> str:
        order = [("PROJECTION", "P1"), ("PRED", "P2"), ("COL_COL", "P2"), ("DISTINCT", "P3"), ("AGG_DISTINCT", "P3"),
                 ("OFFSET", "P4"), ("SUBQUERY", "P5"), ("CTE", "P6"), ("SET_OP", "P6"), ("AGG", "P7"),
                 ("JOIN_KEY", "P8"), ("JOIN", "P8"), ("JOIN_ON", "P8"), ("JOIN_SCOPE", "P8"), ("ORACLE_JOIN_MARK", "P8")]
        found = [p for c, p in order if c in codes]
        return "+".join(dict.fromkeys(found)) if found else "P1"


def _mysql_fmt_to_java(fmt: str) -> str:
    table = {"%Y": "yyyy", "%y": "yy", "%m": "MM", "%c": "M", "%d": "dd", "%e": "d", "%H": "HH", "%k": "H",
             "%h": "hh", "%I": "hh", "%i": "mm", "%s": "ss", "%S": "ss", "%f": "SSSSSS", "%p": "a", "%M": "MMMM",
             "%b": "MMM", "%W": "EEEE", "%a": "EEE", "%j": "DDD", "%%": "%"}
    out = ""
    i = 0
    while i < len(fmt):
        if fmt[i] == "%" and i + 1 < len(fmt) and fmt[i:i + 2] in table:
            out += table[fmt[i:i + 2]]
            i += 2
        else:
            ch = fmt[i]
            out += f"'{ch}'" if ch.isalpha() else ch
            i += 1
    return out
