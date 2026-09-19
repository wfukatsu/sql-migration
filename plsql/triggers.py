"""#12: 移行先に trigger は無いので、**書き込む側が呼ぶ**。

Oracle の trigger は「その表へのすべての書き込み」に掛かっていた。ScalarDB にその仕組みは無いので、
掛けるには**書き込む文のところに置く**しかない（`docs/plsql-trigger-patterns.md` の早見表）:

    UPDATE orders SET status = 'CLOSED' WHERE order_id = :id;

    -> SELECT status INTO v_trg_1 FROM orders WHERE order_id = :id;   -- :OLD
       UPDATE orders SET status = 'CLOSED' WHERE order_id = :id;
       trg_orders_audit.body(:id, 'CLOSED', v_trg_1);                 -- AFTER なので後

**これは網羅性を保証しない。** 掛かるのは生成したコードが通る経路だけで、他システムの直接 DML や
手作業の SQL には掛からない。その穴を塞ぐのではなく**検証で追う**、というのが #12 §0 の決定である。
ここが埋めるのは「見えている経路には掛ける」ところまでで、見えない経路は検証の仕事として残る。

落とさないように気を付けたことが 4 つある（trigger-patterns A）:

* **before と after の両方。** `:OLD` は更新の**前に**読む。読まずに更新すると値が無い
* **発火条件。** `WHEN (OLD.status <> NEW.status)` は trigger 本体の先頭に出ている（生成器が番人と
  して出す）ので、ここでは条件を複製しない——2 か所に書くと片方が古くなる
* **掛かる列。** `UPDATE OF status` は status を SET していない更新には掛からない
* **BEFORE と AFTER の順序。** BEFORE は書く前（`trg_products_audit` は値を拒否する）、
  AFTER は書いた後

**掛けられないときは掛けない。** 1 行に絞れない更新（主キーで特定できない WHERE）は、Oracle なら
行ごとに発火するもので、1 回の呼び出しでは同じにならない。そういう場所は `TRIGGER_NOT_APPLIED` を
残して**見えるようにする**——黙って掛けないのと、掛からないと言うのは違う。
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .ir import model as M
from .symbols import OracleSchema, Symbol, SymbolTable

PREFIX = "v_trg_"
CORRELATION = re.compile(r":?\b(?P<qualifier>NEW|OLD)\s*\.\s*(?P<column>[A-Za-z][\w$#]*)", re.IGNORECASE)
# 複数イベントの trigger の本体が、どのイベントで発火したかを見る述語。呼び出し側は「どの文のところで
# 呼んでいるか」を静的に知っているので、**相関行と同じく引数として渡す**（#29 の 25）
EVENT = re.compile(r"(?<![\w$#.:])(?P<event>INSERTING|UPDATING|DELETING)\b(?!\s*\()", re.IGNORECASE)
# `UPDATING('STATUS')`: 列ごとの述語。SET の列を見れば静的に決まるが、まだ渡す形を持っていない
EVENT_OF_COLUMN = re.compile(r"(?<![\w$#.:])(?:INSERTING|UPDATING|DELETING)\s*\(", re.IGNORECASE)
_LITERAL = re.compile(r"'(?:[^']|'')*'")


@dataclass
class Trigger:
    """1 つの trigger と、それが掛かる条件。"""

    module: M.Module
    routine: M.Routine
    table: str
    timing: str                       # BEFORE | AFTER
    event: str                        # INSERT | UPDATE | DELETE、複数なら `INSERT OR UPDATE`
    columns: list[str] = field(default_factory=list)   # `UPDATE OF <列>`。空なら全列

    @property
    def events(self) -> set[str]:
        return {e.strip() for e in self.event.split(" OR ") if e.strip()}

    @property
    def correlations(self) -> list[str]:
        return list(correlation_row(self.routine, self.module.trigger_when))

    def asks_event_of_column(self) -> bool:
        """`UPDATING('STATUS')` を読む本体。**掛けない**（渡す形が無い）。"""
        from .lower import _walk

        statements = _walk(self.routine.body) + [s for h in self.routine.exception_handlers for s in _walk(h.body)]
        return any(EVENT_OF_COLUMN.search(_LITERAL.sub("''", text or "")) for s in statements for text in _texts(s))

    def sequence_key(self) -> tuple[str, str] | None:
        """(column, sequence) for a trigger that does nothing but `:NEW.<column> := <sequence>.NEXTVAL` before an
        INSERT, with no WHEN or with `WHEN (NEW.<column> IS NULL)`. That shape changes the written row, so it can
        never be a call -- but what it does is known exactly, and the writer can do it (`_inline_sequence`)."""
        from .lower import _walk

        if self.timing != "BEFORE" or self.events != {"INSERT"}:
            return None
        body = [s for s in _walk(self.routine.body) if s.kind != "Null"]
        if len(body) != 1 or body[0].kind != "Assignment" or self.routine.exception_handlers:
            return None
        target = CORRELATION.fullmatch((body[0].target or "").strip())
        source = re.fullmatch(r"\s*([\w$#]+)\s*\.\s*NEXTVAL\s*", body[0].expression or "", re.IGNORECASE)
        if target is None or source is None or target.group("qualifier").upper() != "NEW":
            return None
        column = target.group("column").lower()
        when = (self.module.trigger_when or "").strip()
        if when and not re.fullmatch(rf":?NEW\s*\.\s*{re.escape(column)}\s+IS\s+NULL", when, re.IGNORECASE):
            return None
        return column, source.group(1).lower()

    def assigns_correlation(self) -> bool:
        """`:NEW.x := ...` を書く trigger（採番 trigger）。**掛けない。**

        値を書き換える trigger は「書き込まれる行そのもの」を変える。移行先では呼び出し側が
        値を持っているので、これは採番 Service への**再設計**であって呼び出しではない
        （trigger-patterns C）。生成器も本体を拒否するので、呼べば必ず落ちる。
        """
        from .lower import _walk

        return any(s.kind == "Assignment" and CORRELATION.match((s.target or "").strip())
                   for s in _walk(self.routine.body))


def correlation_row(routine: M.Routine, trigger_when: str | None) -> dict[str, "M.BindVariable | None"]:
    """`:NEW.status` / `:OLD.status` -> それを運ぶ bind。名前順で返す。

    **生成器の引数の並びはこれで決まる**（`gen_java/service.correlation_row` がこれを使う）。
    呼ぶ側と呼ばれる側で別々に数えると、引数が静かにずれる——だから 1 か所に置いてある。
    """
    from .lower import _walk

    seen: dict[str, M.BindVariable | None] = {}
    statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
    for statement in statements:
        for bind in getattr(statement, "binds", None) or []:
            variable = bind.plsql_variable or ""
            if variable.upper().startswith(("NEW.", "OLD.")):
                seen.setdefault(variable, bind)
    # SQL を通らない参照（`IF :NEW.unit_price < :OLD.unit_price * 0.5`、`WHEN (...)`）。型は付いて
    # いないが、**読んでいる事実は同じ**である
    for text in [trigger_when or ""] + [t for s in statements for t in _texts(s)]:
        for match in CORRELATION.finditer(text or ""):
            seen.setdefault(f"{match.group('qualifier').upper()}.{match.group('column')}", None)
        # `IF INSERTING THEN`。文字列リテラルの中の単語は述語ではない
        for match in EVENT.finditer(_LITERAL.sub("''", text or "")):
            seen.setdefault(match.group("event").upper(), None)
    return dict(sorted(seen.items()))


EVENTS = {"INSERTING": "INSERT", "UPDATING": "UPDATE", "DELETING": "DELETE"}


def _texts(statement: M.Statement):
    # SQL の中の `:NEW.order_id` も読んでいる列である。bind として持ち上がるのは変換のあとなので、
    # **書き込む側が trigger を呼ぶ形に書き換える時点では、まだ bind が無い**——文字として見る
    for name in ("expression", "condition", "message", "target", "original_sql"):
        value = getattr(statement, name, None)
        if isinstance(value, str):
            yield value
    for branch in getattr(statement, "branches", []) or []:
        yield branch.condition or ""


def registry(program: M.Program) -> dict[str, list[Trigger]]:
    """表ごとの trigger。BEFORE が先、AFTER が後——Oracle が実行する順である。"""
    out: dict[str, list[Trigger]] = {}
    for module in program.modules:
        if module.module_kind != "trigger" or not module.trigger_table:
            continue
        for routine in module.routines:
            trigger = Trigger(module=module, routine=routine, table=module.trigger_table.lower(),
                              timing=(module.trigger_timing or "BEFORE").upper(),
                              event=(module.trigger_event or "").upper(),
                              columns=list(module.trigger_columns or []))
            out.setdefault(trigger.table, []).append(trigger)
    for triggers in out.values():
        triggers.sort(key=lambda t: (t.timing != "BEFORE", t.routine.id))
    return out


def rewrite(program: M.Program, schema: OracleSchema | None,
            symbols: SymbolTable | None = None) -> None:
    """見えている書き込み経路に trigger を掛ける（in place）。

    1 行に絞れない更新には掛けない——Oracle は行ごとに発火するので、1 回呼ぶのでは同じことに
    ならない。絞れるかどうかは **Oracle の主キー**で決める: それは Oracle の話であって、
    移行先のスキーマが渡っているかどうかで答えが変わってよいものではない。
    """
    found = registry(program)
    if not found:
        return
    for module in program.modules:
        if module.module_kind == "trigger":
            continue
        for routine in module.routines:
            before = len(routine.declarations)
            routine.body = _sequence(routine.body, routine, found, schema)
            for handler in routine.exception_handlers:
                handler.body = _sequence(handler.body, routine, found, schema)
            _declare(symbols, routine, routine.declarations[before:])


def _declare(symbols: SymbolTable | None, routine: M.Routine,
             declarations: list[M.Declaration]) -> None:
    """読み込み先の変数を symbol table にも登録する。登録しないと SQL の側が**列として読む**
    （#9 の RMW で同じことが起きた）。"""
    scope = symbols.scopes.get(routine.id) if symbols else None
    if scope is None:
        return
    for declaration in declarations:
        scope.declare(Symbol(name=declaration.name, kind="variable", scope=routine.id,
                             type=declaration.type, source_range=declaration.source_range))


