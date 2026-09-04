# tests/test_recruit_from.py
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli              # noqa: E402
from kamino import registry as reg  # noqa: E402
from tests.test_rollout import _rollout  # noqa: E402  (fixture builder)


def _isolate(tmp_path):
    os.environ["KAMINO_HOME"] = str(tmp_path / "khome")
    os.environ["KAMINO_CODEX_SESSIONS"] = str(tmp_path / "sessions")
    os.environ.pop("KAMINO_REGISTRY", None)


def test_recruit_from_codex_latest(tmp_path, capsys):
    _isolate(tmp_path)
    _rollout(tmp_path, sid="0199-bbbb")
    rc = cli.main(["recruit-from", "codex", "--name", "Auth Spelunking",
                   "--description", "knows how auth works in this project, including token refresh",
                   "--trim-last"])
    assert rc == 0
    from kamino import home
    roster = reg.load_roster(str(home.registry_path("personal")))
    assert roster[0]["id"] == "auth-spelunking"
    assert roster[0]["origin"] == "codex"
    assert roster[0]["origin_session"] == "0199-bbbb"
    body = open(roster[0]["blob"], encoding="utf-8").read()
    assert "USER: how does auth work here?" in body


def test_recruit_from_codex_no_sessions(tmp_path, capsys):
    _isolate(tmp_path)
    assert cli.main(["recruit-from", "codex", "--name", "x", "--description", "y"]) == 1
    assert "No Codex session" in capsys.readouterr().out


def test_recruit_from_accepts_the_removed_digest_file_flag(tmp_path, capsys):
    """Pre-branch ~/.codex/AGENTS.md emits `--digest-file <tempfile>` and no installer refreshes
    it, so rejecting the flag would mean "save this session" silently freezing nothing."""
    _isolate(tmp_path)
    _rollout(tmp_path, sid="0199-cccc")
    stale = tmp_path / "digest.md"
    stale.write_text("# handwritten digest", encoding="utf-8")
    rc = cli.main(["recruit-from", "codex", "--name", "Auth Spelunking",
                   "--description", "knows how auth works in this project, covering session expiry",
                   "--digest-file", str(stale), "--trim-last"])
    assert rc == 0, "a removed flag must never break a write path"
    from kamino import home
    regp = home.registry_path("personal")
    roster = reg.load_roster(str(regp))
    assert roster[0]["id"] == "auth-spelunking"
    assert not (regp / "digests").exists(), "the flag is ignored, not honoured"
    assert "handwritten digest" not in open(roster[0]["blob"], encoding="utf-8").read()


def test_digest_verb_degrades_instead_of_erroring(tmp_path, capsys):
    _isolate(tmp_path)
    from kamino import home
    regp = home.ensure_registry("personal")
    body = tmp_path / "d.md"
    body.write_text("# digest", encoding="utf-8")
    assert cli.main(["digest", "some-clone", "--file", str(body)]) == 0
    err = capsys.readouterr().err
    assert "kamino serve" in err and "Nothing was written" in err
    assert not (Path(regp) / "digests").exists()


def test_digest_verb_is_hidden_from_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "digest" not in capsys.readouterr().out


def test_promote_codex_clone_prints_codex_resume(tmp_path, capsys):
    _isolate(tmp_path)
    _rollout(tmp_path, sid="0199-bbbb")
    cli.main(["recruit-from", "codex", "--name", "authy",
             "--description", "knows how the authy service resumes codex sessions end to end"])
    capsys.readouterr()
    assert cli.main(["promote", "authy"]) == 0
    out = capsys.readouterr().out
    assert "codex resume 0199-bbbb" in out


def test_promote_codex_clone_does_not_need_claude(tmp_path, monkeypatch, capsys):
    """`promote` on a codex-origin clone hands off to `codex resume` and never spawns
    claude, so a codex-only machine with no `claude` on PATH must still be able to
    promote it -- the E1 guard belongs only on the branch that actually calls claude."""
    _isolate(tmp_path)
    _rollout(tmp_path, sid="0199-bbbb")
    cli.main(["recruit-from", "codex", "--name", "authy",
             "--description", "knows how the authy service resumes codex sessions end to end"])
    capsys.readouterr()
    from kamino import preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (False, "not on PATH"))
    assert cli.main(["promote", "authy"]) == 0
    out = capsys.readouterr().out
    assert "codex resume 0199-bbbb" in out
