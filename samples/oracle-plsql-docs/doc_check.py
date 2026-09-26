"""Oracle で流した結果（work/oracle）を文書の「結果:」と突き合わせる。

文書の結果は SQL*Plus の画面の写しなので、比べられるのは (1) DBMS_OUTPUT の行と (2) エラーの番号（ORA- / PLS-）である。
問合せの表示は値のトークン（列見出しと罫線を除いた語）の並びで比べる。SQL*Plus の決まり文句（"PL/SQL procedure
successfully completed." など）とエラー表示のエコー行は落とす。判定は近似であり、差のある例は差を並べて目で確かめる。

    python3 samples/oracle-plsql-docs/doc_check.py   # -> work/doc-check.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from examples import WORK, load  # noqa: E402

CHATTER = re.compile(
    r"^(PL/SQL procedure successfully completed\.?|"
    r"(Procedure|Function|Package|Package body|Package Body|Trigger|Type|Type body|Table|View|Sequence|Index|Library|"
    r"Synonym|Materialized view|Java source)\s+(created|altered|dropped|compiled)\.?|"
    r"\d+ rows? (created|updated|deleted|selected|merged)\.|no rows selected|"
    r"Commit complete\.|Rollback complete\.|Session altered\.|System altered\.|Call completed\.|Savepoint created\.|"
    r"Warning: .* (created|altered|compiled) with compilation errors\.|Errors for .*:|No errors\.|"
    r"Grant succeeded\.|User created\.|Connected\.|Elapsed: .*|Statement processed\.|Table truncated\.|"
    r"LINE/COL\s+ERROR|-+\s+-+.*|Help: .*|\.\.\.)$", re.I)
ERROR_LINE = re.compile(r"^(ERROR at line \d+:|PL/SQL: (SQL )?Statement ignored|(ORA|PLS|SP2)-\d{4,5}.*|\s*\*\s*$|\d+/\d+\s+(PLS|PL/SQL|SQL).*)$")
CODE = re.compile(r"\b(ORA|PLS)-(\d{4,5})\b")


def doc_codes(texts: list[str]) -> set[str]:
    return {f"{a}-{int(b):05d}" for t in texts for a, b in CODE.findall(t)}


def doc_lines(texts: list[str]) -> list[str]:
    lines = []
    for t in texts:
        raw = t.replace("\r", "").split("\n")
        skip = set()
        for i, line in enumerate(raw):
            if re.match(r"^\s*\*\s*$", line) and i > 0:
                skip.update({i - 1, i})           # エラー位置のエコー行と '*'
            if re.match(r"^ERROR at line", line) and i > 0 and raw[i - 1].strip() and i - 1 not in skip:
                skip.add(i - 1)
        in_errors = False
        for i, line in enumerate(raw):
            s = line.rstrip()
            if i in skip or not s.strip():
                continue
            if ERROR_LINE.match(s) or CHATTER.match(s.strip()):
                in_errors = bool(ERROR_LINE.match(s)) and not s.strip() == "*"
                continue
            if in_errors and (s.startswith(" ") or s.startswith("\t")):
                continue                            # エラーの続きの行
            in_errors = False
            lines.append(s.strip())
    return lines


# 例の冒頭の DROP（前の実行の後始末）が「無い」と言うのは文書の画面には出ていない。照合では数えない
DROP_MISSING = {942, 4043, 2289, 1434, 4080, 1418, 38307}


def oracle_codes(result: dict) -> set[str]:
    codes = set()
    for s in result.get("statements", []):
        if s.get("error") and re.match(r"^\s*DROP\b", s["text"], re.I) and s["error"]["code"] in DROP_MISSING:
            continue
        if s.get("error"):
            codes.add(f"ORA-{s['error']['code']:05d}")
            codes |= {f"{a}-{int(b):05d}" for a, b in CODE.findall(s["error"]["message"])}
        for e in s.get("compile_errors") or []:
            codes |= {f"{a}-{int(b):05d}" for a, b in CODE.findall(e)}
    return codes


def oracle_lines(result: dict, with_rows: bool) -> list[str]:
    lines = []
    for s in result.get("statements", []):
        lines += [l.strip() for l in s.get("output", []) if l.strip()]
        if with_rows and s.get("rows") is not None:
            for row in s["rows"]:
                lines += [str(v).strip() for v in row if v is not None and str(v).strip()]
    return lines


def tokens(lines: list[str]) -> list[str]:
    return [t for l in lines for t in l.split()]


def number_token(t: str) -> str:
    # 文書は 12c の SQL*Plus で書式化されている（24000 と 24000.00、日付 17-JUN-03）。数だけは値で比べる
    try:
        return format(float(t.replace(",", "")), "g")
    except ValueError:
        return t.upper()


def check(ex, result: dict) -> dict:
    docs = ex.results
    if not docs:
        return {"verdict": "NO_DOC_RESULT"}
    if result.get("harness_error"):
        return {"verdict": "HARNESS_ERROR", "detail": result["harness_error"]}
    skipped = [s for s in result["statements"] if s.get("skipped") == "SQL*Plus のバインド変数"]
    d_codes, o_codes = doc_codes(docs), oracle_codes(result)
    has_query = any(s.get("rows") is not None for s in result["statements"])
    d_lines = doc_lines(docs)
    o_lines = oracle_lines(result, with_rows=False)
    elided = any(l.strip() == "..." for t in docs for l in t.split("\n"))
    same_output = d_lines == o_lines
    if not same_output and elided:
        # 文書が途中を「...」で省いている: 文書の行が Oracle の出力に順に現れるか
        it = iter(o_lines)
        same_output = all(l in it for l in d_lines)
    if not same_output and has_query:
        # 問合せの表示: 列見出しと罫線を含むので、Oracle の値のトークンが文書のトークン列に順に含まれるかで見る
        d_tok = [number_token(t) for t in tokens(d_lines)]
        o_tok = [number_token(t) for t in tokens(oracle_lines(result, with_rows=True))]
        it = iter(d_tok)
        same_output = bool(o_tok) and all(t in it for t in o_tok)
    codes_ok = d_codes == o_codes or (d_codes and d_codes <= o_codes and {c for c in o_codes - d_codes} <= {"ORA-06512", "ORA-06550", "PLS-00000"})
    detail = {}
    if not same_output:
        detail["doc"] = d_lines[:30]
        detail["oracle"] = oracle_lines(result, with_rows=has_query)[:30]
    if not codes_ok:
        detail["doc_codes"] = sorted(d_codes)
        detail["oracle_codes"] = sorted(o_codes)
    if skipped:
        verdict = "BIND_VARIABLES"
    elif same_output and codes_ok:
        verdict = "MATCH"
    elif codes_ok:
        verdict = "OUTPUT_DIFFERS"
    elif same_output:
        verdict = "ERRORS_DIFFER"
    else:
        verdict = "BOTH_DIFFER"
    return {"verdict": verdict, "doc_codes": sorted(d_codes), "oracle_codes": sorted(o_codes), **detail}


def main() -> int:
    out = {}
    for ex in load():
        path = WORK / "oracle" / f"{ex.key}.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        out[ex.number] = {"title": ex.title, **check(ex, result)}
    (WORK / "doc-check.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(Counter(v["verdict"] for v in out.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
