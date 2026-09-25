#!/usr/bin/env python3
"""P0-4: run the PL/SQL corpus on Oracle and freeze what it does, as canonical JSON.

Phase 3 compares the generated Java against these captures, so the capture format and the normalisation rules are
decided here, once, and P0-5 only collects with them.

  deploy  create the corpus schema and compile every unit; report what did not compile
  run     run scenarios and write one canonical JSON per scenario
  list    list the scenarios

    .venv/bin/python difftest/plsql_run.py deploy
    .venv/bin/python difftest/plsql_run.py run --out fixtures/plsql/golden

The Oracle connection comes from a profile (difftest/sources.py; default difftest/conf/sources/oracle-local.json).
Both deploy and run create and delete data, so only a disposable database is accepted.

## What the capture holds

    {"scenario", "unit", "routine", "source": "oracle",
     "pinned":    {"sysdate": ..., "sequences": {...}, "user": ...},   # user は scenario が宣言したときだけ
     "sessionUser": ...,       # Oracle の USER の実測値。ターゲット側の AuditContext はこれを使う
     "result":    {"returned": <value>, "out": {name: value}},
     "exception": {"code": -20020, "message": "..."} | null,
     "tables":    {name: {"columns": [...], "rows": [[...]]}},
     "masked":    {table: [column, ...]}}

Values use the same encoding as difftest/golden.py so the two capture formats read alike:
Decimal -> {"$dec": "1.5"}, datetime -> {"$ts": ISO}, date -> {"$date": ISO}, NULL -> null.

## Normalisation rules (these are the rules Phase 3 compares under)

* Rows are sorted by their canonical JSON. Order is not part of the specification for a table dump, but duplicates
  are: sorting is stable and **never removes a duplicate row**.
* A scenario may declare `mask:` for columns written from a clock the harness cannot pin (see below). A masked value
  becomes {"$masked": "<reason>"} and the columns are listed in "masked", so nothing is dropped silently.
* Everything else is compared exactly, including NULL versus empty string and numeric scale.

## What can and cannot be pinned (measured on Oracle 26ai Free, 2026-09-17)

  SYSDATE                                   pinned by ALTER SYSTEM SET FIXED_DATE
  SYSTIMESTAMP, CURRENT_DATE, LOCALTIMESTAMP   NOT pinned by FIXED_DATE -- they keep the real clock
  sequences                                 pinned by recreating them with START WITH

FIXED_DATE is an instance-wide setting and needs a privileged connection (--sys-user, default `system`), which is
why this only ever runs against a disposable container. Columns fed by SYSTIMESTAMP therefore have to be masked per
scenario; `prc_audit_autonomous`, `trg_orders_audit` and `pkg_payment.record_payment` are the ones in this corpus.
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import (DISPOSABLE, SYS_PASSWORD_ENV, ProfileError, parse_profile_args,  # noqa: E402
                     source_config, sys_config)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "plsql"
SRC = FIXTURES / "src"
SCENARIOS = FIXTURES / "scenarios"

# package 仕様を本体より先に、依存される package を依存する側より先に流す。
# corpus は小さいので依存解析はせず、ここに順序を書く（増えたら P1-3 の依存グラフを使う）。
DEPLOY_ORDER = [
    "pkg_customer_crud.pks", "pkg_order_status.pks", "pkg_order_pricing.pks", "pkg_customer_view.pks",
    "pkg_order_report.pks", "pkg_bulk_load.pks", "pkg_dynamic_search.pks", "pkg_money_calc.pks",
    "pkg_stock_reserve.pks", "holdout/pkg_payment.pks", "holdout/pkg_customer_import.pks",
    "holdout/pkg_order_lock.pks",
    "pkg_customer_crud.pkb", "pkg_order_status.pkb", "pkg_order_pricing.pkb", "pkg_customer_view.pkb",
    "pkg_order_report.pkb", "pkg_bulk_load.pkb", "pkg_dynamic_search.pkb", "pkg_money_calc.pkb",
    "pkg_stock_reserve.pkb", "holdout/pkg_payment.pkb", "holdout/pkg_customer_import.pkb",
    "holdout/pkg_order_lock.pkb",
    "prc_add_product.prc", "prc_nightly_close.prc", "prc_audit_autonomous.prc", "prc_remote_sync.prc",
    "holdout/prc_reprice_all.prc",
    "trg_orders_audit.trg", "trg_orders_seq.trg", "holdout/trg_products_audit.trg",
    # Issue #29-25: DELETE / 複数イベントの trigger と、それを routine 経由で通す package
    "pkg_line_edit.pks", "pkg_line_edit.pkb", "trg_lines_audit.trg", "trg_inventory_tx_keep.trg",
    # Issue #29-23: オーバーロード
    "pkg_contact.pks", "pkg_contact.pkb",
    # trigger を routine 経由で確かめるための書き込み経路
    "pkg_write_paths.pks", "pkg_write_paths.pkb",
    # holdout2: ルールを凍結した後に足した独立ホールドアウト（fixtures/plsql/README.md）
    "holdout2/pkg_shipment.pks", "holdout2/pkg_tier_admin.pks",
    "holdout2/pkg_shipment.pkb", "holdout2/pkg_tier_admin.pkb",
    "holdout2/prc_purge_audit.prc", "holdout2/trg_payments_guard.trg",
]

TABLES = ["customers", "products", "orders", "order_lines", "payments", "inventory_tx",
          "audit_log", "counters", "batch_control"]
# DB link の先の表（difftest/plsql-warehouse-init.sh が作る）。capture では移行先と同じ名前——
# `namespace.table`（limits.yaml: dbLinks）——で呼び、Oracle へは link 越しに読む。既定の capture には
# 入れない: link を使わない scenario の golden を動かさないためで、比べたい scenario が名前で挙げる
REMOTE_TABLES = {"warehouse.orders": "orders@warehouse_link",
                 "warehouse.shipment_queue": "shipment_queue@warehouse_link"}
SEQUENCES = {"seq_order_id": 1000, "seq_payment_id": 5000, "seq_audit_id": 1, "seq_tx_id": 1}


def use_project(directory: str | Path) -> Path:
    """Point the harness at a project other than the corpus: `<dir>/src` (with `schema.sql`) and `<dir>/scenarios`.

    The corpus lists its tables, sequences and deploy order by hand because they carry decisions (parents before
    children, specs before the bodies that need them). A project that arrives from outside has no such list, so
    they are read off its DDL and its file names. Connect as that project's own Oracle user
    (`SRC_ORACLE_USER` / `SRC_ORACLE_PASSWORD`): deploy drops and recreates every table the DDL names.
    """
    global SRC, SCENARIOS, TABLES, SEQUENCES, DEPLOY_ORDER, REMOTE_TABLES, IDENTITY_COLUMNS
    root = Path(directory).resolve()
    SRC, SCENARIOS = root / "src", root / "scenarios"
    ddl = (SRC / "schema.sql").read_text(encoding="utf-8")
    ddl = re.sub(r"--[^\n]*", "", ddl)
    TABLES = [m.lower() for m in re.findall(r"CREATE\s+TABLE\s+([A-Za-z_][\w$#]*)", ddl, re.IGNORECASE)]
    SEQUENCES = {}
    for name, rest in re.findall(r"CREATE\s+SEQUENCE\s+([A-Za-z_][\w$#]*)([^;]*);", ddl, re.IGNORECASE):
        start = re.search(r"START\s+WITH\s+(\d+)", rest, re.IGNORECASE)
        SEQUENCES[name.lower()] = int(start.group(1)) if start else 1
    IDENTITY_COLUMNS = _identity_columns(ddl)
    units = sorted(p for p in SRC.rglob("*") if p.suffix in {".pks", ".pkb", ".prc", ".fnc", ".trg"})
    order = {".pks": 0, ".fnc": 1, ".prc": 1, ".pkb": 2, ".trg": 3}   # a spec before whatever calls it
    DEPLOY_ORDER = [str(p.relative_to(SRC)) for p in sorted(units, key=lambda p: (order[p.suffix], p.name))]
    REMOTE_TABLES = {}
    return root


# --------------------------------------------------------------------------------------------------
# canonical encoding (same shapes as difftest/golden.py)
# --------------------------------------------------------------------------------------------------

def encode(v):
    if isinstance(v, decimal.Decimal):
        return {"$dec": str(v)}
    if isinstance(v, datetime.datetime):  # before date: datetime is a date subclass
        return {"$ts": v.isoformat()}
    if isinstance(v, datetime.date):
        return {"$date": v.isoformat()}
    if isinstance(v, (bytes, bytearray)):
        return {"$raw": bytes(v).hex()}
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return {"$str": str(v)}  # LOB handles and the like: keep the text, never guess a type


def canonical_rows(rows: list[list]) -> list[list]:
    """Sort by the encoded form. Stable, and duplicates survive -- row multiplicity is part of the answer."""
    return sorted(rows, key=lambda r: json.dumps(r, ensure_ascii=False, sort_keys=True))


# --------------------------------------------------------------------------------------------------
# Oracle plumbing
# --------------------------------------------------------------------------------------------------

def split_plsql(text: str) -> list[str]:
    """Split a corpus file into statements. PL/SQL bodies end at a line holding only '/'."""
    out, buf = [], []
    for line in text.splitlines():
        if line.strip() == "/":
            body = "\n".join(buf).strip()
            if body:
                out.append(body)
            buf = []
        else:
            buf.append(line)
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        out.append(tail)
    return out


def split_sql(text: str) -> list[str]:
    """Split plain SQL (schema.sql, scenario setup) on ';' at end of line, dropping comment-only chunks."""
    chunks = re.split(r";\s*(?:\n|$)", text)
    return [c.strip() for c in chunks
            if c.strip() and not all(l.strip().startswith("--") or not l.strip() for l in c.splitlines())]


def connect(cfg):
    import oracledb
    oracledb.defaults.fetch_decimals = True  # NUMBER as Decimal, not float: scale is part of the answer
    return oracledb.connect(**cfg.oracle_kwargs())


def object_errors(cur) -> list[dict]:
    cur.execute("SELECT name, type, line, position, text FROM user_errors "
                "WHERE name NOT LIKE 'BIN$%' ORDER BY name, sequence")
    return [{"name": n.lower(), "type": t, "line": line, "position": pos, "text": text.strip()}
            for n, t, line, pos, text in cur.fetchall()]


def invalid_objects(cur) -> list[str]:
    cur.execute("SELECT object_name FROM user_objects WHERE status <> 'VALID' "
                "AND object_name NOT LIKE 'BIN$%' ORDER BY object_name")
    return [r[0].lower() for r in cur.fetchall()]


# --------------------------------------------------------------------------------------------------
# deploy
# --------------------------------------------------------------------------------------------------

def deploy(args) -> int:
    cfg = source_config("oracle", parse_profile_args(args.profile), writes=True)
    print(f"deploy to {cfg.label()}")
    con = connect(cfg)
    cur = con.cursor()
    import oracledb
    try:
        for name in reversed(TABLES):
            try:
                cur.execute(f"DROP TABLE {name} CASCADE CONSTRAINTS")
            except oracledb.DatabaseError:
                pass
        for name in SEQUENCES:
            try:
                cur.execute(f"DROP SEQUENCE {name}")
            except oracledb.DatabaseError:
                pass
        cur.execute("PURGE RECYCLEBIN")  # dropped tables linger as BIN$... objects and read as invalid

        for stmt in split_sql((SRC / "schema.sql").read_text(encoding="utf-8")):
            cur.execute(stmt)
        con.commit()
        print(f"schema: {len(TABLES)} tables, {len(SEQUENCES)} sequences")

        compiled, failed = 0, []
        for relative in DEPLOY_ORDER:
            for stmt in split_plsql((SRC / relative).read_text(encoding="utf-8")):
                try:
                    cur.execute(stmt)
                    compiled += 1
                except oracledb.DatabaseError as e:
                    failed.append((relative, str(e).splitlines()[0]))
        con.commit()

        errors = object_errors(cur)
        invalid = invalid_objects(cur)
        print(f"compiled {compiled}/{len(DEPLOY_ORDER)} units")
        for relative, message in failed:
            print(f"  FAILED {relative}: {message}")
        if invalid:
            print(f"  INVALID: {', '.join(invalid)}")
            for e in errors[:10]:
                print(f"    {e['name']} line {e['line']}: {e['text']}")
        # prc_remote_sync references a DB link that does not exist here; that is expected, not a deploy failure
        unexpected = [o for o in invalid if o not in {"prc_remote_sync"}]
        if unexpected:
            print(f"error: unexpected invalid objects: {', '.join(unexpected)}", file=sys.stderr)
            return 1
        return 0
    finally:
        con.close()


# --------------------------------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------------------------------

def load_scenarios(only: str | None) -> list[dict]:
    files = sorted(SCENARIOS.glob("*.yaml"))
    out = []
    for f in files:
        spec = yaml.safe_load(f.read_text(encoding="utf-8"))
        spec["_file"] = f.name
        if only in (None, spec["name"]):
            out.append(spec)
    if only and not out:
        raise SystemExit(f"no scenario named {only!r} in {SCENARIOS}")
    return out


IDENTITY_COLUMNS: list[tuple[str, str, int]] = []   # (table, column, start) of the project's IDENTITY columns


def _identity_columns(ddl: str) -> list[tuple[str, str, int]]:
    out = []
    for table_match in re.finditer(r"CREATE\s+TABLE\s+([A-Za-z_][\w$#]*)\s*\((.*?)\);", ddl, re.IGNORECASE | re.DOTALL):
        for column_match in re.finditer(
                r"([A-Za-z_][\w$#]*)\s+[^,]*?GENERATED\s+(?:ALWAYS|BY\s+DEFAULT)(?:\s+ON\s+NULL)?\s+AS\s+IDENTITY"
                r"(?:\s*\(([^)]*)\))?", table_match.group(2), re.IGNORECASE | re.DOTALL):
            start = re.search(r"START\s+WITH\s+(\d+)", column_match.group(2) or "", re.IGNORECASE)
            out.append((table_match.group(1).lower(), column_match.group(1).lower(), int(start.group(1)) if start else 1))
    return out


def reset_data(cur, sequences: dict) -> None:
    """Empty every table and put the sequences back to a known value, so a rerun starts from the same state."""
    import oracledb
    for name in reversed(TABLES):  # TABLES は親 -> 子の順。削除は子から
        cur.execute(f"DELETE FROM {name}")
    for remote in REMOTE_TABLES.values():
        try:
            cur.execute(f"DELETE FROM {remote}")
        except oracledb.DatabaseError as e:
            # link が無い環境（plsql-warehouse-init.sh を流していない）でも、link を使わない scenario は動く
            if e.args[0].code not in (2019, 942, 4063):   # link 無し / 表無し / 無効な synonym
                raise
    for name, start in {**SEQUENCES, **(sequences or {})}.items():
        try:
            cur.execute(f"DROP SEQUENCE {name}")
        except oracledb.DatabaseError:
            pass
        cur.execute(f"CREATE SEQUENCE {name} START WITH {int(start)} INCREMENT BY 1 NOCACHE")
    for table, column, start in IDENTITY_COLUMNS:
        # an IDENTITY column keeps counting across DELETEs; the target's counter starts each scenario at the
        # DDL's START WITH, so Oracle is put back there too (samples/oracle-samples emp_audit, 2026-09-25)
        cur.execute(f"ALTER TABLE {table} MODIFY ({column} GENERATED BY DEFAULT AS IDENTITY (START WITH {start}))")


def set_triggers(cur, enabled: bool) -> None:
    """Every trigger on the corpus tables, on or off. DDL, so it commits what came before it."""
    for name in TABLES:
        cur.execute(f"ALTER TABLE {name} {'ENABLE' if enabled else 'DISABLE'} ALL TRIGGERS")


def pin_sysdate(sys_cfg, value: str | None) -> None:
    """ALTER SYSTEM SET FIXED_DATE. Instance-wide and SYSDATE-only; SYSTIMESTAMP keeps the real clock."""
    if sys_cfg is None:
        return
    con = connect(sys_cfg)
    try:
        cur = con.cursor()
        if value:
            cur.execute("ALTER SYSTEM SET FIXED_DATE='%s'" % _fixed_date(value))
        else:
            cur.execute("ALTER SYSTEM SET FIXED_DATE=NONE")
    finally:
        con.close()


def _fixed_date(value: str) -> str:
    """'2026-01-15 09:30:00' -> Oracle's FIXED_DATE literal 'YYYY-MM-DD-HH24:MI:SS'."""
    return value.strip().replace(" ", "-")


