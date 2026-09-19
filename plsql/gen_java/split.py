"""#24 / #14: 決めた routine を、**トランザクション単位に割った部品**として出す。

決定は #3（`docs/plsql-transaction-patterns.md` §E / §F / §G）、形は #24 / #14 である:

* 分割系は **1 反復 = 1 トランザクション**
* ロールバックのあとに書く行（エラー行）は**別トランザクション**
* `batch_control` のような高衝突点も**別トランザクション**
* **ループは呼び出し側にある。** 生成コードは begin も commit もしない（計画 §9）

ここが出すのは部品と、推奨の回し方の**コメント**である。ループをコードとして出さないのは、
1 反復ごとに境界へ踏み込むことになるからで、再試行・並列度・中断再開は移行者がその場で
設計する。コメントは出発点であって、決定ではない。

```
<routine>Start()        ループの前。別トランザクション
<routine>Targets(...)   回す対象を返す問い合わせ
<routine>One(行, ...)   1 反復 = 1 トランザクションの中身
<routine>Failed(行, 例外, ...)  失敗した 1 反復の記録。別トランザクション
<routine>Done()         ループの後。別トランザクション
<routine>FailedBatch()  routine 全体の handler。別トランザクション
```

**割ってよいと決めた routine だけ**を割る（`limits.yaml` の `transactions.perIteration`）。
決めていない routine はいままでどおり 1 つの method として出て、`COMMIT` のところで止まる。
止まっているのが正しい——境界をどこに引くかは業務の設計である。
"""

from __future__ import annotations

import contextvars
import copy
import re

from dataclasses import dataclass, field

from ..ir import model as M
from ..limits import Boundaries
from ..lower import _walk
from .emit import JavaFile
from .types import java_name, routine_stem

_BOUNDARIES: "contextvars.ContextVar[Boundaries]" = contextvars.ContextVar(
    "boundaries", default=Boundaries())


def set_boundaries(boundaries: Boundaries) -> None:
    _BOUNDARIES.set(boundaries)


def runs_separately(routine_id: str) -> bool:
    """別のトランザクションで回すと決めてある routine か（自律トランザクション / #3 §G）。"""
    return routine_id in _BOUNDARIES.get().separate


# handler の中の `SQLERRM` は「いま処理している例外の文言」である。割ったあとは、それを持って
# いるのは呼び出し側が渡す例外なので、そこから読む
FAILED = "failed"
SQLERRM_CALL = re.compile(r"\bSQLERRM\s*\((?:[^()]|\([^()]*\))*\)", re.IGNORECASE)
BULK_ERROR_INDEX = re.compile(
    r"\bSQL%BULK_EXCEPTIONS\s*\(\s*[\w$#]+\s*\)\s*\.\s*ERROR_INDEX\b", re.IGNORECASE)
BULK_COUNT_BOUND = re.compile(
    r"^\s*(?P<index>[\w$#]+)\s+IN\s+1\s*\.\.\s*SQL%BULK_EXCEPTIONS\s*\.\s*COUNT\s*$", re.IGNORECASE)
# `FORALL ... SAVE EXCEPTIONS` がまとめて投げる Oracle の誤り。1 要素 = 1 トランザクションに
# 割ったあとは、失敗は要素ごとに来るので、この番号での分岐は無くなる
BULK_ERRORS = "-24381"


# `COMMIT` / `ROLLBACK` / `SAVEPOINT` / `ROLLBACK TO`。IR では種別が節点の `kind` に入っている
TRANSACTION = ("Commit", "Rollback", "Savepoint")


def _is_transaction(statement: M.Statement) -> bool:
    return isinstance(statement, M.TransactionStatement) or statement.kind in TRANSACTION


@dataclass
class Shape:
    """割った結果。`None` になる部分は、その routine に無かったということである。"""

    kind: str                      # cursor | forall
    loop: M.Loop
    before: list[M.Statement] = field(default_factory=list)
    one: list[M.Statement] = field(default_factory=list)
    failed: list[M.Statement] = field(default_factory=list)
    after: list[M.Statement] = field(default_factory=list)
    failed_batch: list[M.Statement] = field(default_factory=list)
    index: str | None = None       # FORALL の添字。失敗した要素の位置でもある


