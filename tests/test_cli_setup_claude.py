"""`kamino --version` and `kamino setup claude` — the two host-parity gaps.

Claude Code is the primary host, yet only install.sh could install its skill, so a
repo edit to SKILL.md silently drifted from the installed copy (found live: 91-line
repo file vs 62-line installed file, missing the whole Proposals/Curate flow).
"""
import os
from pathlib import Path

import pytest

from kamino import adapters, cli

SKILL_SRC = adapters.skill_source()


@pytest.fixture
def skills(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    monkeypatch.setenv("KAMINO_CLAUDE_SKILLS", str(d))
    return d


def test_version_flag_reports_the_package_version(capsys):
    assert cli.main(["--version"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("kamino ")
    assert out.split()[1][0].isdigit()


def test_version_matches_pyproject_not_frozen_metadata(capsys):
    """The old implementation trusted installed metadata alone, so an editable install kept
    reporting the version its egg-info froze at -- it said 0.3.0 for a tree already bumped to
    0.3.1. A version string that lies is worse than none, because bug reports quote it."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as f:
        declared = next(ln.split('"')[1] for ln in f if ln.startswith("version"))
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"kamino {declared}"


def test_version_falls_back_to_installed_metadata_outside_a_checkout(monkeypatch):
    """A real install (pip/pipx wheel) ships no pyproject.toml beside the package -- the
    source-tree branch above must not fire there, or `_version()` would report "unknown"
    on every packaged install. Simulate that by pointing cli.__file__ at a directory with
    no pyproject.toml, so the pyproject-adjacent lookup misses and the importlib.metadata
    fallback is what actually answers (this dev env has the kamino-clones distribution
    pip-installed -e, so it resolves to a real version rather than raising)."""
    from importlib.metadata import version as installed_version
    monkeypatch.setattr(cli, "__file__", "/nonexistent/site-packages/kamino/cli.py")
    assert cli._version() == installed_version("kamino-clones")


def test_setup_claude_installs_the_skill(skills, capsys):
    assert cli.main(["setup", "claude"]) == 0
    dest = skills / "kamino" / "SKILL.md"
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == SKILL_SRC.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "SKILL.md" in out


def test_setup_claude_is_idempotent_and_refreshes(skills):
    dest = skills / "kamino" / "SKILL.md"
    adapters.setup_claude()
    dest.write_text("stale copy", encoding="utf-8")
    adapters.setup_claude()                     # re-running re-syncs
    assert dest.read_text(encoding="utf-8") == SKILL_SRC.read_text(encoding="utf-8")


def test_setup_claude_reports_missing_source(skills, monkeypatch):
    monkeypatch.setattr(adapters, "skill_source", lambda: Path("/nonexistent/SKILL.md"))
    with pytest.raises(FileNotFoundError):
        adapters.setup_claude()


def test_setup_still_supports_codex_and_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CODEX_HOME", str(tmp_path / "cx"))
    monkeypatch.setenv("KAMINO_CURSOR_HOME", str(tmp_path / "cu"))
    assert cli.main(["setup", "codex"]) == 0
    assert cli.main(["setup", "cursor"]) == 0
