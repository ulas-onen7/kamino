"""Self-growth is a capability you switch on, not a default.

Everything ships in one tree, but a fresh install observes nothing: no corpus is
written, no detection runs, no proposals appear, until the user opts in. This keeps
main feature-complete without turning passive session capture on for anyone who
merely installed Kamino.
"""
import json

import pytest

from kamino import cli, corpus, observe_gate


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAMINO_CLAUDE_PROJECTS", str(tmp_path / "cc"))
    monkeypatch.setenv("KAMINO_CODEX_SESSIONS", str(tmp_path / "cx"))
    monkeypatch.delenv("KAMINO_OBSERVE", raising=False)
    proj = tmp_path / "cc" / "-home-u-repo"
    proj.mkdir(parents=True)
    (proj / "aaaa1111-2222-3333-4444-555566667777.jsonl").write_text(
        "\n".join(json.dumps({"type": "user", "uuid": f"u{i}", "parentUuid": None,
                              "timestamp": f"2026-07-25T08:0{i}:00.000Z",
                              "message": {"role": "user",
                                          "content": f"question {i} " * 90}})
                  for i in range(3)), encoding="utf-8")
    return tmp_path


# --- default is off ----------------------------------------------------------

def test_disabled_by_default(world):
    assert observe_gate.enabled() is False


def test_disabled_install_writes_no_corpus(world):
    corpus.maybe_sync()
    corpus.sync()
    root = corpus.corpus_root()
    assert not (root / "sessions").exists() or \
        not list((root / "sessions").glob("*/*"))
    assert not (root / "cursor.json").exists()


def test_disabled_sync_reports_why(world):
    rep = corpus.sync()
    assert rep["observing"] is False
    assert "kamino observe on" in rep["hint"]


def test_disabled_status_says_off(world, capsys):
    assert cli.main(["observe", "status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["observing"] is False


def test_disabled_scout_explains_instead_of_failing(world, capsys):
    assert cli.main(["scout"]) == 0
    out = capsys.readouterr().out
    assert "off" in out.lower() and "kamino observe on" in out


def test_roster_stays_a_clean_array_when_disabled(world, capsys):
    from kamino import home
    from kamino import registry as reg
    home.ensure_registry()
    # A description at least MIN_BLURB_CHARS long: short enough to trip D4 would
    # append a kamino_health entry here, which is not what this test is about.
    blurb = "Knows the alpha service: its schema, its deploy path, and the retry budget."
    reg.recruit_body("USER: x\n", str(home.registry_path()), "c1", blurb)
    assert cli.main(["roster"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in data] == ["c1"]
    assert not corpus.load_cursor().get("last_sync")


# --- turning it on -----------------------------------------------------------

def test_observe_on_persists_and_enables(world, capsys):
    assert cli.main(["observe", "on"]) == 0
    assert "on" in capsys.readouterr().out.lower()
    assert observe_gate.enabled() is True
    rep = corpus.sync()
    assert rep["ingested"] == 1                      # now it captures


def test_observe_off_stops_capture(world):
    cli.main(["observe", "on"])
    corpus.sync()
    cli.main(["observe", "off"])
    assert observe_gate.enabled() is False
    before = len(corpus.load_metas())
    corpus.sync()
    assert len(corpus.load_metas()) == before        # nothing new ingested


def test_env_var_overrides_stored_setting(world, monkeypatch):
    cli.main(["observe", "off"])
    monkeypatch.setenv("KAMINO_OBSERVE", "1")
    assert observe_gate.enabled() is True
    monkeypatch.setenv("KAMINO_OBSERVE", "off")
    assert observe_gate.enabled() is False


def test_explicit_sync_still_respects_the_gate(world, capsys):
    assert cli.main(["observe", "sync"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["observing"] is False and out["ingested"] == 0


def test_enabled_behaves_exactly_as_before(world):
    cli.main(["observe", "on"])
    rep = corpus.sync()
    assert rep["ingested"] == 1 and rep["observing"] is True
    assert corpus.status()["sessions"] == 1


def test_dormant_install_touches_no_disk_at_all(world):
    """The strongest form of the promise: an install that was never opted into
    leaves the corpus directory nonexistent, not merely empty."""
    from kamino import home, propose
    from kamino import registry as reg
    home.ensure_registry()
    reg.recruit_body("USER: x\n", str(home.registry_path()), "c1", "blurb")
    cli.main(["roster"])
    cli.main(["observe", "sync"])
    cli.main(["scout"])
    cli.main(["proposals"])
    assert propose.surfaced() is None
    assert not corpus.corpus_root().exists(), \
        sorted(p.name for p in corpus.corpus_root().rglob("*"))
