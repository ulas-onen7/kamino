"""Claude Code integration: always-on roster awareness at session start.

Codex reads `~/.codex/AGENTS.md` and Cursor reads an always-apply rule, so on those
hosts Kamino is in context from turn one. Claude Code only had an intent-gated
skill, which means a session could hold six clones and confidently report none.
This module closes that asymmetry with a SessionStart hook that prints the live
roster once per session — plus at most one pending proposal, on the same
once-a-day budget the roster surfacing already uses.

Two hard rules, both because a hook runs before the user can see anything:
  * it must never print noise, and
  * it must never fail — every error path exits 0 with empty output.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

MAX_INJECT_CLONES = 30      # a large registry must not bloat every session start
BLURB_CHARS = 200           # enough to route on, not a transcript
BOUNDARY_MARKER = "Does not cover:"
BOUNDARY_CHARS = 120        # the boundary sentence is kept whole, but still bounded
BEGIN = "<!-- KAMINO ROSTER -->"

# Our own wording stays ASCII: a Windows console is often cp1252 or (Turkish
# locale) cp857, so only user-supplied blurbs should ever need lossy replacement.
HEADER_TEMPLATE = ("The user has {n} Kamino clone(s) in registry '{name}' - frozen "
                   "past AI work sessions, distilled, on this machine only.")
# The consult mechanism is `kamino ask`, same as the skill: ask spawns an isolated read-only
# subprocess and returns ONLY the answer, so isolation is enforced by a process boundary, not
# requested of the agent (#16). The old guidance ("dispatch a subagent to run `kamino serve
# --isolated`") depended on the agent complying, and conflicted with the skill's instruction.
CONSULT_GUIDANCE = (
    "Consult one when a question is likely answered by their own past work (a decision, design, "
    "config, or \"why/how did we...\"). A blurb may end with what its clone does NOT cover - when "
    "it does, use that to rule one out. When coverage is unclear, consult rather than skip: a clone "
    "reports honestly when it cannot answer. Consult by running `kamino ask \"<the question>\"` "
    "with your shell tool - it reads the clone in an isolated subprocess, so only the answer "
    "enters this conversation, never the transcript. Never run `kamino serve` yourself. Relay the "
    "answer and say which clone it came from. If `kamino ask` declines, tell the user in one line "
    "that none of their clones covers this, then answer normally. If the roster clearly has "
    "nothing relevant, answer normally and do not mention Kamino. Never refuse.")


def claude_settings_path() -> Path:
    return Path(os.environ.get("KAMINO_CLAUDE_SETTINGS",
                               str(Path.home() / ".claude" / "settings.json")))


def hook_command() -> str:
    """Absolute interpreter path: a SessionStart hook runs with whatever PATH the
    host happens to have, and `kamino` may not be on it. Quoted when it contains
    spaces — the usual Windows case (`C:\\Program Files\\Python312\\python.exe`),
    where an unquoted path makes the hook silently never run.

    Forward slashes: Claude Code runs hooks through Git Bash on Windows, which
    treats backslashes as escape characters, so a raw backslash-separated interpreter
    path gets its separators silently eaten (exit 127). Forward slashes work in
    Git Bash, cmd, and PowerShell, so they are always safe."""
    exe = sys.executable.replace("\\", "/")
    if " " in exe:
        exe = f'"{exe}"' if os.name == "nt" else f"'{exe}'"
    return f"{exe} -m kamino.cli _inject"


def suppressed() -> bool:
    """KAMINO_NO_INJECT guards child `claude` processes (a deployed clone must not
    receive live registry state); KAMINO_MODE=none is the user's opt-out."""
    return bool(os.environ.get("KAMINO_NO_INJECT")) or \
        os.environ.get("KAMINO_MODE", "").lower() == "none"


def _proposal_line() -> str:
    """At most one pending proposal, consuming the same 24h budget as roster
    surfacing — so session start and `kamino roster` cannot double-nag."""
    try:
        from kamino import propose
        surfaced = propose.surfaced()
    except Exception:
        return ""
    if not surfaced:
        return ""
    p = surfaced["kamino_proposal"]
    return (f"\nPending clone proposal {p['id']}: {p['summary']}\n"
            f"Relay this once, at a natural pause. If they approve: "
            f"`kamino accept {p['id']}`; decline: `kamino decline {p['id']}`; "
            f"later: `kamino snooze {p['id']}`. Never nag, never decide for them.\n")


