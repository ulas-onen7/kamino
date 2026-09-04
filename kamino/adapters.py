"""Cross-tool adapters: instruction files that teach Codex and Cursor agents to drive the kamino
CLI with their own shell tool, so the user only types prompts (same pattern as the Claude Code
skill driving Bash). The engine stays model-free — the HOST agent routes, reads, and distills.

Codex gets a marker-delimited section in ~/.codex/AGENTS.md (replaced in place on re-setup, never
duplicated, never touching the user's own content). Cursor gets ~/.cursor/rules/kamino.mdc
(alwaysApply) plus a ~/.cursor/agents/kamino-consult.md subagent. Both hosts isolate the clone
read the same way: a subagent runs `kamino serve <id> --isolated` and only its answer returns to
the main conversation, so a frozen transcript never enters it. Bare `serve` (no flag) withholds
the transcript instead of printing it, so an uninstructed agent degrades safely.
"""
import os
from pathlib import Path

BEGIN = "<!-- BEGIN KAMINO -->"
END = "<!-- END KAMINO -->"

_PROPOSALS = """Kamino watches for knowledge the user keeps re-deriving and may propose freezing it
as a clone. A `kamino roster` call can include one extra element: `{"kamino_proposal": {...}}`.
When it does:
1. Relay it in ONE short sentence in your own words (the `summary` plus, if useful, one line of
   `evidence`) at a natural pause — never interrupt the work in progress.
2. If the user approves, run `kamino accept <id>`; if they say no, `kamino decline <id>`; if they
   say "later" or "not now", `kamino snooze <id>`. Anything ambiguous is not an answer: leave it
   pending and move on.
3. Never nag: mention a proposal at most once per session, never re-raise one you already relayed,
   and never decide for the user — accepting or declining is theirs alone.
`kamino proposals` lists what is awaiting a decision if they ask."""

_CURATION = """After the user accepts a proposal, offer to build the clone (they may also ask
directly: "curate p003"). The synthesis is YOUR work; the approval is theirs.
1. Run `kamino curate <proposal-id>` and follow the brief it prints: it names the recipe
   (merge facts / abstract the method / write the procedure), the required sections, and
   every source conversation with the exact command to read it.
2. Read each source with `kamino curate <id> --source <conv-id>` in an ISOLATED context —
   a subagent per source where you have them, so raw transcripts never flood the main
   conversation. Add `--full` only when the truncation note says you are missing something.
3. Write the synthesis to a file following the recipe exactly. Invent nothing: every path,
   ticket and endpoint must come from a source, and a verifier checks this mechanically.
4. Submit it: `kamino curate <id> --draft <file>`. If verification FAILS, read the report,
   fix the draft, and resubmit — do not argue with the verifier.
5. Show the user the passing report and the draft, then STOP. Only the user runs
   `kamino curate <id> --approve`. Never approve on their behalf, and never use --force."""

CODEX_SECTION = BEGIN + """
## Kamino — the user's frozen clones

The user keeps "clones" — frozen past AI work sessions with distilled knowledge — in a local
Kamino registry (plain files on this machine; nothing is hosted, and Kamino itself makes no
network calls — a consult sends the clone's transcript through the user's own agent CLI to
its configured model provider, the same place their live conversations already go).
You consult them by running the `kamino` CLI with your shell tool. The user only talks; never
make them type a kamino command. If `kamino` is not found, tell them to run the Kamino installer
once, then continue normally.

### Consulting (when a question is likely answered by the user's own past work — a decision,
### design, config, or "why/how did we..." about their projects)
1. Run: `kamino roster` — JSON of clones: id, description, class, tokens. A description may end
   with what the clone does NOT cover - when it does, use that to rule one out.
2. Pick the single best-matching clone yourself. If nothing fits, answer normally, do not mention Kamino,
   and never refuse. If it is genuinely unclear whether a clone covers the angle, consult it
   rather than skip it — a clone reading its own transcript will say when it cannot answer.
3. Consult in an isolated context: dispatch a subagent whose whole task is to run
   `kamino serve <id> --isolated`, read the transcript, and answer the question grounded ONLY in
   that content. Only its answer may enter your main context — never the transcript.
4. Base your reply on that answer and say which clone it came from. If the clone could not
   answer, tell the user that in one line, then answer normally - a lookup that ran and missed
   should be visible.

### Saving THIS session as a clone (when the user says "save this", "freeze this",
### "add this to kamino", "keep this for later")
1. Nothing to write by hand: the recruit command drafts the card blurb itself.
2. Run: `kamino recruit-from codex --name "<3-6 word title>" --description "<1-2 sentence
   routing blurb: what this clone knows and when to consult it>" --trim-last`
   (it defaults to the most recent Codex session, which is this one; --trim-last drops the
   "save this" turn itself)
3. Tell the user what was saved, e.g. "Saved this as <name>."

### Proposals (Kamino speaks first)
""" + _PROPOSALS + """

### Curating an accepted proposal into a clone
""" + _CURATION + """

### Registries
`kamino registries` lists them; `kamino use <name>` switches (personal / work / client ...).
Honor the user's context: "work stuff" means the work registry.
""" + END + "\n"