def _sequence(statements: list[M.Statement], routine: M.Routine, found: dict[str, list[Trigger]],
              schema: OracleSchema | None) -> list[M.Statement]:
    out: list[M.Statement] = []
    for statement in statements:
        for attribute in ("body", "else_body"):
            nested = getattr(statement, attribute, None)
            if nested:
                setattr(statement, attribute, _sequence(nested, routine, found, schema))
        for branch in getattr(statement, "branches", []) or []:
            branch.body = _sequence(branch.body, routine, found, schema)
        for handler in getattr(statement, "exception_handlers", []) or []:
            handler.body = _sequence(handler.body, routine, found, schema)
        head, tail = _apply(statement, routine, found, schema)
        out.extend(head)
        out.append(statement)
        out.extend(tail)
    return out


def _apply(statement: M.Statement, routine: M.Routine, found: dict[str, list[Trigger]],
           schema: OracleSchema | None):
    """この文に掛かる trigger を、前と後ろに置く文として返す。"""
    head: list[M.Statement] = []
    tail: list[M.Statement] = []
    if statement.kind != "SqlOperation":
        return head, tail
    kind = (statement.sql_kind or "").upper()
    if kind not in ("INSERT", "UPDATE", "DELETE", "MERGE"):
        return head, tail
    try:
        tree = sqlglot.parse_one(statement.original_sql or "", dialect="oracle")
    except Exception:
        return head, tail
    table = _table(tree)
    if table is None:
        return head, tail
    for index, trigger in enumerate(found.get(table.lower(), [])):
        if not _fires(trigger, kind, tree):
            continue
        # 掛け方を知らない形は、**掛けていないと言う**。以前は黙って通り過ぎていたので、trigger のある表へ
        # DELETE / MERGE で書く routine と、複数イベントの trigger のある表へ書く routine が、何の診断も
        # 無いまま AUTO になりえた——正しく掛けられた routine のほうが悪い判定になる、逆転である
        unhandled = ("MERGE は INSERT と UPDATE のどちらで発火するかが行ごとに決まる" if kind == "MERGE" else
                     "本体が `UPDATING('列')` の形で列ごとのイベントを見ている。それを渡す形をまだ持っていない"
                     if trigger.asks_event_of_column() else None)
        if unhandled is not None:
            statement.add("WARN", "TRIGGER_NOT_APPLIED",
                          f"{trigger.module.name} が掛かる書き込みだが、**掛けていない**。{unhandled}（#12）")
            continue
        if trigger.assigns_correlation():
            inlined = _inline_sequence(statement, trigger, kind, tree)
            if inlined is not None:
                if inlined:
                    statement.add("INFO", "TRIGGER_INLINED",
                                  f"{trigger.module.name}: 採番 trigger（`:NEW.{trigger.sequence_key()[0]} := "
                                  f"{trigger.sequence_key()[1]}.NEXTVAL`）を、この INSERT の値として織り込んだ。"
                                  f"番号は移行先の採番方式（計画 §9）で取る。**掛かるのはこの経路だけ**である（#12 §0）")
                continue
            statement.add("WARN", "TRIGGER_REDESIGN",
                          f"{trigger.module.name} は書き込まれる行の値そのものを変える trigger である。"
                          f"呼び出しでは置き換えられない——採番 Service への再設計である"
                          f"（trigger-patterns C / #12）")
            continue
        applied = _call(statement, routine, trigger, kind, tree, schema, index)
        if applied is None:
            statement.add("WARN", "TRIGGER_NOT_APPLIED",
                          f"{trigger.module.name} が掛かる書き込みだが、**掛けていない**。"
                          f"1 行に絞れない更新は Oracle なら行ごとに発火するので、1 回の呼び出しでは"
                          f"同じにならない（#12）")
            continue
        read, call = applied
        if read is not None:
            head.append(read)
        (head if trigger.timing == "BEFORE" else tail).append(call)
        statement.add("INFO", "TRIGGER_APPLIED",
                      f"{trigger.module.name}（{trigger.timing} {trigger.event} ON {trigger.table}）を"
                      f"この書き込みのところで呼ぶ。**掛かるのはこの経路だけ**で、他システムの直接 DML "
                      f"には掛からない——網羅性は検証で追う（#12 §0 の決定）")
    return head, tail


