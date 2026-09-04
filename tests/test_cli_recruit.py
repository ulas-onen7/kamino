# tests/test_cli_recruit.py
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli              # noqa: E402
from kamino import registry as reg  # noqa: E402


def _isolate(tmp_path, monkeypatch):
    os.environ["KAMINO_HOME"] = str(tmp_path / "home")
    os.environ.pop("KAMINO_REGISTRY", None)
    projects = tmp_path / "projects"
    os.environ["KAMINO_CLAUDE_PROJECTS"] = str(projects)
    d = projects / "proj"
    d.mkdir(parents=True)
    p = d / "sess-1.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": "design the cursor pagination"}},
             {"type": "assistant", "message": {"role": "assistant", "content": "use a keyset cursor"}},
             {"type": "user", "message": {"role": "user", "content": "kamino save this"}}]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    os.utime(p, (5000, 5000))
    # never call real claude: fork-drafting declines (None), so recruit falls back to the
    # stubbed sampler -- the same ladder a session the CLI cannot resume takes in production
    from kamino import draft
    monkeypatch.setattr(draft, "draft_card_fork", lambda sid: None)
    monkeypatch.setattr(draft, "draft_card",
                        lambda blob: {"name": "cursor-pagination",
                                     "description": "Keyset pagination design for the cursor endpoint contract.",
                                     "class": "backend"})
    from kamino import preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude 1.0"))


def test_recruit_latest_session_with_flags(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    rc = cli.main(["recruit", "--yes"])
    assert rc == 0
    from kamino import home
    roster = reg.load_roster(str(home.registry_path()))
    assert len(roster) == 1
    c = roster[0]
    assert c["id"] == "cursor-pagination"
    assert "Keyset" in c["blurb"]
    # trailing "kamino save this" user turn was trimmed out of the blob
    assert "save this" not in Path(c["blob"]).read_text()
    # provenance copy exists
    assert (home.sessions_dir() / "sess-1.jsonl").exists()


def test_ask_dispatches_to_commander(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    cli.main(["recruit", "--yes"])
    from kamino import commander
    monkeypatch.setattr(commander, "handle",
                        lambda roster, q, emit=None, model=None, **kw: {"routed_to": "cursor-pagination",
                                                      "final_answer": "Use a keyset cursor.",
                                                      "recommend_promote": False})
    rc = cli.main(["ask", "how do I paginate?"])
    assert rc == 0
    assert "keyset cursor" in capsys.readouterr().out.lower()


def test_promote_prints_resume_cmd(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    cli.main(["recruit", "--yes"])
    from kamino import runtime
    monkeypatch.setattr(runtime, "promote",
                        lambda blob, model=None, read_only=False, files=None: {
                            "session_id": "sid-123", "resume_cmd": "claude --resume sid-123",
                            "ack": "ready", "error": None})
    # without --yes and no tty, promote must refuse before spawning anything (P0-3)
    rc = cli.main(["promote", "cursor-pagination"])
    assert rc == 1
    assert "--yes" in capsys.readouterr().out
    rc = cli.main(["promote", "cursor-pagination", "--yes"])
    assert rc == 0
    assert "claude --resume sid-123" in capsys.readouterr().out


def test_fork_draft_is_primary_and_card_wins(tmp_path, monkeypatch):
    """Clone-knows-best: when the fork returns a card, the sampler is never consulted and
    the fork's card is what gets recruited."""
    _isolate(tmp_path, monkeypatch)
    from kamino import draft
    monkeypatch.setattr(draft, "draft_card_fork",
                        lambda sid: {"name": "fork-card", "class": "backend",
                                     "description": "Written by the session itself, middle "
                                                    "included. Does not cover: sampling."})
    def _never(blob):
        raise AssertionError("sampler must not run when the fork drafted the card")
    monkeypatch.setattr(draft, "draft_card", _never)
    assert cli.main(["recruit", "--yes"]) == 0
    from kamino import home
    roster = reg.load_roster(str(home.registry_path()))
    assert roster[0]["id"] == "fork-card"
    assert "middle" in roster[0]["blurb"]


def test_fork_draft_call_contract(monkeypatch):
    """The fork must resume the ORIGINAL session id with --fork-session, no --model (cache
    lives on the session's own model), no tools, no ambient settings, in a kamino-draft
    scratch cwd -- the slug the sessions/recruit noise filter excludes."""
    from kamino import draft
    captured = {}
    monkeypatch.setattr(draft, "_claude",
                        lambda args, prompt, cwd=None: (captured.update(args=args, cwd=cwd),
                                                        {"result": '{"name": "x", "class": "coding", '
                                                                   '"description": "d"}'})[1])
    out = draft.draft_card_fork("sess-abc")
    a = captured["args"]
    assert out and out["name"] == "x"
    assert a[a.index("--resume") + 1] == "sess-abc" and "--fork-session" in a
    assert "--model" not in a
    assert a[a.index("--tools") + 1] == "" and a[a.index("--setting-sources") + 1] == ""
    assert "kamino-draft-" in captured["cwd"]
    assert draft.draft_card_fork(None) is None


def test_fork_draft_degrades_without_claude(monkeypatch):
    """CI machines have no claude binary: a missing executable must degrade to the fallback
    ladder (spawn-error dict -> fork returns None), never a FileNotFoundError traceback --
    the exact failure of PR #28's first CI run."""
    monkeypatch.setenv("PATH", "/nonexistent")
    from kamino import draft, runtime
    d = runtime._claude(["-p"], "hi")
    assert d.get("_spawn_error") and "result" not in d
    assert runtime._is_error(d)
    assert "launched" in runtime._error_reason(d)
    assert draft.draft_card_fork("some-sid") is None


def test_recruit_skips_background_noise_sessions(tmp_path, monkeypatch):
    """A kamino-draft fork (or observer) session written seconds ago must not win the
    most-recent race over the user's real session."""
    _isolate(tmp_path, monkeypatch)
    noise = Path(os.environ["KAMINO_CLAUDE_PROJECTS"]) / "-tmp-kamino-draft-xyz"
    noise.mkdir(parents=True)
    p = noise / "fork-sess.jsonl"
    p.write_text(json.dumps({"type": "user",
                             "message": {"role": "user", "content": "card prompt"}}),
                 encoding="utf-8")
    os.utime(p, (9000, 9000))          # newer than the real session's mtime 5000
    assert cli.main(["recruit", "--yes"]) == 0
    from kamino import home
    roster = reg.load_roster(str(home.registry_path()))
    assert [c["id"] for c in roster] == ["cursor-pagination"]


def test_warn_if_over_window(capsys):
    """Recruit stays keep-everything, but freezing a transcript no default-window reader can
    consult must not be silent (#19)."""
    from kamino import health
    cli._warn_if_over_window((health.CONSULT_CEILING_TOKENS + 1000) * 4)
    err = capsys.readouterr().err
    assert "warning: transcript" in err and "ceiling" in err, err
    cli._warn_if_over_window(1000 * 4)
    assert capsys.readouterr().err == "", "a normal transcript must not warn"


def test_detect_source_in_and_out_of_a_repo(tmp_path, monkeypatch):
    """Cards promise source pins 'captured at recruit' (design 5.1); #20 wired the capture.
    Inside a git repo: one {repo, sha} pin. Outside: empty, never an error."""
    import subprocess
    repo = tmp_path / "myproj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    pins = cli._detect_source()
    assert pins and pins[0]["repo"] == "myproj" and len(pins[0]["sha"]) >= 7, pins
    outside = tmp_path / "plain"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert cli._detect_source() == []
