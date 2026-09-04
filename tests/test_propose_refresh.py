"""T2: refresh_proposals — scout candidates become records; decisions suppress."""
from datetime import datetime, timezone

import pytest

from kamino import corpus, propose

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return corpus.ensure_store()


def _candidate(project="/home/u/acme-ui", rts=None, members=None, score=20.0):
    rts = rts if rts is not None else ["/home/u/acme-ui/src/a.py", "/home/u/acme-ui/src/b.py"]
    members = members or ["conv1", "conv2", "conv3"]
    return {"cluster_id": "c000", "score": score, "species": "knowledge",
            "project": project, "n_in_window": len(members), "n_evidence_only": 0,
            "why": ["why line"], "signals": {},
            "shared_read_targets": rts, "shared_entities": rts,
            "members": [{"conv_id": m, "tool": "claude", "project": project,
                         "start": "2026-07-20", "end": "2026-07-25",
                         "n_sessions": 1, "opener": f"opener {m}", "countable": True}
                        for m in members]}


def _report(*cands):
    return {"generated_at": NOW.isoformat(), "window_days": 20,
            "n_conversations": 10, "n_edges": 5, "candidates": list(cands)}


def test_new_candidate_creates_pending(store):
    rep = propose.refresh_proposals(_report(_candidate()), now=NOW)
    assert rep == {"created": ["p001"], "refreshed": [], "suppressed": 0}
    data = propose.load_proposals()
    r = data["records"][0]
    assert r["id"] == "p001" and r["state"] == "pending"
    assert r["topic"]["project"] == "/home/u/acme-ui"
    assert r["evidence"]["score"] == 20.0
    assert r["last_seen"] == NOW.isoformat()


def test_two_candidates_two_records(store):
    propose.refresh_proposals(
        _report(_candidate(), _candidate(project="/home/u/other")), now=NOW)
    data = propose.load_proposals()
    assert [r["id"] for r in data["records"]] == ["p001", "p002"]


def test_pending_match_refreshes_in_place(store):
    propose.refresh_proposals(_report(_candidate(score=20.0)), now=NOW)
    rep = propose.refresh_proposals(_report(_candidate(score=25.0)), now=NOW)
    assert rep == {"created": [], "refreshed": ["p001"], "suppressed": 0}
    data = propose.load_proposals()
    assert len(data["records"]) == 1
    assert data["records"][0]["evidence"]["score"] == 25.0


def test_declined_suppresses_even_regrown(store):
    propose.refresh_proposals(_report(_candidate()), now=NOW)
    data = propose.load_proposals()
    data["records"][0]["state"] = "declined"
    propose.save_proposals(data)
    grown = _candidate(rts=["/home/u/acme-ui/src/a.py", "/home/u/acme-ui/src/b.py",
                            "/home/u/acme-ui/src/c.py"],
                       members=["conv1", "conv2", "conv3", "conv4", "conv5"])
    rep = propose.refresh_proposals(_report(grown), now=NOW)
    assert rep == {"created": [], "refreshed": [], "suppressed": 1}
    assert len(propose.load_proposals()["records"]) == 1


def test_accepted_suppresses(store):
    propose.refresh_proposals(_report(_candidate()), now=NOW)
    data = propose.load_proposals()
    data["records"][0]["state"] = "accepted"
    propose.save_proposals(data)
    rep = propose.refresh_proposals(_report(_candidate()), now=NOW)
    assert rep["suppressed"] == 1 and not rep["created"]


def test_curated_suppresses(store):
    # a topic that already became a clone must never be proposed again
    propose.refresh_proposals(_report(_candidate()), now=NOW)
    data = propose.load_proposals()
    data["records"][0]["state"] = "curated"
    data["records"][0]["clone_id"] = "acme-knowledge"
    propose.save_proposals(data)
    rep = propose.refresh_proposals(_report(_candidate()), now=NOW)
    assert rep["suppressed"] == 1 and not rep["created"]
    assert propose.surfaced(now=NOW) is None


def test_snooze_active_then_expired(store):
    propose.refresh_proposals(_report(_candidate()), now=NOW)
    data = propose.load_proposals()
    data["records"][0]["state"] = "snoozed"
    data["records"][0]["snooze_until"] = "2026-08-01T00:00:00+00:00"
    propose.save_proposals(data)
    rep = propose.refresh_proposals(_report(_candidate()), now=NOW)
    assert rep["suppressed"] == 1                          # still snoozed
    later = datetime(2026, 8, 2, tzinfo=timezone.utc)
    rep = propose.refresh_proposals(_report(_candidate(score=30.0)), now=later)
    assert rep == {"created": [], "refreshed": ["p001"], "suppressed": 0}
    r = propose.load_proposals()["records"][0]
    assert r["state"] == "pending" and r["evidence"]["score"] == 30.0