def _inline_sequence(statement: M.SqlOperation, trigger: Trigger, kind: str, tree: exp.Expression) -> bool | None:
    """Put the number a sequence trigger would assign into the INSERT itself.

    True: the statement was rewritten. False: the trigger does not fire on this statement (the key is written and
    the trigger only fills in a NULL one). None: not this shape, or not decidable here -- the caller reports a
    TRIGGER_REDESIGN as before.

    Decidable means statically: a literal key, a NULL, or no key at all. A variable may be NULL or not at run time,
    and `NVL(p_id, seq.NEXTVAL)` is not the same thing -- Oracle evaluates NEXTVAL whether or not it is used, so
    every call would burn a number, which is exactly what a NOCACHE sequence says must not happen.
    """
    key = trigger.sequence_key()
    if key is None or kind != "INSERT" or not isinstance(tree, exp.Insert):
        return None
    column, sequence = key
    schema = tree.this if isinstance(tree.this, exp.Schema) else None
    values = tree.expression
    if schema is None or not isinstance(values, exp.Values) or len(values.expressions) != 1:
        return None
    columns = [c.name.lower() for c in schema.expressions if isinstance(c, (exp.Column, exp.Identifier))]
    row = values.expressions[0]
    if not isinstance(row, exp.Tuple) or len(row.expressions) != len(columns):
        return None
    number = exp.column("NEXTVAL", table=sequence)
    guarded = bool((trigger.module.trigger_when or "").strip())
    if column in columns:
        given = row.expressions[columns.index(column)]
        if guarded and isinstance(given, exp.Literal):
            return False                                   # a key is written: WHEN (NEW.col IS NULL) is false
        if guarded and not isinstance(given, exp.Null):
            return None                                    # a variable or an expression: NULL or not, at run time
        given.replace(number)
    else:
        schema.append("expressions", exp.to_identifier(column))
        row.append("expressions", number)
    statement.original_sql = tree.sql(dialect="oracle")
    return True


