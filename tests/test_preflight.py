import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import preflight  # noqa: E402


def test_missing_binary(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: None)
    ok, msg = preflight.check_claude()
    assert ok is False and "not found" in msg.lower()


def test_present_and_ok(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(preflight.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="claude 1.2.3\n", stderr=""))
    ok, msg = preflight.check_claude()
    assert ok is True and "1.2.3" in msg


def test_present_but_failing(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(preflight.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="not logged in"))
    ok, msg = preflight.check_claude()
    assert ok is False and "logged in" in msg.lower()