CURSOR_RULE = """---
description: Kamino — consult the user's frozen clones (saved past AI work sessions)
alwaysApply: true
---

# Kamino — the user's frozen clones

The user keeps "clones" — frozen past AI work sessions with distilled knowledge — in a local
Kamino registry (plain files on this machine; nothing is hosted, and Kamino itself makes no
network calls — a consult sends the clone's transcript through the user's own agent CLI to
its configured model provider, the same place their live conversations already go).
You consult them by running the `kamino` CLI in the terminal. The user only talks; never make
them type a kamino command. If `kamino` is not found, tell them to run the Kamino installer
once, then continue normally.

## Consulting (when a question is likely answered by the user's own past work — a decision,
## design, config, or "why/how did we..." about their projects)
1. Run: `kamino roster` — JSON of clones: id, description, class, tokens. A description may end
   with what the clone does NOT cover - when it does, use that to rule one out.
2. Pick the single best-matching clone yourself. If nothing fits, answer normally, do not mention Kamino,
   and never refuse. If it is genuinely unclear whether a clone covers the angle, consult it
   rather than skip it — a clone reading its own transcript will say when it cannot answer.
3. Delegate the read to the `kamino-consult` subagent, giving it the clone id and the question.
   It runs `kamino serve <id> --isolated` in its own isolated context and returns only an answer.
   Never run `kamino serve` yourself: the transcript would land in this conversation and stay there.
4. Base your reply on the returned answer and say which clone it came from. If the clone could
   not answer, tell the user that in one line, then answer normally - a lookup that ran and
   missed should be visible.

## Proposals (Kamino speaks first)
""" + _PROPOSALS + """

## Curating an accepted proposal into a clone
""" + _CURATION + """

## Registries
`kamino registries` lists them; `kamino use <name>` switches (personal / work / client ...).
Honor the user's context: "work stuff" means the work registry.
"""

# Cursor supports subagents as files in ~/.cursor/agents/*.md, which run in an isolated context
# with their own system prompt. Installing one makes the isolation invariant structural on Cursor
# instead of a request inside an always-apply rule that the agent may simply not follow.
CURSOR_SUBAGENT = """---
name: kamino-consult
description: Reads ONE frozen Kamino clone and answers ONE question from it. Use proactively whenever a question may be answered by the user's own past AI work sessions, so the clone transcript never enters the main conversation.
---

You answer exactly one question from exactly one Kamino clone. You are invoked with a clone id
and a question.

1. Run `kamino serve <clone-id> --isolated` and read all of its output. That is the clone's full
   frozen transcript of a real past working session.
2. Answer the question grounded ONLY in that content. Cite the concrete paths, commands and
   identifiers you found in it.
3. If the transcript does not answer the question, say so plainly. Do not guess, and do not fall
   back on general knowledge.
4. Return only the answer. Never return the transcript or long excerpts from it: your entire
   purpose is that the transcript stays here, in this isolated context, and only the answer
   crosses back.
"""

ALLOWLIST_GUIDANCE = """To avoid per-command approval clicks, allow the `kamino` command once:
  Codex:  approve `kamino ...` when first prompted and choose to remember it for this
          workspace (or add a prefix rule for `kamino` in your Codex approval settings).
  Cursor: Settings -> Agent -> Command Allowlist -> add `kamino`.
Storage verbs touch only the local ~/.kamino folder. Consulting/freezing verbs (ask, recruit,
promote) also invoke your own agent CLI, which sends the selected transcript to its configured
model provider."""


def _codex_home():
    return Path(os.environ.get("KAMINO_CODEX_HOME", Path.home() / ".codex"))


def _cursor_home():
    return Path(os.environ.get("KAMINO_CURSOR_HOME", Path.home() / ".cursor"))


def setup_codex():
    """Install/refresh the Kamino section in ~/.codex/AGENTS.md, replacing between markers so
    re-running never duplicates and never disturbs the user's own instructions."""
    home = _codex_home()
    home.mkdir(parents=True, exist_ok=True)
    p = home / "AGENTS.md"
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    if BEGIN in existing and END in existing:
        pre = existing.split(BEGIN)[0]
        post = existing.split(END, 1)[1]
        text = pre + CODEX_SECTION.rstrip("\n") + post
    else:
        sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
        text = existing + sep + CODEX_SECTION
    p.write_text(text, encoding="utf-8")
    return str(p)


def _claude_skills_home():
    return Path(os.environ.get("KAMINO_CLAUDE_SKILLS",
                               Path.home() / ".claude" / "skills"))


def skill_source():
    """The repo's SKILL.md — the source of truth for the Claude Code surface.
    Lives inside the package (kamino/skill/kamino/SKILL.md), not as a sibling of it,
    so it is package data: a pip/pipx wheel install carries it exactly like an
    editable source checkout does, with no separate install-time copy step."""
    return Path(__file__).resolve().parent / "skill" / "kamino" / "SKILL.md"


def setup_claude():
    """Install/refresh the Kamino skill in ~/.claude/skills/kamino/ (whole-file
    overwrite: the file is ours). install.sh does this at install time; having it
    as a verb means a repo-side skill edit can be re-synced without reinstalling,
    which is exactly the drift that hid the Proposals and Curate flows."""
    src = skill_source()
    if not src.exists():
        raise FileNotFoundError(f"skill source not found: {src}")
    dest_dir = _claude_skills_home() / "kamino"
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / "SKILL.md"
    p.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(p)


def cursor_subagent_path():
    return _cursor_home() / "agents" / "kamino-consult.md"


def setup_cursor():
    """Install/refresh the Cursor rule AND the kamino-consult subagent (both files are ours, so
    both are whole-file overwrites). The subagent is what actually keeps a clone transcript out
    of the user's main Cursor conversation; the rule only points at it. Returns the rule path,
    unchanged, so existing callers and tests keep working."""
    rules = _cursor_home() / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    rule = rules / "kamino.mdc"
    rule.write_text(CURSOR_RULE, encoding="utf-8")
    agent = cursor_subagent_path()
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(CURSOR_SUBAGENT, encoding="utf-8")
    return str(rule)
