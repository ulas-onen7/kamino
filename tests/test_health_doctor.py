import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli      # noqa: E402
from kamino import health   # noqa: E402


def _isolate_home(tmp_path):
    os.environ["KAMINO_HOME"] = str(tmp_path)
    os.environ.pop("KAMINO_REGISTRY", None)


def test_doctor_on_a_clean_install_exits_zero(tmp_path, capsys, monkeypatch):
    _isolate_home(tmp_path)
    monkeypatch.setattr(health, "collect", lambda *a, **k: [])
    assert cli.main(["doctor"]) == 0
    assert "all checks passed" in capsys.readouterr().out


def test_doctor_exits_one_on_warnings_and_two_on_errors(tmp_path, capsys, monkeypatch):
    _isolate_home(tmp_path)
    warn = health.finding("E2", "git-unavailable", "warn", "git", "not on PATH")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [warn])
    assert cli.main(["doctor"]) == 1
    err = health.finding("D1", "blob-missing", "error", "c", "gone", fix="kamino retire c")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [warn, err])
    assert cli.main(["doctor"]) == 2
    out = capsys.readouterr().out
    assert "1 error, 1 warning" in out


def test_doctor_json_is_machine_readable(tmp_path, capsys, monkeypatch):
    _isolate_home(tmp_path)
    err = health.finding("D1", "blob-missing", "error", "c", "gone", fix="kamino retire c")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [err])
    assert cli.main(["doctor", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["check"] == "D1"
    assert payload[0]["fix"] == "kamino retire c"


def test_guard_returns_two_and_prints_when_an_error_blocks(monkeypatch, capsys):
    err = health.finding("E1", "claude-unavailable", "error", "claude", "gone", fix="install")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [err])
    assert cli._guard("env") == 2
    assert "E1" in capsys.readouterr().err


def test_guard_returns_zero_and_prints_warnings(monkeypatch, capsys):
    warn = health.finding("E2", "git-unavailable", "warn", "git", "not on PATH")
    monkeypatch.setattr(health, "collect", lambda *a, **k: [warn])
    assert cli._guard("env") == 0
    assert "E2" in capsys.readouterr().err


def test_web_and_chat_refuse_without_a_demo_root(monkeypatch, capsys):
    from kamino import chat, web, paths, preflight
    monkeypatch.setattr(paths, "demo_roots", lambda: [])
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "1.0.0"))
    assert chat.main() == 2
    assert "E4" in capsys.readouterr().err
    assert web.main() == 2
    assert "E4" in capsys.readouterr().err


def test_curate_refuses_without_claude(tmp_path, monkeypatch, capsys):
    from kamino import preflight
    os.environ["KAMINO_HOME"] = str(tmp_path)
    monkeypatch.setattr(preflight, "check_claude", lambda: (False, "not on PATH"))
    assert cli.main(["curate", "p001"]) == 2
    assert "E1" in capsys.readouterr().err


def test_doctor_runs_end_to_end_against_a_real_registry(tmp_path, capsys):
    """No mocking of health.collect: this is the only test that exercises the real
    scan path, which is how `doctor` shipped crashing in Task 2 without anyone noticing."""
    from kamino import home
    from kamino import registry as reg
    _isolate_home(tmp_path)
    regp = str(home.ensure_registry("personal"))
    blurb = ("Knows the alpha service: its schema, its deploy path, and the retry budget "
             "and why it is set there.")
    sess = tmp_path / "a.jsonl"
    sess.write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "work"}}), encoding="utf-8")
    reg.recruit(str(sess), regp, "clone-a", blurb)

    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc in (0, 1, 2)              # the contract: a real run never tracebacks
    assert "kamino doctor" in out
    assert "1 clones" in out            # the healthy clone was actually scanned


def test_roster_appends_health_findings_for_the_host_agent(tmp_path, capsys):
    import json as _json
    from kamino import home
    from kamino import registry as reg
    os.environ["KAMINO_HOME"] = str(tmp_path)
    os.environ.pop("KAMINO_REGISTRY", None)
    regp = str(home.ensure_registry("personal"))
    blurb = ("Knows the alpha service: its schema, its deploy path, and the retry budget "
             "and why it is set there.")
    sess = tmp_path / "a.jsonl"
    sess.write_text(_json.dumps(
        {"type": "user", "message": {"role": "user", "content": "work"}}), encoding="utf-8")
    reg.recruit(str(sess), regp, "clone-a", blurb)
    next((Path(regp) / "blobs").iterdir()).unlink()

    assert cli.main(["roster"]) == 0
    payload = _json.loads(capsys.readouterr().out)
    health_entry = payload[-1]["kamino_health"]
    assert health_entry[0]["check"] == "D1"


def test_roster_stays_clean_when_nothing_is_wrong(tmp_path, capsys):
    from kamino import home
    os.environ["KAMINO_HOME"] = str(tmp_path)
    home.ensure_registry("personal")
    assert cli.main(["roster"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert all("kamino_health" not in entry for entry in payload)
