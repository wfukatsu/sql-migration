"""The application's SQL files: every statement, the line it starts on, and the tables it touches.

A table nothing in the PL/SQL touches is not a table nothing touches -- the application may go to it directly, and
then "0 readers" on the page would be a lie about the very tables a migration is most likely to forget.

## The converter is used, not changed

Splitting a script is the converter's business (`scalardb_migrate/converter.py`: top-level `;`, a SQL*Plus `/`
line, a PL/SQL block kept whole), and a second splitter would drift from it. It returns no positions, but each
statement it returns is a slice of the script, so looking for it *after the end of the previous one* finds where
it starts. The converter stays untouched: editing it moves the toolchain fingerprint (`plsql/fingerprint.py`), and
evidence measured on the old one stops counting.

## What cannot be seen is still listed

A statement sqlglot cannot parse, and a PL/SQL block whose inside nobody analysed here, are statements that may
touch any table. They keep their file and line and are marked not visible; dropping them would make the tables
they touch look untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

from scalardb_migrate.converter import PLSQL_BLOCK, _split_statements

from ..sqlbridge import read_write_sets

DDL = (exp.Create, exp.Drop, exp.Alter, exp.TruncateTable, exp.Comment, exp.Grant)
KINDS = {exp.Select: "SELECT", exp.Insert: "INSERT", exp.Update: "UPDATE", exp.Delete: "DELETE", exp.Merge: "MERGE",
         exp.Union: "SELECT", exp.Intersect: "SELECT", exp.Except: "SELECT"}

NOT_PARSED = "解析できない文"
PLSQL_INSIDE = "PL/SQL のブロック（中の SQL はここでは解析していない）"


@dataclass
class AppStatement:
    file: str
    line: int
    end_line: int
    kind: str
    text: str
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    visible: bool = True
    reason: str | None = None


def cte_names(tree: exp.Expression) -> set[str]:
    """`WITH x AS (...)` makes `x` look like a table to anything that lists `exp.Table` nodes."""
    return {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}


def tables_of(sql: str, dialect: str = "oracle") -> tuple[str, list[str], list[str]] | None:
    """(kind, reads, writes) of one statement, without the names that are not tables. `None`: not parsable."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return None
    if tree is None or isinstance(tree, exp.Command):
        return None  # sqlglot's "I kept the text and understood nothing"
    if isinstance(tree, DDL):
        return "DDL", [], []
    kind = next((name for node, name in KINDS.items() if isinstance(tree, node)), tree.key.upper())
    reads, writes = read_write_sets(tree)
    local = cte_names(tree)
    return kind, [t for t in reads if t not in local], [t for t in writes if t not in local]


def _first_token_line(chunk: str, dialect: str) -> int:
    """Lines of comment above a statement belong to the slice and not to the statement."""
    try:
        tokens = sqlglot.tokenize(chunk, read=dialect)
    except sqlglot.errors.SqlglotError:
        tokens = []
    return tokens[0].line if tokens else 1


def statements(path: str | Path, dialect: str = "oracle", label: str | None = None) -> list[AppStatement]:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    out, cursor = [], 0
    for chunk in _split_statements(text, dialect):
        at = text.find(chunk, cursor)
        if at < 0:  # cannot happen while the converter returns slices; if it ever stops, say so rather than guess
            raise ValueError(f"{path}: a statement the splitter returned is not in the file: {chunk[:60]!r}")
        cursor = at + len(chunk)
        first = text.count("\n", 0, at) + 1
        line = first + _first_token_line(chunk, dialect) - 1
        end_line = first + chunk.count("\n")
        body = "\n".join(chunk.split("\n")[line - first:])
        statement = AppStatement(file=label or path.name, line=line, end_line=end_line, kind="?", text=body)
        if PLSQL_BLOCK.match(chunk):
            statement.kind, statement.visible, statement.reason = "PL/SQL", False, PLSQL_INSIDE
        else:
            found = tables_of(chunk, dialect)
            if found is None:
                statement.visible, statement.reason = False, NOT_PARSED
            else:
                statement.kind, statement.reads, statement.writes = found
        out.append(statement)
    return out


def files(paths: list[str | Path]) -> dict[str, Path]:
    """Label -> file, labelled the way `collect` labels its statements."""
    out: dict[str, Path] = {}
    for given in paths:
        given = Path(given)
        for file in (sorted(given.rglob("*.sql")) if given.is_dir() else [given]):
            out[str(file.relative_to(given)) if given.is_dir() else file.name] = file
    return out


def collect(paths: list[str | Path], dialect: str = "oracle") -> list[AppStatement]:
    """Files, or directories searched for `*.sql`. A file is labelled by its path under what was given, so two
    `queries.sql` in different directories stay two files."""
    out: list[AppStatement] = []
    for given in paths:
        given = Path(given)
        files = sorted(given.rglob("*.sql")) if given.is_dir() else [given]
        for file in files:
            label = str(file.relative_to(given)) if given.is_dir() else file.name
            out += statements(file, dialect, label)
    return out
