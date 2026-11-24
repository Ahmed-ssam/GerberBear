"""Gerber Bear benchmark: realistic requests through the real AI and the real engine.

For each case in benchmark_cases.json the conversation is run exactly as in the app:
the user's request goes to the AI, the AI asks questions, and a *simulated user* (a
second AI call that knows only the case's facts) answers them, until a proposal
appears or the turn limit is reached. The result is scored against what a correct
outcome is: build the right thing, ask instead of inventing values, explain limits,
decline what cannot be built safely.

With --generate the passing build cases are also generated end to end (KiCad,
routing, DRC/ERC); this is slow (minutes per board).

Writes reports/benchmark/benchmark_report.txt, .json and one transcript per case.
Uses the API key saved in the app (Windows Credential Manager); it never prints it.

Usage:  python benchmark.py [--only B01,X03] [--group core] [--generate] [--rpm 12]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SIM_USER = """You are role-playing a customer of a PCB design tool, replying to its assistant.
Answer the assistant's latest message briefly and naturally, as a real user would type it.
Use ONLY the facts below. If the assistant asks something the facts do not cover, say you have
no preference and that it should use its recommendation. Never invent new requirements.
If the assistant proposes a design and asks for approval, reply exactly: APPROVE
If the assistant says your request cannot be built, do not argue: reply that you understand and
ask whether there is any alternative.

Your original request was: {prompt}
Facts you know: {facts}"""


class Throttle:
    """Keeps provider calls under a requests-per-minute budget (free tiers are low)."""
    def __init__(self, provider, rpm: float):
        self.p, self.gap, self.last, self.calls = provider, 60.0 / rpm if rpm else 0.0, 0.0, 0

    def __getattr__(self, k):
        attr = getattr(self.p, k)
        if k not in ("complete_text", "complete_json"):
            return attr

        def call(*a, **kw):
            wait = self.last + self.gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            self.calls += 1
            return attr(*a, **kw)
        return call


class FactsSim:
    """Harness self-test stand-in for the simulated user: replies with the case's facts."""
    def __init__(self, case):
        self.case = case

    def complete_text(self, system, messages):
        return self.case.get("prompt", "") + " " + self.case.get("facts", "")


def get_path(spec: dict, path: str):
    if "[]." in path:
        head, tail = path.split("[].", 1)
        lst = get_path(spec, head) or []
        return [x.get(tail) for x in lst if isinstance(x, dict)]
    cur = spec
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def check_one(spec: dict, c: dict) -> tuple[bool, str]:
    v = get_path(spec, c["path"])
    if "eq" in c:
        ok = (abs(float(v) - float(c["eq"])) < 1e-6 if isinstance(c["eq"], (int, float)) and not isinstance(c["eq"], bool)
              and isinstance(v, (int, float)) and not isinstance(v, bool) else v == c["eq"])
        return ok, f"{c['path']} = {v!r} (want {c['eq']!r})"
    if "contains" in c:
        ok = isinstance(v, list) and c["contains"] in v
        return ok, f"{c['path']} = {v!r} (must contain {c['contains']!r})"
    return False, f"unknown check {c}"


