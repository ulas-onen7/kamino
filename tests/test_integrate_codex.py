"""Codex SessionStart hook: awareness without touching the user's instructions.

Codex's Personalization pane is backed by ~/.codex/AGENTS.md, a file the user
authors. Codex does support a SessionStart hook (`~/.codex/hooks.json` plus
`[features] hooks = true`), matching `startup` and `resume`, so awareness
can move off that file entirely and gain a live roster in the bargain.

Two properties matter more than the wiring:
  * config.toml is the user's -- every unrelated table survives, byte for byte;
  * hooks.json is additive -- other tools' hook entries are never clobbered.
"""
import json

import pytest

from kamino import cli, integrate


@pytest.fixture
def codex(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "kamino"))
    monkeypatch.delenv("KAMINO_NO_INJECT", raising=False)
    return tmp_path / "codex"


# --- hooks.json registration -------------------------------------------------

def test_registers_sessionstart_hook(codex):
    rep = integrate.register_codex_hook()
    assert rep["status"] == "installed"
    data = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    entries = data["hooks"]["SessionStart"]
    assert len(entries) == 1
    assert "_inject" in entries[0]["hooks"][0]["command"]
    assert entries[0]["hooks"][0]["type"] == "command"


def test_registration_is_idempotent(codex):
    integrate.register_codex_hook()
    rep = integrate.register_codex_hook()
    assert rep["status"] == "already-installed"
    data = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    assert len(data["hooks"]["SessionStart"]) == 1


def test_other_hooks_survive_registration(codex):
    codex.mkdir(parents=True)
    (codex / "hooks.json").write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "theirs.sh"}]}],
        "PostToolUse": [{"matcher": "Bash",
                         "hooks": [{"type": "command", "command": "fmt.sh"}]}],
    }}), encoding="utf-8")
    integrate.register_codex_hook()
    data = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for e in data["hooks"]["SessionStart"] for h in e["hooks"]]
    assert "theirs.sh" in cmds and any("_inject" in c for c in cmds)
    assert data["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "fmt.sh"


def test_unregister_removes_only_ours(codex):
    codex.mkdir(parents=True)
    (codex / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "theirs.sh"}]}]}}),
        encoding="utf-8")
    integrate.register_codex_hook()
    integrate.unregister_codex_hook()
    data = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for e in data["hooks"]["SessionStart"] for h in e["hooks"]]
    assert cmds == ["theirs.sh"]


def test_corrupt_hooks_file_does_not_lose_the_hook(codex):
    codex.mkdir(parents=True)
    (codex / "hooks.json").write_text("{not json", encoding="utf-8")
    rep = integrate.register_codex_hook()
    assert rep["status"] == "installed"
    data = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    assert "_inject" in data["hooks"]["SessionStart"][0]["hooks"][0]["command"]


# --- config.toml feature flag ------------------------------------------------

USER_CONFIG = """\
personality = "pragmatic"
model = "gpt-5.6-sol"
model_reasoning_effort = "high"

[projects."/home/u/Kamino"]
trust_level = "trusted"

[projects."/home/u/other"]
trust_level = "trusted"
"""


def test_enables_feature_flag_by_appending(codex):
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text(USER_CONFIG, encoding="utf-8")
    rep = integrate.enable_codex_hooks_feature()
    assert rep["status"] == "enabled"
    text = (codex / "config.toml").read_text(encoding="utf-8")
    assert text.startswith(USER_CONFIG)          # user content untouched, verbatim
    assert "[features]" in text and "hooks = true" in text
    # the flag must land in [features], not inside the user's last [projects] table
    assert text.index("[features]") > text.index('[projects."/home/u/other"]')


def test_existing_features_table_is_extended_not_duplicated(codex):
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text(
        '[features]\nsomething_else = true\n\n[projects."/home/u/x"]\n'
        'trust_level = "trusted"\n', encoding="utf-8")
    integrate.enable_codex_hooks_feature()
    text = (codex / "config.toml").read_text(encoding="utf-8")
    assert text.count("[features]") == 1
    assert "something_else = true" in text
    # inside the [features] table, i.e. before the next table header
    assert text.index("hooks = true") < text.index("[projects.")


