# tests/test_adapters.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import adapters  # noqa: E402


def test_setup_codex_creates_agents_md(tmp_path):
    os.environ["KAMINO_CODEX_HOME"] = str(tmp_path)
    p = Path(adapters.setup_codex())
    text = p.read_text(encoding="utf-8")
    assert p == tmp_path / "AGENTS.md"
    assert adapters.BEGIN in text and adapters.END in text
    assert "kamino roster" in text and "kamino serve <id> --isolated" in text


def test_setup_codex_preserves_existing_content(tmp_path):
    os.environ["KAMINO_CODEX_HOME"] = str(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# My rules\nalways be nice\n", encoding="utf-8")
    adapters.setup_codex()
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "always be nice" in text and adapters.BEGIN in text


def test_setup_codex_idempotent(tmp_path):
    os.environ["KAMINO_CODEX_HOME"] = str(tmp_path)
    adapters.setup_codex()
    adapters.setup_codex()
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count(adapters.BEGIN) == 1


def test_setup_cursor_writes_rule(tmp_path):
    os.environ["KAMINO_CURSOR_HOME"] = str(tmp_path)
    p = Path(adapters.setup_cursor())
    assert p == tmp_path / "rules" / "kamino.mdc"
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "alwaysApply: true" in text
    assert "kamino serve <id> --isolated" in text and "kamino-consult" in text


def test_every_surface_mandates_isolation():
    from kamino import adapters
    assert "subagent" in adapters.CODEX_SECTION.lower()
    assert "kamino-consult" in adapters.CURSOR_RULE, "Cursor must delegate to the installed subagent"
    assert "digest" not in adapters.CODEX_SECTION.lower()
    assert "digest" not in adapters.CURSOR_RULE.lower()
    assert not hasattr(adapters, "_DIGEST_OUTLINE")


def test_cursor_subagent_is_a_valid_definition():
    from kamino import adapters
    s = adapters.CURSOR_SUBAGENT
    assert s.startswith("---\n")
    assert "name: kamino-consult" in s
    assert "description:" in s
    assert "kamino serve <clone-id> --isolated" in s
    assert "only the answer" in s.lower()


def test_setup_cursor_installs_rule_and_subagent(tmp_path, monkeypatch):
    from kamino import adapters
    monkeypatch.setenv("KAMINO_CURSOR_HOME", str(tmp_path))
    rule = Path(adapters.setup_cursor())          # still the rule path, unchanged signature
    agent = tmp_path / "agents" / "kamino-consult.md"
    assert rule == tmp_path / "rules" / "kamino.mdc"
    assert agent.exists(), "isolation on Cursor is the subagent file, not just the rule text"
    assert "name: kamino-consult" in agent.read_text(encoding="utf-8")
    assert adapters.cursor_subagent_path() == agent


def test_setup_cursor_is_idempotent(tmp_path, monkeypatch):
    from kamino import adapters
    monkeypatch.setenv("KAMINO_CURSOR_HOME", str(tmp_path))
    adapters.setup_cursor()
    first = (tmp_path / "agents" / "kamino-consult.md").read_text(encoding="utf-8")
    adapters.setup_cursor()
    assert (tmp_path / "agents" / "kamino-consult.md").read_text(encoding="utf-8") == first


def test_contract_never_mention_kamino(tmp_path):
    for t in (adapters.CODEX_SECTION, adapters.CURSOR_RULE):
        assert "never refuse" in t.lower()
        assert "do not mention kamino" in t.lower().replace("don't", "do not")


def test_cli_setup_codex(tmp_path, capsys):
    from kamino import cli
    os.environ["KAMINO_CODEX_HOME"] = str(tmp_path)
    assert cli.main(["setup", "codex"]) == 0
    out = capsys.readouterr().out
    assert "AGENTS.md" in out and "allow" in out.lower()
    assert (tmp_path / "AGENTS.md").exists()


def test_cli_setup_cursor(tmp_path, capsys):
    from kamino import cli
    os.environ["KAMINO_CURSOR_HOME"] = str(tmp_path)
    assert cli.main(["setup", "cursor"]) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "rules" / "kamino.mdc").exists()
    # the subagent is the isolation, so cmd_setup must install it and say where it went
    agent = tmp_path / "agents" / "kamino-consult.md"
    assert agent.exists()
    assert "name: kamino-consult" in agent.read_text(encoding="utf-8")
    assert "cursor consult subagent:" in out and str(agent) in out


def test_codex_adapter_has_save_flow(tmp_path):
    assert "recruit-from codex" in adapters.CODEX_SECTION
    assert "--trim-last" in adapters.CODEX_SECTION
    os.environ["KAMINO_CODEX_HOME"] = str(tmp_path)
    adapters.setup_codex()
    assert "recruit-from codex" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_skill_source_is_package_data():
    """A pip/pipx wheel install only ever carries files that live inside the `kamino`
    package directory (declared via [tool.setuptools.package-data]); a sibling `skill/`
    directory beside the package is invisible to that mechanism and silently drops out
    of the built wheel (found live: `kamino setup claude` raised FileNotFoundError on a
    pipx install because SKILL.md was never packaged). Pin skill_source() inside the
    package tree so this cannot regress unnoticed."""
    pkg_root = Path(adapters.__file__).resolve().parent
    src = adapters.skill_source()
    assert pkg_root in src.resolve().parents, "skill source must live inside the kamino package"
    assert src.exists()
