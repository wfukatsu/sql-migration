"""文書の例を、変換ツールと実 DB 比較のハーネス（difftest/plsql_run.py --project）が読めるプロジェクトに組み立てる。

    SRC_ORACLE_USER=plsqldoc_b SRC_ORACLE_PASSWORD=plsqldoc_b .venv/bin/python samples/oracle-plsql-docs/build_projects.py
    ... build_projects.py --static     # 実行できない例（UNITS_ONLY / SOURCE_REJECTS）を work/static に（解析と生成だけ）

例ごとに:
  1. Oracle でそのまま流した結果（work/oracle、oracle_run.py）から、比べられる例かを分ける（CATEGORY）
  2. 無名ブロック（と SQL*Plus の EXEC）を引数なしの procedure `ex_<章>_<番号>[_b<k>]` に包む。本文は原文のまま
  3. 呼び出しごとに、Oracle（ユーザ plsqldoc_b）で「その呼び出しの直前まで」を流し、例が参照する表の定義（辞書から）と
     行（INSERT 文）を読む。これがシナリオの準備行と schema.sql になる。前の例に依存する例は、その文を先に流す
  4. 例ごとに 1 つのプロジェクト（work/projects/ex_<章>_<番号>、ScalarDB の namespace も同じ名前）

ScalarDB のキーは表の主キー。主キーの無い表は先頭の列を partition key にした（人の決定ではなく、この検証の既定）。
Oracle の DATE は TIMESTAMP に写す（samples/oracle-samples と同じ）。HR の外部キーと HR の trigger は持ち込まない。
"""
from __future__ import annotations

import datetime
import decimal
import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import oracledb
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from examples import HR_OBJECTS, WORK, Example, load, words  # noqa: E402
from oracle_run import connect, execute, hr_statements, reset  # noqa: E402
from script import Statement  # noqa: E402
from scalardb_migrate.converter import convert_script  # noqa: E402
from scalardb_migrate.schema import SchemaRegistry  # noqa: E402

PROJECTS = WORK / "projects"
oracledb.defaults.fetch_decimals = True      # NUMBER を float で読むと 2800 が 2800.0 になる
SYSDATE = "2026-09-26 09:30:00"
HR_TABLES = ["regions", "countries", "locations", "departments", "jobs", "employees", "job_history"]
DML = re.compile(r"^\s*(INSERT|UPDATE|DELETE|MERGE)\b", re.I)
SUFFIX = {"PROCEDURE": ".prc", "FUNCTION": ".fnc", "PACKAGE": ".pks", "PACKAGE BODY": ".pkb", "TRIGGER": ".trg"}


# --------------------------------------------------------------------------------------------------
# 1. 分類
# --------------------------------------------------------------------------------------------------

def categorize(ex: Example, asis: dict) -> tuple[str, str]:
    """(CATEGORY, 理由)。RUN だけがシナリオになる。"""
    if asis.get("harness_error"):
        return "HARNESS_ERROR", asis["harness_error"][:200]
    kinds = Counter(s.kind for s in ex.statements)
    units = [s for s in ex.statements if s.kind == "plsql_unit"]
    if not kinds["block"] and not units and not any(re.match(r"^\s*EXEC", s.text, re.I) for s in ex.statements):
        return "NO_PLSQL", "PL/SQL のブロックもユニットも無い（SQL 文・構文の図だけ）"
    records = asis["statements"]
    if any(r.get("skipped") == "SQL*Plus のバインド変数" for r in records):
        return "BIND_VARIABLES", "SQL*Plus のバインド変数（VARIABLE / :x）を使う"
    if any(re.search(r"&\w", s.text) for s in ex.statements if s.kind == "block"):
        return "SUBSTITUTION", "SQL*Plus の置換変数（&x）を使う"
    for s, r in zip(ex.statements, records):
        if s.kind == "plsql_unit" and s.unit_type == "LIBRARY":
            return "OUT_OF_SCOPE", "CREATE LIBRARY（外部プロシージャ）"
        if s.kind == "plsql_unit" and r.get("compile_errors"):
            return "SOURCE_REJECTS", f"{s.name}: {r['compile_errors'][0]}"
        if s.kind in ("block", "sqlplus") and r.get("error") and r["error"]["code"] == 6550:
            return "SOURCE_REJECTS", r["error"]["message"].splitlines()[1] if "\n" in r["error"]["message"] else r["error"]["message"]
        if s.kind == "plsql_unit" and r.get("error"):
            return "SOURCE_REJECTS", f"{s.name}: {r['error']['message'].splitlines()[0]}"
    if any(s.kind == "sqlplus" and re.match(r"^\s*(CONNECT|CONN)\b", s.text, re.I) for s in ex.statements):
        return "OUT_OF_SCOPE", "別ユーザへの CONNECT"
    if not calls(ex):
        return "UNITS_ONLY", "ユニットを作るだけで呼び出しが無い"
    return "RUN", ""


