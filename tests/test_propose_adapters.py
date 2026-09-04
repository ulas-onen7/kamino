"""T6: the proposals paragraph reaches every host-agent instruction surface."""
import os

from kamino import adapters


def _texts():
    return {"codex": adapters.CODEX_SECTION, "cursor": adapters.CURSOR_RULE,
            "skill": adapters.skill_source().read_text(encoding="utf-8")}


def test_every_surface_explains_relaying_a_proposal():
    for name, text in _texts().items():
        low = text.lower()
        assert "kamino_proposal" in low, name
        assert "kamino accept" in low and "kamino decline" in low, name
        assert "kamino snooze" in low, name


def test_every_surface_forbids_nagging_and_deciding_for_the_user():
    for name, text in _texts().items():
        low = text.lower()
        assert "never nag" in low, name
        assert "never decide" in low or "do not decide" in low, name


def test_codex_setup_installs_proposal_section(tmp_path):
    os.environ["KAMINO_CODEX_HOME"] = str(tmp_path)
    adapters.setup_codex()
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "kamino_proposal" in text
    adapters.setup_codex()                                   # still idempotent
    text2 = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text2.count("kamino_proposal") == text.count("kamino_proposal")
    assert text2.count(adapters.BEGIN) == 1


def test_cursor_setup_installs_proposal_section(tmp_path):
    os.environ["KAMINO_CURSOR_HOME"] = str(tmp_path)
    adapters.setup_cursor()
    assert "kamino_proposal" in (tmp_path / "rules" / "kamino.mdc").read_text(
        encoding="utf-8")