def call_routine(cur, spec: dict) -> tuple[dict, dict | None]:
    """Run the scenario's call. Returns (result, exception)."""
    import oracledb
    call = spec["call"]
    binds = {}
    for name, value in (call.get("args") or {}).items():
        # リストは PL/SQL の索引付き表（INDEX BY PLS_INTEGER）として束縛する
        binds[name] = cur.arrayvar(_array_type(value), value) if isinstance(value, list) else value
    out_specs = call.get("out") or {}
    out_vars = {}
    for name, oracle_type in out_specs.items():
        out_vars[name] = cur.var(_oracle_type(cur, oracle_type))
        if name in binds and not isinstance(binds[name], list):
            # IN OUT: the same name is in `args` (its value going in) and in `out` (its type coming back)
            out_vars[name].setvalue(0, binds[name])
        binds[name] = out_vars[name]

    def invoke():
        if call["kind"] == "function":
            return cur.callfunc(call["name"], _oracle_type(cur, call["returns"]), keyword_parameters=binds)
        if call["kind"] == "block":
            # 無名ブロック。PL/SQL 専用の戻り（%ROWTYPE、BOOLEAN）や、routine を持たない経路
            # （trigger を素の INSERT で踏むなど）はこれでないと捕まえられない
            cur.execute(call["body"], binds)
            return None
        cur.callproc(call["name"], keyword_parameters=binds)
        return None

    try:
        try:
            returned = invoke()
        except oracledb.DatabaseError as e:
            # ORA-04068: a package with state was invalidated (a sequence it uses was recreated for the
            # scenario) and Oracle discards the state on the first call; the call itself is fine the second
            # time. Retrying once is what every client does (samples/oracle-samples emp_api, 2026-09-25)
            if e.args and getattr(e.args[0], "code", None) == 4068:
                returned = invoke()
            else:
                raise
    except oracledb.DatabaseError as e:
        (err,) = e.args
        # 例外は結果である。握り潰さず、コードとメッセージの 1 行目を記録する
        return ({"returned": None, "out": {}},
                {"code": err.code and -err.code, "message": (err.message or "").splitlines()[0]})

    return ({"returned": encode(returned),
             "out": {n: encode(v.getvalue()) for n, v in out_vars.items()}}, None)


