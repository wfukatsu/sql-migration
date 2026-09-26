"""Oracle Database PL/SQL 言語リファレンス 12c R1（日本語）の「例一覧」から、例ごとのコードと「結果:」を抜き出す。

    python3 samples/oracle-plsql-docs/extract.py [--fetch]

--fetch で https://docs.oracle.com/cd/E57425_01/121/LNPLS/ の loe.htm と各章を work/html/ に取る。
出力は work/examples.json（例ごとに番号・題・章・部品の列）。文書の原文は Oracle の著作物なので git に入れない。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from pathlib import Path

BASE = "https://docs.oracle.com/cd/E57425_01/121/LNPLS/"
HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
HTML = WORK / "html"


def fetch() -> None:
    HTML.mkdir(parents=True, exist_ok=True)
    loe = urllib.request.urlopen(BASE + "loe.htm").read()
    (HTML / "loe.htm").write_bytes(loe)
    for page in sorted(set(re.findall(r'<a href="([a-z_]+\.htm)#', loe.decode("utf-8")))):
        (HTML / page).write_bytes(urllib.request.urlopen(BASE + page).read())


HR = "https://raw.githubusercontent.com/oracle-samples/db-sample-schemas/v12.1.0.2/human_resources/"


def fetch_hr() -> None:
    """文書（12c R1）が前提にしている HR スキーマの版。新しい版は日付と電話番号が違う。"""
    (WORK / "hr").mkdir(parents=True, exist_ok=True)
    for name in ("hr_cre.sql", "hr_popul.sql", "hr_idx.sql", "hr_code.sql"):
        (WORK / "hr" / name).write_bytes(urllib.request.urlopen(HR + name).read())


def text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def examples() -> list[dict]:
    loe = (HTML / "loe.htm").read_text(encoding="utf-8")
    listed = re.findall(r'<a href="([a-z_]+\.htm)#([^"]+)"[^>]*>(.*?)</a>', loe, re.S)
    pages: dict[str, str] = {}
    out = []
    for page, anchor, label in listed:
        pages.setdefault(page, (HTML / page).read_text(encoding="utf-8"))
        body = pages[page]
        start = body.find(f'id="{anchor}"')
        if start < 0:
            raise SystemExit(f"{page}#{anchor}: not found")
        end = body.find('<!-- class="example" -->', start)
        block = body[start:end]
        number, _, title = text(label).strip().partition(" ")
        parts, caption = [], None
        # 例の中は <p>（説明・「結果:」）と <pre>（コードまたは出力）が交互に並ぶ
        for kind, inner in re.findall(r"<(p|pre)\b[^>]*>(.*?)</\1>", block, re.S):
            if kind == "p":
                caption = text(inner).strip()
                continue
            parts.append({"caption": caption, "text": text(inner)})
            caption = None
        for part in parts:
            c = part["caption"] or ""
            part["role"] = "result" if c.startswith("結果") or c.lower().startswith("result") else "code"
        out.append({"number": number, "title": title.strip(), "page": page,
                    "url": f"{BASE}{page}#{anchor}", "parts": parts})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true")
    args = ap.parse_args(argv)
    if args.fetch or not (HTML / "loe.htm").exists():
        fetch()
    if args.fetch or not (WORK / "hr" / "hr_cre.sql").exists():
        fetch_hr()
    found = examples()
    (WORK / "examples.json").write_text(json.dumps(found, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(found)} examples -> {WORK / 'examples.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