def emit(file: JavaFile, module: M.Module, routine: M.Routine, result, domain_package: str) -> bool:
    """割って出したら True。割らない（決めていない・形が合わない）なら False で、呼び出し側が
    いままでどおり 1 つの method として出す。"""
    from .service import _method, _source_comment

    if not _BOUNDARIES.get().decided(routine.id):
        return False
    if routine.id in _BOUNDARIES.get().separate:
        return _separate(file, module, routine, result, domain_package)
    shape = _shape(routine)
    if shape is None:
        # 決めてあっても、形が合わなければ割らない。**合わない形を割ると、元と違うことをする**
        file.comment(f"{routine.id} は 1 反復 = 1 トランザクションに割ると決めてあるが、"
                     f"この routine の形（ループが 1 つ、その中が 1 反復）に当てはまらないので"
                     f"割っていない。境界は人が決める")
        return False

    parts = _parts(file, module, routine, shape, result, domain_package)
    _caller_comment(file, routine, shape, parts)
    for index, part in enumerate(parts):
        if index:
            file.line()
        part.emit()
    return True


def _separate(file: JavaFile, module: M.Module, routine: M.Routine, result,
              domain_package: str) -> bool:
    """自分だけで 1 つのトランザクションになる routine（自律トランザクション / #3 §G）。

    `PRAGMA AUTONOMOUS_TRANSACTION` の routine は、**親のトランザクションが rollback しても残る**。
    Oracle 23ai で実測した性質である。移行先で同じにするには、呼び出し側が**別のトランザクションで**
    呼ぶしかない——同じ中で呼べば、親と一緒に消える。

    だから生成するのは中身だけで、`COMMIT` / `ROLLBACK` は出さない。境界は呼び出し側にある
    （計画 §9）。`WHEN OTHERS THEN ROLLBACK; RAISE;` は「失敗したら巻き戻して投げ直す」で、
    別のトランザクションで呼ぶ側がまさにそれをするので、handler ごと出さない——出すと
    `RAISE` が別の例外に包み直され、**元の例外が変わる**。
    """
    from .service import _method

    handlers = []
    for handler in routine.exception_handlers:
        body = _plain(handler.body)
        if len(body) == 1 and body[0].kind == "Raise" and body[0].error_code is None \
                and not body[0].exception:
            continue   # 投げ直すだけ。呼び出し側の境界が同じことをする
        if body:
            handlers.append(type(handler)(**{**handler.__dict__, "body": body}))
    synthetic = M.Routine(**{**routine.__dict__, "body": _plain(routine.body),
                             "exception_handlers": handlers})
    name = java_name(routine_stem(routine))
    for line in ["**別のトランザクションで呼ぶ**（自律トランザクション / #3 §G）。",
                 "呼び出し側のトランザクションの中で呼ぶと、親が rollback したときに一緒に消える——",
                 "Oracle では消えなかった（23ai で実測）。回し方の出発点:",
                 "",
                 f"  tx.runSeparately(() -> service.{name}(...));   // 親とは別の境界",
                 "",
                 "`COMMIT` / `ROLLBACK` は出していない。境界は呼び出し側にある（計画 §9）。",
                 "**このコメントは出発点であって、決定ではない。**"]:
        file.comment(line)
    _method(file, module, synthetic, result, domain_package,
            notes=[f"{routine.id} は別トランザクションで回すと決めてある"
                   f"（{_BOUNDARIES.get().separate[routine.id]}）"])
    return True


