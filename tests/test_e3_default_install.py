"""E3's other half of the fix (see the E3 row in docs/health.md): a host the user has not
installed is normal (see test_health_env.py for that half, now `info`) -- but a host that
IS installed, where Kamino set up an integration that has since drifted from what
`kamino setup <host>` would write today (upgraded package, hand-edited file, truncated
markers), is real breakage worth a warning. Never installing an integration at all is a
deliberate choice, not damage, and must stay silent -- otherwise a plain `pip install` with
no `kamino setup` ever run would warn forever, defeating this task's whole point.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import adapters  # noqa: E402
from kamino import health    # noqa: E402


def _by_check(findings):
    return {f["check"]: f for f in findings}


def _present(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: "/usr/bin/" + name)


def test_installed_host_never_set_up_is_silent(monkeypatch, tmp_path):
    _present(monkeypatch)
    monkeypatch.setenv("KAMINO_CODEX_HOME", str(tmp_path / "codex-home"))
    assert "E3" not in _by_check(health.check_env("host", host="codex"))


def test_installed_host_freshly_set_up_is_silent(monkeypatch, tmp_path):
    _present(monkeypatch)
    monkeypatch.setenv("KAMINO_CODEX_HOME", str(tmp_path / "codex-home"))
    adapters.setup_codex()
    assert "E3" not in _by_check(health.check_env("host", host="codex"))


def test_codex_stale_section_warns(monkeypatch, tmp_path):
    _present(monkeypatch)
    monkeypatch.setenv("KAMINO_CODEX_HOME", str(tmp_path / "codex-home"))
    adapters.setup_codex()
    p = tmp_path / "codex-home" / "AGENTS.md"
    p.write_text(p.read_text(encoding="utf-8").replace("kamino roster", "kamino roster-OLD"),
                encoding="utf-8")
    f = _by_check(health.check_env("host", host="codex"))["E3"]
    assert f["severity"] == "warn" and f["subject"] == "codex"


def test_codex_broken_markers_warns(monkeypatch, tmp_path):
    _present(monkeypatch)
    home_dir = tmp_path / "codex-home"
    home_dir.mkdir()
    monkeypatch.setenv("KAMINO_CODEX_HOME", str(home_dir))
    (home_dir / "AGENTS.md").write_text(adapters.BEGIN + "\ntruncated, no end marker\n",
                                        encoding="utf-8")
    f = _by_check(health.check_env("host", host="codex"))["E3"]
    assert f["severity"] == "warn"


def test_claude_stale_skill_warns(monkeypatch, tmp_path):
    _present(monkeypatch)
    monkeypatch.setenv("KAMINO_CLAUDE_SKILLS", str(tmp_path / "skills"))
    adapters.setup_claude()
    p = tmp_path / "skills" / "kamino" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nhand-edited\n", encoding="utf-8")
    f = _by_check(health.check_env("host", host="claude"))["E3"]
    assert f["severity"] == "warn" and f["subject"] == "claude"


def test_cursor_stale_rule_warns(monkeypatch, tmp_path):
    _present(monkeypatch)
    monkeypatch.setenv("KAMINO_CURSOR_HOME", str(tmp_path / "cursor-home"))
    adapters.setup_cursor()
    p = tmp_path / "cursor-home" / "rules" / "kamino.mdc"
    p.write_text(p.read_text(encoding="utf-8") + "\nhand-edited\n", encoding="utf-8")
    f = _by_check(health.check_env("host", host="cursor"))["E3"]
    assert f["severity"] == "warn" and f["subject"] == "cursor"


def test_cursor_stale_when_only_the_subagent_is_edited(monkeypatch, tmp_path):
    """The rule alone is not the whole integration: adapters.py's own module docstring
    calls the subagent file the thing that "actually keeps a clone transcript out of the
    user's main Cursor conversation" -- the rule only points at it. A rule that still
    matches exactly, sitting next to a hand-edited subagent, must still warn."""
    _present(monkeypatch)
    monkeypatch.setenv("KAMINO_CURSOR_HOME", str(tmp_path / "cursor-home"))
    adapters.setup_cursor()
    p = Path(adapters.cursor_subagent_path())
    p.write_text(p.read_text(encoding="utf-8") + "\nhand-edited\n", encoding="utf-8")
    f = _by_check(health.check_env("host", host="cursor"))["E3"]
    assert f["severity"] == "warn" and f["subject"] == "cursor"


def test_cursor_stale_when_the_subagent_is_missing_entirely(monkeypatch, tmp_path):
    _present(monkeypatch)
    monkeypatch.setenv("KAMINO_CURSOR_HOME", str(tmp_path / "cursor-home"))
    adapters.setup_cursor()
    Path(adapters.cursor_subagent_path()).unlink()
    f = _by_check(health.check_env("host", host="cursor"))["E3"]
    assert f["severity"] == "warn" and f["subject"] == "cursor"


def test_uninstalled_host_never_reaches_a_spend_path_verbs_stderr(monkeypatch, capsys):
    """`recruit-from <tool>` and `setup <tool>` funnel through `cli._guard("host", ...)`,
    which already drops `info` severity entirely -- an uninstalled host must not nag on
    every invocation of those verbs."""
    from kamino import cli
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    assert cli._guard("host", host="codex") == 0
    assert "E3" not in capsys.readouterr().err

