"""T7: scout end-to-end on a synthetic corpus — planted topic surfaces, noise
does not, the window controls actionability, the JSON contract holds."""
import json
from datetime import datetime, timezone

import pytest

from kamino import corpus, detect

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


# Same knowledge re-derived in DIFFERENT words each time (the knowledge quadrant:
# entity overlap high, structure overlap low). Near-verbatim prose would correctly
# classify as runbook instead.
_VARIANTS = [
    ("the value report service pulls declarations from the ledger client and "
     "aggregates them by workplace registry number before rendering monthly. ",
     "USER: and where do retroactive corrections land?\n\n"
     "ASSISTANT: corrections re-open the affected month, adjust the aggregate "
     "totals, and mark the rendered report stale so the scheduler rebuilds it. "
     "The invalidation queue lives beside the renderer and drains nightly. "),
    ("walked the code again: declarations arrive via ledger_client, get grouped on "
     "the registry number of each workplace, then the monthly output is built. ",
     "USER: remind me about the premium basis fields\n\n"
     "ASSISTANT: premium basis comes in as day-count plus gross earnings per "
     "insured person; the grouping step folds those into workplace totals with "
     "ceiling checks applied per statutory bracket before anything renders. "),
    ("so to summarize once more, monthly rendering sits on top of an aggregation "
     "keyed by workplace registry, fed by whatever the client fetched. ",
     "USER: which endpoint did we settle on for backfill?\n\n"
     "ASSISTANT: backfill hits the bulk declaration listing with a period range "
     "parameter, pages through archived months, and replays each one through "
     "the ordinary ingestion path so no special-case math exists anywhere. "),
]


def _topic_text(i):
    lead, follow = _VARIANTS[i % len(_VARIANTS)]
    return (f"USER: how does the acme value report pipeline work again (run {i})\n\n"
            "ASSISTANT: re-reading the service layer.\n"
            '[tool call: Read {"file_path": "/home/u/acme/services/value_report.py"}]\n'
            '[tool call: Read {"file_path": "/home/u/acme/services/ledger_client.py"}]\n\n'
            f"ASSISTANT: {lead}\n\n{follow}\n")


def _noise_text():
    return ("USER: draft a linkedin post about our team offsite\n\n"
            "ASSISTANT: here is a draft about the offsite, the venue, and the "
            "workshop agenda. No code involved at all.\n")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return corpus.ensure_store()


def _put(store, sid, text, end, cwd="/home/u/acme"):
    d = store / "sessions" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"session_id": sid, "tool": "claude", "src": "", "end": end,
            "pinned": False, "tier": "full", "chars": len(text), "user_turns": 3,
            "start": end, "opener": text.split("\n")[0][6:], "link": None,
            "flags": {}, "ingested_at": end, "cwd": cwd,
            "project_slug": None, "pseudo_project": None}
    (d / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / f"{sid}.txt").write_text(text, encoding="utf-8")


def _plant(store, ends):
    for i, end in enumerate(ends):
        _put(store, f"topic{i}", _topic_text(i), end)
    _put(store, "noise", _noise_text(), "2026-07-24T10:00:00.000Z",
         cwd="/home/u/elsewhere")


def test_planted_topic_surfaces_noise_does_not(store):
    _plant(store, ["2026-07-25T10:00:00.000Z", "2026-07-18T10:00:00.000Z",
                   "2026-07-12T10:00:00.000Z"])
    report = detect.scout(now=NOW)
    assert report["window_days"] == 20
    assert len(report["candidates"]) == 1
    c = report["candidates"][0]
    assert c["species"] == "knowledge"
    assert c["project"] == "/home/u/acme"
    assert {m["conv_id"] for m in c["members"]} == {"topic0", "topic1", "topic2"}
    assert "/home/u/acme/services/value_report.py" in c["shared_read_targets"]
    assert c["n_in_window"] == 3 and all(m["countable"] for m in c["members"])


def test_aged_out_topic_is_not_actionable(store):
    _plant(store, ["2026-05-25T10:00:00.000Z", "2026-05-18T10:00:00.000Z",
                   "2026-05-12T10:00:00.000Z"])
    report = detect.scout(now=NOW)
    assert report["candidates"] == []
    # window off (0): the same corpus is a candidate again
    report = detect.scout(now=NOW, window_days=0)
    assert len(report["candidates"]) == 1


def test_json_contract_shape(store):
    _plant(store, ["2026-07-25T10:00:00.000Z", "2026-07-18T10:00:00.000Z",
                   "2026-07-12T10:00:00.000Z"])
    report = detect.scout(now=NOW)
    assert set(report) == {"generated_at", "window_days", "n_conversations",
                           "n_edges", "candidates"}
    c = report["candidates"][0]
    for key in ("cluster_id", "score", "species", "project", "n_in_window",
                "n_evidence_only", "why", "signals", "shared_read_targets",
                "shared_entities", "members"):
        assert key in c
    m = c["members"][0]
    for key in ("conv_id", "tool", "project", "start", "end", "n_sessions",
                "opener", "countable"):
        assert key in m
