#!/usr/bin/env python3
"""SQL を、Source 方言から Target 方言 (SQLGlot の 32 方言) または ScalarDB SQL に変換する。

SQL を一度 AST に抽象化してから Target 向けに生成し直す。素の sqlglot.transpile() が
黙って通してしまう構文 (ROWNUM / CONNECT BY / NEXTVAL / ROWID など) を直すか、検出して報告する。

使い方 (リポジトリルートから):
    .venv/bin/python skills/sql-transpile/scripts/transpile.py samples/oracle.sql \\
        --source oracle --target postgres --out-dir out/transpile

    .venv/bin/python skills/sql-transpile/scripts/transpile.py samples/oracle.sql \\
        --source oracle --target scalardb --out-dir out/transpile

出力 (--out-dir 指定時):
    <stem>.<target>.sql   変換後 SQL。変換できない文は原文をコメントで残す
    <stem>.report.md      文ごとの状態・理由、指摘の集計、変換率
    <stem>.report.json    機械可読

終了コード:
    0 = ERROR の文なし
    1 = ERROR の文あり (手作業が要る)
    2 = 実行エラー (ファイルが無い、方言名が不正など)

出力の最終 3 行は機械可読:
    TOTAL=21
    CONVERTED=15
    RATE=71.4
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# スキル単体で動かすため、同梱モジュールを import パスに載せる
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sqlglot  # noqa: E402

import generic  # noqa: E402
import report  # noqa: E402
from _scalardb.appside import parse_expected_rows  # noqa: E402
from _scalardb.converter import convert_script as scalardb_convert  # noqa: E402
from _scalardb.schema import SchemaRegistry  # noqa: E402

SCALARDB_ONLY = ("keys", "storage", "plan_dir", "expected_rows", "h2_indexes")

SQLGLOT_DIALECTS = sorted(d.value for d in sqlglot.Dialects if d.value)
TARGETS = sorted(set(SQLGLOT_DIALECTS) | {"scalardb"})
SCALARDB_SOURCES = {"oracle", "postgres", "mysql"}  # ScalarDB の型対応表が作り込まれている方言


def _parse_keys(items: list[str] | None) -> dict[str, tuple[list[str], list[str]]]:
    """--keys table=p1,p2/c1,c2 (パーティションキー / クラスタリングキー)"""
    out = {}
    for item in items or []:
        table, _, spec = item.partition("=")
        p, _, c = spec.partition("/")
        out[table.strip().lower()] = ([x.strip() for x in p.split(",") if x.strip()],
                                      [x.strip() for x in c.split(",") if x.strip()])
    return out


def _generic_schema(registry: SchemaRegistry) -> dict:
    """Schema Loader JSON の表定義を、型の判定に使う {表名: {列名: 型}} にする。ScalarDB の型名はそのまま SQL の型名になる。"""
    return {meta.name: dict(meta.columns) for meta in registry.tables()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="SQL を Source 方言から Target 方言 または ScalarDB SQL に変換する",
        epilog=f"方言: {', '.join(TARGETS)}")
    ap.add_argument("file", help="変換する SQL スクリプト")
    ap.add_argument("--source", required=True, choices=SQLGLOT_DIALECTS, metavar="DIALECT",
                    help="変換元の方言 (例: oracle, postgres, mysql, tsql, snowflake)")
    ap.add_argument("--target", required=True, choices=TARGETS, metavar="DIALECT",
                    help="変換先の方言。scalardb で ScalarDB SQL")
    ap.add_argument("--out-dir", help="出力先ディレクトリ。省略時は標準出力にサマリのみ")
    ap.add_argument("--schema",
                    help="既存の表定義 (ScalarDB Schema Loader JSON)。ScalarDB 向けはキー設計に、"
                         "ほかの変換先は整数除算などの型に依存する書き換えに使う")
    ap.add_argument("--keys", action="append",
                    help="[scalardb のみ] キー指定 table=p1,p2/c1,c2 (パーティション/クラスタリング)")
    ap.add_argument("--mysql-case-insensitive", action="store_true",
                    help="MySQL の大文字小文字を区別しない文字列比較を、変換先でも区別しない形に書き換える。"
                         "索引が使われなくなることがある")
    ap.add_argument("--storage", default="jdbc", choices=["jdbc", "cassandra"],
                    help="[scalardb のみ] ScalarDB の背後のストレージ。cassandra ではクロスパーティションスキャンを使わない前提で判定する")
    ap.add_argument("--plan-dir",
                    help="[scalardb のみ] 変換できない読み取り文を実行計画（ScalarDB から取得し、残りを H2 で実行）に分解し、"
                         "計画の JSON をここに書く")
    ap.add_argument("--expected-rows", action="append", metavar="TABLE=N[:PER_KEY]",
                    help="[scalardb のみ] 表の行数（: の後にキーあたりの行数）。取得コストの見積もりに使う")
    ap.add_argument("--isolation", default="SERIALIZABLE", choices=["SERIALIZABLE", "SNAPSHOT", "READ_COMMITTED"],
                    help="[scalardb のみ] 見積もりの前提にする分離レベル。SERIALIZABLE はコミット時にスキャンを読み直す")
    ap.add_argument("--h2-indexes", action="store_true",
                    help="[scalardb のみ] 実行計画に、取得した表へ H2 の索引を作る指定を入れる。大きな表を結合するバッチ処理向け"
                         "（小さな要求や 1 表だけの計画では、索引を作る時間とメモリの分だけ遅くなる）")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"ファイルが見つかりません: {path}", file=sys.stderr)
        return 2
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        print(f"UTF-8 として読めません: {path}（{e.reason}、{e.start} バイト目）。UTF-8 に変換してから渡してください", file=sys.stderr)
        return 2

    # SQLGlot の「未対応」警告は Issue として回収済みなので、標準エラーの重複表示を抑える
    logging.getLogger("sqlglot").setLevel(logging.ERROR)

    # 入力の誤り（無いファイル、壊れた JSON、形式の違う引数）は 2。1 は「ERROR の文がある」のためにあり、
    # トレースバックで 1 が返ると、レポートの無い失敗と区別がつかない
    try:
        registry = SchemaRegistry.from_schema_loader_json(args.schema) if args.schema else SchemaRegistry()
        keys = _parse_keys(args.keys)
        expected_rows = parse_expected_rows(args.expected_rows)
    except (OSError, ValueError, KeyError) as e:
        print(f"引数を読めません: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if args.target == "scalardb":
        if args.source not in SCALARDB_SOURCES:
            print(f"注意: ScalarDB の型対応表は {', '.join(sorted(SCALARDB_SOURCES))} 向けに作り込まれています。"
                  f"{args.source} では型変換の精度が落ちることがあります。", file=sys.stderr)
        if args.mysql_case_insensitive:
            print("注意: --mysql-case-insensitive は ScalarDB 向けには効きません。無視します。", file=sys.stderr)
        # 実行計画への分解は --plan-dir を指定したときだけ行う
        results, _ = scalardb_convert(text, args.source, registry, keys, decompose=bool(args.plan_dir),
                                      storage=args.storage, expected_rows=expected_rows,
                                      isolation=args.isolation, h2_indexes=args.h2_indexes)
    else:
        for name in SCALARDB_ONLY:
            if getattr(args, name) not in (None, "jdbc", False):
                print(f"注意: --{name.replace('_', '-')} は --target scalardb のときだけ有効です。無視します。", file=sys.stderr)
        results = generic.convert_script(text, args.source, args.target,
                                         schema=_generic_schema(registry) if args.schema else None,
                                         case_insensitive=args.mysql_case_insensitive)

    for r in results:
        head = r.source_sql.splitlines()[0][:66] if r.source_sql else ""
        print(f"[{r.index:>3}] {r.status:<5} {r.kind:<10} {head}")
        for i in r.issues:
            if i.severity != "INFO":
                print(f"        {i.severity:<5} {i.code}: {i.message}")

    s = report.summarize(results)
    planned = f" / PLANNED {s['planned']}" if s["planned"] else ""
    print(f"\n{args.source} → {args.target}: {s['total']} 文 / OK {s['ok']} / WARN {s['warn']}{planned} / ERROR {s['error']}")
    print(f"変換率 {s['rate']}% ({s['converted']}/{s['total']})")

    if args.out_dir:
        for f in report.write_outputs(results, Path(args.out_dir), path.stem, args.source, args.target, str(path)):
            print(f"  出力: {f}")
    if args.target == "scalardb" and args.plan_dir:
        for f in report.write_plans(results, Path(args.plan_dir), path.stem):
            print(f"  計画: {f}")

    print(f"TOTAL={s['total']}")
    print(f"CONVERTED={s['converted']}")
    print(f"RATE={s['rate']}")
    if not s["total"]:
        # 空のファイルやコメントだけのファイルは「ERROR 0 件」で 0 を返していた。変換するものが無かったのは
        # 成功ではなく、たいていは渡すファイルの間違いである
        print(f"変換する文がありません: {path}", file=sys.stderr)
        return 2
    return 1 if s["error"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