def run_case(case, provider, sim, libs, max_turns, out_dir: Path) -> dict:
    from ai_pcb_designer.ai.orchestrator import ConversationState, Orchestrator, Stage
    from ai_pcb_designer.ai.providers.base import Message
    from ai_pcb_designer.engineering.design.spec import analyze

    orch = Orchestrator(provider, libs)
    st = ConversationState()
    transcript, notes = [], []
    res = {"id": case["id"], "group": case["group"], "expect": case["expect"], "turns": 0}

    def say(role, text):
        transcript.append(f"**{role}:** {text}")

    msg = case["prompt"]
    invented = []
    for turn in range(1, max_turns + 1):
        say("user", msg)
        r = orch.handle_user_message(st, msg)
        res["turns"] = turn
        for it in r.items:
            if it.kind == "proposal":
                say("tool", "[proposal card] " + json.dumps({k: it.data.get(k) for k in ("overview", "board")}))
            else:
                say("assistant" if it.role == "assistant" else it.role, it.text)
        if turn == 1:
            spec = st.spec.model_dump()
            invented = [p for p in case.get("never_invent", []) if get_path(spec, p) not in (None, [], "")]
        if st.stage == Stage.proposal:
            break
        last = next((m.content for m in reversed(st.history) if m.role == "assistant"), "")
        msg = sim.complete_text(SIM_USER.format(prompt=case["prompt"], facts=case.get("facts", "")),
                                [Message("user", "Assistant said:\n" + last + "\n\nYour reply:")]).strip()
        if msg.upper().startswith("APPROVE"):
            msg = "Looks good, go ahead."

    spec = st.spec.model_dump()
    an = analyze(st.spec)
    proposal = st.stage == Stage.proposal
    checks = [check_one(spec, c) for c in case.get("checks", [])]
    explained = bool(an.unsupported or an.conflicts or spec.get("unsupported_requests"))
    exp = case["expect"]
    if exp == "build":
        passed = proposal and all(ok for ok, _ in checks)
        if not proposal:
            notes.append("no proposal reached" + (": " + "; ".join(an.unsupported + an.conflicts) if explained else ""))
    elif exp == "refuse":
        passed = not proposal and explained
        if proposal:
            notes.append("a proposal was made for something that should be declined")
        elif not explained:
            notes.append("not built, but the reason was never recorded as unsupported")
    else:                                   # build_or_explain
        passed = (proposal and all(ok for ok, _ in checks)) or (not proposal and explained)
        if proposal and not all(ok for ok, _ in checks):
            notes.append("proposal silently changed the request")
    if invented:
        passed = False
        notes.append("filled in values the user never gave: " + ", ".join(invented))
    notes += [d for ok, d in checks if not ok]
    res.update(passed=passed, proposal=proposal, explained=explained, checks=[d for _, d in checks],
               notes=notes, spec=spec, unsupported=an.unsupported, conflicts=an.conflicts,
               state=st, orchestrator=orch)
    (out_dir / f"{case['id']}.md").write_text(f"# {case['id']} ({case['group']}, expect {exp})\n\n" +
                                              "\n\n".join(transcript) + "\n\n## Result\n" +
                                              ("PASS" if passed else "FAIL") + "\n" + "\n".join(f"- {n}" for n in notes),
                                              encoding="utf-8")
    return res


