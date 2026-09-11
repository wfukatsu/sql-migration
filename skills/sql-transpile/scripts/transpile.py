#!/usr/bin/env python3
"""SQL を、Source 方言から Target 方言 (SQLGlot の 32 方言) または ScalarDB SQL に変換する。

SQL を一度 AST に抽象化してから Target 向けに生成し直す。素の sqlglot.transpile() が
黙って通してしまう構文 (ROWNUM / CONNECT BY / NEXTVAL / ROWID など) を検出して報告する。

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
from _scalardb.converter import convert_script as scalardb_convert  # noqa: E402
from _scalardb.schema import SchemaRegistry  # noqa: E402

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
    ap.add_argument("--schema", help="[scalardb のみ] 既存テーブル定義 (Schema Loader JSON)")
    ap.add_argument("--keys", action="append",
                    help="[scalardb のみ] キー指定 table=p1,p2/c1,c2 (パーティション/クラスタリング)")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"ファイルが見つかりません: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")

    # SQLGlot の「未対応」警告は Issue として回収済みなので、標準エラーの重複表示を抑える
    logging.getLogger("sqlglot").setLevel(logging.ERROR)

    if args.target == "scalardb":
        if args.source not in SCALARDB_SOURCES:
            print(f"注意: ScalarDB の型対応表は {', '.join(sorted(SCALARDB_SOURCES))} 向けに作り込まれています。"
                  f"{args.source} では型変換の精度が落ちることがあります。", file=sys.stderr)
        registry = SchemaRegistry.from_schema_loader_json(args.schema) if args.schema else SchemaRegistry()
        # 出力範囲は変換 + 診断レポート。実行計画への分解は行わない
        results, _ = scalardb_convert(text, args.source, registry, _parse_keys(args.keys), decompose=False)
    else:
        if args.schema or args.keys:
            print("注意: --schema / --keys は --target scalardb のときだけ有効です。無視します。", file=sys.stderr)
        results = generic.convert_script(text, args.source, args.target)

    for r in results:
        head = r.source_sql.splitlines()[0][:66] if r.source_sql else ""
        print(f"[{r.index:>3}] {r.status:<5} {r.kind:<10} {head}")
        for i in r.issues:
            if i.severity != "INFO":
                print(f"        {i.severity:<5} {i.code}: {i.message}")

    s = report.summarize(results)
    print(f"\n{args.source} → {args.target}: {s['total']} 文 / OK {s['ok']} / WARN {s['warn']} / ERROR {s['error']}")
    print(f"変換率 {s['rate']}% ({s['converted']}/{s['total']})")

    if args.out_dir:
        for f in report.write_outputs(results, Path(args.out_dir), path.stem, args.source, args.target, str(path)):
            print(f"  出力: {f}")

    print(f"TOTAL={s['total']}")
    print(f"CONVERTED={s['converted']}")
    print(f"RATE={s['rate']}")
    return 1 if s["error"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
