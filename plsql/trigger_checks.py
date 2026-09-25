"""#12 §0「検証で追う」: 移行先の trigger を通らなかった書き込みを、**データだけで**見つける照合。

決定（2026-09-19）:

1. **データだけで照合する（1a）。** 列も変更ログも足さない。trigger の中身から、型ごとの照合を
   生成器が作る
2. **型ごとに間隔を変える（2b）。** A（監査）・C（採番）は日次、B（拒否）・D（別表を読む検証）は短い
   間隔。ずれに気づくまでの時間が、そのまま被害の残る時間だからである
3. **B / D の表は、直接の書き込みを権限で禁じる。** 被害が残るのはそこだけなので、§0 の方法 1 を
   その表に限って併用する
4. **A のずれは補う。** 補った監査行には印を付ける——「誰が」「いつ」は分からないからである

照合の作り方（trigger の IR から読む）:

| 型 | 見つけ方 | 突き合わせ |
|---|---|---|
| A 監査 | `INSERT INTO <監査表> ... VALUES (..., TO_CHAR(:NEW.<主キー>), ..., :NEW.<列>)` | 今の値と、最後に監査した値 |
| B 拒否 | `IF <:NEW / :OLD の条件> THEN RAISE` | 同じ条件を「最後に監査した値 → 今の値」に当てる |
| C 採番 | `:NEW.<主キー> := <sequence>.NEXTVAL` | 使われているキーの最大値（採番の次の値と比べる） |
| D 別表検証 | 書かない本体に `RAISE` がある | 生成した本体を、今ある行ごとに呼ぶ |

**照合の限界**（どの形でも同じ）: 値を変えて同じ値に戻した書き込みは見えない。監査行が 1 行も無い
行は、比べる相手が無いので見ない。D は「いま違反している行」を挙げるので、当時は正しかった行
（支払いのあとで取消された注文など）も含む——**判断は人に回す**。
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .ir import model as M
from .lower import _walk
from .symbols import OracleSchema
from .triggers import CORRELATION, registry

DAILY, HOURLY = "daily", "hourly"


@dataclass
class Audit:
    """A 型の監査 INSERT を、照合と補完に使える形に読んだもの。"""

    table: str                          # 監査表（audit_log）
    columns: list[str]                  # INSERT の列の並び
    roles: dict[str, str]               # 列 -> id / key / new / old / time / who / literal
    literals: dict[str, str]            # literal の列 -> 値（照合のときの絞り込みでもある）
    sequence: str | None                # id を引く sequence
    key: str                            # 監査される表の主キー列
    watched: str                        # 監査される列
    text: bool                          # 値を TO_CHAR で文字にして記録しているか


@dataclass
class Check:
    trigger: str                        # trigger の module 名
    kind: str                           # A / B / C / D
    table: str                          # trigger が掛かる表
    interval: str
    key: list[str] = field(default_factory=list)
    audit: Audit | None = None
    condition: str | None = None        # B: 拒否の条件（PL/SQL のまま）
    sequence: str | None = None         # C
    reads: list[str] = field(default_factory=list)   # D: 本体に渡す :NEW の列（本体の引数の順）
    refused: str | None = None          # 組めなかった理由


def checks(program: M.Program, schema: OracleSchema | None) -> list[Check]:
    out: list[Check] = []
    for table, triggers in sorted(registry(program).items()):
        key = schema.primary_key(table) if schema is not None else []
        for trigger in triggers:
            out.extend(_for(trigger, table, key))
    return out


def _for(trigger, table: str, key: list[str]) -> list[Check]:
    name = trigger.module.name
    body = _walk(trigger.routine.body)
    found: list[Check] = []

    for statement in body:
        if statement.kind == "Assignment" and CORRELATION.match((statement.target or "").strip()):
            sequence = re.match(r"^\s*([\w$#]+)\s*\.\s*NEXTVAL\s*$", statement.expression or "", re.IGNORECASE)
            column = CORRELATION.match(statement.target.strip()).group("column").lower()
            found.append(Check(name, "C", table, DAILY, key=[column],
                               sequence=sequence.group(1).lower() if sequence else None,
                               refused=None if sequence else "採番以外の代入。照合を組めない"))

    audit = next((a for a in (_audit(s, key) for s in body if s.kind == "SqlOperation") if a), None)
    if audit is not None:
        found.append(Check(name, "A", table, DAILY, key=key, audit=audit))

    raises = [s for s in body if s.kind == "If" and any(r.kind == "Raise" for b in s.branches
                                                          for r in _walk(b.body))]
    writes = [s for s in body if s.kind == "SqlOperation"
              and (s.sql_kind or "").upper() in ("INSERT", "UPDATE", "DELETE", "MERGE")]
    for statement in raises:
        condition = statement.branches[0].condition or ""
        if CORRELATION.search(condition) and not _reads_locals(condition):
            # B: 拒否の条件が行の値だけで決まる。「最後に監査した値 → 今の値」に当てる
            found.append(Check(name, "B", table, HOURLY, key=key, audit=audit, condition=condition,
                               refused=None if audit else
                               "拒否の条件はあるが、前の値を知る監査が無い。比べる相手が無い"))
        elif not writes:
            # D: 書かない本体。生成した本体を、今ある行ごとに呼べばよい
            # the body takes one argument per correlation in name order (`gen_java.service.correlation_row`):
            # a stored row is both its OLD and its NEW state for this check, so OLD.x is read from the same
            # column as NEW.x (#40: passing NEW only left the call one argument short and javac refused it)
            reads = [n for n in _correlations(trigger) if n.upper().startswith(("NEW.", "OLD."))]
            found.append(Check(name, "D", table, HOURLY, key=key,
                               reads=[n.partition(".")[2].lower() for n in reads]))
    return found


def _correlations(trigger) -> list[str]:
    from .triggers import correlation_row

    return list(correlation_row(trigger.routine, trigger.module.trigger_when))


def _reads_locals(condition: str) -> bool:
    """条件が行の値以外（本体で読んだ局所変数など）を読むか。読むなら B の形では当てられない。"""
    stripped = CORRELATION.sub("", condition)
    names = re.findall(r"\b[A-Za-z_][\w$#]*\b", re.sub(r"'[^']*'", "", stripped))
    return any(n.upper() not in {"AND", "OR", "NOT", "NULL", "IS"} for n in names)


def _audit(statement: M.SqlOperation, key: list[str]) -> Audit | None:
    """`INSERT INTO audit_log (...) VALUES (seq.NEXTVAL, 'ORDERS', TO_CHAR(:NEW.order_id), ...)` を読む。"""
    if (statement.sql_kind or "").upper() != "INSERT" or len(key) != 1:
        return None
    try:
        tree = sqlglot.parse_one(statement.original_sql or "", dialect="oracle")
    except Exception:
        return None
    if not isinstance(tree, exp.Insert) or not isinstance(tree.this, exp.Schema):
        return None
    columns = [c.name.lower() for c in tree.this.expressions]
    values = tree.expression.expressions[0].expressions if isinstance(tree.expression, exp.Values) else []
    if len(columns) != len(values):
        return None
    roles, literals = {}, {}
    sequence = watched = None
    text = False
    for column, value in zip(columns, values):
        rendered = value.sql(dialect="oracle")
        inner = value.this if isinstance(value, exp.Anonymous) or type(value).__name__ == "ToChar" else value
        correlation = CORRELATION.search(rendered)
        if re.search(r"\.\s*NEXTVAL\b", rendered, re.IGNORECASE):
            roles[column] = "id"
            sequence = rendered.split(".")[0].strip().lower()
        elif isinstance(value, exp.Literal) and value.is_string:
            roles[column] = "literal"
            literals[column] = value.this
        elif correlation and correlation.group("column").lower() == key[0] and \
                correlation.group("qualifier").upper() == "NEW":
            roles[column] = "key"
        elif correlation and correlation.group("qualifier").upper() == "NEW":
            roles[column] = "new"
            watched = correlation.group("column").lower()
            text = not isinstance(value, exp.Column) and "TO_CHAR" in rendered.upper()
        elif correlation and correlation.group("qualifier").upper() == "OLD":
            roles[column] = "old"
        elif rendered.upper() in ("SYSTIMESTAMP", "SYSDATE", "CURRENT_TIMESTAMP") or \
                type(value).__name__ in ("Systimestamp", "CurrentTimestamp"):
            roles[column] = "time"
        elif rendered.upper() == "USER" or (isinstance(value, exp.Column) and value.name.upper() == "USER"):
            roles[column] = "who"
        else:
            return None   # 役割の分からない列がある。補うときに何を書けばよいか決められない
        del inner
    if watched is None or "key" not in roles.values():
        return None
    target = tree.this.this.name.lower()
    return Audit(table=target, columns=columns, roles=roles, literals=literals, sequence=sequence,
                 key=key[0], watched=watched, text=text)
