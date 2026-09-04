#!/usr/bin/env python3
"""Kamino — local demo chat (Kamino-of-Kamino).

Talk to the Clone Commander. Watch it scan its roster of frozen specialist clones, route your
question to the best fit, deploy that clone in isolation, and synthesize an answer — or decline
when nothing fits. The clones hold the REAL knowledge from building Kamino, so you can fact-check.

    python -m kamino.chat          # interactive
    python -m kamino.chat --tour   # guided highlights (incl. a no-match case)

Requires: Python 3 + the `claude` CLI logged in (your own Claude Code login). No cloud, no API key.
The greyed lines are demo narration of the commander's reasoning — they would NOT appear in production.
"""
import sys

from . import registry as reg
from . import commander as cmd
from .flatten import approx_tokens
from .paths import REGISTRY

ROSTER = reg.load_roster(str(REGISTRY))
ROSTER_TOK = reg.roster_tokens(ROSTER)

DIM, B, C, G, R0 = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[0m"


def banner():
    print(f"\n{B}Kamino -- Clone Commander (local demo){R0}")
    print(f"{DIM}Roster: {len(ROSTER)} frozen clones loaded from the local git registry (no cloud, nothing to recruit):{R0}")
    for c in ROSTER:
        print(f"   {C}{c['id']:26s}{R0}{DIM}[{c['class']}, ~{c['transcript_tokens']} tok frozen]{R0}")
    print(f"{DIM}Grey lines = the commander thinking out loud (demo only). Each clone holds a real frozen")
    print(f"conversation from building Kamino -- ask hard questions and check the answers.{R0}\n")


def turn(user_input, echo):
    if echo:
        print(f"{B}You:{R0} {user_input}")
    try:
        r = cmd.handle(ROSTER, user_input)
    except Exception as e:                       # one bad turn must not kill the REPL (web degrades; CLI now too)
        print(f"{DIM}  ⚠️  that turn failed ({e}); try again.{R0}\n{DIM}{'─' * 72}{R0}")
        return
    if r["routed_to"]:
        ans = (r["clone_answer"] or "").strip()
        clip = ans[:420] + ("..." if len(ans) > 420 else "")
        dm = r.get("deploy_meta") or {}
        print(f"{DIM}  🔍 commander: scanning {len(ROSTER)} clones -> best fit = {r['routed_to']}  ({r['route_reason']}){R0}")
        print(f"{DIM}  📤 commander -> {r['routed_to']}: \"{r['clone_question']}\"{R0}")
        print(f"{DIM}  📥 {r['routed_to']}: {clip}{R0}")
        qa = approx_tokens((r.get("clone_question") or "") + ans + (r.get("final_answer") or ""))
        print(f"{DIM}  🧠 commander: clone answered -- synthesizing. (held ~{ROSTER_TOK + qa} tok = the {len(ROSTER)}-clone roster + this Q&A; "
              f"the clone's ~{dm.get('clone_transcript_tokens','?')}-tok transcript never entered its context){R0}")
    else:
        print(f"{DIM}  🔍 commander: scanned all {len(ROSTER)} clones -- none fit ({r['route_reason']}). "
              f"Standing down; deploying no one (no point burning a clone that can't help).{R0}")
    print(f"{G}Commander:{R0} {r['final_answer']}\n{DIM}{'─'*72}{R0}")


TOUR = [
    "How does Kamino seed a fresh agent with a captured session — and why not just use --resume?",
    "How do we keep a deployed clone read-only? Is denying Write/Edit/Bash good enough?",
    "How does the commander decide which clone to use, and what does it do if none fit?",
    "How can Kamino make money if the core is open-source?",
    "Aren't we just rebuilding what Salesforce Agentforce already does?",
    "What's the best topping for a pizza?",
]


def _guard_demo():
    """`chat` does not import `cli`, so it carries its own copy of the guard-and-print
    pattern rather than reaching across module boundaries for one call."""
    from . import health
    try:
        noted = health.require("env", "demo", blocking=("E1", "E4"))
    except health.HealthError as e:
        print(health.format_report(e.findings), file=sys.stderr)
        return 2
    for f in noted:
        print(health.format_line(f), file=sys.stderr)
    return 0


def main():
    from . import console
    console.degrade()
    rc = _guard_demo()
    if rc:
        return rc
    if not ROSTER:
        print("No clones found. Build the demo registry: python -m kamino.build")
        return 1
    banner()
    if "--tour" in sys.argv:
        for q in TOUR:
            turn(q, echo=True)
        return 0
    print(f"{DIM}Type a question (or 'quit'). Try injection, deploy safety, routing, money, competitors -- "
          f"or something off-topic to see it decline.{R0}")
    while True:
        try:
            ui = input(f"{B}You:{R0} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not ui:
            continue
        if ui.lower() in ("quit", "exit", "q"):
            break
        turn(ui, echo=False)
    return 0


if __name__ == "__main__":
    main()