@dataclass
class Call:
    index: int                  # ex.statements の位置
    kind: str                   # procedure（包んだブロック）/ dml（素の DML。trigger の例）
    name: str                   # procedure 名か、DML の本文
    text: str


def calls(ex: Example) -> list[Call]:
    blocks = [i for i, s in enumerate(ex.statements)
              if s.kind == "block" or (s.kind == "sqlplus" and re.match(r"^\s*EXEC", s.text, re.I))]
    out = []
    for k, i in enumerate(blocks, start=1):
        s = ex.statements[i]
        name = ex.key + (f"_b{k}" if len(blocks) > 1 else "")
        text = s.text
        if s.kind == "sqlplus":
            text = "BEGIN\n  " + re.sub(r"^\s*EXEC(?:UTE)?\s+", "", s.text, flags=re.I).rstrip().rstrip(";") + ";\nEND;"
        out.append(Call(i, "procedure", name, text))
    # trigger の例: trigger を作った後の素の DML は、PL/SQL の外から表へ書く経路として比べる
    triggers = [i for i, s in enumerate(ex.statements) if s.kind == "plsql_unit" and s.unit_type == "TRIGGER"]
    if triggers:
        for i, s in enumerate(ex.statements):
            if i > triggers[0] and s.kind == "sql" and DML.match(s.text) and not re.search(r"&\w", s.text):
                out.append(Call(i, "dml", s.text, s.text))
    return sorted(out, key=lambda c: c.index)


def wrap(call: Call) -> str:
    """無名ブロックを引数なしの procedure に。DECLARE の宣言部はそのまま procedure の宣言部に、ラベル付きは入れ子で。"""
    text = call.text.strip()
    m = re.match(r"^DECLARE\b(.*)$", text, re.I | re.S)
    if m:
        return f"CREATE OR REPLACE PROCEDURE {call.name} AS{m.group(1)}"
    if re.match(r"^BEGIN\b", text, re.I):
        return f"CREATE OR REPLACE PROCEDURE {call.name} AS\n{text}"
    return f"CREATE OR REPLACE PROCEDURE {call.name} AS\nBEGIN\n{text}\nEND;"


# --------------------------------------------------------------------------------------------------
# 3. Oracle の状態を読む
# --------------------------------------------------------------------------------------------------

def column_type(c: dict) -> str:
    t = c["data_type"]
    if t == "NUMBER":
        if c["data_precision"] is None and c["data_scale"] is None:
            return "NUMBER"
        if c["data_precision"] is None and c["data_scale"] == 0:
            return "INTEGER"
        return f"NUMBER({c['data_precision']})" if not c["data_scale"] else f"NUMBER({c['data_precision']},{c['data_scale']})"
    if t in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        n = c["char_length"] or c["data_length"]
        return f"{t}({n}{' CHAR' if c['char_used'] == 'C' and t in ('VARCHAR2', 'CHAR') else ''})"
    if t == "RAW":
        return f"RAW({c['data_length']})"
    if t == "FLOAT":
        return f"FLOAT({c['data_precision']})" if c["data_precision"] else "FLOAT"
    return t