def generate(res, caps, jar, out_dir: Path) -> dict:
    from ai_pcb_designer.engineering.design.compiler import compile_spec
    from ai_pcb_designer.engineering.pipeline import DesignPipeline
    st = res["state"]
    comp = compile_spec(st.spec, res["orchestrator"].libs)
    t = time.monotonic()
    rec = DesignPipeline(caps, jar).run(comp.circuit, out_dir / "boards" / res["id"])
    s = rec.summary or {}
    drc = s.get("drc") or {}
    return {"status": rec.validation_status, "parts": len(comp.circuit.components),
            "drc_errors": drc.get("errors"), "unconnected": drc.get("unconnected"),
            "erc": (s.get("erc") or {}).get("counts"), "seconds": round(time.monotonic() - t)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(HERE / "benchmark_cases.json"))
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--group", help="core, clarify, limits, expanded, refuse")
    ap.add_argument("--generate", action="store_true", help="also generate passing build cases (slow)")
    ap.add_argument("--turns", type=int, default=5)
    ap.add_argument("--rpm", type=float, default=12, help="max AI requests per minute (free tier ~15)")
    ap.add_argument("--dev-scripted", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out", default=str(HERE.parent / "reports" / "benchmark"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    from ai_pcb_designer.ai.providers.base import ProviderConfig
    from ai_pcb_designer.ai.providers.vendors import DEFAULT_MODELS, make_provider
    from ai_pcb_designer.core.config import Settings
    from ai_pcb_designer.kicad.detection import detect
    from ai_pcb_designer.kicad.library import LibraryManager
    from ai_pcb_designer.security.secrets import get_key

    st = Settings.load()
    prov = st.ai.provider if st.ai.provider in ("gemini", "openai", "openai_compatible") else "gemini"
    if a.dev_scripted:                       # harness self-test only: keyword matcher, not an AI
        prov = "scripted"
    key = get_key(prov) if prov != "scripted" else "-"
    if not key and prov != "openai_compatible":
        print(f"No {prov} API key saved. Open the app, Settings -> AI, save the key, then run this again.")
        return 2
    model = st.ai.model if st.ai.provider == prov and st.ai.model else DEFAULT_MODELS.get(prov, "simulated")
    provider = Throttle(make_provider(ProviderConfig(prov, model, api_key=key, base_url=st.ai.base_url or None)), a.rpm)
    caps = detect()
    libs = LibraryManager(caps.symbol_dir, caps.footprint_dir, caps.models3d_dir)

    cases = json.loads(Path(a.cases).read_text(encoding="utf-8"))["cases"]
    if a.only:
        ids = {x.strip().upper() for x in a.only.split(",")}
        cases = [c for c in cases if c["id"].upper() in ids]
    if a.group:
        cases = [c for c in cases if c["group"] == a.group]
    print(f"Benchmark: {len(cases)} cases, model {prov}/{model}, max {a.rpm:g} requests/min\n")

    from ai_pcb_designer.ai.providers.base import ProviderError

    def busy(e):   # the AI service was unavailable: says nothing about Gerber Bear's quality
        return isinstance(e, ProviderError) and e.kind in ("server", "rate_limit", "quota", "network")

    def attempt(c):
        try:
            return run_case(c, provider, FactsSim(c) if a.dev_scripted else provider, libs, a.turns, out)
        except Exception as e:
            return {"id": c["id"], "group": c["group"], "expect": c["expect"], "passed": False, "turns": 0,
                    "skipped": busy(e), "notes": [f"{'AI service unavailable' if busy(e) else 'crashed'}: "
                                                  f"{type(e).__name__}: {e}"],
                    "trace": traceback.format_exc()[-2000:]}

    results = []
    queue = list(cases)
    retried = set()
    while queue:
        c = queue.pop(0)
        t = time.monotonic()
        r = attempt(c)
        if r.get("skipped") and c["id"] not in retried:
            retried.add(c["id"])
            print(f"[BUSY] {c['id']:<4} AI service unavailable - will retry at the end", flush=True)
            queue.append(c)
            if len(queue) == 1 or all(x["id"] in retried for x in queue):
                time.sleep(60)
            continue
        r["seconds"] = round(time.monotonic() - t)
        if a.generate and r.get("passed") and r.get("proposal"):
            from ai_pcb_designer.core.config import freerouting_jar
            try:
                r["generated"] = generate(r, caps, freerouting_jar(st), out)
            except Exception as e:
                r["generated"] = {"status": "crashed", "error": f"{type(e).__name__}: {e}"}
        r.pop("state", None)
        r.pop("orchestrator", None)
        results.append(r)
        g = r.get("generated")
        tag = lambda r: "SKIP" if r.get("skipped") else ("PASS" if r["passed"] else "FAIL")
        print(f"[{tag(r)}] {r['id']:<4} {r['group']:<9} turns={r['turns']} "
              f"{r['seconds']:>4}s" + (f"  board: {g.get('status')}" if g else "") +
              ("" if r["passed"] else "  - " + "; ".join(r.get("notes", []))[:300]), flush=True)

    groups = sorted({r["group"] for r in results})
    lines = ["Gerber Bear benchmark", "=" * 21, f"model: {prov}/{model}   AI calls: {provider.calls}", ""]
    tag = lambda r: "SKIP" if r.get("skipped") else ("PASS" if r["passed"] else "FAIL")
    scored = [r for r in results if not r.get("skipped")]
    for g in groups:
        rs = [r for r in scored if r["group"] == g]
        lines.append(f"{g:<10} {sum(r['passed'] for r in rs)}/{len(rs)} passed")
    tot = sum(r["passed"] for r in scored)
    nskip = len(results) - len(scored)
    lines += ["", f"TOTAL      {tot}/{len(scored)} passed" +
              (f"   ({nskip} skipped: AI service unavailable, re-run later)" if nskip else ""), ""]
    for r in results:
        lines.append(f"[{tag(r)}] {r['id']} ({r['group']}, expect {r['expect']}, {r['turns']} turns)")
        lines += [f"     - {n}" for n in r.get("notes", [])]
        if r.get("generated"):
            lines.append(f"     board: {r['generated']}")
    (out / "benchmark_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "benchmark_report.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print("\n" + "\n".join(lines[:len(groups) + 6]))
    print(f"Report: {out / 'benchmark_report.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
