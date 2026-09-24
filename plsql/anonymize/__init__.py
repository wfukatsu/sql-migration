"""Anonymise PL/SQL and DDL so that real code can join the corpus without carrying its owner with it (#15).

    python -m plsql.anonymize <real-src> --out <anonymised-dir> --mapping-out /secure/dir/mapping.json

What is replaced (docs/design/plsql-corpus-anonymization.md is the policy, decided 2026-09-20):

* **every name** that is not Oracle's -- schemas, tables, columns, routines, variables, labels -- by a name that
  says nothing (`n0001`), the same original always becoming the same replacement, case-insensitively, across every
  file of the run. A conventional prefix (`p_`, `v_`, ...) is kept: it carries the author's habit, not the business;
* **every string literal**, by a dummy of the same length; equal literals stay equal, so `status = 'X'` still
  compares against what the INSERT wrote. A literal that is itself SQL (dynamic SQL) is anonymised as SQL, and a
  date or number format mask is kept -- both are structure, not data;
* **every number**, except the ones that are structure: 0 to 10, a size in a type (`VARCHAR2(100)`), and the
  application error codes (-20000 to -20999), which the policy keeps;
* **every comment**, removed. Its line breaks stay, so line numbers do not move.

## The check that makes the result usable

Anonymising must not change what the tool concludes, or the KPIs measured on the result say nothing about the
original. `check` analyses both trees and compares, routine by routine, the shape of what was found: statement
kinds, SQL kinds, how many tables each statement reads and writes, locks, diagnostics, the rules that fired and the
verdict. A difference means one of two things, and both need a person: a name that is Oracle's was replaced (add it
with `--keep`), or a rule depends on what something is *called* -- which is a finding about the rule.

## What this cannot promise

It works on tokens, not on meaning. It cannot know that a column *name* is itself confidential in combination with
others, or that the shape of a routine identifies a customer. The policy requires a person to read the result
before it is committed; `leaks` lists every original name and literal that still appears in the output, to make
that reading shorter, not to replace it.

The mapping from original to replacement is the key to undoing all this. It is written only where `--mapping-out`
says, never inside this repository, and never next to the output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from antlr4 import InputStream

from ..grammar.generated.PlSqlLexer import PlSqlLexer

SUFFIXES = (".pks", ".pkb", ".prc", ".fnc", ".trg", ".sql")
_NAMES = PlSqlLexer.symbolicNames
_IDENT = {"REGULAR_ID"}
_QUOTED = {"DELIMITED_ID"}
_STRING = {"CHAR_STRING", "NATIONAL_CHAR_STRING_LIT"}
_NUMBER = {"UNSIGNED_INTEGER", "APPROXIMATE_NUM_LIT"}
_COMMENT = {"SINGLE_LINE_COMMENT", "MULTI_LINE_COMMENT", "REMARK_COMMENT"}
_TYPES_WITH_SIZE = {"VARCHAR2", "VARCHAR", "CHAR", "NCHAR", "NVARCHAR2", "NUMBER", "NUMERIC", "DECIMAL", "DEC",
                    "FLOAT", "RAW", "TIMESTAMP", "INTERVAL", "INTEGER", "INT", "BINARY_FLOAT", "BINARY_DOUBLE"}
_PREFIX = re.compile(r"^(p|v|l|g|c|i|o|io|r|t|e|x|cur|rec|pk|fk|uq|ck|ix|idx|trg|pkg|prc|fnc|seq|vw)_", re.I)
_FORMAT_MASK = re.compile(r"^[YMDHSFPAXTZRIWQJ0-9:/\-., \"$BCEGLNUV]+$", re.I)
_LOOKS_LIKE_SQL = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|MERGE|WITH|FROM|WHERE|AND|OR|ORDER\s+BY|GROUP\s+BY|"
                             r"SET|VALUES|INTO|BEGIN|CALL|ALTER|CREATE|DROP|TRUNCATE|LOCK)\b", re.I)

# Names that are Oracle's and that the lexer hands over as ordinary identifiers. Replacing one of these changes what
# the code means -- and usually what the analysis says, which is how `check` finds the ones missing from this list.
KEEP = {name.upper() for name in """
    no_data_found too_many_rows dup_val_on_index zero_divide value_error invalid_number invalid_cursor
    cursor_already_open login_denied not_logged_on program_error storage_error timeout_on_resource others
    case_not_found collection_is_null subscript_beyond_count subscript_outside_limit access_into_null
    rowtype_mismatch self_is_null sys_invalid_rowid no_data_needed
    raise_application_error sqlcode sqlerrm nextval currval rownum rowid level sysdate systimestamp user uid
    current_date current_timestamp localtimestamp sessiontimezone dbtimezone dual
    found notfound isopen rowcount bulk_rowcount bulk_exceptions count first last next prior exists delete extend
    trim limit
    nvl nvl2 coalesce decode nullif greatest least to_char to_date to_number to_timestamp to_timestamp_tz to_clob
    to_nchar cast trunc round ceil floor mod abs sign power sqrt substr substrb instr instrb length lengthb upper
    lower initcap lpad rpad ltrim rtrim replace translate concat chr ascii regexp_like regexp_replace regexp_substr
    regexp_instr regexp_count add_months months_between last_day next_day extract numtodsinterval numtoyminterval
    sys_context sys_guid userenv listagg row_number rank dense_rank lag lead first_value last_value ntile
    sum avg min max stddev variance median hextoraw rawtohex utl_raw
    dbms_output put_line put new_line dbms_lock sleep dbms_random value string dbms_utility format_error_backtrace
    format_error_stack get_time dbms_sql dbms_lob dbms_job dbms_scheduler dbms_session dbms_application_info
    dbms_stats dbms_transaction dbms_crypto dbms_xmlgen dbms_metadata utl_file utl_http utl_smtp utl_mail
    pragma autonomous_transaction exception_init serially_reusable restrict_references inline
    new old inserting updating deleting
    pls_integer binary_integer simple_integer natural naturaln positive positiven boolean varchar2 nvarchar2
    clob nclob blob bfile xmltype sys_refcursor anydata urowid long
