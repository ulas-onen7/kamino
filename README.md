<p align="center">
  <img src=".github/banner.png" alt="Kamino: frozen sessions as crystals around a live agent — read-only access in, answer only out" width="100%">
</p>

<p align="center"><em>Freeze your best AI sessions into clones. Ask them anything; your context stays clean.</em></p>

Kamino keeps entire finished agent sessions — not summaries — as frozen specialists you can
question from any future session. The transcript is read inside an isolated subagent; only the
answer returns to the conversation you are having. Your live context never grows.

Nothing is summarized at capture time — no model runs in the capture path, and no model decides
what is worth keeping. The registry is plain files on your disk; consulting runs on your own
agent CLI and login (see Privacy).

## The problem

Past sessions hold knowledge you cannot get back to. Three things go wrong as they accumulate:

- **You re-explain stable facts.** Every fresh session starts from nothing, so you type the same
  context again.
- **You cannot find the session that knows.** Somewhere around five hundred accumulated sessions,
  the knowledge exists and locating it becomes the bottleneck.
- **You are the message bus.** When work spans specialists, you carry answers between your own
  sessions by hand, losing something at every hop.
- **And the originals expire.** Your agent CLI does not necessarily keep transcripts forever —
  Claude Code, for one, deletes them after 30 days by default (`cleanupPeriodDays`). Whatever
  only a session knew goes with it — unless it was frozen first.

Memory tools address the first by summarizing sessions as they end. That summary is written before
you know the question — so the reasoning you eventually need is often in the part that was thrown
away, and an omission leaves no trace you can detect. Kamino makes the opposite trade: keep
everything, and pay for it with isolation rather than compression.

## Install

    pipx install kamino-clones   # Python 3.9+; installs the `kamino` command
    kamino setup <host>          # one-time: claude, codex, or cursor

The distribution is `kamino-clones` because the PyPI name `kamino` belongs to an unrelated
project; everything you type after install is plain `kamino`.

`setup` installs that host's instructions and session hook; run it once per host you use — they
all share the same registry. One dependency to know: `kamino ask` spawns its isolated reader
through the `claude` CLI, while Codex and Cursor consult through their own host subagent
(`kamino serve --isolated`), staying on that host's provider. See the host matrix below for
what each host can and cannot do.

## Usage

After setup, just talk — your agent runs the CLI for you:

    "save this session"                       -> freezes it into a clone
    "why did we reject the other approach?"   -> consults the right clone, answers from it
    "what can I ask my clones about?"         -> shows your roster
    "let's continue the backend API work"     -> promotes a clone into a live session

Or drive the CLI directly:

    kamino ask "question"        # route a question to the right clone, answer from it
    kamino recruit               # freeze your latest session
    kamino sessions              # list recruitable past sessions (ids for recruit --session)
    kamino list                  # see your roster
    kamino promote <id>          # resume a clone as a live full-tool session (asks first)
    kamino retire <id>           # remove a clone
    kamino doctor                # check every invariant and report

**Your roster is never empty.** `kamino setup` seeds one clone: a specialist on operating
Kamino itself, so the first question you ask has somewhere to go before you have frozen
anything. It is generated from the shipped CLI surface rather than captured from anyone's
machine, so it cannot describe a verb this version does not have. Retire it like any other
clone (`kamino retire using-kamino`) and it stays retired; recruit your own clone under that
id and yours wins.

## Privacy: no one new sees your data

The guarantee is not "nothing ever leaves your machine" — no tool that answers with a model can
promise that honestly. The guarantee is stronger for being honest: **no one new**. Your frozen
sessions are shared with nobody who was not already part of the original conversation — not with
us (there is no us to send anything to), and not with any additional provider.

Storage is local by construction rather than by configuration: plain files under `~/.kamino`,
content-addressed, readable with `cat`. No server, no database, no account, no API key, no
telemetry. Kamino itself makes no network calls; the optional local demo server (`kamino.web`)
opens a local port and does a zero-byte UDP connect to discover which local IP it would use,
purely for the share-URL it prints — no packet leaves the machine for that call.

Storage is not the same as execution, though: answering a question means shelling out to your
own agent CLI (`claude`, `codex`, ...), and that call carries the clone's transcript as its
prompt — so consultation necessarily sends the transcript to whichever provider that CLI is
configured against. Nothing here is different from asking your normal agent CLI a question; it
is not a *second* disclosure, just the same one you already made when you had the original
conversation. The one case where that stops being true is a clone recruited from a different
tool than the one reading it — a `kamino recruit-from codex` clone read on `claude` would send
a codex-origin session's content to a provider that never saw that conversation. `kamino ask`
refuses that read before anything is sent, until you consent: `--allow-cross-provider` for one
read, or standing (`KAMINO_ALLOW_CROSS_PROVIDER=1`, or `{"cross_provider_reads": true}` in
`~/.kamino/policy.json`) if you trust your one chosen reader provider with everything you have
recorded anywhere.

