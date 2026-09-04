---
name: kamino
description: Use whenever the user wants to reuse, recall, or build on their own past Claude Code work — answering a question their prior sessions likely covered, saving/"freezing" the current or a past session as a reusable clone, listing what they've saved, or picking a past piece of work back up. Makes Claude act as the user's Clone Commander so they never type kamino CLI commands themselves.
---

# Kamino — talk to your frozen clones

Kamino lets the user reuse their own past Claude Code sessions ("clones"). You are their **Clone
Commander**: when a request would benefit from their past work, you consult the right clone and
answer; when they want to keep a session, you freeze it — all by running the `kamino` CLI for them,
so they never type a command. The user just talks.

## Prerequisite
The `kamino` command must be installed and on PATH (public install: `pipx install kamino-clones`). If a
`kamino` call returns "command not found", fall back to `py -m kamino.cli` (or
`python3 -m kamino.cli` on macOS/Linux) as the command prefix for all kamino invocations in this
session -- the package is installed but the scripts directory is not on PATH. Every `kamino` call
runs on their own Claude login -- no API key, no telemetry, no hosted service. Storage is local
(~/.kamino); consulting or freezing a clone sends the selected transcript to the same model
provider their own `claude` CLI already uses.

## When to act — and when NOT to
- **Consult** when the user asks something their OWN past work likely answers — a decision, design,
  config, or "why/how did we…" about a project they've worked on.
- **Recruit** when they signal they want to keep the work: "save this", "remember this session",
  "freeze this", "add this to kamino", "keep this for later".
- **Promote** when they want to continue/iterate inside past work: "let's pick the X work back up",
  "continue the Y session".
- **List** when they ask what they have: "what can I ask about", "what clones do I have".
- **Otherwise, just answer normally.** Do NOT consult a clone for general questions unrelated to
  their saved work, and **never refuse** — if nothing fits, answer exactly as you normally would and
  don't mention Kamino.

## How to act (run these with the Bash tool, quietly)

### Consult — answer from past work
Run: `kamino ask "<the user's question, verbatim or lightly cleaned>"`
Add `--model <id>` only if the user asks for a specific model (e.g. a cheap one for routine
consults); without it the clone runs on whatever model their own `claude` CLI is set to.
The commander routes to the best clone and returns an answer (or declines). Base your reply on that
answer; ground it in what the clone returned, never fabricate. If it declines / says no clone fits,
tell the user in one line that none of their saved clones covers this, then answer normally — a
lookup that ran and missed should be visible, so a mis-filed clone can be noticed and fixed.
If it refuses because the clone is cross-provider (recorded by codex), relay the refusal and ask
the user; only after they explicitly agree, re-run with `--allow-cross-provider`. If they say
"always allow this", tell them about KAMINO_ALLOW_CROSS_PROVIDER=1 — never set standing policy
for them yourself.

### Recruit — save a session
Run: `kamino recruit --yes`
This freezes the user's most recent session (usually the one you're in — the "save this" turn is
trimmed out automatically) and auto-names it. Then tell the user what was saved, e.g. *"Saved this as
**<name>**."* If they want a different name: `kamino retire <name>` then
`kamino recruit --yes --name "<their-name>"`. If they clearly mean an OLDER session, run
`kamino sessions` (NOT `kamino list` — that shows already-frozen clones, not recruitable
sessions), show the user the matching candidates, then `kamino recruit --session <id> --yes`.

### Promote — continue past work
If you don't know the clone's id, run `kamino list` first. Then run
`kamino promote <clone-id> --yes` (same optional `--model`; by default it inherits their
configured model). `--yes` confirms launching a full-tool live session seeded from frozen
transcript text: pass it ONLY because the user themselves asked to continue this work — their
request is the confirmation. Never promote unasked.
It prints a `claude --resume <sid>` command — give that to the user (and offer to run it) so they
continue inside the clone's full context. Promoting never consumes the clone; it stays saved.

### List — what's available
Run: `kamino list` and summarize the clones for the user in plain language.

## Proposals — when Kamino speaks first
Kamino watches for knowledge the user keeps re-deriving across sessions and may propose freezing it
as a clone. A `kamino roster` call can include one extra element: `{"kamino_proposal": {...}}`.
When it does:
1. Relay it in ONE short sentence in your own words (its `summary`, plus one line of `evidence` if
   that helps) at a natural pause — never interrupt work in progress.
2. If the user approves, run `kamino accept <id>`; if they decline, `kamino decline <id>`; if they
   say "later", `kamino snooze <id>`. Anything ambiguous is not an answer: leave it pending.
3. Never nag: mention a proposal at most once per session, never re-raise one you already relayed,
   and never decide for the user — the verdict is theirs alone.
If they ask what is waiting, run `kamino proposals`.

## Curate — build a clone from an accepted proposal
After the user accepts a proposal (or asks directly: "curate p003"), you write the synthesis
and they approve it. Never the other way round.
1. Run `kamino curate <proposal-id>` and follow the brief: it names the recipe (merge the
   facts / abstract the method / write the procedure), the required sections, and every
   source conversation with the command to read it.
2. Read each source with `kamino curate <id> --source <conv-id>` — dispatch a subagent per
   source so raw transcripts stay out of this conversation. Use `--full` only if the
   truncation note says you are missing something.
3. Write the synthesis to a file, following the recipe exactly. **Invent nothing**: every
   path, ticket and endpoint must come from a source; a mechanical verifier enforces it.
4. Submit: `kamino curate <id> --draft <file>`. If it FAILS, read the report, fix the
   draft, resubmit.
5. Show the user the passing report plus the draft and stop. Only the user runs
   `kamino curate <id> --approve` — never approve for them, never pass `--force`.
To refresh an existing synthesized clone later: `kamino curate <clone-id> --rebrief`.

## Registries (personal / work / client)
If the user mentions a context like "work" or "a client", scope THAT COMMAND to it:
`kamino recruit --registry <name> --yes` recruits straight into a registry, and
`kamino registries` lists them. Do NOT run `kamino use` from an inferred context — it switches
the active registry machine-wide and persistently, so a concurrent session in another terminal
would silently land in the wrong registry. Run `kamino use <name>` only when the user
explicitly asks to switch. By default everything uses their `personal` registry.

## Style
- Stay quiet about the machinery. The user speaks naturally; you run the `kamino` commands behind the
  scenes and respond. Never make them learn or type a verb.
- One clone per request — let `kamino ask` do the routing; don't fan out.
