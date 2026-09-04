"""T10: optional SessionEnd hook installer — dry-run by default, idempotent writes."""
import json
from pathlib import Path

import pytest

from kamino import corpus


@pytest.fixture
def settings(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setenv("KAMINO_CLAUDE_SETTINGS", str(p))
    return p


def test_dry_run_shows_snippet_and_touches_nothing(settings):
    rep = corpus.install_hook(write=False)
    assert rep["status"] == "dry-run"
    assert rep["snippet"]["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == corpus.HOOK_COMMAND
    assert not settings.exists()


def test_write_installs_and_preserves_existing_settings(settings):
    settings.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": ["x"]}}),
                        encoding="utf-8")
    rep = corpus.install_hook(write=True)
    assert rep["status"] == "installed"
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved["model"] == "opus"                      # unrelated keys survive
    assert saved["hooks"]["PreToolUse"] == ["x"]         # unrelated hooks survive
    cmds = [h["command"] for e in saved["hooks"]["SessionEnd"] for h in e["hooks"]]
    assert cmds == [corpus.HOOK_COMMAND]


def test_second_install_is_idempotent(settings):
    corpus.install_hook(write=True)
    rep = corpus.install_hook(write=True)
    assert rep["status"] == "already-installed"
    saved = json.loads(settings.read_text(encoding="utf-8"))
    cmds = [h["command"] for e in saved["hooks"]["SessionEnd"] for h in e["hooks"]]
    assert cmds == [corpus.HOOK_COMMAND]                 # no duplicate