The engine is dependency-free stdlib Python — auditing the privacy claim means reading a few
thousand lines, not trusting a binary.

## What actually happens when you consult

1. **Recruit** freezes a finished session: the full transcript is stored as an immutable,
   content-addressed blob, plus a small card holding its name and description. No LLM decides
   what to keep.
2. **Consult** (`kamino ask`) routes across the cards, spawns a throwaway subagent restricted to
   read-only tools, hands it the chosen transcript, and returns only its answer.
3. **Promote** reopens a clone's full context as a live, resumable session — after you confirm:
   it launches with full tools, seeded from frozen text.

The costs are measured, not estimated — the same transcript inflated 1x/2x/5x/10x, same
question each time:

| transcript tokens | the clone reads (isolated) | your live session holds |
|---:|---:|---:|
| 7,142 | 31,723 | ~638 |
| 14,284 | 40,370 | ~630 |
| 35,711 | 66,311 | ~623 |
| 71,422 | 109,546 | ~625 |

Ten times the knowledge, no growth in your context, because the transcript never crosses back. Read
the middle column honestly: the work still costs tokens, in a process that is then discarded. What
stays flat is the conversation you are actually having.

A roster of 50 clones costs about 5k tokens of live context — roughly 100 tokens per card. Cards
are descriptions, not content. (The session-start injection caps at the newest 30 cards and says
how many older ones it left out; `kamino ask` always routes across the full roster.)

Other measured behavior, from the same runs (~175 live deploys): 28 of 28 paraphrased questions
routed to the correct clone; zero hallucinated clone ids; off-topic questions declined rather than
answered from the wrong clone; a clone told to write a file was blocked at the permission boundary;
2 of 2 prompt-injection attempts inside transcripts were resisted and flagged; all blob digests
unchanged after a full run.

## How this differs from memory tools

The memory tools you have heard of all converged on the same two moves. **Distill:** an LLM
extracts facts or writes summaries from your sessions — mem0, claude-mem, Letta, and the native
memory in Claude Code and Codex all work this way. **Inject:** those notes, or search-retrieved
fragments of stored history, are pasted into your future context. Even the tools that keep
verbatim history (SpecStory, MemPalace) consume it as fragments retrieved into your live session.

Kamino is a third mechanism: keep the whole session, and ask it questions.

| | distill-and-inject | verbatim + search | Kamino |
|---|---|---|---|
| what is stored | facts an LLM chose to keep | full history, chunk-indexed | full sessions, frozen whole |
| what enters your context | notes, injected up front | retrieved fragments | only the answer to your question |
| capture cost | an LLM pass per session | embedding indexing | none — deterministic text |
| runs on your machine | worker service + vector store | vector DB + embedding model | plain files + stdlib Python |
| consult one past session in isolation | no | no | yes — this is the product |

Two honest notes. Those tools search everything at once; Kamino routes to one specialist at a
time (see Honest limits). And staleness — which Mem0's own "State of AI Agent Memory 2026" report
calls "a harder, open problem" for high-relevance memories — is not solved here either: Kamino
detects it and says so (drift, shelf-life, and recurrence signals on stale clones, surfaced by
`kamino doctor` and the roster) rather than pretending frozen knowledge stays current.

## Commands

| | |
|---|---|
| `setup` | install/refresh a host agent's instructions (`claude`, `codex`, `cursor`) |
| `recruit`, `recruit-from`, `sessions` | freeze a session (this tool, or Codex) into a clone; list recruitable session ids |
| `list`, `roster`, `registries`, `use` | browse clones; switch between named registries |
| `ask` | route a question to the right clone and answer |
| `serve` | print a clone's transcript for an isolated subagent to read (bare `serve` withholds it; `--isolated` is the only call that prints it) |
| `promote` | reopen a clone's full context as a live, resumable session (full tools — asks for confirmation) |
| `retire`, `package`, `doctor` | remove a clone; zip one for a bug report; check invariants |
| `observe`, `scout`, `proposals`, `curate` | optional, off by default (`kamino observe on`): detect knowledge you keep re-deriving, propose a clone, and synthesize it once you approve |

## Host support

Honest state, not aspiration — verified against the adapters that are actually installed, not the
plan for them:

| | Claude Code | Codex | Cursor |
|---|---|---|---|
| roster arrives | pushed (SessionStart hook) | pushed (SessionStart hook) | pulled (the rule runs `kamino roster`) |
| isolation | process-level (`ask` spawns an isolated `claude -p` subprocess restricted to read-only tools) | requested only — the agent is told to dispatch a subagent, but nothing verifies it did | structural — a dedicated, isolated subagent file is installed |
| consult | yes | yes | yes |
| recruit | yes | yes (`recruit-from codex`) | no |
| promote | yes | no | no |

