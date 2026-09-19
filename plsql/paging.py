"""#19 の決定（2026-09-19）: 割った routine の処理対象（Targets）を、**件数つきで繰り返し読む**。

1 反復 = 1 トランザクションに割った routine（`transactions.perIteration`）は、処理対象を先に全部読む。
その件数は「その日たまたま溜まった数」で、業務では決められない——上限に当たれば夜間バッチが止まり、
翌朝まで誰も気づかない。

**件数つきで繰り返し読めば、上限は「何行来うるか」ではなく「1 回に何行ずつ取るか」になる。** これは
業務の数ではなく運用の調整値である。

    SELECT order_id FROM orders WHERE status = 'SHIPPED' AND ordered_at < p_batch_date

    -> SELECT order_id FROM orders WHERE status = 'SHIPPED' AND ordered_at < p_batch_date
         AND order_id > p_after_key ORDER BY order_id FETCH FIRST p_batch ROWS ONLY

**キー順に先へ進める形（keyset）に揃える。** 処理すると対象から外れる routine（`nightly_close` は
SHIPPED -> CLOSED、`purge_audit` は DELETE）なら同じ問い合わせを繰り返せば済むが、外れない routine
（`reprice_all` は status を変えない）は同じ行が返って終わらない。keyset はどちらでも正しいので、
1 つの形にする。並べ替えはパーティションをまたぐが、移行先は JDBC に限定した（#20）。

**変わること**: Oracle は OPEN の時点の集合を回した。こちらはページごとに読むので、途中で増えた行
（キーが先にあるもの）も回る。1 反復 = 1 トランザクションにした時点で「読んだ時点と処理する時点が
ずれる」ことは受け入れてあり（transaction-patterns §E）、これはその延長である。

組めないとき（問い合わせが主キーを 1 列で選んでいない、キーが数でない、結果に効く並べ替えがある）は
触らない。今までどおり全部読み、行数の上限を人に求める。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .ir import model as M
from .limits import Boundaries
from .symbols import OracleSchema, Symbol, SymbolTable

AFTER, BATCH = "p_after_key", "p_batch"


def rewrite(program: M.Program, boundaries: Boundaries | None, schema: OracleSchema | None,
            symbols: SymbolTable | None = None) -> None:
    decided = boundaries or Boundaries()
    for module in program.modules:
        for routine in module.routines:
            if routine.id not in decided.per_iteration:
                continue
            loops = [s for s in routine.body if s.kind == "Loop"]
            if len(loops) != 1 or loops[0].loop_kind != "cursor-for" or loops[0].query is None:
                continue
            _page(routine, loops[0], schema, symbols)


def _joins_keep_one_row_per_key(tree: exp.Select, schema: OracleSchema | None) -> bool:
    """Every join reaches at most one row: it is to a base table, and its ON pins that table's whole primary key
    with equalities (`JOIN customers c ON c.customer_id = o.customer_id`). Then the driving table's key is still
    unique in the result. Anything else -- a join to the many side, to a subquery, on part of a key, a comma join
    -- may repeat the key, and is not shown here to be safe."""
    for join in tree.args.get("joins") or []:
        joined = join.this
        if schema is None or not isinstance(joined, exp.Table) or join.args.get("using"):
            return False
        if (join.side or "").upper() in ("RIGHT", "FULL") or (join.kind or "").upper() == "CROSS":
            return False
        key = {c.lower() for c in schema.primary_key(joined.name.lower())}
        on = join.args.get("on")
        if not key or on is None:
            return False
        pinned: set[str] = set()
        for leaf in on.flatten() if isinstance(on, exp.And) else [on]:
            leaf = leaf.unnest()
            if not isinstance(leaf, exp.EQ):
                return False
            for side, other in ((leaf.this, leaf.expression), (leaf.expression, leaf.this)):
                if isinstance(side, exp.Column) and (side.table or "").lower() == joined.alias_or_name.lower() \
                        and not (isinstance(other, exp.Column)
                                 and (other.table or "").lower() == joined.alias_or_name.lower()):
                    pinned.add(side.name.lower())
        if not key <= pinned:
            return False
    return True


def _page(routine: M.Routine, loop: M.Loop, schema: OracleSchema | None, symbols: SymbolTable | None) -> None:
    query = loop.query
    try:
        tree = sqlglot.parse_one(query.original_sql or "", dialect="oracle")
    except Exception:
        return
    if not isinstance(tree, exp.Select) or tree.args.get("limit") or tree.args.get("fetch"):
        return
    source = tree.args.get("from_") or tree.args.get("from")
    if source is None or not isinstance(source.this, exp.Table):
        return
    # keyset は「キーが結果の中で一意」でないと成り立たない。`orders o JOIN order_lines l` は 1 つの order_id に
    # 何行も返し、ページの境目がその途中に来ると、`order_id > :after` が残りの行を飛ばす——エラーにはならず、
    # 処理されない明細が出るだけである。DISTINCT や集約も、行とキーの対応を崩す。こういう形は割らない
    shape = next((label for label, present in (
        ("1 対多になりうる JOIN", not _joins_keep_one_row_per_key(tree, schema)),
        ("DISTINCT", tree.args.get("distinct")),
        ("GROUP BY", tree.args.get("group")), ("HAVING", tree.args.get("having")),
        ("集約・ウィンドウ関数", any(e.find(exp.AggFunc, exp.Window) for e in tree.expressions))) if present), None)
    if shape is not None:
        query.add("WARN", "PAGING_REFUSED",
                  f"{shape} のある問合せはキー順のページに割らない。1 つのキーに複数の行が対応しうるので、"
                  f"ページの境目で行を取りこぼす。走査行数の上限（limits.yaml の scanRows）の下で 1 回で読む")
        return
    table = source.this.name.lower()
    alias = source.this.alias_or_name
    key = schema.primary_key(table) if schema is not None else []
    if len(key) != 1:
        return   # 複合キーの keyset は比較の形が変わる。まずは 1 列だけを扱う
    selected = [e for e in tree.expressions
                if isinstance(e, exp.Column) and e.name.lower() == key[0]
                and (not e.table or e.table.lower() == alias.lower())]
    if not selected:
        return   # 行からキーが取れない。次のページの起点が分からない
    kind = (schema.column(table, key[0]) or "").upper() if schema is not None else ""
    if not kind.startswith("NUMBER"):
        return   # 最初のページの起点（いちばん小さい値）を数でしか決めていない
    order = tree.args.get("order")
    if order is not None:
        ordered = [o.this for o in order.expressions]
        if not (len(ordered) == 1 and isinstance(ordered[0], exp.Column) and ordered[0].name.lower() == key[0]):
            return   # 結果に効く並べ替えがある。キー順に変えると回す順が変わる
    column = selected[0].sql(dialect="oracle")
    tree = tree.where(f"{column} > {AFTER}", dialect="oracle", copy=False)
    tree.set("order", exp.Order(expressions=[exp.Ordered(this=sqlglot.parse_one(column, dialect="oracle"))]))
    sql = tree.sql(dialect="oracle") + f" FETCH FIRST {BATCH} ROWS ONLY"
    query.original_sql = sql
    loop.cursor = f"{loop.variable or 'r'} IN ({sql})"
    query.add("INFO", "PAGED",
              f"処理対象を {key[0]} の順に {BATCH} 件ずつ読む（#19 の決定）。上限は「何行来うるか」ではなく"
              f"「1 回に何行ずつ取るか」になる。ページごとに読むので、途中で増えた行も回る")
    for name, oracle in ((AFTER, schema.column(table, key[0])), (BATCH, "PLS_INTEGER")):
        type_ = M.TypeRef(oracle=oracle, resolved=oracle, origin="declared")
        routine.parameters.append(M.Parameter(id=f"{routine.id}#param-{name}", kind="Parameter", name=name,
                                              direction="IN", type=type_))
        scope = symbols.scopes.get(routine.id) if symbols is not None else None
        if scope is not None:
            scope.declare(Symbol(name=name, kind="parameter", scope=routine.id, type=type_, direction="IN"))
    loop.paged_key = key[0]