def _blurb_line(text) -> str:
    """Fit a blurb to the injection budget without severing its coverage boundary.

    A drafted blurb ends with a sentence starting exactly "Does not cover:", and
    CONSULT_GUIDANCE tells the reader to use it to rule a clone OUT. That sentence lands
    past character 300 on real cards, so a flat slice at BLURB_CHARS deletes precisely the
    half that prevents a misroute -- on the one surface whose own guidance promises it. So
    the two parts are capped separately rather than the budget being raised: awareness.md
    advertises ~375 tokens for a six-clone registry, and both caps keep that roughly true.
    Blurbs without the marker (7 of 8 today) truncate exactly as before.
    """
    blurb = " ".join((text or "").split())
    head, marker, tail = blurb.partition(BOUNDARY_MARKER)
    if not marker:
        return blurb[:BLURB_CHARS]
    boundary = f"{marker} {tail.strip()}".strip()[:BOUNDARY_CHARS].rstrip()
    lead = head[:BLURB_CHARS].rstrip()
    if len(head.rstrip()) > len(lead):
        lead += "..."       # ASCII: without it the elision reads as a corrupted sentence
    return f"{lead} {boundary}".strip()


def roster_context() -> str:
    """The block injected at session start. Empty string means stay silent."""
    from kamino import home
    from kamino import registry as reg

    name = home.active_name()
    regp = str(home.registry_path(name))
    # One shallow scan serves both views (roster + findings) -- not health.inspect_registry,
    # which is deep and would hash every blob in the registry on every session start.
    roster, findings = reg._scan_cards(regp)

    # "unusable" must mean "dropped from the roster", not "carries an error-severity
    # finding": D4 (thin description) and D5's files/provenance-json checks are errors
    # that keep the clone fully usable (see the D4/D5 tests in test_health_registry.py),
    # so counting severities alone both nagged healthy registries and miscounted clones
    # by findings instead of cards (two findings on one card read as two unusable clones).
    # Comparing cards-on-disk to the roster instead derives exactly the set that actually
    # dropped, with no need to enumerate which checks do that.
    cards_dir = os.path.join(regp, "cards")
    try:
        card_stems = {fn[:-3] for fn in os.listdir(cards_dir) if fn.endswith(".md")}
    except OSError:
        card_stems = set()
    dropped = card_stems - {c["id"] for c in roster}
    notice = ""
    if dropped:
        checks = sorted({f["check"] for f in findings if f["subject"] in dropped})
        notice = (f"\n{len(dropped)} clone(s) in registry '{name}' are unusable "
                  f"({', '.join(checks)}). "
                  f"Tell the user to run `kamino doctor`.\n")

    if not roster:
        # An empty roster with broken clones is not an empty registry -- staying silent
        # there would hide the user's whole registry at the one moment they would notice.
        return (BEGIN + notice) if notice else ""
    # Newest cards first, then cap: past the cap, eviction must be by recency, not by what
    # letter an id starts with (v0.2.0 plan; #21). frozen_at is the primary signal because
    # mtime does not survive zip extract / git checkout (#20); mtime covers undated old cards.
    def _recency(c):
        fa = c.get("frozen_at")
        if fa:
            try:
                return datetime.fromisoformat(fa).timestamp()
            except ValueError:
                pass
        return c.get("card_mtime", 0)

    shown = sorted(roster, key=_recency, reverse=True)[:MAX_INJECT_CLONES]
    lines = [BEGIN,
             HEADER_TEMPLATE.format(n=len(roster), name=name),
             CONSULT_GUIDANCE,
             ""]
    from kamino import freshness, health
    ledger = freshness.load_ledger(regp)
    for c in shown:
        blurb = _blurb_line(c.get("blurb"))
        frozen = f", frozen {c['frozen_at'][:10]}" if c.get("frozen_at") else ""
        over = (", over default reader window"
                if (c.get("transcript_tokens") or 0) > health.CONSULT_CEILING_TOKENS else "")
        c["freshness"] = ledger.get(c["id"])
        stale = freshness.hot_marker(c)
        lines.append(f"- {c['id']} [{c.get('class') or 'clone'}, "
                     f"~{c.get('transcript_tokens') or 0} tok{frozen}{over}{stale}]: {blurb}")
    if len(roster) > len(shown):
        lines.append(f"- ... and {len(roster) - len(shown)} older clone(s) not shown "
                     f"(newest {len(shown)} kept; `kamino list` for all)")
    block = "\n".join(lines) + "\n"
    return block + notice + _proposal_line()


def inject() -> str:
    """What the hook prints. Swallows everything: a broken hook must not degrade
    a session, and a partial or panicky message is worse than silence."""
    if suppressed():
        return ""
    try:
        return roster_context()
    except Exception:
        return ""


# --- settings.json hook registration ----------------------------------------
# Deliberately separate from corpus.install_hook (SessionEnd, observation): that
# module must not import this one — integrate -> propose -> corpus already runs
# that way, and the reverse would close a cycle.


def _load_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _entries(settings: dict) -> list:
    return settings.setdefault("hooks", {}).setdefault("SessionStart", [])


def _installed(entries: list) -> bool:
    return any("_inject" in (h.get("command") or "")
               for e in entries if isinstance(e, dict)
               for h in e.get("hooks", []) if isinstance(h, dict))


def hook_snippet() -> dict:
    return {"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": hook_command()}]}]}}