def _parts(file, module, routine, shape, result, domain_package) -> list:
    """出す部品を、出す順に。`Part` は「どう出すか」と「呼び出し側からどう呼ぶか」を同じ 1 か所で
    決める——2 か所で決めると、引数の並びがコメントと signature で食い違う。"""
    from .service import _method, needs_audit

    out: list[Part] = []
    scope, element = _element(file, routine, shape)
    collections = {c.lower() for c in shape_collections(shape)} if shape.kind == "forall" else set()

    def part(suffix, statements, *, carries=(), notes=(), names=None):
        synthetic = _routine(routine, suffix, statements, exclude=collections)
        taken = [c for c in carries if _takes(c, statements)]
        parameters = [(c.java_type, c.name) for c in taken]
        arguments = [c.caller for c in taken]
        if needs_audit(synthetic):
            arguments.append("audit")
        visible = {**scope, **(names or {})}
        out.append(Part(suffix, arguments, lambda: _method(
            file, module, synthetic, result, domain_package,
            method_name=f"{routine.name}_{suffix}", extra_parameters=parameters,
            extra_scope=visible, notes=list(notes) + _carried_over(synthetic))))

    if shape.before:
        part("start", shape.before,
             notes=["ループの前。**別トランザクション**である——`batch_control` のような行は"
                    "高衝突点なので、本体と同じトランザクションに入れない（#3 §D / §F）"])
    if shape.kind == "cursor":
        targets = [name for _, name in _parameters(file, routine, [shape.loop.query])]
        if _query_needs_audit(shape.loop.query):
            targets.append("audit")
        out.append(Part("targets", targets,
                        lambda: _targets(file, routine, shape, result, domain_package)))
    part("one", shape.one, carries=element,
         notes=["**1 反復 = 1 トランザクション**（#3 §E）。呼び出し側がこれを 1 回呼ぶたびに "
                "1 つのトランザクションになる。途中の `COMMIT` は、境界そのものになったので"
                "出していない"])
    if shape.failed:
        part("failed", shape.failed, carries=list(element) + _failure(shape),
             names={"sqlerrm": f"{FAILED}.getMessage()"},
             notes=["失敗した 1 反復の記録。**別トランザクション**である——失敗した"
                    "トランザクションと同じ中に入れると、記録ごと巻き戻る（#3 §F / §G）",
                    f"`SQLERRM` は呼び出し側が渡す例外から読む（`{FAILED}.getMessage()`）"]
             + _position_note(shape))
    if shape.after:
        part("done", shape.after, notes=["ループの後。**別トランザクション**"])
    if shape.failed_batch:
        part("failed_batch", shape.failed_batch,
             notes=["routine 全体の handler。**別トランザクション**である",
                    "`RAISE` は出していない——例外を持っているのは呼び出し側で、"
                    "この部品は記録を書くだけである"])
    return out


@dataclass
class Part:
    """1 つの部品。`arguments` は呼び出し側がこれを呼ぶときに渡すもので、コメントがそれを使う。"""

    suffix: str
    arguments: list[str]
    emit: object

    def call(self, name: str) -> str:
        return f"{name}{''.join(w.title() for w in self.suffix.split('_'))}({', '.join(self.arguments)})"


@dataclass
class Carried:
    """呼び出し側のループが持っていて、部品に渡すもの——行・要素・失敗した位置・例外。"""

    java_type: str
    name: str
    caller: str
    key: str | None = None   # 部品の中でその値を指している PL/SQL の書き方（`p_ids(i)` など）


def _takes(carried: "Carried", statements: list[M.Statement]) -> bool:
    """その部品が本当にその値を読むか。読まないものを引数にすると、呼び出し側に用意させるだけ
    になる（`AuditContext` を使う routine にだけ付けているのと同じ理由）。

    探すのは**参照の書き方そのもの**（`p_deltas(i)`）である。名前を単語に割って探すと、失敗した
    要素の位置に使っている `i` が「コレクションを読んでいる」ことにされてしまう。
    """
    if carried.key is None:
        return True
    pattern = r"\s*".join(re.escape(part) for part in re.findall(r"[\w$#]+|[().]", carried.key))
    return any(re.search(rf"\b{pattern}", text or "", re.IGNORECASE)
               for statement in _walk(statements) for text in _texts(statement))