def _array_type(values: list):
    import oracledb
    return oracledb.DB_TYPE_VARCHAR if any(isinstance(v, str) for v in values) else oracledb.DB_TYPE_NUMBER


def _oracle_type(cur, name: str):
    import oracledb
    return {
        "NUMBER": oracledb.DB_TYPE_NUMBER,
        "VARCHAR2": oracledb.DB_TYPE_VARCHAR,
        "DATE": oracledb.DB_TYPE_DATE,
        "TIMESTAMP": oracledb.DB_TYPE_TIMESTAMP,
        "TIMESTAMP WITH TIME ZONE": oracledb.DB_TYPE_TIMESTAMP_TZ,
    }[name.upper()]


def dump_tables(cur, tables: list[str], mask: dict) -> tuple[dict, dict]:
    dumped, masked = {}, {}
    for table in tables:
        cur.execute(f"SELECT * FROM {REMOTE_TABLES.get(table, table)}")
        columns = [d[0].lower() for d in cur.description]
        mask_columns = [c.lower() for c in (mask or {}).get(table, [])]
        rows = []
        for row in cur.fetchall():
            encoded = []
            for column, value in zip(columns, row):
                if column in mask_columns and value is not None:
                    encoded.append({"$masked": "clock the harness cannot pin"})
                else:
                    encoded.append(encode(value))
            rows.append(encoded)
        dumped[table] = {"columns": columns, "rows": canonical_rows(rows)}
        if mask_columns:
            masked[table] = mask_columns
    return dumped, masked


