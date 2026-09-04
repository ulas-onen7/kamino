"""T4: decision CLI — proposals / accept / decline / snooze."""
import json
from datetime import datetime, timezone

import pytest

from kamino import cli, corpus, propose

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    root = corpus.ensure_store()
    monkeypatch.setattr(corpus, "maybe_sync", lambda *a, **k: None)
    return root


def _put_session(root, sid, project="/home/u/acme-ui"):
    d = root / "sessions" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"session_id": sid, "tool": "claude", "src": "", "chars": 5000,
            "end": "2026-07-25T10:00:00.000Z", "start": "2026-07-25T09:00:00.000Z",
            "pinned": False, "tier": "full", "user_turns": 3, "opener": "x",
            "link": None, "flags": {}, "ingested_at": "2026-07-25T10:00:00.000Z",
            "cwd": project, "project_slug": None, "pseudo_project": None}
    (d / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / f"{sid}.txt").write_text("USER: hi", encoding="utf-8")


def _candidate(members=("conv1", "conv2", "conv3")):
    rts = ["/home/u/acme-ui/src/a.py", "/home/u/acme-ui/src/b.py"]
    return {"cluster_id": "c000", "score": 20.0, "species": "knowledge",
            "project": "/home/u/acme-ui", "n_in_window": len(members),
            "n_evidence_only": 0, "why": ["3 distinct conversations across 3 days"],
            "signals": {}, "shared_read_targets": rts, "shared_entities": rts,
            "members": [{"conv_id": m, "tool": "claude", "project": "/home/u/acme-ui",
                         "start": "2026-07-20", "end": "2026-07-25", "n_sessions": 1,
                         "opener": f"opener {m}", "countable": True} for m in members]}


@pytest.fixture
def seeded(store):
    propose.refresh_proposals({"candidates": [_candidate()]}, now=NOW)
    for m in ("conv1", "conv2", "conv3"):
        _put_session(store, m)
    return store


def test_proposals_lists_pending(seeded, capsys):
    assert cli.main(["proposals"]) == 0
    out = capsys.readouterr().out
    assert "p001" in out and "pending" in out
    assert "3 distinct conversations" in out


def test_proposals_json(seeded, capsys):
    assert cli.main(["proposals", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in data] == ["p001"]


def test_decline_is_permanent(seeded, capsys):
    assert cli.main(["decline", "p001"]) == 0
    capsys.readouterr()                      # drop the decline confirmation
    assert propose.load_proposals()["records"][0]["state"] == "declined"
    # default listing hides it; --all shows it
    cli.main(["proposals"])
    assert "p001" not in capsys.readouterr().out
    cli.main(["proposals", "--all"])
    assert "declined" in capsys.readouterr().out
    # re-running is idempotent, not an error
    assert cli.main(["decline", "p001"]) == 0


def test_accept_pins_evidence_and_prints_pack(seeded, capsys):
    assert cli.main(["accept", "p001"]) == 0
    out = capsys.readouterr().out
    pack = json.loads(out[out.index("{"):])
    assert pack["proposal_id"] == "p001"
    assert pack["species"] == "knowledge"
    assert len(pack["members"]) == 3
    rec = propose.load_proposals()["records"][0]
    assert rec["state"] == "accepted" and rec["decided_at"]
    pinned = [m["session_id"] for m in corpus.load_metas() if m.get("pinned")]
    assert sorted(pinned) == ["conv1", "conv2", "conv3"]


def test_snooze_sets_date_and_hides(seeded, capsys):
    assert cli.main(["snooze", "p001", "--days", "7"]) == 0
    capsys.readouterr()                      # drop the snooze confirmation
    rec = propose.load_proposals()["records"][0]
    assert rec["state"] == "snoozed" and rec["snooze_until"]
    cli.main(["proposals"])
    assert "p001" not in capsys.readouterr().out


def test_unknown_id_errors(seeded, capsys):
    assert cli.main(["accept", "p999"]) == 1
    assert "p999" in capsys.readouterr().err


def test_empty_listing_says_so(store, capsys):
    assert cli.main(["proposals"]) == 0
    assert "no " in capsys.readouterr().out.lower()
