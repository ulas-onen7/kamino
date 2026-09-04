"""T3: proposals refresh piggybacks corpus sync; status reports proposal states."""
import json

import pytest

from kamino import corpus, propose

OPENERS = ["how does the value report pipeline work again",
           "walk me through the value report flow once more",
           "remind me how value reports get built"]
BODIES = [
    "the value report service pulls declarations from the client and aggregates "
    "them by workplace registry number before rendering the monthly output. "
    "corrections re-open the affected month and mark the report stale. ",
    "declarations arrive through the client, get grouped on the registry number "
    "of each workplace, and the monthly output is built from those totals. "
    "premium basis folds in day counts and gross earnings per insured person. ",
    "monthly rendering sits on an aggregation keyed by workplace registry, fed "
    "by whatever the client fetched. backfill replays archived periods through "
    "the ordinary ingestion path so no special-case math exists. ",
]


def _session(i, day):
    recs = [{"type": "user", "uuid": f"u{i}0", "parentUuid": None,
             "cwd": "/home/u/acme",
             "timestamp": f"2026-07-{day}T08:00:00.000Z",
             "message": {"role": "user", "content": OPENERS[i]}},
            {"type": "assistant", "uuid": f"u{i}1", "parentUuid": f"u{i}0",
             "cwd": "/home/u/acme",
             "timestamp": f"2026-07-{day}T08:01:00.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "name": "Read",
                  "input": {"file_path": "/home/u/acme/services/value_report.py"}}]}},
            {"type": "assistant", "uuid": f"u{i}2", "parentUuid": f"u{i}1",
             "cwd": "/home/u/acme",
             "timestamp": f"2026-07-{day}T08:02:00.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "name": "Read",
                  "input": {"file_path": "/home/u/acme/services/ledger_client.py"}}]}},
            {"type": "assistant", "uuid": f"u{i}3", "parentUuid": f"u{i}2",
             "cwd": "/home/u/acme",
             "timestamp": f"2026-07-{day}T08:03:00.000Z",
             # x6 clears the 1500-char skip floor: these stand in for real sessions
             "message": {"role": "assistant", "content": BODIES[i] * 6}},
            {"type": "user", "uuid": f"u{i}4", "parentUuid": f"u{i}3",
             "cwd": "/home/u/acme",
             "timestamp": f"2026-07-{day}T08:04:00.000Z",
             "message": {"role": "user", "content": f"thanks, and what about part {i}"}}]
    return "\n".join(json.dumps(r) for r in recs)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    cc = tmp_path / "cc"
    monkeypatch.setenv("KAMINO_CLAUDE_PROJECTS", str(cc))
    monkeypatch.setenv("KAMINO_CODEX_SESSIONS", str(tmp_path / "cx"))
    proj = cc / "-home-u-acme"
    proj.mkdir(parents=True)
    for i, day in enumerate(("25", "18", "12")):
        (proj / f"aaaa{i}111-2222-3333-4444-55556666777{i}.jsonl").write_text(
            _session(i, day), encoding="utf-8")
    # keep the planted sessions inside the detection window AND out of retention's reach:
    # they carry fixed July-2026 dates, so the real grace_days purge began eating them as
    # the calendar advanced past the plant date (launch review P0-6)
    monkeypatch.setattr(corpus, "load_config", lambda: {**corpus.DEFAULTS,
                                                        "window_days": 0,
                                                        "grace_days": 36500})
    return proj


def test_sync_creates_proposals(world):
    rep = corpus.sync()
    assert rep["ingested"] == 3
    assert rep["proposals"]["created"] == ["p001"]
    r = propose.load_proposals()["records"][0]
    assert r["state"] == "pending"
    assert r["topic"]["project"] == "/home/u/acme"
    assert "/home/u/acme/services/value_report.py" in r["topic"]["read_targets"]


def test_second_sync_skips_refresh_when_nothing_changed(world):
    corpus.sync()
    rep = corpus.sync()
    assert rep["ingested"] == 0
    assert rep["proposals"] is None          # no ingest, records exist: skipped
    assert len(propose.load_proposals()["records"]) == 1


def test_status_reports_proposal_states(world):
    corpus.sync()
    st = corpus.status()
    assert st["proposals"] == {"pending": 1}
    data = propose.load_proposals()
    data["records"][0]["state"] = "declined"
    propose.save_proposals(data)
    assert corpus.status()["proposals"] == {"declined": 1}


def test_refresh_failure_never_breaks_sync(world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("detector exploded")
    monkeypatch.setattr(propose, "refresh_proposals", boom)
    rep = corpus.sync()
    assert rep["ingested"] == 3 and rep["proposals"] is None