def test_flag_already_true_is_a_noop(codex):
    codex.mkdir(parents=True)
    before = "[features]\nhooks = true\n"
    (codex / "config.toml").write_text(before, encoding="utf-8")
    rep = integrate.enable_codex_hooks_feature()
    assert rep["status"] == "already-enabled"
    assert (codex / "config.toml").read_text(encoding="utf-8") == before


def test_flag_set_false_is_flipped_in_place(codex):
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text(
        "[features]\nhooks = false\nother = 1\n", encoding="utf-8")
    integrate.enable_codex_hooks_feature()
    text = (codex / "config.toml").read_text(encoding="utf-8")
    assert "hooks = true" in text
    assert "hooks = false" not in text
    assert "other = 1" in text


def test_legacy_alias_is_migrated_to_the_canonical_key(codex):
    """Codex resolves `codex_hooks` but warns "deprecated ... use [features].hooks"
    at every session start, and an earlier Kamino wrote the legacy key."""
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text(
        "[features]\ncodex_hooks = true\nother = 1\n", encoding="utf-8")
    rep = integrate.enable_codex_hooks_feature()
    assert rep["status"] == "enabled"
    text = (codex / "config.toml").read_text(encoding="utf-8")
    assert "codex_hooks" not in text
    assert "hooks = true" in text and "other = 1" in text
    assert text.count("hooks = true") == 1


def test_plugin_hooks_is_not_mistaken_for_the_flag(codex):
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text(
        "[features]\nplugin_hooks = false\n", encoding="utf-8")
    integrate.enable_codex_hooks_feature()
    text = (codex / "config.toml").read_text(encoding="utf-8")
    assert "plugin_hooks = false" in text        # a different feature, left alone
    assert "\nhooks = true" in text


def test_missing_config_is_created(codex):
    integrate.enable_codex_hooks_feature()
    text = (codex / "config.toml").read_text(encoding="utf-8")
    assert "[features]" in text and "hooks = true" in text


# --- the injected payload ----------------------------------------------------

def _one_clone():
    from kamino import home
    from kamino import registry as reg
    home.ensure_registry()
    reg.recruit_body("USER: x\n", str(home.registry_path()), "c1",
                     "Knows the c1 service: its config format and how it validates input.")


def test_json_mode_emits_the_sessionstart_envelope(codex, capsys):
    _one_clone()
    assert cli.main(["_inject", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert "c1" in out["additionalContext"]
    assert integrate.BEGIN in out["additionalContext"]


def test_json_mode_stays_silent_with_an_empty_registry(codex, capsys):
    assert cli.main(["_inject", "--json"]) == 0
    assert capsys.readouterr().out == ""


def test_json_mode_respects_the_suppression_guard(codex, capsys, monkeypatch):
    _one_clone()
    monkeypatch.setenv("KAMINO_NO_INJECT", "1")
    assert cli.main(["_inject", "--json"]) == 0
    assert capsys.readouterr().out == ""


def test_plain_mode_is_unchanged(codex, capsys):
    """Claude Code's proven path must not move: bare stdout, no envelope."""
    _one_clone()
    assert cli.main(["_inject"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(integrate.BEGIN)
    assert "hookSpecificOutput" not in out


def test_codex_hook_command_asks_for_json(codex):
    assert integrate.codex_hook_command().endswith("_inject --json")


# --- setup wiring ------------------------------------------------------------

def test_setup_codex_installs_the_hook(codex, capsys):
    assert cli.main(["setup", "codex"]) == 0
    out = capsys.readouterr().out
    assert "SessionStart" in out
    data = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    assert "_inject" in data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "hooks = true" in (codex / "config.toml").read_text(encoding="utf-8")


def test_setup_codex_no_hook_skips_it(codex, capsys):
    assert cli.main(["setup", "codex", "--no-hook"]) == 0
    assert "--no-hook" in capsys.readouterr().out
    assert not (codex / "hooks.json").exists()
