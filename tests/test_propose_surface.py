"""T5: budgeted roster surfacing — at most one proposal, at most once a day."""
import json
from datetime import datetime, timezone

import pytest

from kamino import cli, corpus, propose

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(corpus, "maybe_sync", lambda *a, **k: None)
    return corpus.ensure_store()


def _candidate(project="/home/u/acme-ui", members=("conv1", "conv2", "conv3")):
    rts = [f"{project}/src/a.py", f"{project}/src/b.py"]
    return {"cluster_id": "c000", "score": 20.0, "species": "knowledge",
            "project": project, "n_in_window": len(members), "n_evidence_only": 1,
            "why": ["3 distinct conversations across 3 days",
                    "6 read targets shared by >= 3 convs"],
            "signals": {}, "shared_read_targets": rts, "shared_entities": rts,
            "members": [{"conv_id": m, "tool": "claude", "project": project,
                         "start": "2026-07-20", "end": "2026-07-25", "n_sessions": 1,
                         "opener": f"opener {m}", "countable": True} for m in members]}


def test_no_pending_no_surface(store):
    assert propose.surfaced(now=NOW) is None


def test_surfaces_once_then_throttled(store):
    propose.refresh_proposals({"candidates": [_candidate()]}, now=NOW)
    first = propose.surfaced(now=NOW)
    assert first["kamino_proposal"]["id"] == "p001"
    assert "acme-ui" in first["kamino_proposal"]["summary"]
    assert "each re-reading" in first["kamino_proposal"]["summary"]
    assert "kamino accept p001" in first["kamino_proposal"]["how_to_answer"]
    # persisted: a second call inside the window stays silent
    assert propose.surfaced(now=NOW) is None
    later = NOW.replace(day=27, hour=13)
    assert propose.surfaced(now=later)["kamino_proposal"]["id"] == "p001"


def test_highest_score_first_and_only_one(store):
    low = _candidate(project="/home/u/low", members=("a1", "a2", "a3"))
    high = dict(_candidate(project="/home/u/high", members=("b1", "b2", "b3")),
                score=99.0)
    propose.refresh_proposals({"candidates": [low, high]}, now=NOW)
    out = propose.surfaced(now=NOW)
    assert "high" in out["kamino_proposal"]["summary"]
    assert len(out) == 1                      # exactly one marker key, one proposal


def test_summary_is_budgeted(store):
    propose.refresh_proposals({"candidates": [_candidate()]}, now=NOW)
    p = propose.surfaced(now=NOW)["kamino_proposal"]
    assert len(p["summary"]) <= 300           # context tax stays a rounding error
    assert set(p) == {"id", "summary", "evidence", "how_to_answer"}


def test_declined_never_surfaces(store):
    propose.refresh_proposals({"candidates": [_candidate()]}, now=NOW)
    propose.decide("p001", "declined", now=NOW)
    assert propose.surfaced(now=NOW) is None


def test_roster_appends_proposal_once(store, capsys):
    propose.refresh_proposals({"candidates": [_candidate()]}, now=NOW)
    assert cli.main(["roster"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list)
    assert "kamino_proposal" in out[-1]
    assert cli.main(["roster"]) == 0           # throttled: plain roster again
    out2 = json.loads(capsys.readouterr().out)
    assert not any("kamino_proposal" in e for e in out2)