def _position_note(shape: Shape) -> list[str]:
    """失敗した要素の**位置**は、数え始めが Oracle と違う。黙って 1 ずれるより、書いておく。"""
    if shape.kind != "forall" or not shape.index:
        return []
    return [f"`{shape.index}` は失敗した要素の位置である。Oracle の `ERROR_INDEX` は **1 から**"
            f"数え、生成したループは **0 から**数える——記録に残る値が 1 ずれる。1 から数えた"
            f"値を残すなら、呼び出し側が `{java_name(shape.index)} + 1` を渡す"]


def _failure(shape: Shape) -> list["Carried"]:
    """失敗したときだけ渡すもの。FORALL では**失敗した要素の位置**も渡す——Oracle はそれを
    `SQL%BULK_EXCEPTIONS(i).ERROR_INDEX` から読んでいた。"""
    index = [Carried("int", java_name(shape.index), java_name(shape.index), shape.index)] \
        if shape.kind == "forall" and shape.index else []
    return index + [Carried("Exception", FAILED, FAILED)]


def _carried_over(routine: M.Routine) -> list[str]:
    """反復をまたいで持ち越していた局所変数についての注意書き。

    `v_processed := v_processed + 1` のような数えは、割ったあと **1 回ごとに 0 から始まる**。
    自分の代入の中でしか触られていない変数がそれで、黙って残すと「数えている」ように見える。
    """
    out = []
    for declaration in routine.declarations:
        name = declaration.name.lower()
        references = 0
        for statement in _walk(routine.body):
            if statement.kind == "Assignment" and (statement.target or "").lower() == name:
                continue   # 自分への代入（右辺で自分を読むのも数えない）
            references += sum(1 for text in _texts(statement)
                              if re.search(rf"\b{re.escape(name)}\b", (text or "").lower()))
        if references == 0 and any(s.kind == "Assignment" and (s.target or "").lower() == name
                                   for s in _walk(routine.body)):
            out.append(f"`{declaration.name}` は反復をまたいで数えていた値である。割ったあとは"
                       f"1 回ごとに 0 から始まるので、**数えるのは呼び出し側**になる（#3 §E）")
    return out


def _texts(statement: M.Statement):
    from .service import _expression_texts

    yield from _expression_texts(statement)
    for bind in getattr(statement, "binds", None) or []:
        yield bind.plsql_variable or ""


def _caller_comment(file: JavaFile, routine: M.Routine, shape: Shape, parts: list) -> None:
    """推奨の回し方。**コードとしては出さない**（#24 の決定、2026-09-18）。"""
    name = java_name(routine_stem(routine))
    by_suffix = {part.suffix: part for part in parts}
    lines = ["1 反復 = 1 トランザクション（#3 / transaction-patterns §E）。",
             "**ループは呼び出し側にある**——生成コードは begin も commit もしない（計画 §9）。",
             "回し方の出発点:", ""]
    if "start" in by_suffix:
        lines.append(f"  tx.run(() -> service.{by_suffix['start'].call(name)});")
    paged = shape.kind == "cursor" and shape.loop.paged_key
    if paged:
        variable = java_name(shape.loop.variable or "r")
        lines.append("  BigDecimal after = null;   // 1 回に取る件数（pBatch）は運用の調整値")
        lines.append("  List<...> page;")
        lines.append(f"  while (!(page = tx.run(() -> service.{by_suffix['targets'].call(name)})).isEmpty()) {{")
        lines.append(f"    for (var {variable} : page) {{")
    elif shape.kind == "cursor":
        variable = java_name(shape.loop.variable or "r")
        lines.append(f"  for (var {variable} : "
                     f"tx.run(() -> service.{by_suffix['targets'].call(name)})) {{")
    else:
        collections = shape_collections(shape)
        lines.append(f"  for (int {java_name(shape.index)} = 0; {java_name(shape.index)} < "
                     f"{java_name(collections[0])}.size(); {java_name(shape.index)}++) {{")
    indent = "  " if paged else ""
    lines.append(f"{indent}      try {{ tx.run(() -> service.{by_suffix['one'].call(name)}); }}")
    if "failed" in by_suffix:
        lines.append(f"{indent}      catch (Exception {FAILED}) {{ "
                     f"tx.run(() -> service.{by_suffix['failed'].call(name)}); }}")
    if paged:
        lines.append("    }")
        lines.append(f"    after = {name}After(page.get(page.size() - 1));")
    lines.append("  }")
    if "done" in by_suffix:
        lines.append(f"  tx.run(() -> service.{by_suffix['done'].call(name)});")
    if "failed_batch" in by_suffix:
        lines.append(f"  // 全体が失敗したら: tx.run(() -> service.{by_suffix['failed_batch'].call(name)});")
    lines += ["",
              "再試行・並列度・中断再開はここで設計する。",
              "**このコメントは出発点であって、決定ではない。**"]
    for line in lines:
        file.comment(line)


