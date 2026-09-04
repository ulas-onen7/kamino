#!/usr/bin/env python3
"""Generate the starter clone that ships with Kamino.

Every install seeds one clone -- a specialist on operating Kamino itself -- so a new
user's roster is never empty and the first question they ask has somewhere to go.

The clone's transcript is GENERATED, not captured, for one reason: a captured session
goes stale the moment the CLI moves (the author's own hand-recruited `using-kamino`
predated `kamino doctor` entirely and still taught a consult path that was later
replaced). Here the verb surface is introspected from argparse at build time, so the
shipped clone cannot claim a verb or flag this version does not have, and refreshing it
for a release is one command:

    python3 scripts/gen_starter_clone.py      # -> kamino/starter/

Run it whenever the verb surface changes; tests/test_starter.py fails if the shipped
copy has drifted from what this script would emit.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kamino import __version__, cli  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "kamino", "starter")
CLONE_ID = "using-kamino"
CLASS = "knowledge"
BLURB = (
    "Specialist on operating Kamino itself: installing it for Claude Code, Codex or "
    "Cursor, every CLI verb and flag, the four agent workflows (consult, recruit, "
    "promote, list), the self-growth pipeline, registries, the staleness signals, and "
    "what to do when something breaks. Deploy it when the user asks how to use Kamino, "
    "what a verb does, why a consult failed, or how any part of the tool works. "
    "Does not cover: the user's own projects or any knowledge they have frozen "
    "themselves -- only Kamino's own operation."
)


def verb_surface():
    """(name, help, flags) for every public verb, straight from the parser."""
    parser = cli.build_parser()
    subs = [a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)][0]
    helps = {c.dest: (c.help or "") for c in subs._choices_actions}
    rows = []
    for name, sp in subs.choices.items():
        if not helps.get(name):
            continue                      # unlisted internals (_inject, digest)
        flags = []
        for a in sp._actions:
            if a.dest == "help" or a.help == argparse.SUPPRESS:
                continue
            flags.append("/".join(a.option_strings) if a.option_strings
                         else f"<{a.dest}>")
        rows.append((name, helps[name], flags))
    return rows


GROUPS = [
    ("Consulting your frozen work", ["ask", "promote", "serve", "list", "roster"]),
    ("Freezing new work", ["recruit", "recruit-from", "retire", "package"]),
    ("Self-growth: Kamino proposing its own clones",
     ["observe", "scout", "proposals", "accept", "decline", "snooze", "curate"]),
    ("Setup, registries, health", ["setup", "registries", "use", "doctor"]),
]


def verb_reference(rows):
    by_name = {n: (h, f) for n, h, f in rows}
    out, seen = [], set()
    for title, names in GROUPS:
        out.append(f"### {title}\n")
        for n in names:
            if n not in by_name:
                continue
            help_text, flags = by_name[n]
            seen.add(n)
            flag_str = f"  Flags: {' '.join(flags)}" if flags else ""
            out.append(f"- `kamino {n}` -- {help_text}.{flag_str}")
        out.append("")
    rest = [n for n, _, _ in rows if n not in seen]
    if rest:
        out.append("### Other verbs\n")
        for n in rest:
            help_text, flags = by_name[n]
            flag_str = f"  Flags: {' '.join(flags)}" if flags else ""
            out.append(f"- `kamino {n}` -- {help_text}.{flag_str}")
        out.append("")
    return "\n".join(out)


REQUEST = """You are building the durable operator's reference for Kamino {version} -- \
the document a future AI session will be handed when someone asks how to use this tool. \
Cover installation per host, the complete verb surface, the workflows an agent runs on \
the user's behalf, the self-growth pipeline, registries, the staleness signals, and the \
failure modes with their fixes. Ground every claim in the shipped code. Invent nothing: \
no verb, flag or path that does not exist in this version."""

REFERENCE = """# Using Kamino {version}

## What Kamino is, in one paragraph

Kamino freezes a finished AI coding session whole -- the transcript, not a summary -- and
makes it consultable later as a "clone". A Clone Commander reads a roster of short
descriptions ("cards"), routes a question to at most one clone, deploys that clone in an
isolated subprocess to read its own transcript, and relays back only the answer. The
transcript never enters the conversation that asked. That single property -- isolation --
is what keeps a consult cheap no matter how large the frozen session was: a 71k-token
transcript costs the live conversation roughly 600 tokens, because only the answer
crosses back.