def _table(tree: exp.Expression) -> str | None:
    target = tree.this if isinstance(tree, (exp.Update, exp.Insert, exp.Delete, exp.Merge)) else None
    if isinstance(target, exp.Schema):
        target = target.this
    return target.name if isinstance(target, exp.Table) else None


def _fires(trigger: Trigger, kind: str, tree: exp.Expression) -> bool:
    if kind == "MERGE":
        return bool(trigger.events & {"INSERT", "UPDATE", "DELETE"})   # どの枝が走るかは行ごとに決まる
    if kind not in trigger.events:
        return False
    if kind == "UPDATE" and trigger.columns:
        # `UPDATE OF status` は status を SET していない更新には掛からない
        return bool({c.lower() for c in _assignments(tree)} & {c.lower() for c in trigger.columns})
    return True


def _assignments(tree: exp.Expression) -> dict[str, str]:
    """`SET a = x, b = y` の列 -> 値（Oracle のまま）。"""
    out: dict[str, str] = {}
    for assignment in tree.expressions or []:
        if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Column):
            out[assignment.this.name.lower()] = assignment.expression.sql(dialect="oracle")
    return out


def _inserted(tree: exp.Expression) -> dict[str, str] | None:
    """`INSERT INTO t (a, b) VALUES (x, y)` の列 -> 値。列を書いていない INSERT は扱わない。"""
    schema = tree.this if isinstance(tree.this, exp.Schema) else None
    values = tree.expression if isinstance(tree, exp.Insert) else None
    if schema is None or not isinstance(values, exp.Values) or not values.expressions:
        return None
    # 列は `Identifier`（`INSERT INTO t (a, b)`）で来ることも `Column` で来ることもある。
    # 片方だけ見ると、**列の並びが読めずに trigger が黙って掛からない**
    columns = [c.name.lower() for c in schema.expressions if isinstance(c, (exp.Column, exp.Identifier))]
    row = values.expressions[0]
    if not isinstance(row, exp.Tuple) or len(row.expressions) != len(columns):
        return None
    return {column: value.sql(dialect="oracle") for column, value in zip(columns, row.expressions)}