def table_ddl(cur, table: str) -> tuple[str, str, list[str], bool]:
    """(Oracle の DDL、ScalarDB 用に単純化した DDL、単一列の索引、主キーがあったか)。"""
    cur.execute("""SELECT column_name, data_type, data_type_owner, data_length, char_length, char_used, data_precision,
                          data_scale, nullable, data_default, virtual_column, hidden_column, identity_column, user_generated
                   FROM user_tab_cols WHERE table_name = :t AND user_generated = 'YES' ORDER BY internal_column_id""",
                t=table.upper())
    names = [d[0].lower() for d in cur.description]
    cols = [dict(zip(names, r)) for r in cur.fetchall()]
    cur.execute("SELECT column_name, generation_type FROM user_tab_identity_cols WHERE table_name = :t", t=table.upper())
    identity = dict(cur.fetchall())
    cur.execute("""SELECT c.constraint_name, c.constraint_type, c.search_condition_vc,
                          LISTAGG(cc.column_name, ',') WITHIN GROUP (ORDER BY cc.position)
                   FROM user_constraints c LEFT JOIN user_cons_columns cc ON cc.constraint_name = c.constraint_name
                   WHERE c.table_name = :t AND c.constraint_type IN ('P', 'U', 'C')
                   GROUP BY c.constraint_name, c.constraint_type, c.search_condition_vc""", t=table.upper())
    constraints = cur.fetchall()
    lines, simple = [], []
    for c in cols:
        name = c["column_name"].lower()
        if c["data_type_owner"] and c["data_type_owner"] not in ("SYS", "PUBLIC"):
            kind = c["data_type"].lower()
        else:
            kind = column_type(c)
        line = f"  {name} {kind}"
        if c["virtual_column"] == "YES":
            line += f" GENERATED ALWAYS AS ({(c['data_default'] or '').strip()}) VIRTUAL"
        elif c["identity_column"] == "YES":
            line += f" GENERATED {identity.get(c['column_name'], 'BY DEFAULT')} AS IDENTITY"
        elif c["data_default"] is not None and c["data_default"].strip().upper() != "NULL":
            line += f" DEFAULT {c['data_default'].strip()}"
        if c["hidden_column"] == "YES":
            line += " INVISIBLE"
        if c["nullable"] == "N" and c["identity_column"] != "YES":
            line += " NOT NULL"
        lines.append(line)
        simple.append((name, "TIMESTAMP" if c["data_type"] == "DATE" else kind))
    pk = None
    for cname, ctype, cond, columns in constraints:
        columns = (columns or "").lower()
        if ctype == "P":
            pk = columns.split(",")
            lines.append(f"  CONSTRAINT {cname.lower()} PRIMARY KEY ({columns})")
        elif ctype == "U":
            lines.append(f"  CONSTRAINT {cname.lower()} UNIQUE ({columns})")
        elif ctype == "C" and cond and not re.fullmatch(r'"\w+" IS NOT NULL', cond.strip()):
            name = "" if cname.startswith("SYS_C") else f"CONSTRAINT {cname.lower()} "
            lines.append(f"  {name}CHECK ({cond.strip()})")
    oracle = f"CREATE TABLE {table} (\n" + ",\n".join(lines) + "\n);"
    had_pk = pk is not None
    key = pk or [simple[0][0]]
    simple_ddl = (f"CREATE TABLE {table} (\n  " + ",\n  ".join(f"{n} {t}" for n, t in simple)
                  + f",\n  PRIMARY KEY ({', '.join(key)})\n);")
    cur.execute("""SELECT MIN(column_name) FROM user_ind_columns WHERE table_name = :t
                   GROUP BY index_name HAVING COUNT(*) = 1""", t=table.upper())
    indexes = sorted({r[0].lower() for r in cur.fetchall()} - set(key[:1]))
    return oracle, simple_ddl, indexes, had_pk


def literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, datetime.datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, datetime.date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, oracledb.LOB):
        value = value.read()
    if isinstance(value, (bytes, bytearray)):
        return f"HEXTORAW('{value.hex().upper()}')"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise ValueError(f"no literal for {type(value).__name__}")


def rows(cur, table: str) -> list[str]:
    cur.execute(f"SELECT * FROM {table}")
    columns = [d[0].lower() for d in cur.description]
    out = []
    for row in cur.fetchall():
        out.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(literal(v) for v in row)})")
    return out


