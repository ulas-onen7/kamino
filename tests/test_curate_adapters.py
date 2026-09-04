"""T7: the curation flow reaches every host-agent instruction surface."""
import os

from kamino import adapters


def _texts():
    return {"codex": adapters.CODEX_SECTION, "cursor": adapters.CURSOR_RULE,
            "skill": adapters.skill_source().read_text(encoding="utf-8")}


def test_every_surface_carries_the_curation_flow():
    for name, text in _texts().items():
        low = text.lower()
        assert "kamino curate" in low, name
        assert "--source" in low and "--draft" in low, name
        assert "--approve" in low, name


def test_every_surface_forbids_self_approval():
    for name, text in _texts().items():
        low = text.lower()
        assert "never approve" in low or "do not approve" in low, name
        assert "user" in low, name


def test_every_surface_tells_the_agent_to_read_sources_in_isolation():
    for name, text in _texts().items():
        low = text.lower()
        assert "subagent" in low or "isolat" in low, name


def test_every_surface_forbids_inventing_facts():
    for name, text in _texts().items():
        assert "invent" in text.lower(), name


def test_setup_installs_the_flow(tmp_path):
    os.environ["KAMINO_CODEX_HOME"] = str(tmp_path / "cx")
    os.environ["KAMINO_CURSOR_HOME"] = str(tmp_path / "cu")
    adapters.setup_codex()
    adapters.setup_cursor()
    assert "kamino curate" in (tmp_path / "cx" / "AGENTS.md").read_text(encoding="utf-8")
    assert "kamino curate" in (tmp_path / "cu" / "rules" / "kamino.mdc").read_text(
        encoding="utf-8")