def shape_collections(shape: Shape) -> list[str]:
    """FORALL が回すコレクション。本体が要素を読んでいるものすべて、名前の出てくる順で。"""
    return list(dict.fromkeys(
        reference.group("collection")
        for statement in _walk(shape.one)
        for bind in (getattr(statement, "binds", None) or [])
        for reference in [COLLECTION_ELEMENT.match(bind.plsql_variable or "")] if reference))


COLLECTION_ELEMENT = re.compile(r"^(?P<collection>[\w$#]+)\s*\(\s*(?P<index>[\w$#]+)\s*\)$")


def _element(file: JavaFile, routine: M.Routine, shape: Shape):
    """1 反復が受け取るもの。cursor なら行、FORALL なら要素である。

    返すのは (式の scope, 渡すもの)。`p_ids(i)` のような参照は Java の名前ではないので、scope で
    「その引数」に結び付ける。
    """
    from .repository import loop_record
    from .types import java_type

    if shape.kind == "cursor":
        record = loop_record(routine, shape.loop)
        variable = java_name(shape.loop.variable or "r")
        file.add_import(f"{_domain()}.{record}")
        key = shape.loop.variable or "r"
        return {key: variable}, [Carried(record, variable, variable, key)]

    scope, carried = {}, []
    # 要素の並びは**元の routine の引数の並び**にする。本体が読んだ順にすると、`p_deltas(i)` が
    # `p_product_ids(i)` より前に出てきただけで引数が入れ替わる——呼び出し側は PL/SQL の
    # signature しか見ていないので、そこから導けない並びにしてはならない
    order = [p.name.lower() for p in routine.parameters]
    for collection in sorted(shape_collections(shape),
                             key=lambda c: order.index(c.lower()) if c.lower() in order else len(order)):
        declared = next((p.type for p in routine.parameters if p.name.lower() == collection.lower()),
                        None)
        mapped = java_type(declared.resolved if declared else None)
        # `List<BigDecimal>` の要素は `BigDecimal`。要素の型が読めなければ `Object` で受ける
        element = mapped.name[len("List<"):-1] if mapped.name.startswith("List<") else "Object"
        file.add_import(*mapped.imports)
        name = java_name(collection) + "Item"
        key = f"{collection}({shape.index})"
        scope[key.lower()] = name
        carried.append(Carried(element, name, f"{java_name(collection)}.get({java_name(shape.index)})",
                               key))
    if shape.index:
        # 失敗した要素の**位置**。Oracle は `SQL%BULK_EXCEPTIONS(i).ERROR_INDEX` で受け取っていた
        # ——1 要素 = 1 トランザクションなら、それを知っているのはループを回している側である
        scope[shape.index.lower()] = java_name(shape.index)
    return scope, carried


