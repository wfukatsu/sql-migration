"""P4-10: prepare what a model would need to explain a REDESIGN, and keep its answer in its place.

A routine the rules send to a person arrives with a rule id and one sentence. A model can say more: what the
construct did, which of the recorded patterns applies, what a migration of it would look like. That is useful,
and it is also the easiest place in this whole pipeline to do harm, so three things are structural rather than
advisory.

**Nothing leaves the machine unless somebody says so.** Building a request writes a file; sending it is a
separate step with a named provider. The source of a customer's PL/SQL going to a third party is a decision for
the customer, not a default in a tool. `python -m plsql.remediate --prepare` writes the requests and stops, and
prints exactly what they contain.

**Advice is never evidence.** The verdict engine does not read this module's output, and cannot: advice lives
in its own file and carries its own verdict, which is always REVIEW. A model that produced perfect code would
still not move a routine to AUTO, because nothing here has been compared against Oracle. That is what AUTO
means (docs/design/plsql-kpi.md §3), and a model's confidence is not a substitute for a measurement.

**Every answer says where it came from.** Model, timestamp, and a digest of the exact request. Advice whose
request has since changed is stale, and the tool says so rather than showing it as current.

    python -m plsql.remediate --prepare --out out/plsql/advice
    # look at what it wrote, then, if the customer has agreed to it:
    python -m plsql.remediate --send --provider <name> --out out/plsql/advice
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .ir import model as M

# Advice is always this, and there is no code path that sets it to anything else.
ADVICE_VERDICT = "REVIEW"

# Which recorded pattern document covers which rule. A model that is not pointed at these will invent its own
# advice, and then the project has two answers to the same question.
PATTERNS = {
    "CUR-001": "docs/plsql-migration/plsql-cursor-patterns.md",
    "CUR-002": "docs/plsql-migration/plsql-cursor-patterns.md",
    "LOCK-001": "docs/plsql-migration/plsql-transaction-patterns.md",
    "LOCK-002": "docs/plsql-migration/plsql-transaction-patterns.md",
    "TX-001": "docs/plsql-migration/plsql-transaction-patterns.md",
    "TX-002": "docs/plsql-migration/plsql-transaction-patterns.md",
    "TX-003": "docs/plsql-migration/plsql-transaction-patterns.md",
    "TX-004": "docs/plsql-migration/plsql-transaction-patterns.md",
    "TRG-001": "docs/plsql-migration/plsql-trigger-patterns.md",
    "DYN-002": "docs/design/plsql-conversion-implementation-plan.md",
    "DYN-003": "docs/plsql-migration/plsql-trigger-patterns.md",
}


@dataclass
class Request:
    """Everything a model is given about one routine, and nothing else.

    Listed explicitly rather than assembled ad hoc, because this is the payload that would leave the machine.
    A reviewer deciding whether that is acceptable has to be able to read what is in it.
    """

    routine: str
    verdict: str
    source_file: str
    source_lines: tuple[int, int]
    source_text: str
    rules: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True,
                                         ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict:
        return {"routine": self.routine, "verdict": self.verdict, "sourceFile": self.source_file,
                "sourceLines": list(self.source_lines), "sourceText": self.source_text,
                "rules": self.rules, "diagnostics": self.diagnostics, "patterns": self.patterns}

    def prompt(self) -> str:
        """What the model is asked. Kept with the request so the two cannot drift apart."""
        rules = "\n".join(f"- {r['id']} ({r['decision']}): {r['message']}" for r in self.rules) or "- (なし)"
        remediation = "\n".join(f"- {step}" for r in self.rules for step in r.get("remediation", [])) \
            or "- (ルールに代替案が書かれていない)"
        patterns = "\n".join(f"- {p}" for p in self.patterns) or "- (該当する設計テンプレートなし)"
        return (
            "次の PL/SQL routine を ScalarDB へ移行する。判定は "
            f"{self.verdict} で、理由は下のルールである。\n\n"
            f"## routine: {self.routine} ({self.source_file}:{self.source_lines[0]})\n\n"
            f"```sql\n{self.source_text}\n```\n\n"
            f"## 当たったルール\n{rules}\n\n"
            f"## ルールが挙げる代替案\n{remediation}\n\n"
            f"## 参照すべき設計テンプレート\n{patterns}\n\n"
            "## 求めるもの\n"
            "1. この routine が何をしていたか（移行先に無い機能に依存している部分を名指しで）\n"
            "2. 上のテンプレートのどの型に当たるか。当たらないなら、なぜ当たらないか\n"
            "3. 移行後の形の案。**決めるべきことが残るなら、決めずに問いとして残すこと**\n\n"
            "推測を事実として書かないこと。テンプレートに書かれていないことを新しく提案する場合は、"
            "そう明示すること。")


@dataclass
class Advice:
    """One model's answer, with where it came from. Always REVIEW; nothing here can make a routine AUTO."""

    routine: str
    request_digest: str
    model: str
    produced_at: str
    text: str
    verdict: str = ADVICE_VERDICT

    def as_dict(self) -> dict:
        return {"routine": self.routine, "requestDigest": self.request_digest, "model": self.model,
                "producedAt": self.produced_at, "verdict": ADVICE_VERDICT, "text": self.text}


