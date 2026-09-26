"""work/summary.json から README の付録の表を作る。

    python3 samples/oracle-plsql-docs/make_tables.py > samples/oracle-plsql-docs/result/tables.md
"""
from __future__ import annotations

import json
import re
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"

CATEGORY = OrderedDict([
    ("RUN", "実行して比較"), ("UNITS_ONLY", "ユニットだけ（呼び出し無し）"), ("SOURCE_REJECTS", "Oracle が断る例"),
    ("NO_PLSQL", "PL/SQL が無い"), ("BIND_VARIABLES", "SQL*Plus のバインド変数"), ("UNSEEDABLE", "表の行を作れない"),
    ("OUT_OF_SCOPE", "対象外"), ("HARNESS_ERROR", "ハーネスの失敗")])
OUTCOME = OrderedDict([
    ("IDENTICAL", "一致"), ("VALUE", "値が違う"), ("NOT_RAISED", "例外が出ない"), ("EXCEPTION", "別の例外"),
    ("ORDER", "並びだけ違う"), ("DIRECT_DML", "直接の DML を断る"), ("UNSUPPORTED", "未対応で止まる"),
    ("JAVA_COMPILE", "javac エラー"), ("NOT_RUN", "流せない"), ("HARNESS", "ハーネスの失敗")])
DOC = {"MATCH": "一致", "NO_DOC_RESULT": "結果なし", "OUTPUT_DIFFERS": "出力が違う", "ERRORS_DIFFER": "エラーが違う",
       "BOTH_DIFFER": "両方違う", "BIND_VARIABLES": "―", "HARNESS_ERROR": "―"}


def outcome(v: dict) -> str | None:
    if v["category"] != "RUN":
        return None
    return v.get("kind") if v.get("outcome") == "DIFFERS" else v.get("outcome")


def reason(v: dict) -> str:
    if v["category"] in ("UNITS_ONLY", "SOURCE_REJECTS"):
        c = {"OK": "javac 可", "JAVAC_ERRORS": "javac エラー", "NOT_RUN": "生成で落ちる"}.get(v.get("compile"), "")
        detail = (v.get("javac") or [""])[0] if v.get("compile") == "JAVAC_ERRORS" else ""
        text = c + (f": {detail}" if detail else "")
        if v["category"] == "SOURCE_REJECTS":
            text = f"Oracle: {v['reason'][:70]} / 変換: {text}"
        return text
    if v["category"] != "RUN":
        return v.get("reason", "")[:90]
    if v.get("outcome") == "JAVA_COMPILE":
        return (v.get("javac") or [""])[0][:110]
    for sc in (v.get("scenarios") or {}).values():
        if sc["status"] == "DIFFERS":
            return sc["differences"][0][:130]
        if sc["status"] == "NOT_RUN":
            return str(sc.get("reason"))[:130]
    return ""


def chapter_order(chapter: str) -> int:
    head = chapter.split()[0]
    return int(head) if head.isdigit() else 100 + ord(head[0])


def cell(text: str) -> str:
    # 2 つ以上続く空白は ␣ で見せる（3-1 の CHAR の空白埋めのように、空白の数が差そのものの例がある）
    text = re.sub(r"[\r\n\t]+", " ", str(text))
    return re.sub(r" {2,}", lambda m: "␣" * len(m.group()), text).replace("|", "\\|")


def main() -> int:
    s = json.loads((WORK / "summary.json").read_text(encoding="utf-8"))
    rows = list(s.values())
    print("<!-- make_tables.py の出力。手で直さない -->\n")
    print("## 表 1. 章ごとの分類\n")
    cats = list(CATEGORY)
    print("| 章 | 例 | " + " | ".join(CATEGORY[c] for c in cats) + " |")
    print("|---|---:|" + "---:|" * len(cats))
    by = OrderedDict()
    for v in rows:
        by.setdefault(v["chapter"], []).append(v)
    for ch in sorted(by, key=chapter_order):
        c = Counter(v["category"] for v in by[ch])
        print(f"| {ch} | {len(by[ch])} | " + " | ".join(str(c.get(k, 0) or "") for k in cats) + " |")
    c = Counter(v["category"] for v in rows)
    print(f"| **計** | **{len(rows)}** | " + " | ".join(f"**{c.get(k, 0)}**" for k in cats) + " |")

    print("\n## 表 2. 実行した例の結果（章ごと）\n")
    outs = list(OUTCOME)
    print("| 章 | 実行 | " + " | ".join(OUTCOME[o] for o in outs) + " |")
    print("|---|---:|" + "---:|" * len(outs))
    for ch in sorted(by, key=chapter_order):
        run = [v for v in by[ch] if v["category"] == "RUN"]
        if not run:
            continue
        c = Counter(outcome(v) for v in run)
        print(f"| {ch} | {len(run)} | " + " | ".join(str(c.get(k, 0) or "") for k in outs) + " |")
    run = [v for v in rows if v["category"] == "RUN"]
    c = Counter(outcome(v) for v in run)
    print(f"| **計** | **{len(run)}** | " + " | ".join(f"**{c.get(k, 0)}**" for k in outs) + " |")

    print("\n## 表 3. 例ごとの結果\n")
    print("文書: Oracle 26ai で流した結果と文書の「結果:」の照合。判定: 証拠を渡した後の判定（ルールだけの判定）。"
          "理由の欄の ␣ は続く空白 1 つ分。\n")
    print("| 例 | 題 | 文書 | 分類 | 結果 | 判定 | 理由・最初の差 |")
    print("|---|---|---|---|---|---|---|")
    for v in rows:
        o = outcome(v)
        verdict = f"{v.get('verdict') or ''}" + (f"（{v['ruleVerdict']}）" if v.get("ruleVerdict") and v.get("ruleVerdict") != v.get("verdict") else "")
        print(f"| [{v['number']}]({v['url']}) | {cell(v['title'])} | {DOC.get(v['doc'], v['doc'])} | "
              f"{CATEGORY.get(v['category'], v['category'])} | {OUTCOME.get(o, '') if o else ''} | {verdict} | "
              f"{cell(reason(v))} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