def _targets(file: JavaFile, routine: M.Routine, shape: Shape, result, domain_package: str) -> None:
    """回す対象を返す問い合わせ。ループの行を読むだけで、書かない。

    **対象を読むのは 1 反復とは別のトランザクションである。** だから、本体が書く表をこの問い合わせ
    が読んでいても構わない——同じトランザクションで書いた表を読み直せないという制限（P2-4）に
    当たらない。割らずに 1 つの method として出していたときは、まさにそこで拒んでいた。

    代わりに**読んだ時点と処理する時点がずれる**。対象が 1 反復のあいだに変わっていることは
    ありうるので、1 反復の側が自分で確かめる必要がある（`SHIPPED` -> `CLOSED` の遷移がそれを
    持っている、というのが #3 の判断である）。
    """
    from .repository import loop_method, loop_record
    from .service import _arguments

    query = shape.loop.query
    record = loop_record(routine, shape.loop)
    file.add_import(f"{domain_package}.{record}")
    file.add_import("java.util.List")
    parameters = _parameters(file, routine, [query])
    if _query_needs_audit(query):
        # `WHERE changed_at < SYSTIMESTAMP - p_keep_days` の時刻は呼び出し側が渡す（#8）。
        # 渡さずに出すと、repository の呼び出しが `audit` を使うのに signature に無い Java になる
        file.add_import("com.scalar.migrate.plsql.AuditContext")
        parameters.append(("AuditContext", "audit"))
    arguments = _arguments(file, query, routine, result)
    file.comment("回す対象。**1 反復とは別のトランザクション**で読む——だから本体が書く表を"
                 "読んでいてもよい（同じトランザクションで書いた表は読み直せない: P2-4）。"
                 "代わりに読んだ時点と処理する時点がずれるので、1 反復の側が自分で確かめる")
    signature = ", ".join(f"{t} {n}" for t, n in parameters)
    key = shape.loop.paged_key
    if key:
        file.comment("**キー順に件数つきで読む**（#19 の決定）。最初のページは pAfterKey = null で呼び、"
                     "次からは前のページの最後の行を After(...) に通した値を渡す。空が返ったら終わり")
    with file.block(f"public List<{record}> {java_name(routine_stem(routine))}Targets({signature}) "
                    "throws Exception") as f:
        if key:
            # 1 回に取る件数は運用の調整値で、Oracle の引数ではない。0 以下を黙って通すと、
            # LIMIT 0 で 1 件も返らず「対象が無い」ように見える
            with f.block("if (pBatch == null || pBatch < 1)") as g:
                g.line('throw new IllegalArgumentException("pBatch は 1 以上: " + pBatch);')
            # 最初のページの起点は、いちばん小さい値である。null のまま渡すと `key > NULL` が偽になり、
            # 1 件も返らない
            f.line("if (pAfterKey == null) pAfterKey = BigDecimal.valueOf(Long.MIN_VALUE);")
        f.line(f"return repository.{loop_method(routine, shape.loop)}({arguments});")
    if key:
        file.line()
        file.comment("次のページの起点: そのページの最後の行のキー")
        with file.block(f"public static BigDecimal {java_name(routine_stem(routine))}After({record} row)") as f:
            f.line(f"return row.{java_name(key)}();")


def _query_needs_audit(query: M.SqlOperation) -> bool:
    from .repository import needs_audit

    return needs_audit(query)


def _parameters(file: JavaFile, routine: M.Routine, statements: list[M.Statement]):
    """その部品が名前で触れている引数だけを、宣言された型で。"""
    from .types import java_type

    used = _used_names(statements)
    out = []
    for parameter in routine.parameters:
        if parameter.direction != "IN" or parameter.name.lower() not in used:
            continue
        mapped = java_type(parameter.type.resolved if parameter.type else None)
        file.add_import(*mapped.imports)
        out.append((mapped.name, java_name(parameter.name)))
    return out


def _domain() -> str:
    from .service import _DOMAIN

    return _DOMAIN.get() or ""


def _wants_audit(statements: list[M.Statement]) -> bool:
    from .service import _expression_texts
    from .expr import translate

    return any(translate(text).audit for s in _walk(statements) for text in _expression_texts(s))


# --- 形を見つける ----------------------------------------------------------------------------------