Everything is local: plain files under `~/.kamino`, stdlib-only Python, no server, no
account, no telemetry. Kamino shells out to the `claude` CLI you are already logged into.

## Installation and setup

Install the package, then install the instructions for each agent tool you use:

- `kamino setup claude` -- writes a skill into your Claude Code skills directory AND
  registers a SessionStart hook. The hook prints your roster at the start of every
  session, so the agent knows which clones exist without being asked. Verify with
  `kamino doctor`; re-run `kamino setup claude` any time you move or rebuild your Python
  environment.
- `kamino setup codex` -- writes a marked block into `~/.codex/AGENTS.md`, which Codex
  loads on every session.
- `kamino setup cursor` -- writes an always-apply rule plus a dedicated `kamino-consult`
  subagent file, so Cursor reads clones in a genuinely separate context.

`--no-hook` skips the SessionStart hook on the Claude Code path if you want the skill
only. The installers (`install.sh`, `install.ps1`) run `kamino setup claude` for you and
refresh Codex/Cursor only where Kamino was already installed -- an upgrade never opts you
into an integration you did not ask for.

## The verb surface

{verbs}
## The four workflows an agent runs for the user

The user should never have to type a Kamino command. The agent runs these quietly.

**1. Consult -- answer from past work.** Run `kamino ask "<the user's question>"`. The
commander scans the roster, picks at most one clone, deploys it in an isolated
subprocess restricted to read-only tools, and prints the answer. Base your reply on that
answer and say which clone it came from. If it declines, tell the user in one line that
none of their clones covers this, then answer normally -- a lookup that ran and missed
should be visible, so a mis-filed clone can be noticed and fixed.

**Never run `kamino serve <id>` in the main conversation.** `serve` prints an entire
frozen transcript to stdout; reading it yourself dumps the whole session into the live
context and defeats the one property Kamino exists to provide. `serve --isolated` exists
for a dedicated subagent whose only job is to read it and report back. On Claude Code,
prefer `kamino ask`, which enforces the isolation in a subprocess instead of asking the
agent to be disciplined about it.

**2. Recruit -- save this session.** Run `kamino recruit --yes`. It freezes the most
recent session, drafts the card name and description itself, and trims the trailing
"save this" turn. Add `--name` and `--description` to override the drafting, `--class` to
set the kind, and `--registry` to target a non-active registry. Then tell the user what
was saved. Use `kamino recruit-from codex` for a session that happened in Codex.

**3. Promote -- pick the work back up.** Run `kamino promote <clone-id>`. Unlike a
consult, this adopts the clone's full context into a live, resumable session and prints
the command to resume it. Use it when the user wants to continue the work rather than ask
one question about it.

**4. List -- what do I have?** Run `kamino list` for humans, `kamino roster` for JSON.
Each line carries the clone's id, class, approximate transcript size, freeze date, and
any staleness marker.

## Self-growth: Kamino noticing what deserves to be frozen

Kamino watches your session corpus for knowledge you keep re-deriving, and proposes a
clone rather than waiting to be asked.

1. `kamino observe sync` ingests recent sessions into a local corpus (text plus metadata,
   still only on your machine).
2. `kamino scout` clusters them and ranks candidates: the same knowledge re-derived
   across several conversations scores highest.
3. `kamino proposals` lists what is waiting. The verdict is always the user's:
   `kamino accept <id>`, `kamino decline <id>`, or `kamino snooze <id> --days N`.
4. `kamino curate <proposal-id>` turns an accepted proposal into a synthesized clone. The
   agent reads each source with `kamino curate <id> --source <conv-id>` (one subagent per
   source, so raw transcripts stay out of the main conversation), writes a draft, and
   submits it with `--draft <file>`. A mechanical verifier checks that every path, ticket
   and endpoint in the draft actually appears in a source -- invention fails the gate.
   Only the user runs `--approve`.

Relay a pending proposal at most once per session, at a natural pause, and never decide
for the user.

## Registries

A registry is a folder of cards and blobs. `personal` is the default; create others to
separate contexts (work, a client). Prefer targeting one per command with
`--registry <name>` (or the KAMINO_REGISTRY env var): `kamino use <name>` switches the
active registry machine-wide and persistently, which surprises concurrent sessions, so
reserve it for when the user explicitly asks to switch. `kamino registries` lists them.
Clones never cross registries on their own.