def _call(statement: M.SqlOperation, routine: M.Routine, trigger: Trigger, kind: str,
          tree: exp.Expression, schema: OracleSchema | None, index: int):
    """(:OLD を読む文, trigger を呼ぶ文)。掛けられなければ None。"""
    # どのイベントの文のところで呼んでいるかは、ここで静的に決まっている
    events = {name: ("TRUE" if EVENTS[name] == kind else "FALSE") for name in trigger.correlations if name in EVENTS}
    correlations = [name for name in trigger.correlations if name not in EVENTS]
    if kind == "INSERT":
        written = _inserted(tree)
        if written is None:
            return None
        # INSERT に :OLD の行は無い。Oracle でも `:OLD.x` は NULL である（複数イベントの本体は UPDATING の
        # 枝で :OLD を読むので、読んでいること自体は掛けない理由にならない）
        values = {name: ("NULL" if name.upper().startswith("OLD.")
                         else written.get(name.partition(".")[2].lower(), "NULL")) for name in correlations}
        # INSERT は必ず 1 行入るので、掛かる条件は無い
        return None, _invocation(statement, trigger, {**values, **events}, index)

    written = _assignments(tree) if kind == "UPDATE" else {}
    where = tree.args.get("where")
    if where is None or not _one_row(where, _table(tree), schema):
        return None
    read_columns: list[str] = []
    values: dict[str, str] = {}
    pending: dict[str, str] = {}      # 相関名 -> 先に読む列。変数名は下で決まる
    for name in correlations:
        qualifier, _, column = name.partition(".")
        column = column.lower()
        if kind == "DELETE" and qualifier.upper() == "NEW":
            values[name] = "NULL"                   # DELETE に :NEW の行は無い。Oracle でも NULL
        elif qualifier.upper() == "NEW" and column in written:
            values[name] = written[column]          # 書き込む値そのもの
        else:
            # `:OLD` と、この更新が触っていない `:NEW`（更新後も同じ値である）
            if column not in read_columns:
                read_columns.append(column)
            pending[name] = column
    if not read_columns and trigger.timing == "BEFORE":
        # BEFORE は書く前に呼ぶので、「当たる行があるか」を `SQL%ROWCOUNT` では見られない。相関行を 1 つも
        # 読まない本体でも、行があるかだけは読む——無い行の UPDATE / DELETE に trigger は発火しない
        read_columns.append(schema.primary_key(_table(tree))[0].lower())
    read = None
    flag = f"trg{index}"
    if read_columns:
        variables = {}
        for column in read_columns:
            variable = _free_name(routine, variables.values())
            variables[column] = variable
            routine.declarations.append(_declaration(routine, variable, _table(tree), column, schema,
                                                     statement))
        values.update({name: variables[column] for name, column in pending.items()})
        values = {name: values[name] for name in correlations}   # 呼ばれる側と同じ並び
        read = M.SqlOperation(
            id=f"{statement.id}trg{index}", kind="SqlOperation", source_range=statement.source_range,
            sql_kind="SELECT", cardinality="AT_MOST_ONE", not_found_flag=flag,
            original_sql=f"SELECT {', '.join(read_columns)} FROM {_table(tree)} "
                         f"{where.sql(dialect='oracle')}",
            into_targets=[variables[c] for c in read_columns])
        read.add("INFO", "TRIGGER_OLD",
                 f"{trigger.module.name} が読む :OLD の値。**更新の前に**読む——後では元の値が無い"
                 f"（trigger-patterns A-1）。**行が無くても例外にしない**: 更新する行が無ければ "
                 f"trigger は掛からないので、これは「無い」が答えである")
    return read, _guarded(statement, trigger, {**values, **events}, index, flag if read is not None else None)