@dataclass
class State:
    tables: dict[str, tuple[str, str, list[str], bool]]      # 名前 -> table_ddl
    setup: list[str]
    sequences: dict[str, int]
    others: list[str]                                        # schema.sql に置く型・sequence・view（原文）
    type_bodies: list[str]
    problem: str = ""


def referenced_tables(cur, ex: Example) -> list[str]:
    text = ex.code + "\n".join(s.text for _, s in ex.prerequisites)
    used = words(text)
    cur.execute("SELECT LOWER(table_name) FROM user_tables WHERE table_name NOT LIKE 'BIN$%' "
                "AND temporary = 'N' AND nested = 'NO' AND iot_type IS NULL")
    tables = [t for (t,) in cur.fetchall()]
    chosen = {t for t in tables if t in used}
    # view が参照する表
    cur.execute("SELECT LOWER(name), LOWER(referenced_name) FROM user_dependencies WHERE referenced_type = 'TABLE' "
                "AND type IN ('VIEW', 'TRIGGER')")
    for name, ref in cur.fetchall():
        if name in used and ref in tables:
            chosen.add(ref)
    return sorted(chosen, key=lambda t: (HR_TABLES.index(t) if t in HR_TABLES else 99, t))


def state_before(con, hr, ex: Example, index: int) -> State:
    cur = con.cursor()
    reset(cur, hr)
    cur.execute("DROP TRIGGER update_job_history")      # HR の trigger は持ち込まない
    cur.callproc("DBMS_OUTPUT.ENABLE", [None])
    for _, s in ex.prerequisites:
        execute(cur, s)
    for s in ex.statements[:index]:
        execute(cur, s)
    con.commit()
    tables, setup = {}, []
    problem = ""
    for t in referenced_tables(cur, ex):
        try:
            tables[t] = table_ddl(cur, t)
            setup += rows(cur, t)
        except ValueError as e:
            problem = f"{t}: {e}"
    sequences = {}
    cur.execute("SELECT LOWER(sequence_name) FROM user_sequences WHERE sequence_name NOT LIKE 'ISEQ$$%'")
    for (name,) in cur.fetchall():
        if name in words(ex.code) and name not in HR_OBJECTS:
            cur.execute(f"SELECT {name}.NEXTVAL FROM dual")
            sequences[name] = int(cur.fetchone()[0])
    others, bodies = [], []
    for _, s in list(ex.prerequisites) + [(ex.number, s) for s in ex.statements]:
        if s.kind == "plsql_unit" and s.unit_type == "TYPE":
            others.append(s.text.rstrip().rstrip(";").rstrip() + ";")
        elif s.kind == "plsql_unit" and s.unit_type == "TYPE BODY":
            bodies.append(s.text)
        elif s.kind == "sql" and re.match(r"^\s*CREATE\s+(OR\s+REPLACE\s+)?(SEQUENCE|VIEW|SYNONYM)\b", s.text, re.I):
            others.append(s.text.rstrip().rstrip(";") + ";")
    return State(tables, setup, sequences, others, bodies, problem)


# --------------------------------------------------------------------------------------------------
# 4. プロジェクト
# --------------------------------------------------------------------------------------------------

@dataclass
class Project:
    name: str
    tables: dict[str, tuple] = field(default_factory=dict)
    units: dict[str, tuple[str, str]] = field(default_factory=dict)      # ファイル名 -> (本文, 例番号)
    others: dict[str, str] = field(default_factory=dict)                 # 文 -> 例番号
    type_bodies: dict[str, str] = field(default_factory=dict)
    scenarios: list[dict] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def conflicts(self, tables: dict, units: dict, others: list[str]) -> bool:
        for t, ddl in tables.items():
            if t in self.tables and self.tables[t][0] != ddl[0]:
                return True
        for f, (body, _) in units.items():
            if f in self.units and self.units[f][0] != body:
                return True
        names = {object_name(o): o for o in self.others}
        return any(object_name(o) in names and names[object_name(o)] != o for o in others)


def object_name(statement: str) -> str:
    m = re.match(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TYPE|SEQUENCE|VIEW|SYNONYM)\s+(?:\w+\.)?\"?(\w+)", statement, re.I)
    return m.group(1).lower() if m else statement


