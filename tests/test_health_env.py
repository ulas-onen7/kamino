import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import health      # noqa: E402
from kamino import preflight   # noqa: E402


def _by_check(findings):
    return {f["check"]: f for f in findings}


def test_e1_reports_a_missing_claude_cli(monkeypatch):
    monkeypatch.setattr(preflight, "check_claude", lambda: (False, "not on PATH"))
    f = _by_check(health.check_env("env"))["E1"]
    assert f["severity"] == "error" and f["fix"]


def test_e1_is_silent_when_claude_is_present(monkeypatch):
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "1.0.0"))
    assert "E1" not in _by_check(health.check_env("env"))


def test_e5_reports_an_unwritable_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "1.0.0"))
    assert "E5" not in _by_check(health.check_env("env"))

    def boom(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(Path, "write_text", boom)
    f = _by_check(health.check_env("env"))["E5"]
    assert f["severity"] == "error" and "KAMINO_HOME" in f["fix"]


def test_e2_warns_when_git_is_absent(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    f = _by_check(health.check_env("env"))["E2"]
    assert f["severity"] == "warn"


def test_e6_informs_when_the_registry_is_not_in_a_repo(monkeypatch, tmp_path):
    """No git repo anywhere above the registry is the default install -- see
    tests/test_e6_default_install.py for the full contract (option 3: warn only when a repo
    exists above the registry but does not track it) and for why E6 lives in the "doctor"
    scope, not "env" (a `warn` here must never reach a spend-path verb's stderr)."""
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    from kamino import home
    home.ensure_registry("personal")
    f = _by_check(health.check_env("doctor")).get("E6")
    assert f is not None and f["severity"] == "info"


def test_e6_is_silent_when_git_is_absent(monkeypatch):
    """E2 already reports a missing git; two findings for one cause is noise."""
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    assert "E6" not in _by_check(health.check_env("doctor"))


def test_e6_no_longer_lives_in_the_env_scope(monkeypatch, tmp_path):
    """Pins the scope fix itself: a `warn`-severity E6 finding must not be reachable through
    "env", the scope every spend-path verb (recruit/ask/curate/observe/promote) requests --
    only through "doctor", which nothing else asks for."""
    import subprocess
    from kamino import home
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(home_dir)], check=True)
    home.ensure_registry("personal")
    assert "E6" not in _by_check(health.check_env("env"))
    assert _by_check(health.check_env("doctor"))["E6"]["severity"] == "warn"


def test_e3_informs_for_a_named_host_whose_binary_is_absent(monkeypatch):
    """Codex/Cursor missing is normal on most machines -- see
    tests/test_e3_default_install.py for the "installed but stale/broken" warn case."""
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    f = _by_check(health.check_env("host", host="codex"))["E3"]
    assert f["severity"] == "info" and f["subject"] == "codex"


def test_e3_rejects_an_unknown_host():
    f = _by_check(health.check_env("host", host="emacs"))["E3"]
    assert f["severity"] == "error"


def test_e3_covers_every_host_when_none_is_named(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    findings = health.check_env("host")
    subjects = {f["subject"] for f in findings}
    checks = {f["check"] for f in findings}
    severities = {f["severity"] for f in findings}
    assert subjects == set(health.HOST_BINARIES)
    assert checks == {"E3"}
    assert severities == {"info"}


def test_e4_errors_when_no_demo_root_has_cards(monkeypatch, tmp_path):
    from kamino import paths
    monkeypatch.setattr(paths, "demo_roots", lambda: [])
    f = _by_check(health.check_env("demo"))["E4"]
    assert f["severity"] == "error"
    assert "kamino.build" in f["fix"]


def test_e4_is_silent_when_a_demo_root_exists(monkeypatch, tmp_path):
    from kamino import paths
    root = tmp_path / "data-demo"
    (root / "registry" / "cards").mkdir(parents=True)
    monkeypatch.setattr(paths, "demo_roots", lambda: [root])
    assert "E4" not in _by_check(health.check_env("demo"))


def test_e4_warns_when_a_demo_root_is_unreadable(monkeypatch, tmp_path):
    from kamino import paths
    root = tmp_path / "data-demo"
    cards = root / "registry" / "cards"
    cards.mkdir(parents=True)
    monkeypatch.setattr(paths, "demo_roots", lambda: [root])

    orig_iterdir = Path.iterdir

    def boom(self):
        if self == cards:
            raise OSError("permission denied")
        return orig_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", boom)
    f = _by_check(health.check_env("demo"))["E4"]
    assert f["severity"] == "warn"