def _guarded(statement: M.SqlOperation, trigger: Trigger, values: dict[str, str], index: int,
             flag: str | None) -> M.Statement:
    """**当たった行が無ければ掛けない。** Oracle の trigger は行ごとに発火するので、0 行の更新では
    1 度も発火しない。

    条件の取り方が BEFORE と AFTER で違う。AFTER は書いたあとなので `SQL%ROWCOUNT` が答えを持って
    いる。BEFORE はまだ書いていないので、:OLD を読んだときに行があったかで決める——読まない
    BEFORE（INSERT）は必ず 1 行入るので、条件が要らない。

    これが無いと、**更新する行が無いときに :OLD の読みが例外になり、元のコードが `SQL%ROWCOUNT`
    で見ていた「無かった」が別の失敗に化ける**（`mark_shipped` で実際そうなった）。
    """
    call = _invocation(statement, trigger, values, index)
    condition = "SQL%ROWCOUNT > 0" if trigger.timing != "BEFORE" else \
        (f"NOT {flag}%NOTFOUND" if flag else None)
    if condition is None:
        return call
    return M.If(id=f"{call.id}if", kind="If", source_range=statement.source_range,
                branches=[M.Branch(condition=condition, body=[call])])


def _invocation(statement: M.SqlOperation, trigger: Trigger, values: dict[str, str],
                index: int) -> M.Call:
    """trigger を呼ぶ文。引数は**名前付き**で持つ——並びは呼ばれる側が決めるものなので、
    ここで順番を決め打つと、片方が変わったときに静かにずれる。"""
    call = M.Call(id=f"{statement.id}call{index}", kind="Call", source_range=statement.source_range,
                  callee=trigger.routine.id, resolved_to=trigger.routine.id,
                  arguments=[f"{name} => {value}" for name, value in values.items()])
    call.add("INFO", "TRIGGER_CALL",
             f"{trigger.module.name} を呼ぶ。Oracle では表への書き込みすべてに掛かっていたものが、"
             f"**この経路にだけ**掛かる（#12 §0）")
    return call


def _one_row(where: exp.Expression, table: str | None, schema: OracleSchema | None) -> bool:
    """WHERE が主キーを等値で全部押さえているか。押さえていなければ複数行に当たりうる。"""
    key = schema.primary_key(table) if schema and table else []
    if not key:
        return False
    spelled = set()
    conditions = [where.this] if isinstance(where, exp.Where) else [where]
    while conditions:
        condition = conditions.pop()
        if isinstance(condition, exp.And):
            conditions.extend([condition.this, condition.expression])
        elif isinstance(condition, exp.EQ) and isinstance(condition.this, exp.Column):
            spelled.add(condition.this.name.lower())
        elif isinstance(condition, exp.Paren):
            conditions.append(condition.this)
        else:
            return False   # OR / IN / 範囲。1 行とは限らない
    return all(column.lower() in spelled for column in key)


def _free_name(routine: M.Routine, taken) -> str:
    used = {d.name.lower() for d in routine.declarations} | {t.lower() for t in taken} \
        | {p.name.lower() for p in routine.parameters}
    index = 1
    while f"{PREFIX}{index}" in used:
        index += 1
    return f"{PREFIX}{index}"


def _declaration(routine: M.Routine, variable: str, table: str | None, column: str,
                 schema: OracleSchema | None, statement: M.Statement) -> M.Declaration:
    oracle = (schema.column(table, column) if schema and table else None) or f"{table}.{column}%TYPE"
    if re.fullmatch(r"(?i)(NUMBER|NUMERIC|DECIMAL|INTEGER|INT|SMALLINT)\s*(\(\s*\d+\s*(,\s*0\s*)?\))?", oracle.strip()):
        # trigger の本体は相関行の数値を PL/SQL の NUMBER として受ける。列の幅（NUMBER(10) -> Long）で宣言すると
        # その引数に渡せない
        oracle = "NUMBER"
    return M.Declaration(id=f"{routine.id}#decl-{variable}", kind="Declaration", name=variable,
                         source_range=statement.source_range,
                         type=M.TypeRef(oracle=oracle, resolved=oracle, origin="column-type"))
