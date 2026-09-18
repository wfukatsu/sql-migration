"""#9: `UPDATE t SET c = c + :x` を「同じトランザクションの中で読んでから書く」へ移す。

Oracle のこの形は**行ロックの下で原子的**である。ScalarDB SQL は列を読む式を受け付けない
（`ERROR RMW`: 値を持っているのはデータベースであってアプリではない）ので、置き換えるなら
読みを明示するしかない:

    UPDATE products SET stock_qty = stock_qty + :qty WHERE product_id = :id;

    -> SELECT stock_qty INTO v_rmw_1 FROM products WHERE product_id = :id;
       UPDATE products SET stock_qty = v_rmw_1 + :qty WHERE product_id = :id;

**安全なのは同じトランザクションの中で読んで書くから**で、衝突は Consensus Commit が弾く
（P3-4 で実測）。弾かれたものを再試行するのは呼び出し側の責務である。だから **A 型と同じ決定が
要る**——`limits.yaml` の `rowLocks.optimistic` に記録された routine だけを書き換える。

同じ行を 1 つのトランザクションが 2 回触る場合（注文明細に同じ商品が 2 行ある、など）に
更新が失われないことは、`RmwIT` が実クラスタで測ってある——**自分の書き込みは読める**。
測るまでは仮定にできない性質だった。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .ir import model as M
from .limits import RowLocks
from .symbols import OracleSchema, Symbol, SymbolTable

PREFIX = "v_rmw_"


def rewrite(program: M.Program, row_locks: RowLocks | None, schema: OracleSchema | None,
            symbols: SymbolTable | None = None) -> None:
    """記録された routine の RMW を、読み + 書きの 2 文へ置き換える（in place）。

    作った変数は **symbol table にも登録する**。登録しないと、SQL の側はそれを変数と知らず、
    素の識別子＝列として読む——`SET stock_qty = v_rmw_1 + :qty` が再び「列を読む式」になり、
    割った意味が無くなる（実際そうなった）。
    """
    locks = row_locks or RowLocks()
    for module in program.modules:
        for routine in module.routines:
            if not locks.decided(routine.id):
                continue   # 決めた人がいない。ロックが落ちたままの書き換えは進めない
            before = len(routine.declarations)
            routine.body = _sequence(routine.body, routine, schema)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, routine, schema)
            _declare(symbols, routine, routine.declarations[before:])


def _declare(symbols: SymbolTable | None, routine: M.Routine,
             declarations: list[M.Declaration]) -> None:
    scope = symbols.scopes.get(routine.id) if symbols else None
    if scope is None:
        return
    for declaration in declarations:
        scope.declare(Symbol(name=declaration.name, kind="variable", scope=routine.id,
                             type=declaration.type, source_range=declaration.source_range))


def _sequence(statements: list[M.Statement], routine: M.Routine,
              schema: OracleSchema | None) -> list[M.Statement]:
    out: list[M.Statement] = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, schema))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, schema)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, routine, schema)
        read = _split(statement, routine, schema)
        if read is not None:
            out.append(read)
        out.append(statement)
    return out


def _split(statement: M.Statement, routine: M.Routine,
           schema: OracleSchema | None) -> M.SqlOperation | None:
    """`SET c = <c を読む式>` を、読みの文とそれを使う書きに割る。割れなければ None。"""
    if statement.kind != "SqlOperation" or (statement.sql_kind or "").upper() != "UPDATE":
        return None
    try:
        tree = sqlglot.parse_one(statement.original_sql or "", dialect="oracle")
    except Exception:
        return None
    if not isinstance(tree, exp.Update):
        return None
    table = tree.this.name if isinstance(tree.this, exp.Table) else None
    if not table:
        return None
    assignments = [a for a in (tree.expressions or []) if isinstance(a, exp.EQ)]
    reads: list[tuple[str, str]] = []   # (列, 読み込み先の変数)
    for assignment in assignments:
        target = assignment.this
        if not isinstance(target, exp.Column):
            return None
        column = target.name.lower()
        referenced = [n for n in assignment.expression.walk()
                      if isinstance(n, exp.Column) and n.name.lower() == column]
        if not referenced:
            continue
        if len(referenced) != 1:
            return None   # 同じ列を 2 回読む式。1 つの読みでは置き換えられない
        variable = _free_name(routine, reads)
        referenced[0].replace(exp.column(variable))
        reads.append((column, variable))
    if not reads:
        return None
    if len(reads) != 1:
        return None   # 複数列の RMW。読みが複数要るので、まずは 1 つだけを扱う
    column, variable = reads[0]
    where = tree.args.get("where")
    if where is None:
        return None   # 全行更新。キーで届かない読みになるので触らない
    routine.declarations.append(_declaration(routine, variable, table, column, schema, statement))
    statement.original_sql = tree.sql(dialect="oracle")
    statement.add("INFO", "RMW_SPLIT",
                  f"{table}.{column} を読んでから書く 2 文に割った。ScalarDB SQL は列を読む式を"
                  f"受け付けないので、値はアプリで計算する。**同じトランザクションの中で読んで書く**"
                  f"ので、衝突は commit で弾かれる（再試行は呼び出し側の責務・#9）")
    read = f"SELECT {column} FROM {table} {where.sql(dialect='oracle')}"
    # 生成される method 名は `<routine>Stmt<id の末尾>` である。書きの文と並べて読めるように、
    # その番号に `read` を足した形にする（`cancelStmt6` の相方が `cancelStmt6read`）
    return M.SqlOperation(id=f"{statement.id}read", kind="SqlOperation",
                          source_range=statement.source_range, sql_kind="SELECT",
                          original_sql=read, into_targets=[variable], cardinality="EXACTLY_ONE")


def _free_name(routine: M.Routine, taken: list[tuple[str, str]]) -> str:
    """まだ使われていない `v_rmw_N`。元のコードが同じ名前を持っていても踏まない。"""
    used = {d.name.lower() for d in routine.declarations} | {v.lower() for _, v in taken} \
        | {p.name.lower() for p in routine.parameters}
    index = 1
    while f"{PREFIX}{index}" in used:
        index += 1
    return f"{PREFIX}{index}"


def _declaration(routine: M.Routine, variable: str, table: str, column: str,
                 schema: OracleSchema | None, statement: M.Statement) -> M.Declaration:
    oracle = (schema.column(table, column) if schema else None) or f"{table}.{column}%TYPE"
    return M.Declaration(id=f"{routine.id}#decl-{variable}", kind="Declaration", name=variable,
                         source_range=statement.source_range,
                         type=M.TypeRef(oracle=oracle, resolved=oracle, origin="column-type"))
