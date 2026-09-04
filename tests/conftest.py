"""Global guard: no test may ever touch the user's real ~/.kamino corpus or the
real tool session stores. cmd_roster/cmd_serve fire corpus.maybe_sync() as a side
effect, so every test runs against isolated throwaway roots; tests that need real
fixtures override these with their own monkeypatched paths."""
import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_kamino_stores(tmp_path, monkeypatch):
    # Observation is OFF for real installs; the suite's baseline is ON so every
    # capture/detect/propose test exercises real behavior. The off-by-default
    # property itself is covered by tests/test_observe_gate.py, which clears this.
    monkeypatch.setenv("KAMINO_OBSERVE", "1")
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "_isolated_corpus"))
    monkeypatch.setenv("KAMINO_CLAUDE_PROJECTS", str(tmp_path / "_isolated_cc"))
    monkeypatch.setenv("KAMINO_CODEX_SESSIONS", str(tmp_path / "_isolated_cx"))
    monkeypatch.setenv("KAMINO_CLAUDE_SETTINGS", str(tmp_path / "_isolated_settings.json"))


# POSIX permission bits are unenforceable on Windows (os.chmod only toggles the
# read-only flag there), so the 0700/0600 assertions are skipped rather than
# silently weakened — NTFS ACLs are the protection on that platform.
posix_perms = pytest.mark.skipif(os.name == "nt",
                                 reason="POSIX permission bits are a no-op on Windows")