def run(args) -> int:
    profiles = parse_profile_args(args.profile)
    cfg = source_config("oracle", profiles, writes=True)
    if cfg.environment not in DISPOSABLE:
        raise ProfileError(f"run needs a disposable database; profile environment is {cfg.environment!r}")
    sys_cfg = None
    if not args.no_pin_sysdate:
        sys_cfg = sys_config(cfg, args.sys_user, args.sys_password_env)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    scenarios = load_scenarios(args.scenario)
    print(f"run {len(scenarios)} scenario(s) on {cfg.label()}")

    con = connect(cfg)
    failures = 0
    try:
        cur = con.cursor()
        # Oracle の USER は接続しているスキーマユーザである。生成コードはこれを呼び出し側の引数として
        # 受け取るので（#1）、シナリオが固定した値と食い違えば changed_by が食い違う。実測値を capture に
        # 入れて、食い違いが黙って通らないようにする
        cur.execute("SELECT USER FROM dual")
        session_user = cur.fetchone()[0]
        for spec in scenarios:
            pinned = spec.get("pinned") or {}
            try:
                pin_sysdate(sys_cfg, pinned.get("sysdate"))
                # リセットと setup は「呼ぶ前からあったデータ」を作るだけで、比べる対象ではない。trigger を
                # 生かしたままだと、前の scenario の行を消す DELETE や setup の INSERT で監査行と採番が進み、
                # ScalarDB 側（setup は trigger の無い直接の DML）と初期状態が食い違う（#29 の 25）
                set_triggers(cur, enabled=False)
                try:
                    reset_data(cur, pinned.get("sequences"))
                    # setup の各要素が 1 文である。連結してから分割すると、末尾に ';' の無い文が繋がってしまう
                    for stmt in (spec.get("setup") or []):
                        cur.execute(stmt.strip().rstrip(";").rstrip())
                    con.commit()
                finally:
                    set_triggers(cur, enabled=True)

                result, exception = call_routine(cur, spec)
                try:
                    con.commit()
                except Exception as e:
                    # 失敗した呼び出しは、ふつう Oracle が文の単位で巻き戻すので、commit しても何も残らない。
                    # DB link 越しに書いた後の失敗だけは違う: Oracle はリモートの分だけを巻き戻せず、
                    # トランザクション全体の rollback を求める（ORA-02067）。呼び出し側に残された道はそれだけ
                    # なので、そうする——移行先の「例外なら rollback」と同じ結末になる
                    if exception is None or "ORA-02067" not in str(e):
                        raise
                    con.rollback()
                tables, masked = dump_tables(cur, spec.get("capture_tables") or TABLES, spec.get("mask"))
            finally:
                pin_sysdate(sys_cfg, None)

            capture = {
                "scenario": spec["name"], "unit": spec["unit"], "routine": spec["routine"], "source": "oracle",
                # `pinned` は**両側が使うよう指示された値**であり、scenario が宣言したものだけが入る。
                # 実測した USER は `sessionUser` にだけ置く（#22）。両方に置くと、ターゲット側の
                # capture は scenario の宣言しか写さないので、golden を取り直した瞬間に全件が
                # `pinned` の差分になる——比較が通っていたのは golden が古いおかげだった。
                "pinned": {k: v for k, v in
                           (("sysdate", pinned.get("sysdate")),
                            ("sequences", pinned.get("sequences") or {}),
                            ("user", pinned.get("user")))
                           if not (k == "user" and v is None)},
                "sessionUser": session_user,
                "result": result, "exception": exception, "tables": tables, "masked": masked,
            }
            path = out_dir / f"{spec['name']}.json"
            path.write_text(json.dumps(capture, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
                            encoding="utf-8")
            summary = "raised %s" % exception["code"] if exception else "returned %r" % (result["returned"],)
            print(f"  {spec['name']:<34} {summary}")
    finally:
        con.close()
    print(f"wrote {len(scenarios)} capture(s) to {out_dir}/")
    return 1 if failures else 0


def list_scenarios(args) -> int:
    for spec in load_scenarios(None):
        print(f"{spec['name']:<34} {spec['unit']}.{spec['routine']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", action="append", metavar="DIALECT=PATH")
    common.add_argument("--project", metavar="DIR",
                        help="a project other than the corpus: DIR/src (with schema.sql) and DIR/scenarios; "
                             "captures go to DIR/golden. Connect as that project's own user")

    p = sub.add_parser("deploy", parents=[common], help="create the schema and compile the corpus")
    p.set_defaults(func=deploy)

    p = sub.add_parser("run", parents=[common], help="run scenarios and write captures")
    p.add_argument("--out", default=None, help="default: fixtures/plsql/golden, or DIR/golden with --project")
    p.add_argument("--scenario", help="run only this scenario")
    p.add_argument("--sys-user", default="system", help="privileged user for ALTER SYSTEM SET FIXED_DATE")
    p.add_argument("--sys-password-env", default=SYS_PASSWORD_ENV,
                   help="特権ユーザのパスワードを持つ環境変数の名前（引数では渡さない: ps から読める）")
    p.add_argument("--no-pin-sysdate", action="store_true",
                   help="skip FIXED_DATE; captures then differ between runs wherever SYSDATE is stored")
    p.set_defaults(func=run)

    p = sub.add_parser("list", parents=[common], help="list the scenarios")
    p.set_defaults(func=list_scenarios)

    args = ap.parse_args(argv)
    project = use_project(args.project) if args.project else None
    if getattr(args, "out", "set") is None:
        args.out = str((project or FIXTURES) / "golden")
    try:
        return args.func(args)
    except ProfileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