def build_requests(program: M.Program, decisions: dict, sources: Path) -> list[Request]:
    """One request per routine a person has to look at. AUTO routines are not sent anywhere."""
    routines = {r.id: (m, r) for m in program.modules for r in m.routines}
    out: list[Request] = []
    for routine_id in sorted(decisions):
        decision = decisions[routine_id]
        if decision.verdict == "AUTO":
            continue
        module, routine = routines.get(routine_id, (None, None))
        if routine is None or routine.source_range is None:
            continue
        where = routine.source_range
        rules = [{"id": m.rule.id, "decision": m.rule.decision, "message": m.rule.message,
                  "remediation": list(m.rule.remediation), "source": m.rule.source}
                 for m in decision.matches]
        out.append(Request(
            routine=routine_id, verdict=decision.verdict, source_file=where.file,
            source_lines=(where.start_line, where.end_line),
            source_text=_slice(sources, where.file, where.start_line, where.end_line),
            rules=rules,
            diagnostics=[{"severity": d.severity, "code": d.code, "message": d.message}
                         for d in routine.diagnostics],
            patterns=sorted({PATTERNS[r["id"]] for r in rules if r["id"] in PATTERNS})))
    return out


def _slice(sources: Path, name: str, start: int, end: int) -> str:
    for path in sources.rglob(name):
        lines = path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[max(start - 1, 0):end])
    return ""


def write_requests(requests: list[Request], out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    payload = []
    for request in requests:
        payload.append({**request.as_dict(), "digest": request.digest(), "prompt": request.prompt()})
    path = out_dir / "requests.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    written["requests"] = path

    # What would leave the machine, in a form somebody can read before deciding whether it may
    manifest = out_dir / "what-would-be-sent.md"
    manifest.write_text(_manifest(requests), encoding="utf-8")
    written["manifest"] = manifest
    return written


def _manifest(requests: list[Request]) -> str:
    files = sorted({r.source_file for r in requests})
    lines = [
        "# 送られる内容", "",
        "`--send` を実行した場合に外部へ渡るものの一覧である。**`--prepare` は何も送っていない。**", "",
        f"- routine: {len(requests)} 件", f"- 元ファイル: {len(files)} 件", "",
        "## 各リクエストに含まれるもの", "",
        "- routine の **PL/SQL ソースそのもの**（判定対象の行範囲）",
        "- 当たったルールの id・判定・メッセージ・代替案",
        "- routine に付いた診断",
        "- 参照すべき設計テンプレートのファイル名（**中身は送らない**）", "",
        "## 含まれないもの", "",
        "- データ（fixture の行、capture、golden）",
        "- 接続情報・スキーマ JSON",
        "- 他の routine のソース", "",
        "## 元ファイル", "",
    ]
    lines += [f"- `{f}`" for f in files]
    lines += ["", "顧客のコードを第三者へ渡してよいかは**顧客の判断**であり、ツールの既定ではない。"]
    return "\n".join(lines) + "\n"


def record(advice: list[Advice], out_dir: Path) -> Path:
    path = Path(out_dir) / "advice.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"note": "助言であって証拠ではない。判定エンジンはこのファイルを読まない。"
                 "ここに書かれたコードは必ず REVIEW であり、AUTO には上がらない。",
         "advice": [a.as_dict() for a in advice]},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def stale(advice: list[Advice], requests: list[Request]) -> list[str]:
    """Advice whose request has changed since. Showing it as current would be showing a stale answer."""
    current = {r.routine: r.digest() for r in requests}
    return sorted(a.routine for a in advice if current.get(a.routine) != a.request_digest)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------------------------------

