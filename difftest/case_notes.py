"""What a case file declares about one statement, in comment lines just above it (Issues #57 and #58).

Two declarations, both of which need a reason -- a declaration without one is how a real difference gets waved
through:

  -- @nondeterministic: unordered; ignore=rn; reason=salary 17000 の 2 人は同順位で、ROW_NUMBER の番号がどちらに付くかは決まらない
  -- @source-rejects: sample; reason=別名 share は Oracle 26ai の予約語（ORA-00923）

``@nondeterministic`` says the source's own answer is not one answer, and how to compare it anyway:

* ``unordered``   compare as a multiset even though the statement has an ORDER BY (ties in the sort key)
* ``ignore=a,b``  leave these result columns out of the comparison, on both sides (by name as the source database
                  reports it, case-insensitive, or by 1-based position). For a value that depends on the order
                  of tied rows, or on the clock or the session
* ``count``       compare the row count only (ROWNUM without ORDER BY: any N rows)

The statement still passes only when that weaker comparison agrees; it is then PASS with the declaration recorded,
and counted as DECLARED so that a reader can see how much of PASS it is.

``@source-rejects`` says the migration source is expected to reject the statement, and why:

* ``sample``   the case itself is wrong for this source (a reserved word used as an alias, ...)
* ``harness``  the harness cannot run it as written (a flashback query right after the tables were created, ...)

When the source does reject it, the statement is SKIP with that reason instead of CASE_ERROR. When the source
accepts it after all, the declaration does nothing and the statement is compared as usual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REJECT_KINDS = {"sample": "移行元の不備", "harness": "ハーネスの都合"}

_LINE = re.compile(r"^\s*--\s*@(nondeterministic|source-rejects)\s*:\s*(.*?)\s*$", re.M)


class NoteError(ValueError):
    """A declaration that cannot be read or does not fit the result. The case is broken, not the conversion."""


@dataclass(frozen=True)
class Nondeterministic:
    reason: str
    unordered: bool = False
    ignore: tuple[str, ...] = ()
    count_only: bool = False

    def describe(self) -> str:
        how = ["count" if self.count_only else "", "unordered" if self.unordered else "",
               f"ignore={','.join(self.ignore)}" if self.ignore else ""]
        return "; ".join(h for h in how if h)


@dataclass(frozen=True)
class SourceRejects:
    kind: str
    reason: str

    def label(self) -> str:
        return REJECT_KINDS[self.kind]


@dataclass(frozen=True)
class Notes:
    nondeterministic: Nondeterministic | None = None
    source_rejects: SourceRejects | None = None


def _fields(text: str, what: str) -> tuple[list[str], str]:
    """(flags, reason). The reason is everything after ``reason=``, so it may contain ``;``."""
    head, sep, reason = text.partition("reason=")
    reason = reason.strip()
    if not sep or not reason:
        raise NoteError(f"@{what} needs a reason (…; reason=<なぜ>)")
    return [f.strip() for f in head.split(";") if f.strip()], reason


def parse(sql: str) -> Notes:
    """The declarations among the comment lines of ``sql`` (a statement with the comments above it)."""
    nondet = rejects = None
    for what, text in _LINE.findall(sql):
        flags, reason = _fields(text, what)
        if what == "nondeterministic":
            if nondet:
                raise NoteError("@nondeterministic is declared twice")
            unordered = count_only = False
            ignore: list[str] = []
            for f in flags:
                key, _, value = f.partition("=")
                key = key.strip().lower()
                if key == "unordered" and not value:
                    unordered = True
                elif key == "count" and not value:
                    count_only = True
                elif key == "ignore" and value.strip():
                    ignore += [c.strip() for c in value.split(",") if c.strip()]
                else:
                    raise NoteError(f"@nondeterministic: unknown flag {f!r} (unordered / ignore=<列> / count)")
            if not (unordered or ignore or count_only):
                raise NoteError("@nondeterministic says nothing about how to compare (unordered / ignore=<列> / count)")
            nondet = Nondeterministic(reason, unordered, tuple(ignore), count_only)
        else:
            if rejects:
                raise NoteError("@source-rejects is declared twice")
            if len(flags) != 1 or flags[0] not in REJECT_KINDS:
                raise NoteError(f"@source-rejects: say which kind, one of {', '.join(REJECT_KINDS)}")
            rejects = SourceRejects(flags[0], reason)
    return Notes(nondet, rejects)


def ignored_positions(note: Nondeterministic, columns: list[str]) -> set[int]:
    """0-based positions of the ignored columns in a result whose columns are ``columns``."""
    upper = [c.upper() for c in columns]
    out = set()
    for c in note.ignore:
        if c.isdigit():
            i = int(c) - 1
            if not 0 <= i < len(columns):
                raise NoteError(f"@nondeterministic: ignore={c} but the result has {len(columns)} columns")
        elif upper.count(c.upper()) == 1:
            i = upper.index(c.upper())
        else:
            raise NoteError(f"@nondeterministic: ignore={c} is {'ambiguous' if c.upper() in upper else 'not a column'} "
                            f"of the result ({', '.join(columns)})")
        out.add(i)
    return out


def weaken(expected: list, actual: list, note: Nondeterministic, columns: list[str]) -> tuple[list, list]:
    """Both sides as the declaration says to compare them. ``count`` keeps the rows and drops every column."""
    drop = set(range(len(columns))) if note.count_only else ignored_positions(note, columns)

    if any(len(row) != len(columns) for row in actual):
        # another width is left whole on both sides: cutting only the source's columns could make a result that
        # lost exactly the ignored column look like agreement
        return [list(row) for row in expected], [list(row) for row in actual]

    def cut(rows):
        return [[v for i, v in enumerate(row) if i not in drop] for row in rows]
    return cut(expected), cut(actual)