""".split()}


# Packages Oracle supplies are too many to list, and they share their prefixes. A name with one of these prefixes
# stays, and so does the member after it (`DBMS_ASSERT.SIMPLE_SQL_NAME`): the first run over the corpus renamed
# exactly that, the analysis then saw a call to a routine it did not know, and `check` reported the extra CALL-001.
_ORACLE_PACKAGE = re.compile(r"^(DBMS|UTL|SYS|APEX|OWA|CTX|SDO|ORD|XDB|WPG|HTMLDB)_|^(HTP|HTF|STANDARD|DBMS_STANDARD)$",
                             re.I)


class AnonymizeError(ValueError):
    pass


@dataclass
class Mapping:
    names: dict[str, str] = field(default_factory=dict)
    strings: dict[str, str] = field(default_factory=dict)
    numbers: dict[str, str] = field(default_factory=dict)
    keep: set[str] = field(default_factory=lambda: set(KEEP))

    def is_oracle(self, original: str) -> bool:
        return original.upper() in self.keep or bool(_ORACLE_PACKAGE.match(original))

    def name(self, original: str) -> str:
        key = original.upper()
        if self.is_oracle(original):
            return original
        if key not in self.names:
            prefix = _PREFIX.match(original)
            self.names[key] = (prefix.group(0).lower() if prefix else "") + f"n{len(self.names) + 1:04d}"
        return self.names[key]

    def string(self, inner: str) -> str:
        """The inside of a literal, quotes excluded. Same length, same value for the same original."""
        if inner == "" or _FORMAT_MASK.match(inner) and re.search(r"YY|MM|DD|HH|MI|SS|FM|99|00", inner, re.I):
            return inner
        if _LOOKS_LIKE_SQL.match(inner.replace("''", "'")):
            return anonymize_text(inner.replace("''", "'"), self, keep_comments=False).replace("'", "''")
        if inner not in self.strings:
            body = f"S{len(self.strings) + 1}"
            self.strings[inner] = (body + "x" * len(inner))[:max(len(inner), len(body))]
        return self.strings[inner]

    def number(self, original: str) -> str:
        if original not in self.numbers:
            digits = re.sub(r"\D", "", original)
            width = max(len(digits), 2)
            replacement = str(10 ** (width - 1) + len(self.numbers) + 1)[:width].rjust(width, "1")
            self.numbers[original] = replacement if "." not in original else replacement[:-1] + "." + replacement[-1]
        return self.numbers[original]

    def document(self) -> dict:
        return {"names": dict(sorted(self.names.items())), "strings": self.strings, "numbers": self.numbers}


def _tokens(text: str):
    lexer = PlSqlLexer(InputStream(text))
    lexer.removeErrorListeners()
    return lexer.getAllTokens()


def _is_structural_number(text: str, before: list[str]) -> bool:
    """0..10, a size inside a type, and an application error code."""
    try:
        value = float(text)
    except ValueError:
        return False
    if value == int(value) and 0 <= value <= 10:
        return True
    if 20000 <= value <= 20999 and before[-1:] == ["-"]:
        return True
    # NUMBER(12, 2): walk back over `digits` and `,` to the opening parenthesis, then to the type's name
    index = len(before) - 1
    while index >= 0 and (before[index] == "," or before[index].replace(".", "").isdigit()):
        index -= 1
    return index >= 1 and before[index] == "(" and before[index - 1].upper() in _TYPES_WITH_SIZE


def anonymize_text(text: str, mapping: Mapping, keep_comments: bool = False) -> str:
    out: list[str] = []
    significant: list[str] = []   # the text of the tokens that are neither space nor comment, as written
    for token in _tokens(text):
        kind = _NAMES[token.type] if 0 <= token.type < len(_NAMES) else ""
        piece = token.text
        if kind in _COMMENT and not keep_comments:
            out.append("\n" * piece.count("\n"))
            continue
        if kind == "SPACES" or kind in _COMMENT:
            out.append(piece)
            continue
        if kind in _IDENT:
            # the member of a package Oracle supplies is Oracle's too: DBMS_ASSERT . SIMPLE_SQL_NAME
            member = significant[-1:] == ["."] and len(significant) >= 2 and \
                bool(_ORACLE_PACKAGE.match(significant[-2]))
            piece = piece if member else mapping.name(piece)
        elif kind in _QUOTED:
            inner = piece[1:-1]
            piece = piece if inner.upper() in mapping.keep else '"' + mapping.name(inner).upper() + '"'
        elif kind in _STRING:
            start = piece.index("'")
            piece = piece[:start + 1] + mapping.string(piece[start + 1:-1]) + "'"
        elif kind in _NUMBER and not _is_structural_number(piece, significant):
            piece = mapping.number(piece)
        out.append(piece)
        significant.append(token.text)
    return "".join(out)


def anonymize_tree(src: str | Path, out: str | Path, mapping: Mapping | None = None) -> tuple[Mapping, list[str]]:
    """Every source file under `src`, written under `out` with its name anonymised too. Returns the mapping and the
    files written. File names are names: `pkg_acme_billing.pkb` says whose code it is."""
    src, out = Path(src), Path(out)
    if not src.is_dir():
        raise AnonymizeError(f"{src} is not a directory")
    if out.resolve() == src.resolve() or src.resolve() in out.resolve().parents:
        raise AnonymizeError("the output may not be the source tree or inside it: the original must stay untouched")
    mapping = mapping or Mapping()
    files = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
    if not files:
        raise AnonymizeError(f"{src} holds no {', '.join(SUFFIXES)} file")
    # DDL first: a table is then named before the code that uses it, which keeps the numbering stable to read
    files.sort(key=lambda p: (p.suffix.lower() != ".sql", str(p)))
    written = []
    for path in files:
        text = anonymize_text(path.read_text(encoding="utf-8-sig"), mapping)
        relative = path.relative_to(src)
        stem = relative.stem if relative.stem.lower() == "schema" else mapping.name(relative.stem)
        target = out / relative.parent / (stem + path.suffix.lower())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(str(target.relative_to(out)))
    return mapping, written


def leaks(out: str | Path, mapping: Mapping) -> list[dict]:
    """Original names and literals that still appear in the output. Not proof of a leak -- `id` maps to `n0007` and
    `id` may be a substring of nothing -- but each is a place for the person reading the result to look."""
    originals = {name for name in mapping.names if len(name) >= 4}
    literals = {literal for literal in mapping.strings if len(literal) >= 4}
    found = []
    for path in sorted(Path(out).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        upper = text.upper()
        for name in sorted(originals):
            if re.search(rf"(?<![A-Z0-9_$#]){re.escape(name)}(?![A-Z0-9_$#])", upper):
                found.append({"file": path.name, "kind": "name", "original": name})
        for literal in sorted(literals):
            if f"'{literal}'" in text:   # as a literal: `OPEN` the keyword is not 'OPEN' the value
                found.append({"file": path.name, "kind": "string", "original": literal})
    return found


def write_mapping(mapping: Mapping, path: str | Path, repository: Path) -> Path:
    path = Path(path).resolve()
    if repository.resolve() == path or repository.resolve() in path.parents:
        raise AnonymizeError(f"{path} is inside the repository. The mapping undoes the anonymisation: keep it where "
                             "the original code is kept, never here")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping.document(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path