The differences track what each host's CLI exposes, not a preference: Claude Code's CLI has the
resume/fork and tool-restriction flags the engine leans on, so it currently covers every axis.
Codex and Cursor are mirror images rather than a ranking: Codex wins awareness and feature
coverage, Cursor wins isolation. Gaps close as the host CLIs grow the hooks to close them.

## What it refuses to do

These are deliberate, and they are the design:

- **No vector database, no embedding-based recall as the primary signal.** Routing reads clone
  descriptions directly. A resident roster gives associative recognition that chunk retrieval
  destroys.
- **No LLM extraction at ingest.** Capture (flatten) is deterministic text transformation — no
  model filters or decides what gets kept in the stored transcript. (Drafting a new clone's
  name/description is a separate, one-time model call on your own login. It never touches what's
  stored in the blob — but the description it writes is also what routing reads, so it is a
  routing key, not just a label; see Honest limits.)
- **No fragment injection.** You get an answer from a session, not notes assembled into your prompt.
- **No telemetry, no account, no server, no API key.** Consultation runs on your own agent login.
- **No mutation of the evidence.** Blobs are content-addressed and immutable; corrections are new
  versions, never edits to what you actually said.

## Honest limits

- **Capture is lossy as a format conversion.** Every user-visible text turn is preserved verbatim,
  but tool inputs are truncated at 800 characters and tool results at 1500. That bites hardest on a
  session whose value was reading large reference material. "Full fidelity" here means undistilled,
  not byte-identical.
- **Native thinking blocks are dropped.** Measured across this project: 1,596 thinking blocks
  carried text in only 24 cases, because the harness stores a placeholder rather than the reasoning.
- **Flat is exactly flat for single, short consults.** Multi-turn dialogue and multi-clone tasks add
  the exchanges. Context grows with clone count and exchanges, never with clone content.
- **Read-only is not filesystem-confined.** The reader's tool set is enforced (non-whitelisted
  tools do not exist in its session, and your own permission rules are not loaded into it), but
  `Read` can still follow an absolute path anywhere on disk. Full filesystem sandboxing needs
  OS-level support and is not implemented.
- **Nothing here is benchmarked against reflection-based memory systems.** This is an architectural
  argument, not a measured win.
- **Team features: planned.** A trusted-team bridge exists in the engine but is not yet
  CLI-exposed; secret scrubbing and review gates are not written. Do not share a registry you
  have not read.
- **Routing recall is bounded by the blurb.** The router reads capture-time descriptions, not
  transcripts. The description is written by the session itself — recruit forks the original
  conversation (the fork is discarded; the real session and the frozen blob never see the
  drafting turn) so the writer knows the whole session, middle included. When the CLI cannot
  resume a session, a head-and-tail sampler drafts instead, and its blind spot returns: a
  question whose answer lives only mid-session can be declined even though the transcript
  holds it. Either way the 28/28 figure above measures paraphrase routing, not the
  false-negative rate, which is unmeasured. A lookup that ran and declined is reported to you
  in one line, so a miss is at least visible rather than silent.
- **One reader window per consult.** `ask` (its internal deploy step), `promote`, and `serve`
  hand the whole transcript to a single reader, so a clone past roughly 130k transcript tokens
  cannot be consulted on a default 200k-window model. `recruit` warns at freeze time, `doctor`
  flags it (D10), and roster surfaces label such clones; today the only way to read one is a
  larger-window model.
- **Two host gaps, not hidden:** Codex's isolation is requested of the agent, not enforced by
  Kamino, and Codex has no `promote`; Cursor has no `recruit` path yet. See the matrix above.

## Self-growing pipeline (opt-in)

Kamino can detect knowledge you keep re-deriving across sessions and propose freezing it as a
clone. Off by default. Enable with `kamino observe on`; `kamino scout` runs detection now,
`kamino proposals` shows what awaits your decision, and `kamino curate` synthesizes a clone
from a proposal you accepted — you approve the result, never the other way around. The seeded
`using-kamino` clone answers operating questions about all of it.

## Repo map

```text
kamino/                     the engine (stdlib only, zero dependencies)
kamino/skill/               host instructions ("just talk") for Claude Code; Codex and Cursor
                            adapters are generated by `setup` from templates in the engine
kamino/starter/             the built-in clone seeded at setup, bundled as package data
tests/                      the suite (no network calls, no spend)
scripts/                    the starter-clone generator (regenerate after CLI changes)
```

(The development repo additionally carries `docs/`, the `data*/` demo registries that feed the
local web demo, and release tooling — none of it part of this shipped tree.)

## License and support

Apache-2.0. No CLA — contributions are Apache-2.0 in and out.

Maintained by Ulas, Alen and Aytug. Issues are read; response is best-effort. Small focused
pull requests are much easier to accept than large ones.