def unit_files(ex: Example, calls_: list[Call]) -> dict[str, tuple[str, str]]:
    out = {}
    for number, s in list(ex.prerequisites) + [(ex.number, s) for s in ex.statements]:
        if s.kind == "plsql_unit" and s.unit_type in SUFFIX:
            out[f"{s.name.lower()}{SUFFIX[s.unit_type]}"] = (s.text.rstrip() + "\n/\n", number)
    for c in calls_:
        if c.kind == "procedure":
            out[f"{c.name}.prc"] = (wrap(c).rstrip() + "\n/\n", ex.number)
    return out


STATIC = WORK / "static"


def build_static(examples, categories: dict) -> None:
    """呼び出しの無い例（UNITS_ONLY）と Oracle が断る例（SOURCE_REJECTS）: 実行はできないので、解析と Java の生成・
    コンパイルだけを通す。ユニットと包んだブロックを work/static/<ex_n_m>/src に置く。表の定義は例を最後まで流した状態から。"""
    hr = hr_statements()
    con = connect()
    try:
        for ex in examples:
            if categories.get(ex.number, {}).get("category") not in ("UNITS_ONLY", "SOURCE_REJECTS"):
                continue
            con.close()
            con = connect()
            st = state_before(con, hr, ex, len(ex.statements))
            project = Project(ex.key, tables=st.tables, units=unit_files(ex, calls(ex)))
            for o in st.others:
                project.others.setdefault(o, ex.number)
            write_project(project, STATIC)
            print(f"{ex.number:>6} static {len(project.units)} unit(s)", flush=True)
    finally:
        con.close()


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv[:1] == ["--static"]:
        categories = json.loads((WORK / "categories.json").read_text(encoding="utf-8"))
        if STATIC.exists():
            shutil.rmtree(STATIC)
        build_static(load(), categories)
        return 0
    only = set(argv)
    examples = load()
    hr = hr_statements()
    con = connect()
    categories: dict[str, dict] = {}
    projects: list[Project] = []
    try:
        for ex in examples:
            if only and ex.number not in only:
                continue
            asis = json.loads((WORK / "oracle" / f"{ex.key}.json").read_text(encoding="utf-8"))
            category, reason = categorize(ex, asis)
            entry = {"title": ex.title, "category": category, "reason": reason, "url": ex.url}
            categories[ex.number] = entry
            if category != "RUN":
                continue
            calls_ = calls(ex)
            units = unit_files(ex, calls_)
            states = []
            for c in calls_:
                con.close()
                con = connect()
                states.append(state_before(con, hr, ex, c.index))
            tables = {}
            for st in states:
                tables.update(st.tables)
            others = list(dict.fromkeys(o for st in states for o in st.others))
            problem = next((st.problem for st in states if st.problem), "")
            if problem:
                entry.update(category="UNSEEDABLE", reason=f"表の行を INSERT 文にできない（{problem}）")
                continue
            # 例ごとに 1 プロジェクト。Gradle は生成物を木ごとコンパイルするので、1 つの例の javac エラーが同じ
            # プロジェクトの全部を止める（2026-09-26、引用識別子 "HELLO" で確かめた）。名前の衝突も起きない
            project = Project(ex.key)
            projects.append(project)
            project.tables.update(tables)
            project.units.update(units)
            for o in others:
                project.others.setdefault(o, ex.number)
            for st in states:
                for b in st.type_bodies:
                    project.type_bodies.setdefault(b, ex.number)
            project.examples.append(ex.number)
            entry["project"] = project.name
            entry["scenarios"] = []
            trigger = next((s.name.lower() for s in ex.statements if s.kind == "plsql_unit" and s.unit_type == "TRIGGER"), None)
            for k, (c, st) in enumerate(zip(calls_, states), start=1):
                name = c.name if c.kind == "procedure" else f"{ex.key}_dml{k}"
                spec = {"name": name, "unit": c.name if c.kind == "procedure" else trigger,
                        "routine": c.name if c.kind == "procedure" else trigger,
                        "pinned": {"sysdate": SYSDATE, **({"sequences": st.sequences} if st.sequences else {})},
                        "setup": st.setup,
                        "call": ({"kind": "procedure", "name": c.name} if c.kind == "procedure"
                                 else {"kind": "block", "body": "BEGIN\n  " + c.text.strip().rstrip(";") + ";\nEND;"}),
                        "capture_tables": list(st.tables) or ["harness_anchor"],
                        "output": True,
                        "note": f"例{ex.number} {ex.title}"}
                project.scenarios.append(spec)
                entry["scenarios"].append(name)
            print(f"{ex.number:>6} {project.name} {len(calls_)} call(s), tables {', '.join(tables) or '-'}", flush=True)
    finally:
        con.close()

    if PROJECTS.exists() and not only:
        shutil.rmtree(PROJECTS)
    for p in projects:
        if (PROJECTS / p.name).exists():
            shutil.rmtree(PROJECTS / p.name)
        write_project(p)
    if only and (WORK / "categories.json").exists():
        # 一部の例だけを組み直した: ほかの例の分類は残す
        categories = {**json.loads((WORK / "categories.json").read_text(encoding="utf-8")), **categories}
    (WORK / "categories.json").write_text(json.dumps(categories, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(Counter(e["category"] for e in categories.values()))
    print(f"{len(projects)} project(s), {sum(len(p.scenarios) for p in projects)} scenario(s) -> {PROJECTS}")
    return 0


def write_project(p: Project, base: Path = PROJECTS) -> None:
    root = base / p.name
    src = root / "src"
    (root / "scenarios").mkdir(parents=True, exist_ok=True)
    src.mkdir(parents=True, exist_ok=True)
    # 型は表より先に（オブジェクト型の列）。view は表の後に
    types = [o for o in p.others if re.match(r"^\s*CREATE\s+(OR\s+REPLACE\s+)?TYPE\b", o, re.I)]
    rest = [o for o in p.others if o not in types]
    schema = ["-- 生成物（build_projects.py）。表は Oracle の辞書から、型・sequence・view は例の原文から", ""]
    anchor = [] if p.tables else ["CREATE TABLE harness_anchor (id NUMBER(9) PRIMARY KEY);"]
    schema += types + [t[0] for t in p.tables.values()] + anchor + rest
    (src / "schema.sql").write_text("\n\n".join(schema) + "\n", encoding="utf-8")
    if p.type_bodies:
        (src / "type_bodies.sql").write_text("\n/\n".join(p.type_bodies) + "\n/\n", encoding="utf-8")
    for fname, (body, _) in p.units.items():
        (src / fname).write_text(body, encoding="utf-8")
    registry = SchemaRegistry()
    simple = "\n".join(t[1] for t in p.tables.values())
    results, registry = convert_script(simple, "oracle", registry)
    schema_json = {}
    for t, (_, _, indexes, _) in p.tables.items():
        meta = registry.get(t)
        if meta is None:
            continue
        columns = {c: ("TIMESTAMP" if kind == "DATE" else kind) for c, kind in meta.columns.items()}
        entry = {"transaction": True, "partition-key": meta.partition_key, "columns": columns}
        if meta.clustering_key:
            entry["clustering-key"] = [f"{c} ASC" for c in meta.clustering_key]
        if indexes:
            entry["secondary-index"] = [i for i in indexes if i in columns]
        schema_json[f"{p.name}.{t}"] = entry
    if not schema_json:
        # 表を使わない例でも namespace が無いと ScalarDbCaptureIT の接続が落ちる。例が触らない目印の表を両側に 1 つ置く
        schema_json[f"{p.name}.harness_anchor"] = {"transaction": True, "partition-key": ["id"], "columns": {"id": "INT"}}
    refused = {r.source_sql[:80]: [i.message for i in r.issues if i.severity == "ERROR"] for r in results if r.status == "ERROR"}
    (root / "scalardb-schema.json").write_text(json.dumps(schema_json, indent=1) + "\n", encoding="utf-8")
    if refused:
        (root / "scalardb-schema-refused.json").write_text(json.dumps(refused, ensure_ascii=False, indent=1) + "\n",
                                                           encoding="utf-8")
    for spec in p.scenarios:
        (root / "scenarios" / f"{spec['name']}.yaml").write_text(
            yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    (root / "examples.txt").write_text("\n".join(p.examples) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