## Staleness: how Kamino flags a clone that may have gone out of date

A frozen session cannot notice that the world moved. Kamino computes three mechanical
signals -- no model involved -- when you run `kamino doctor`, and records the verdicts in
a `freshness.json` ledger beside the cards so the roster can mark a clone without
recomputing anything:

- **Source drift (D11).** If the clone was recruited inside a git repository, its card
  pins the commit, and the freeze records which files the transcript actually discussed.
  Drift counts only commits that touched *those* files. A clone about a stable corner
  scores zero no matter how hard the rest of the repo churns.
- **Shelf life (D12).** Opt in at freeze time with `kamino recruit --shelf-life DAYS` for
  knowledge that ages by the calendar rather than by code -- legislation, pricing, policy.
  Pure date arithmetic.
- **Topic recurrence (D13).** If sessions recorded *after* the freeze keep reading the
  same files the clone covers, you are re-deriving knowledge you already froze: the clone
  is either stale or never being consulted. Both are worth knowing.

Only flagged clones carry a marker; a marker on every clone would be a marker on none.
The remedy is always the same and always the user's call: re-recruit a current session on
the topic, or retire the clone.

## Failure modes and fixes

- **`kamino doctor` is the first thing to run.** It reports every invariant at once:
  environment checks (E1-E6), registry data checks (D1-D13), and corpus checks (C1-C3).
  Exit code 0 means clean, 1 means warnings only, 2 means at least one error. It never
  repairs anything on its own, and every finding carries its own fix line.
- **A consult fails with a context-window error.** The clone's transcript is too large for
  the reader model to hold in one window. `doctor` flags such clones (D10) and `list`
  labels them. Consult with a larger-window model, or freeze a shorter session.
- **The agent does not know your clones exist.** On Claude Code that is the SessionStart
  hook; re-run `kamino setup claude`. On Cursor the rule is pulled rather than pushed, so
  the agent runs `kamino roster` itself.
- **`claude` is not found.** Kamino shells out to your own logged-in CLI. Install Claude
  Code and log in; on Windows the launcher is `claude.cmd` and Kamino resolves it by path.
- **A blob fails its digest.** Blobs are content-addressed: the filename is a hash of the
  bytes. If git rewrote line endings on checkout the hash stops matching. The shipped
  `.gitattributes` prevents this for versioned registries.

Opt-out and override environment variables, all read at runtime:
`KAMINO_HOME` (where registries live), `KAMINO_REGISTRY`, `KAMINO_DATA`, `KAMINO_CORPUS`,
`KAMINO_OBSERVE`, `KAMINO_MODE`, `KAMINO_NO_INJECT` (suppress the roster injection --
Kamino sets this for its own child processes so a deployed clone never sees live registry
state), plus `KAMINO_CLAUDE_SETTINGS`, `KAMINO_CLAUDE_SKILLS`, `KAMINO_CLAUDE_PROJECTS`,
`KAMINO_CODEX_HOME`, `KAMINO_CODEX_SESSIONS`, and `KAMINO_CURSOR_HOME`.

## Honest limits

Capture is lossy as a format conversion: every user-visible text turn survives verbatim,
but tool inputs and results are truncated, so a session whose value was reading large
reference material keeps the record of *what* was consulted rather than a copy of it.
Routing reads capture-time descriptions, not transcripts, so a question whose answer sits
only in the middle of a session can be declined even though the transcript holds it.
Every consult needs the whole transcript to fit one reader window. And nothing here is
benchmarked against reflection-based memory systems -- the case for it is architectural.
"""


def transcript(version):
    rows = verb_surface()
    body = REFERENCE.format(version=version, verbs=verb_reference(rows))
    return (f"USER: {REQUEST.format(version=version)}\n\n"
            f"ASSISTANT: {body}\n")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    text = transcript(__version__)
    with open(os.path.join(OUT_DIR, f"{CLONE_ID}.txt"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(text)
    manifest = {"id": CLONE_ID, "class": CLASS, "blurb": BLURB,
                "generated_for": __version__}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"starter clone: {len(text)} chars, {len(verb_surface())} verbs "
          f"-> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
