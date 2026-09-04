# tests/test_cli_e2e.py
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli              # noqa: E402
from kamino import registry as reg  # noqa: E402


def test_full_local_loop(tmp_path, monkeypatch, capsys):
    # isolate home + claude-project store
    os.environ["KAMINO_HOME"] = str(tmp_path / "home")
    os.environ.pop("KAMINO_REGISTRY", None)
    projects = tmp_path / "projects" / "proj"
    projects.mkdir(parents=True)
    (projects / "s.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"type": "user", "message": {"role": "user", "content": "design backend pagination"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "keyset cursor over (created_at,id)"}},
    ]), encoding="utf-8")
    os.environ["KAMINO_CLAUDE_PROJECTS"] = str(tmp_path / "projects")

    # mock the claude touchpoints -- fork-drafting declines so the sampler stub answers
    from kamino import draft, preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    monkeypatch.setattr(draft, "draft_card_fork", lambda sid: None)
    monkeypatch.setattr(draft, "draft_card",
                        lambda blob: {"name": "backend-pagination",
                                     "description": "Keyset cursor pagination over the backend service catalog.",
                                     "class": "backend"})

    # registries: create a second one so recruit must target explicitly
    assert cli.main(["use", "personal"]) == 0
    assert cli.main(["use", "work"]) == 0
    assert cli.main(["use", "personal"]) == 0

    # recruit → list → retire
    assert cli.main(["recruit", "--yes", "--registry", "personal"]) == 0
    from kamino import home
    assert len(reg.load_roster(str(home.registry_path("personal")))) == 1
    cli.main(["list"]); assert "backend-pagination" in capsys.readouterr().out
    assert cli.main(["retire", "backend-pagination"]) == 0
    assert len(reg.load_roster(str(home.registry_path("personal")))) == 0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
