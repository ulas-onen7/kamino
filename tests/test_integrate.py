"""Roster injection: always-on awareness for Claude Code (option B).

Codex and Cursor get Kamino in an always-loaded instruction file; Claude Code only
had an intent-gated skill, so a session could hold six clones and never know it.
A SessionStart hook prints the live roster once per session — names, classes and
blurbs, plus at most one pending proposal — and stays silent on every failure,
because a broken hook would degrade every session start.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kamino import cli, corpus, integrate, propose
from kamino import registry as reg


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_CLAUDE_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("KAMINO_CLAUDE_SKILLS", str(tmp_path / "skills"))
    for v in ("KAMINO_NO_INJECT", "KAMINO_MODE"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


def _seed_clones(n=2):
    from kamino import home
    home.ensure_registry()
    regp = str(home.registry_path())
    for i in range(n):
        reg.recruit_body(f"USER: work {i}\n\nASSISTANT: done {i}\n", regp,
                         f"clone-{i}", f"Blurb for clone {i}. " * 20,
                         clazz="knowledge")
    return regp


def _seed_proposal():
    rts = ["/home/u/p/a.py", "/home/u/p/b.py"]
    cand = {"cluster_id": "c000", "score": 30.0, "species": "knowledge",
            "project": "/home/u/p", "n_in_window": 3, "n_evidence_only": 0,
            "why": ["3 distinct conversations across 3 days"], "signals": {},
            "shared_read_targets": rts, "shared_entities": rts,
            "members": [{"conv_id": f"c{i}", "tool": "claude", "project": "/home/u/p",
                         "start": "2026-07-25", "end": "2026-07-26", "n_sessions": 1,
                         "opener": f"op {i}", "countable": True} for i in range(3)]}
    propose.refresh_proposals({"candidates": [cand]})


# --- the injected block ------------------------------------------------------

def test_roster_context_names_every_clone(world):
    _seed_clones(3)
    block = integrate.roster_context()
    for i in range(3):
        assert f"clone-{i}" in block
    assert "personal" in block                      # which registry is active
    assert 'kamino ask' in block                    # the enforced consult path (#16)
    assert "serve <id> --isolated" not in block     # never the request-tier mechanism


def test_roster_context_truncates_blurbs_and_caps_clones(world, monkeypatch):
    monkeypatch.setattr(integrate, "MAX_INJECT_CLONES", 2)
    _seed_clones(5)
    block = integrate.roster_context()
    assert block.count("\n- clone-") == 2           # only the cap's worth of clones
    assert "3 older clone(s) not shown" in block    # says what it left out, and by what rule (#21)
    assert len(block) < 2200                        # a session start budget, not a dump


def _set_frozen_at(regp, cid, iso):
    """Rewrite (or strip, iso=None) a card's frozen_at line. Tests must pin dates
    explicitly: on Windows the clock is coarse enough that back-to-back recruits tie on
    frozen_at, and a stable sort then keeps alphabetical order among ties."""
    p = os.path.join(regp, "cards", f"{cid}.md")
    out = [ln for ln in open(p, encoding="utf-8").read().splitlines(keepends=True)
           if not ln.startswith("frozen_at:")]
    if iso is not None:
        for i, ln in enumerate(out):
            if ln.startswith("class:"):
                out.insert(i + 1, f"frozen_at: {iso}\n")
                break
    open(p, "w", encoding="utf-8", newline="\n").write("".join(out))


def test_roster_context_caps_at_30_newest_cards_first(world, monkeypatch):
    """Eviction past the cap must be by recency, not alphabet: the v0.2.0 plan specified
    newest-first ordering (and this test name); the shipped cap dropped it (#21). Cards are
    stripped of frozen_at because the mtime FALLBACK (pre-#20 undated cards) is what this
    test pins -- the frozen_at path has its own test below."""
    import time
    monkeypatch.setattr(integrate, "MAX_INJECT_CLONES", 2)
    regp = _seed_clones(3)                          # alphabetical order would keep 0 and 1
    now = time.time()
    for i, age in [(0, 3000), (1, 2000), (2, 1000)]:   # clone-2 is the newest
        _set_frozen_at(regp, f"clone-{i}", None)
        os.utime(os.path.join(regp, "cards", f"clone-{i}.md"), (now - age, now - age))
    block = integrate.roster_context()
    assert "clone-2" in block, "newest card must survive the cap"
    assert "clone-0" not in block, "the oldest card is the one evicted"
    assert block.index("clone-2") < block.index("clone-1"), "shown newest-first"


def test_frozen_at_survives_materialization_and_orders_the_cap(world, monkeypatch):
    """mtime dies on zip extract / git checkout, so a distributed registry was dateless and
    the #21 recency fix decayed back to alphabet. frozen_at is the card's own record (#20):
    ordering must survive every file getting the same mtime."""
    import time
    monkeypatch.setattr(integrate, "MAX_INJECT_CLONES", 2)
    regp = _seed_clones(3)
    card = open(os.path.join(regp, "cards", "clone-0.md"), encoding="utf-8").read()
    assert "frozen_at: " in card, "recruit must stamp the freeze date on the card"
    for i in range(3):                              # explicit dates: recruits tie on a coarse clock
        _set_frozen_at(regp, f"clone-{i}", f"2026-08-0{i + 1}T00:00:00+00:00")
    now = time.time()
    for dirpath, _, fns in os.walk(regp):           # what zip extract / git checkout do
        for fn in fns:
            os.utime(os.path.join(dirpath, fn), (now, now))
    block = integrate.roster_context()
    assert "clone-2" in block, "newest card must survive the cap without mtimes"
    assert "clone-0" not in block, "the oldest card is still the one evicted"
    assert ", frozen 20" in block                   # the injected line carries the date


def test_roster_line_labels_over_window_clones(world):
    """A clone no default reader can open must say so wherever it is offered (#19)."""
    from kamino import health, home
    home.ensure_registry()
    regp = str(home.registry_path())
    reg.recruit_body("x" * ((health.CONSULT_CEILING_TOKENS + 1000) * 4), regp, "clone-huge",
                     "A clone whose transcript is far past the default reader window. " * 6,
                     clazz="knowledge")
    block = integrate.roster_context()
    assert "over default reader window" in block


def test_blurb_truncation_keeps_a_late_coverage_boundary(world):
    """Real cards put "Does not cover:" past character 300, and CONSULT_GUIDANCE tells the
    reader to rule a clone out with it. A flat slice at BLURB_CHARS deletes exactly that."""
    blurb = ("Padding about what this clone knows. " * 12 +
             "Does not cover: operating the CLI, installation, or the self-growth pipeline.")
    assert blurb.index("Does not cover:") > integrate.BLURB_CHARS
    line = integrate._blurb_line(blurb)
    assert "Does not cover: operating the CLI, installation, or the self-growth pipeline." in line
    assert line.startswith("Padding about what this clone knows.")


def test_blurb_truncation_without_the_marker_is_unchanged(world):
    blurb = "Knows the thing. " * 40
    assert "Does not cover:" not in blurb
    assert integrate._blurb_line(blurb) == " ".join(blurb.split())[:integrate.BLURB_CHARS]


def test_blurb_truncation_bounds_a_pathological_boundary_sentence(world):
    blurb = "Head. " * 60 + "Does not cover: " + "a very long tail clause, " * 200
    line = integrate._blurb_line(blurb)
    assert "Does not cover:" in line
    assert len(line) <= integrate.BLURB_CHARS + integrate.BOUNDARY_CHARS + 4, len(line)


def test_injected_boundary_sentence_survives_the_real_injection(world):
    from kamino import home
    home.ensure_registry()
    reg.recruit_body("USER: work\n\nASSISTANT: done\n", str(home.registry_path()), "clone-x",
                     "Long routing prose. " * 20 +
                     "Does not cover: the adjacent thing it does not know.",
                     clazz="knowledge")
    block = integrate.roster_context()
    assert "Does not cover: the adjacent thing it does not know." in block


def test_roster_context_empty_registry_is_silent(world):
    from kamino import home
    home.ensure_registry()
    assert integrate.roster_context() == ""


def test_session_start_says_so_when_every_clone_is_broken(world):
    import json as _json
    from kamino import home
    from kamino import registry as reg
    regp = str(home.ensure_registry("personal"))
    blurb = ("Knows the alpha service: its schema, its deploy path, and the retry budget "
             "and why it is set there.")
    sess = world / "a.jsonl"
    sess.write_text(_json.dumps(
        {"type": "user", "message": {"role": "user", "content": "work"}}), encoding="utf-8")
    reg.recruit(str(sess), regp, "clone-a", blurb)
    next((Path(regp) / "blobs").iterdir()).unlink()

    block = integrate.roster_context()
    assert "kamino doctor" in block
    assert block == block.encode("ascii", "strict").decode()   # stays ASCII


def test_roster_context_includes_one_pending_proposal(world):
    _seed_clones(1)
    _seed_proposal()
    block = integrate.roster_context()
    assert "p001" in block
    assert "kamino accept p001" in block
    # honours the same once-a-day budget as roster surfacing
    assert "p001" not in integrate.roster_context()


def test_roster_context_survives_a_broken_proposal_store(world):
    _seed_clones(1)
    corpus.ensure_store()
    (corpus.corpus_root() / "proposals.json").write_text("{broken", encoding="utf-8")
    block = integrate.roster_context()
    assert "clone-0" in block                       # clones still injected


# --- the _inject verb --------------------------------------------------------

def test_inject_prints_the_block(world, capsys):
    _seed_clones(2)
    assert cli.main(["_inject"]) == 0
    assert "clone-1" in capsys.readouterr().out


def test_inject_silent_when_guarded(world, capsys, monkeypatch):
    _seed_clones(2)
    monkeypatch.setenv("KAMINO_NO_INJECT", "1")
    assert cli.main(["_inject"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.delenv("KAMINO_NO_INJECT")
    monkeypatch.setenv("KAMINO_MODE", "none")
    assert cli.main(["_inject"]) == 0
    assert capsys.readouterr().out == ""


def test_inject_never_fails_the_session(world, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("registry on fire")
    monkeypatch.setattr(integrate, "roster_context", boom)
    assert cli.main(["_inject"]) == 0               # exit 0 no matter what
    assert capsys.readouterr().out == ""


def test_inject_is_not_advertised_in_help(world, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "_inject" not in capsys.readouterr().out


# --- hook registration -------------------------------------------------------

def test_register_hook_is_additive_and_idempotent(world):
    settings = Path(os.environ["KAMINO_CLAUDE_SETTINGS"])
    settings.write_text(json.dumps({
        "model": "opus",
        "hooks": {"SessionStart": [{"hooks": [{"type": "command",
                                               "command": "node gsd-check-update.js"}]}],
                  "PostToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}}),
        encoding="utf-8")
    first = integrate.register_hook()
    assert first["status"] == "installed"
    saved = json.loads(settings.read_text(encoding="utf-8"))
    cmds = [h["command"] for e in saved["hooks"]["SessionStart"] for h in e["hooks"]]
    assert "node gsd-check-update.js" in cmds       # the other tool survives
    assert any("_inject" in c for c in cmds)
    assert saved["model"] == "opus"                 # unrelated settings survive
    assert saved["hooks"]["PostToolUse"]            # unrelated hooks survive

    again = integrate.register_hook()
    assert again["status"] == "already-installed"
    saved2 = json.loads(settings.read_text(encoding="utf-8"))
    cmds2 = [h["command"] for e in saved2["hooks"]["SessionStart"] for h in e["hooks"]]
    assert len([c for c in cmds2 if "_inject" in c]) == 1


def test_hook_command_uses_the_absolute_interpreter(world):
    cmd = integrate.hook_command()
    expected = sys.executable.replace("\\", "/")
    assert cmd.startswith(expected)                 # PATH can never break it
    assert "\\" not in cmd                          # Git Bash eats backslashes
    assert "-m kamino.cli _inject" in cmd


def test_unregister_removes_only_our_entry(world):
    settings = Path(os.environ["KAMINO_CLAUDE_SETTINGS"])
    settings.write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "node other.js"}]}]}}),
        encoding="utf-8")
    integrate.register_hook()
    integrate.unregister_hook()
    saved = json.loads(settings.read_text(encoding="utf-8"))
    cmds = [h["command"] for e in saved["hooks"]["SessionStart"] for h in e["hooks"]]
    assert cmds == ["node other.js"]


# --- setup wiring ------------------------------------------------------------

def test_setup_claude_installs_skill_and_hook(world, capsys):
    assert cli.main(["setup", "claude"]) == 0
    out = capsys.readouterr().out
    assert "SKILL.md" in out and "SessionStart" in out
    saved = json.loads(Path(os.environ["KAMINO_CLAUDE_SETTINGS"]).read_text(encoding="utf-8"))
    assert any("_inject" in h["command"]
               for e in saved["hooks"]["SessionStart"] for h in e["hooks"])


def test_setup_claude_no_hook_flag(world):
    assert cli.main(["setup", "claude", "--no-hook"]) == 0
    settings = Path(os.environ["KAMINO_CLAUDE_SETTINGS"])
    assert not settings.exists() or "_inject" not in settings.read_text(encoding="utf-8")


# --- self-injection guard ----------------------------------------------------

def test_child_claude_processes_are_guarded():
    """A deployed clone must not receive the roster: it would contaminate a frozen
    context with live registry state."""
    import inspect
    from kamino import runtime
    src = inspect.getsource(runtime._claude)
    assert "KAMINO_NO_INJECT" in src


def test_consult_guidance_mandates_isolation_and_drops_digest():
    from kamino import integrate
    g = integrate.CONSULT_GUIDANCE
    # Isolation is enforced by `kamino ask`'s read-only subprocess (#16): the hook must
    # prescribe that path and must never tell the agent to open a transcript itself --
    # the historical main-context leak came from a serve instruction here (design 4.3.1).
    assert "kamino ask" in g, "the hook must prescribe the enforced consult path"
    assert "serve <id>" not in g, "the request-tier mechanism must not be prescribed"
    assert "never run `kamino serve` yourself" in g.lower()
    assert "digest" not in g.lower()
    g.encode("ascii")  # Windows cp857 console safety; tested in test_windows_release too
