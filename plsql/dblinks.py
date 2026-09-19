"""`table@link` -> `namespace.table`, for the links somebody mapped (limits.yaml: dbLinks).

Oracle writes a remote table inside a distributed transaction. The one form that keeps that meaning on the target is
to bring the remote tables under ScalarDB too, as another namespace, and write both in one ScalarDB transaction
(decided 2026-09-20). Only a person can say that a link leads somewhere ScalarDB manages, so only a mapped link is
rewritten; any other stays as it is, and `LINK-001` stays open for it.

The rewrite is on the SQL text and runs before the capability check, so the rewritten statement is converted and
judged like any other -- the same position `merge.rewrite` and `rmw.rewrite` take.
"""

from __future__ import annotations

import re

from .ir import model as M

REMOTE = re.compile(r"(?<![\w$#.\"])(?P<table>[A-Za-z][\w$#]*)\s*@\s*(?P<link>[A-Za-z][\w$#]*)")
_LITERAL = re.compile(r"'(?:[^']|'')*'")


def rewrite_sql(sql: str, db_links) -> tuple[str, list[str]]:
    """(the statement with every mapped `table@link` named by its namespace, what was mapped)."""
    mapped: list[str] = []
    if db_links is None or not db_links.namespaces or "@" not in (sql or ""):
        return sql, mapped

    def to_namespace(match: re.Match) -> str:
        namespace = db_links.namespace(match.group("link"))
        if namespace is None:
            return match.group(0)
        mapped.append(f"{match.group('table')}@{match.group('link')} -> {namespace}.{match.group('table')}")
        return f"{namespace}.{match.group('table')}"

    # a literal can hold an `@` of its own ('a@example.com'): only what is outside the quotes is a name
    pieces = [REMOTE.sub(to_namespace, piece) for piece in _LITERAL.split(sql)]
    literals = _LITERAL.findall(sql)
    if not mapped:
        return sql, mapped
    return "".join(p + (literals[i] if i < len(literals) else "") for i, p in enumerate(pieces)), mapped


def rewrite(program: M.Program, db_links) -> None:
    if db_links is None or not db_links.namespaces:
        return
    from .lower import _walk

    for module in program.modules:
        for routine in module.routines:
            statements = _walk(routine.body) + [s for h in routine.exception_handlers for s in _walk(h.body)]
            for statement in statements:
                if statement.kind != "SqlOperation" or "@" not in (statement.original_sql or ""):
                    continue
                rewritten, mapped = rewrite_sql(statement.original_sql, db_links)
                if not mapped:
                    continue
                statement.original_sql = rewritten
                statement.add("INFO", "DBLINK_MAPPED",
                              f"{', '.join(mapped)}: この DB link の表は ScalarDB の namespace として同じトランザクションで"
                              f"書く（limits.yaml: dbLinks）。Oracle の分散トランザクションと同じく、両方が確定するか、"
                              f"どちらも確定しない")