def _shape(routine: M.Routine) -> Shape | None:
    loops = [s for s in routine.body if s.kind == "Loop"]
    if len(loops) != 1:
        return None   # 反復が 1 つでなければ「1 反復」が何を指すか決まらない
    loop = loops[0]
    at = routine.body.index(loop)
    before, after = _plain(routine.body[:at]), _plain(routine.body[at + 1:])
    if loop.loop_kind == "cursor-for" and loop.query is not None:
        one, failed = _iteration(loop.body)
        if one is None:
            return None
        return Shape(kind="cursor", loop=loop, before=before, after=after, one=one, failed=failed,
                     failed_batch=_batch(routine.exception_handlers))
    if loop.loop_kind == "forall":
        failed, index = _save_exceptions(routine.exception_handlers)
        if index is None:
            return None
        return Shape(kind="forall", loop=loop, before=before, after=after, one=list(loop.body),
                     failed=failed, index=index)
    return None


def _plain(statements: list[M.Statement]) -> list[M.Statement]:
    """トランザクション制御を落とす。**落としたのではなく、境界そのものになった**——

    `COMMIT` は「ここが 1 つの単位の終わり」と言っていた。割ったあとは部品の境目がそれを言うので、
    文として残すと二重になる。`SAVEPOINT` / `ROLLBACK TO` も同じで、巻き戻る範囲は 1 反復である。
    """
    out = []
    for statement in statements:
        if _is_transaction(statement):
            continue
        if statement.kind == "If":
            branches = [b for b in statement.branches if _plain(b.body)]
            if not branches and not _plain(statement.else_body):
                # 中身が `COMMIT` だけだった分岐（`IF MOD(v_processed, 100) = 0 THEN COMMIT`）。
                # 中間コミットは 1 反復 = 1 トランザクションに吸収された
                continue
        out.append(statement)
    return out


def _iteration(body: list[M.Statement]):
    """cursor FOR ループの本体を、1 反復の中身と、失敗したときの記録に割る。"""
    inner = [s for s in body if not _is_transaction(s)]
    if len(inner) == 1 and inner[0].kind == "Block":
        block = inner[0]
        handlers = [h for h in block.exception_handlers
                    if any(e.upper() == "OTHERS" for e in h.exceptions)]
        if len(block.exception_handlers) != len(handlers):
            return None, []   # OTHERS 以外の handler がある。どれがどこへ行くかは人が決める
        failed = _from_handler(handlers[0].body) if handlers else []
        return _plain(block.body), failed
    return _plain(inner), []


def _batch(handlers: list[M.ExceptionHandler]) -> list[M.Statement]:
    """routine 全体の handler。`RAISE` は落とす——例外を持っているのは呼び出し側である。"""
    others = [h for h in handlers if any(e.upper() == "OTHERS" for e in h.exceptions)]
    if not others:
        return []
    statements = _from_handler(others[0].body)
    while statements and statements[-1].kind == "Raise" and statements[-1].error_code is None:
        statements = statements[:-1]
    return statements


def _from_handler(body: list[M.Statement]) -> list[M.Statement]:
    """handler の中身を、割った部品の中身にする。`SQLERRM` は渡された例外から読む。"""
    return [_rewritten(s) for s in _plain(body)]