class Provider:
    """Somewhere a request can be sent. Named, so the log says who saw the code."""

    name = "none"

    def complete(self, request: Request) -> str:  # pragma: no cover - the base is never used
        raise NotImplementedError


class Unconfigured(Provider):
    """The default. Refuses rather than picking a service on the user's behalf."""

    name = "unconfigured"

    def complete(self, request: Request) -> str:
        raise RuntimeError(
            "no provider is configured. Sending a customer's PL/SQL to a third party is the customer's "
            "decision, so this tool has no default. Read out/plsql/advice/what-would-be-sent.md, then pass "
            "--provider with one that has been agreed.")


PROVIDERS: dict[str, type[Provider]] = {"unconfigured": Unconfigured}


def main(argv=None) -> int:
    import argparse

    from . import review
    from .analysis import analyse as analyse_program
    from .report import analyse
    from .rules.engine import RuleSet, decide

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default="fixtures/plsql/src")
    ap.add_argument("--schema", default="fixtures/plsql/src/schema.sql")
    ap.add_argument("--scalardb-schema", default="fixtures/plsql/scalardb-schema.json")
    ap.add_argument("--evidence", help="P3-2's comparison report, so AUTO routines are left out")
    ap.add_argument("--out", default="out/plsql/advice")
    ap.add_argument("--prepare", action="store_true", help="write the requests and stop. Sends nothing.")
    ap.add_argument("--send", action="store_true", help="send the prepared requests to --provider")
    ap.add_argument("--provider", default="unconfigured")
    args = ap.parse_args(argv)

    if not args.prepare and not args.send:
        args.prepare = True  # the safe one is the default

    program_analysis = analyse(args.src, args.schema, scalardb_schema=args.scalardb_schema)
    known = review.routine_ids(program_analysis.program)
    evidence = review.credit_private_callees(
        review.evidence_from_diff(args.evidence, None, known), program_analysis.program,
        analyse_program(program_analysis.program).call_graph)
    decisions = decide(program_analysis.program, analyse_program(program_analysis.program),
                       RuleSet.load(), evidence)

    requests = build_requests(program_analysis.program, decisions, Path(args.src))
    out_dir = Path(args.out)
    written = write_requests(requests, out_dir)
    print(f"{len(requests)} request(s) prepared")
    for name, path in written.items():
        print(f"  {name:10} {path}")

    if not args.send:
        print("\nnothing was sent. Read what-would-be-sent.md, then re-run with --send --provider <name>.")
        return 0

    provider = PROVIDERS.get(args.provider, Unconfigured)()
    produced = []
    for request in requests:
        produced.append(Advice(routine=request.routine, request_digest=request.digest(),
                               model=provider.name, produced_at=now(),
                               text=provider.complete(request)))
    path = record(produced, out_dir)
    print(f"{len(produced)} advice written to {path} (all REVIEW; none of it is evidence)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