def register_hook(write: bool = True) -> dict:
    """Additive and idempotent: other tools own SessionStart entries too (gsd,
    claude-mem), and clobbering them would break the user's setup."""
    path = claude_settings_path()
    settings = _load_settings(path)
    entries = _entries(settings)
    if _installed(entries):
        return {"status": "already-installed", "path": str(path)}
    if not write:
        return {"status": "dry-run", "snippet": hook_snippet(), "path": str(path)}
    entries.append({"hooks": [{"type": "command", "command": hook_command()}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return {"status": "installed", "path": str(path)}


def _codex_home() -> Path:
    return Path(os.environ.get("KAMINO_CODEX_HOME", str(Path.home() / ".codex")))


def codex_hooks_path() -> Path:
    return _codex_home() / "hooks.json"


def codex_config_path() -> Path:
    return _codex_home() / "config.toml"


def codex_hook_command() -> str:
    """Codex consumes SessionStart output as the Claude-shaped JSON envelope, so the
    hook asks for `--json`; the bare form stays Claude Code's proven path."""
    return hook_command() + " --json"


FEATURE_TABLE = "[features]"
FEATURE_LINE = "hooks = true"
# `codex_hooks` is the legacy alias. Codex still resolves it -- `codex doctor` reports
# `legacy alias codex_hooks -> hooks` -- but warns "deprecated ... use [features].hooks
# instead" at session start, so the canonical key is what we write, migrating the old
# one in place if a previous Kamino install (or the user) left it behind.
_FLAG_RE = re.compile(r"^\s*(?P<key>codex_hooks|hooks)\s*=\s*(?P<value>\S+)")


def enable_codex_hooks_feature(write: bool = True) -> dict:
    """Codex runs hooks only behind `[features] hooks = true`. config.toml is the
    user's file (model, personality, per-project trust), and no stdlib TOML writer
    exists, so this edits text: the flag is inserted into an existing [features]
    table or appended as a new one, and nothing else is ever rewritten."""
    path = codex_config_path()
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        text = ""
    lines = text.splitlines(keepends=True)

    head = next((i for i, ln in enumerate(lines)
                 if ln.strip() == FEATURE_TABLE), None)
    if head is None:
        # A new table header at end-of-file cannot land inside another table.
        sep = "\n" if text and not text.endswith("\n\n") else ""
        new = text + sep + FEATURE_TABLE + "\n" + FEATURE_LINE + "\n"
    else:
        end = next((i for i in range(head + 1, len(lines))
                    if lines[i].lstrip().startswith("[")), len(lines))
        at = next((i for i in range(head + 1, end) if _FLAG_RE.match(lines[i])), None)
        if at is not None:
            m = _FLAG_RE.match(lines[at])
            if m.group("key") == "hooks" and m.group("value").rstrip(",") == "true":
                return {"status": "already-enabled", "path": str(path)}
            lines[at] = FEATURE_LINE + "\n"
        else:
            lines.insert(head + 1, FEATURE_LINE + "\n")
        new = "".join(lines)

    if not write:
        return {"status": "dry-run", "path": str(path)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return {"status": "enabled", "path": str(path)}


def register_codex_hook(write: bool = True) -> dict:
    """Codex's own SessionStart hook, so awareness no longer needs a section in
    ~/.codex/AGENTS.md -- a file the user writes in the Personalization pane.
    Additive like the Claude registration: other tools own hook entries too."""
    path = codex_hooks_path()
    data = _load_settings(path)
    entries = _entries(data)
    feature = enable_codex_hooks_feature(write=write)
    if _installed(entries):
        return {"status": "already-installed", "path": str(path),
                "feature": feature["status"], "config": feature["path"]}
    if not write:
        return {"status": "dry-run", "path": str(path),
                "feature": feature["status"], "config": feature["path"]}
    entries.append({"hooks": [{"type": "command", "command": codex_hook_command()}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return {"status": "installed", "path": str(path),
            "feature": feature["status"], "config": feature["path"]}


def unregister_codex_hook() -> dict:
    """Remove only our entry; the feature flag stays, since other hooks may need it."""
    path = codex_hooks_path()
    data = _load_settings(path)
    entries = _entries(data)
    kept = []
    for e in entries:
        hooks = [h for h in e.get("hooks", []) if isinstance(h, dict)
                 and "_inject" not in (h.get("command") or "")]
        if hooks:
            kept.append({**e, "hooks": hooks})
        elif not e.get("hooks"):
            kept.append(e)
    data["hooks"]["SessionStart"] = kept
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return {"status": "removed", "path": str(path)}


def unregister_hook() -> dict:
    """Remove only our entry, leaving every other SessionStart hook untouched."""
    path = claude_settings_path()
    settings = _load_settings(path)
    entries = _entries(settings)
    kept = []
    for e in entries:
        hooks = [h for h in e.get("hooks", []) if isinstance(h, dict)
                 and "_inject" not in (h.get("command") or "")]
        if hooks:
            kept.append({**e, "hooks": hooks})
        elif not e.get("hooks"):
            kept.append(e)
    settings["hooks"]["SessionStart"] = kept
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return {"status": "removed", "path": str(path)}
