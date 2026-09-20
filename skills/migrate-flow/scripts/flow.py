#!/usr/bin/env python3
"""移行の一連の流れ（現行仕様 → 変換と判断 → 変換後の仕様 → 承認 → テスト）の、いまどこにいるかを持つ。

    python skills/migrate-flow/scripts/flow.py init --out out/migrate/shop --kind plsql \\
        --src fixtures/plsql-external/create_order/src --scalardb-schema … --limits … [--record …] [--evidence …]
    python ... status  --out out/migrate/shop                  # 段階ごとの状態と、次にすること
    python ... approve --out out/migrate/shop spec --by 業務担当 --date 2026-09-20
    python ... gate    --out out/migrate/shop                  # 0 = テストしてよい / 1 = まだ
    python ... tested  --out out/migrate/shop --result pass --report difftest/work/plsql-diff.json

承認は 3 つある: `spec`（現行の仕様）、`decisions`（人の判断）、`converted`（変換後の仕様）。承認には
**承認した人と日付**が要り、その段階の検査（未記入が無い、図が入っている、事実の欄が古くない、など）が
通っていなければ受け付けない。承認したときのファイルの指紋を控えるので、**承認のあとで中身が変わると、
承認は「古い」になり、`gate` は通らない**。承認されていないものをテストしない、が、この流れの約束である。

終了コード: 0 = よい / 1 = まだ（`gate`、検査の通らない `approve`）/ 2 = 実行エラー。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

SKILLS = Path(__file__).resolve().parents[2]
STAGES = ("spec", "decisions", "converted")
TITLES = {"spec": "現行の仕様", "decisions": "人の判断", "converted": "変換後の仕様", "test": "テスト"}
UNWRITTEN = "（未記入"
HEADER = """\
# 移行の流れの状態。skills/migrate-flow/scripts/flow.py が読み書きする。
# 承認には 承認した人 と 日付 が要る。指紋は承認したときのファイルの中身で、あとで変わると承認は古くなる。
"""


class FlowError(Exception):
    """入力の誤り（終了コード 2）。"""


def _script(skill: str, name: str):
    path = SKILLS / skill / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module   # dataclasses が module を引く
    spec.loader.exec_module(module)
    return module


def _state_path(out: Path) -> Path:
    return out / "flow.yaml"


def load(out: Path) -> dict:
    path = _state_path(out)
    if not path.exists():
        raise FlowError(f"{path} が無い。先に init を回す")
    state = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(state.get("inputs"), dict):
        raise FlowError(f"{path} に inputs が無い")
    return state


def save(out: Path, state: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    _state_path(out).write_text(HEADER + yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _files(state: dict, out: Path, stage: str) -> list[Path]:
    """その段階で承認されるもの。指紋はこのファイルの中身から取る。"""
    inputs = state["inputs"]
    if stage == "spec":
        # 原文も入れる。仕様書は原文の写しなので、原文が変われば、承認した仕様はもう現行の仕様ではない
        return sorted((out / "spec").glob("*.md")) + _sources(inputs)
    if stage == "converted":
        return sorted((out / "docs").glob("*.md"))
    chosen = [inputs.get("limits"), inputs.get("record")]
    reports = [out / "generated" / "generation-report.json"] if inputs["kind"] == "plsql" else sorted((out / "converted").glob("*.report.json"))
    return [Path(f) for f in chosen if f and Path(f).exists()] + [r for r in reports if r.exists()]


def _sources(inputs: dict) -> list[Path]:
    src = Path(inputs.get("src") or "")
    if src.is_file():
        return [src]
    return sorted(f for f in src.rglob("*") if f.is_file() and not any(part.startswith(".") for part in f.relative_to(src).parts))


def _record(inputs: dict) -> dict[str, dict]:
    """記録（項目 ID → 内容）。まだ無ければ空。形が違えば入力の誤り（2）にする。"""
    path = Path(inputs["record"]) if inputs.get("record") else None
    if path is None or not path.exists():
        return {}
    try:
        items = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("items") or {}
    except (yaml.YAMLError, AttributeError) as e:
        raise FlowError(f"{path}: 記録が読めない: {str(e).splitlines()[0]}") from None
    if not isinstance(items, dict) or not all(isinstance(entry, dict) for entry in items.values()):
        raise FlowError(f"{path}: `items:` の下に 項目 ID → 内容（名前: 値）を並べた形であるべき")
    return items


def fingerprint(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _mermaid_problems(files: list[Path], what: str) -> list[str]:
    problems = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if text.count(UNWRITTEN):
            problems.append(f"{path.name}: 未記入が {text.count(UNWRITTEN)} か所")
        if text.count("```mermaid") != len(re.findall(r"```mermaid\n.*?\n```", text, re.S)):
            problems.append(f"{path.name}: 閉じていない mermaid の塊がある")
    if files and not any("```mermaid" in p.read_text(encoding="utf-8") for p in files):
        problems.append(f"{what}に Mermaid の図が 1 つも無い")
    return problems


def problems_of(state: dict, out: Path, stage: str) -> list[str]:
    """その段階を承認に出せない理由。空なら出せる。"""
    inputs, files = state["inputs"], _files(state, out, stage)
    if stage == "spec":
        pages = [f for f in files if f.parent == out / "spec"]
        if not pages:
            return ["現行の仕様（spec/*.md）がまだ無い"]
        found = _mermaid_problems(pages, "現行の仕様")
        if inputs["kind"] == "plsql":
            facts = _script("plsql-spec", "spec_facts")
            modules, routines, inventory = facts.load(out / "spec-analysis")
            found = facts.check(modules, routines, inventory, out / "spec")[0] + [f for f in found if "未記入" not in f]
        return found
    if stage == "decisions":
        if not files or not any(f.name.endswith("report.json") for f in files):
            return ["変換がまだ済んでいない（変換の報告が無い）"]
        found, record = [], _record(inputs)
        if inputs["kind"] == "plsql":
            report = json.loads((out / "generated" / "generation-report.json").read_text(encoding="utf-8"))
            summary = report.get("summary") or {}
            if summary.get("untranslatedStatements"):
                found.append(f"変換できなかった文が {summary['untranslatedStatements']} 残っている")
        else:
            # 記録のファイルを渡してあるだけでは足りない。変換できなかった文の 1 つずつに、どう扱うかの項目が要る
            for report in (f for f in files if f.name.endswith(".report.json")):
                failed = [s for s in json.loads(report.read_text(encoding="utf-8")).get("results", []) if s.get("status") == "ERROR"]
                missing = [f"SQL-{s.get('index')}" for s in failed if f"SQL-{s.get('index')}" not in record]
                if missing:
                    found.append(f"{report.name}: 変換できなかった文のうち {len(missing)} に、どう扱うか（書き直す・アプリへ移す・"
                                 f"対象から外す）の項目が記録（--record）に無い: {'、'.join(missing)}")
        for item, entry in record.items():
            if entry.get("状態") == "決定" and not (entry.get("決めた人") and entry.get("日付")):
                found.append(f"記録の {item} は「決定」だが、決めた人か日付が無い")
        return found
    if not files:
        return ["変換後の仕様（docs/*.md）がまだ無い"]
    found = _mermaid_problems(files, "変換後の仕様")
    if inputs["kind"] == "plsql":
        doc = _script("plsql-migrate", "migration_doc")
        args = argparse.Namespace(src=inputs.get("src"), generated=str(out / "generated"), analysis=str(out / "generated" / "analysis"),
                                  limits=inputs.get("limits"), evidence=inputs.get("evidence"), record=inputs.get("record"))
        found = doc.check(doc.load(args), out / "docs")[0] + [f for f in found if "未記入" not in f]
    return found


def open_items(state: dict, out: Path) -> list[str]:
    """人の判断のうち、まだ決まっていないもの: 記録の未決の項目と、判定が REVIEW のままの routine
    （REVIEW は「人が決めると生成コードが変わる」という意味である）。残したまま進めるなら、その理由を承認に控える。"""
    found = []
    report = out / "generated" / "generation-report.json"
    if state["inputs"]["kind"] == "plsql" and report.exists():
        verdicts = json.loads(report.read_text(encoding="utf-8")).get("verdicts") or {}
        # 生成の報告は証拠を見ない（`plsql.generate` に `--evidence` は無い）ので、当たったルールの無い routine も
        # 「まだ誰も Oracle と突き合わせていない」だけで REVIEW と出る。テストのあと、証拠つきで作り直した解析
        # （`plsql.cli --evidence … --out-dir generated/analysis`）が AUTO と言うなら、それは人の判断の未決ではない
        credited = _analysed_verdicts(out)
        found += [f"REVIEW: {routine}" for routine, v in sorted(verdicts.items())
                  if v.get("verdict") == "REVIEW" and credited.get(routine) != "AUTO"]
        redesigns = sorted(routine for routine, v in verdicts.items() if v.get("verdict") == "REDESIGN")
        answered = _redesign_answers(state["inputs"], out) if redesigns else {}
        for routine in redesigns:
            if answered is None:
                found.append(f"REDESIGN: {routine}（決めたかどうかが分からない。generated/analysis を、生成と同じ --limits で作り直す）")
            elif answered.get(routine, ["?"]):
                found.append(f"REDESIGN: {routine}（limits.yaml に答えの無いルール: {'、'.join(answered.get(routine, ['?']))}）")
    found += sorted(k for k, v in _record(state["inputs"]).items() if v.get("状態") == "未決")
    return found


def _analysed_verdicts(out: Path) -> dict[str, str]:
    """routine → 判定。決定（と、渡されていれば証拠）を適用した解析から読む。無ければ空。"""
    decisions = out / "generated" / "analysis" / "decisions.json"
    if not decisions.exists():
        return {}
    try:
        routines = json.loads(decisions.read_text(encoding="utf-8")).get("routines") or []
    except ValueError:
        return {}
    return {r.get("routine"): r.get("verdict") for r in routines if isinstance(r, dict)}


def _redesign_answers(inputs: dict, out: Path) -> dict[str, list[str]] | None:
    """REDESIGN の routine → まだ `limits.yaml` に答えの無いルール。解析が無いか、`limits.yaml` より古ければ None。

    REDESIGN という判定は、決定のあとも REDESIGN のままである（ルールが当たった事実は変わらない）。決まったか
    どうかは、決定を適用した解析（`plsql.cli --limits … --out-dir generated/analysis`）の `redesign.open` にある。
    """
    decisions = out / "generated" / "analysis" / "decisions.json"
    limits = Path(inputs["limits"]) if inputs.get("limits") else None
    if not decisions.exists() or (limits and limits.exists() and limits.stat().st_mtime > decisions.stat().st_mtime):
        return None
    routines = json.loads(decisions.read_text(encoding="utf-8")).get("routines") or []
    return {r["routine"]: list((r.get("redesign") or {}).get("open") or []) for r in routines if r.get("redesign")}


def stage_state(state: dict, out: Path, stage: str) -> tuple[str, list[str]]:
    approval = (state.get("approvals") or {}).get(stage)
    problems = problems_of(state, out, stage)
    if approval:
        if approval.get("指紋") != fingerprint(_files(state, out, stage)):
            return "承認が古い", ["承認のあとで中身が変わった。見直して、承認を取り直す"] + problems
        return "承認済み", []
    return ("承認待ち", []) if not problems else ("作業中", problems)


def cmd_init(args) -> int:
    out = Path(args.out)
    existing = yaml.safe_load(_state_path(out).read_text(encoding="utf-8")) if _state_path(out).exists() else {}
    inputs = {"kind": args.kind, "src": str(Path(args.src).resolve())}
    for key in ("scalardb_schema", "limits", "record", "evidence", "source_dialect", "target_dialect"):
        if getattr(args, key):
            # どこから呼んでも同じファイルを指すようにする（相対のままだと、別の場所から呼んだときに指紋が変わる）
            inputs[key] = getattr(args, key) if key.endswith("_dialect") else str(Path(getattr(args, key)).resolve())
    if not Path(args.src).exists():
        raise FlowError(f"{args.src} が無い")
    save(out, {"inputs": inputs, "approvals": (existing or {}).get("approvals") or {}, "test": (existing or {}).get("test")})
    print(f"{_state_path(out)} を書いた。次: 現行の仕様を調べる（status で確かめられる）")
    return 0


def cmd_status(args) -> int:
    out = Path(args.out)
    state = load(out)
    print(f"# 移行の流れ — {state['inputs']['kind']} / {state['inputs']['src']}\n")
    print("| 段階 | 状態 | 承認 |\n|---|---|---|")
    next_step, details = None, []
    for stage in STAGES:
        name, problems = stage_state(state, out, stage)
        approval = (state.get("approvals") or {}).get(stage) or {}
        who = f"{approval.get('承認した人')}（{approval.get('日付')}）" if approval else "—"
        print(f"| {TITLES[stage]}（`{stage}`） | {name} | {who} |")
        details += [f"- `{stage}`: {p}" for p in problems]
        if next_step is None and name != "承認済み":
            next_step = (stage, name)
    test = state.get("test") or {}
    print(f"| テスト | {test.get('結果', 'まだ')} | {test.get('日付', '—')} |")
    if details:
        print("\n残っていること\n\n" + "\n".join(details))
    remaining = open_items(state, out)
    if remaining:
        print("\n未決の判断（承認する人に見せる）: " + "、".join(remaining))
    if next_step:
        stage, name = next_step
        action = {"作業中": "を仕上げる", "承認待ち": "を利用者に見せて、承認を求める", "承認が古い": "を見直して、承認を取り直す"}[name]
        print(f"\n次にすること: {TITLES[stage]}{action}")
    elif not test:
        print("\n次にすること: テストを実施する（gate が 0 を返す）")
    print(f"STAGE={next_step[0] if next_step else 'test'}", file=sys.stderr)
    return 0


def cmd_approve(args) -> int:
    out = Path(args.out)
    state = load(out)
    try:
        datetime.date.fromisoformat(args.date)
    except ValueError:
        raise FlowError(f"日付は YYYY-MM-DD で書く: {args.date}")
    for earlier in STAGES[:STAGES.index(args.stage)]:
        if stage_state(state, out, earlier)[0] != "承認済み":
            print(f"`{earlier}`（{TITLES[earlier]}）が承認されていない。順に承認する")
            return 1
    problems = problems_of(state, out, args.stage)
    if problems:
        print(f"`{args.stage}` は承認に出せない:")
        print("\n".join(f"- {p}" for p in problems))
        return 1
    remaining = open_items(state, out) if args.stage == "decisions" else []
    if remaining and not args.with_open:
        print("未決の判断が残っている: " + "、".join(remaining))
        print("残したまま進めると利用者が決めたなら、その理由を --with-open に書く（承認に控える）")
        return 1
    approval = {"承認した人": args.by, "日付": args.date, "指紋": fingerprint(_files(state, out, args.stage)),
                "対象": [str(f) for f in _files(state, out, args.stage)]}
    if remaining:
        approval["未決のまま進める"] = {"項目": remaining, "理由": args.with_open}
    if args.note:
        approval["メモ"] = args.note
    state.setdefault("approvals", {})[args.stage] = approval
    state["test"] = _test_after(state, args.stage, approval, args.date)
    save(out, state)
    print(f"`{args.stage}`（{TITLES[args.stage]}）を {args.by} が {args.date} に承認した")
    return 0


def _test_after(state: dict, stage: str, approval: dict, date: str) -> dict | None:
    """承認を取り直したあとに、前のテストの結果が残るか。

    テストが確かめたのは、現行の仕様（`spec`）と、決定を適用して生成したコード（`decisions`）である。どちらかの
    承認が変われば、前のテストは何を確かめたのか分からなくなるので消す。`converted` は文書で、テストの結果を
    文書に入れる（`--evidence`）と必ず変わる——そこで消すと、結果を文書に書くたびにテストが無かったことになる。
    """
    test = state.get("test")
    if not test or stage != "converted":
        return None
    tested, approvals = test.get("承認の指紋") or {}, state.get("approvals") or {}
    if any(tested.get(s) != (approvals.get(s) or {}).get("指紋") for s in ("spec", "decisions")):
        return None
    test["承認の指紋"] = {**tested, "converted": approval["指紋"]}
    test["テストのあとで取り直した承認"] = [*test.get("テストのあとで取り直した承認", []), f"converted（{date}）"]
    return test


def cmd_gate(args) -> int:
    out = Path(args.out)
    state = load(out)
    blocked = [(stage, *stage_state(state, out, stage)) for stage in STAGES]
    blocked = [(stage, name, problems) for stage, name, problems in blocked if name != "承認済み"]
    for stage, name, problems in blocked:
        print(f"`{stage}`（{TITLES[stage]}）: {name}" + "".join(f"\n- {p}" for p in problems))
    print(f"GATE={'closed' if blocked else 'open'}", file=sys.stderr)
    return 1 if blocked else 0


def cmd_tested(args) -> int:
    out = Path(args.out)
    state = load(out)
    if cmd_gate(args) != 0:
        print("承認がそろっていないので、テストの結果は記録しない")
        return 1
    state["test"] = {"結果": args.result, "日付": args.date or datetime.date.today().isoformat(), "報告": args.report,
                     "承認の指紋": {stage: state["approvals"][stage]["指紋"] for stage in STAGES}}
    if args.note:
        state["test"]["メモ"] = args.note
    if state["inputs"]["kind"] == "plsql" and args.report and Path(args.report).exists():
        # 比較の結果を文書に入れる（migration_doc.py の --evidence）と、事実の欄が変わる。`converted` の検査が
        # 同じ比較を見ていなければ、文書は「古い事実」になり、承認を取り直せない
        state["inputs"]["evidence"] = args.report = str(Path(args.report).resolve())
    save(out, state)
    print(f"テストの結果（{args.result}）を記録した")
    if state["inputs"].get("evidence") == args.report and args.report:
        print(f"比較の結果（{args.report}）を入力に控えた。文書に入れるときは migration_doc.py に --evidence {args.report} を渡す")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="入力を控える（承認は消さない）")
    init.add_argument("--kind", required=True, choices=("plsql", "sql"))
    init.add_argument("--src", required=True, help="PL/SQL のディレクトリ、または SQL のファイル")
    init.add_argument("--scalardb-schema", dest="scalardb_schema")
    init.add_argument("--limits")
    init.add_argument("--record")
    init.add_argument("--evidence")
    init.add_argument("--source-dialect", dest="source_dialect")
    init.add_argument("--target-dialect", dest="target_dialect")
    init.set_defaults(func=cmd_init)
    sub.add_parser("status", help="段階ごとの状態と、次にすること").set_defaults(func=cmd_status)
    approve = sub.add_parser("approve", help="1 つの段階の承認を記録する")
    approve.add_argument("stage", choices=STAGES)
    approve.add_argument("--by", required=True, help="承認した人（役割）")
    approve.add_argument("--date", required=True, help="YYYY-MM-DD")
    approve.add_argument("--with-open", dest="with_open", help="未決の判断を残したまま進める理由（利用者が決めたとき）")
    approve.add_argument("--note")
    approve.set_defaults(func=cmd_approve)
    sub.add_parser("gate", help="テストしてよいか（0 = よい）").set_defaults(func=cmd_gate)
    tested = sub.add_parser("tested", help="テストの結果を記録する")
    tested.add_argument("--result", required=True, choices=("pass", "fail"))
    tested.add_argument("--report", help="結果の報告のファイル")
    tested.add_argument("--date")
    tested.add_argument("--note")
    tested.set_defaults(func=cmd_tested)
    for command in sub.choices.values():
        command.add_argument("--out", required=True, help="この移行の作業ディレクトリ")
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FlowError as e:
        print(f"入力が読めない: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"ファイルが無い: {e.filename}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeDecodeError) as e:
        print(f"入力が読めない: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