def _save_exceptions(handlers: list[M.ExceptionHandler]):
    """`FORALL ... SAVE EXCEPTIONS` の handler を、**失敗した 1 要素**の記録に割る。

    Oracle の形はこうである:

        EXCEPTION WHEN OTHERS THEN
          IF SQLCODE = -24381 THEN
            FOR i IN 1 .. SQL%BULK_EXCEPTIONS.COUNT LOOP
              ... SQL%BULK_EXCEPTIONS(i).ERROR_INDEX ... SQLERRM(-... ERROR_CODE) ...
            END LOOP;
          ELSE RAISE; END IF;

    このループは**失敗した要素を回すループ**である。1 要素 = 1 トランザクションに割ると、
    それを回しているのは呼び出し側になる——だからループは消え、中身だけが部品になる。
    `SQL%BULK_EXCEPTIONS` は移行先に無いが、**どの要素が失敗したかは catch した側が知っている**。

    `ELSE RAISE` も消える。-24381（一括の中に失敗があった）という番号は、まとめて投げていたから
    付いていたもので、要素ごとに失敗が来るなら、それ以外の誤りはそのまま呼び出し側へ出る。
    """
    for handler in handlers:
        if not any(e.upper() == "OTHERS" for e in handler.exceptions):
            continue
        body = [s for s in handler.body if not _is_transaction(s)]
        if len(body) != 1 or body[0].kind != "If" or len(body[0].branches) != 1:
            continue
        branch = body[0].branches[0]
        if BULK_ERRORS not in (branch.condition or "").replace(" ", ""):
            continue
        loops = [s for s in branch.body if s.kind == "Loop"]
        if len(loops) != 1 or len(branch.body) != 1:
            continue
        bound = BULK_COUNT_BOUND.match(loops[0].cursor or "")
        if bound is None:
            continue
        return [_rewritten(s) for s in _plain(loops[0].body)], bound.group("index")
    return [], None


def _rewritten(statement: M.Statement) -> M.Statement:
    """割ったあと意味が変わる参照を書き換える。元の IR は触らない（写しを返す）。

    * `SQLERRM(...)` -> `SQLERRM`。渡された例外から読むので、引数は要らない
    * `SQL%BULK_EXCEPTIONS(i).ERROR_INDEX` -> 添字そのもの。失敗した要素の位置である
    """
    copied = copy.deepcopy(statement)
    for node in _walk([copied]) + [copied]:
        for field in ("expression", "condition", "message"):
            text = getattr(node, field, None)
            if isinstance(text, str) and text:
                setattr(node, field, _rewritten_text(text))
        for branch in getattr(node, "branches", []) or []:
            branch.condition = _rewritten_text(branch.condition or "")
        for bind in getattr(node, "binds", None) or []:
            if bind.expression:
                bind.expression = _rewritten_text(bind.expression)
            if bind.plsql_variable:
                bind.plsql_variable = _rewritten_text(bind.plsql_variable)
    return copied


def _rewritten_text(text: str) -> str:
    return BULK_ERROR_INDEX.sub(lambda m: _index_of(m.group()), SQLERRM_CALL.sub("SQLERRM", text))


def _index_of(reference: str) -> str:
    return reference.split("(", 1)[1].split(")", 1)[0].strip()


# --- 部品を routine として組み立てる ---------------------------------------------------------------

def _routine(routine: M.Routine, suffix: str, statements: list[M.Statement],
             exclude: "set[str]" = frozenset()) -> M.Routine:
    """部品を、生成器から見てふつうの routine にする。

    `name` は元のままにする——repository の method 名は元の routine 名から作られていて、
    部品はその同じ method を呼ぶからである。Java の method 名は別に渡す。
    """
    used = _used_names(statements)
    return M.Routine(
        id=routine.id, kind="Routine", source_range=_range(statements) or routine.source_range,
        name=routine.name, routine_kind=routine.routine_kind, visibility="public",
        # 要素として渡しているコレクションそのものは引数にしない。1 反復が受け取るのは要素である
        parameters=[p for p in routine.parameters
                    if p.direction == "IN" and p.name.lower() in used
                    and p.name.lower() not in exclude],
        declarations=[d for d in routine.declarations if d.name.lower() in used],
        body=statements)


def _range(statements: list[M.Statement]):
    return next((s.source_range for s in statements if s.source_range is not None), None)


def _used_names(statements: list[M.Statement]) -> set[str]:
    """部品が名前で触れているもの。触れていない引数や局所変数は、その部品には出さない。"""
    from .service import _expression_texts

    out: set[str] = set()
    for statement in _walk(statements):
        texts = list(_expression_texts(statement))
        texts += [b.plsql_variable or "" for b in (getattr(statement, "binds", None) or [])]
        texts += list(getattr(statement, "into_targets", None) or [])
        for text in texts:
            out.update(m.group().lower() for m in re.finditer(r"[A-Za-z][\w$#]*", text or ""))
    return out
