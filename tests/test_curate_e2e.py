"""T8: the full self-growing arc — raw sessions to a servable synthesized clone.

observe (sync ingests) -> detect (scout clusters) -> propose (accept) ->
curate (brief, read sources, draft, verify, approve) -> a clone in the registry
that `serve` and `serve --full` return, with provenance back to its sources.
"""
import json

import pytest

from kamino import cli, corpus, curate, home, preflight, propose
from kamino import registry as reg

BODIES = [
    "the value report service pulls declarations from the client and aggregates them by "
    "workplace registry number before rendering the monthly document. corrections re-open "
    "the affected month and mark the rendered report stale. ",
    "declarations arrive through the client, get grouped on the registry number of each "
    "workplace, and the monthly document is built from those totals. premium basis folds "
    "in day counts and gross earnings per insured person. ",
    "monthly rendering sits on an aggregation keyed by workplace registry, fed by whatever "
    "the client fetched. backfill replays archived periods through the ordinary ingestion "
    "path so no special-case math exists. ",
]
READS = ["/home/u/acme/services/value_report.py", "/home/u/acme/services/client.py"]


def _iso_day(n):
    """Fixture dates are relative to today so corpus retention (grace_days=45) can never age
    members out as wall-clock time passes (#22). The offsets keep every session older than the
    7-day recency bonus, matching how the original fixed July dates scored."""
    from datetime import date, timedelta
    return (date.today() - timedelta(days=44 - n)).isoformat()


def _session(i, day):
    recs = [{"type": "user", "uuid": f"u{i}0", "parentUuid": None, "cwd": "/home/u/acme",
             "timestamp": f"{day}T08:00:00.000Z",
             "message": {"role": "user",
                         "content": f"how does the value report pipeline work (run {i})"}}]
    for j, f in enumerate(READS):
        recs.append({"type": "assistant", "uuid": f"u{i}r{j}", "parentUuid": f"u{i}0",
                     "cwd": "/home/u/acme",
                     "timestamp": f"{day}T08:0{j + 1}:00.000Z",
                     "message": {"role": "assistant", "content": [
                         {"type": "tool_use", "name": "Read", "input": {"file_path": f}}]}})
    recs.append({"type": "assistant", "uuid": f"u{i}b", "parentUuid": f"u{i}0",
                 "cwd": "/home/u/acme",
                 "timestamp": f"{day}T08:05:00.000Z",
                 "message": {"role": "assistant", "content": BODIES[i % 3] * 10}})
    recs.append({"type": "user", "uuid": f"u{i}q", "parentUuid": f"u{i}b",
                 "cwd": "/home/u/acme",
                 "timestamp": f"{day}T08:06:00.000Z",
                 "message": {"role": "user", "content": f"thanks, part {i}"}})
    return "\n".join(json.dumps(r) for r in recs)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAMINO_CLAUDE_PROJECTS", str(tmp_path / "cc"))
    monkeypatch.setenv("KAMINO_CODEX_SESSIONS", str(tmp_path / "cx"))
    monkeypatch.setattr(corpus, "load_config",
                        lambda: {**corpus.DEFAULTS, "window_days": 0})
    # cli.main's spend-path guard calls the real preflight check; CI has no `claude` on PATH.
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    proj = tmp_path / "cc" / "-home-u-acme"
    proj.mkdir(parents=True)
    for i, day in enumerate((_iso_day(25), _iso_day(18), _iso_day(12))):
        (proj / f"aaaa{i}111-2222-3333-4444-55556666777{i}.jsonl").write_text(
            _session(i, day), encoding="utf-8")
    return tmp_path


SYNTHESIS = """# AcmeCo value report pipeline

## What this covers
How the monthly value report is produced, and how corrections and backfill flow through it.

## Concrete facts
- /home/u/acme/services/value_report.py aggregates declarations by workplace registry
  number and renders the monthly document.
- /home/u/acme/services/client.py fetches the declarations.
- Premium basis arrives as day counts plus gross earnings per insured person.

## Decisions and why
Corrections re-open the affected month instead of patching totals, so the rendered report
is marked stale and rebuilt. Backfill replays archived periods through the ordinary
ingestion path, which is why no special-case math exists.

## Gotchas
A stale report is only rebuilt on the scheduler pass.

## Open ends
Backfill of archived periods was never verified end to end.
"""


def test_full_arc_to_a_servable_clone(world, capsys, tmp_path):
    # observe + detect: sync ingests and the detector proposes
    rep = corpus.sync()
    assert rep["ingested"] == 3 and rep["proposals"]["created"] == ["p001"]

    # propose: the user accepts
    assert cli.main(["accept", "p001"]) == 0
    capsys.readouterr()

    # curate: the brief names the recipe and every source
    assert cli.main(["curate", "p001"]) == 0
    brief = capsys.readouterr().out
    assert "knowledge" in brief and "--source" in brief
    rec = curate.get_record("p001")
    source_ids = [m["conv_id"] for m in rec["evidence"]["members"]]
    assert len(source_ids) == 3

    # the agent reads each source in isolation
    for cid in source_ids:
        assert cli.main(["curate", "p001", "--source", cid]) == 0
        assert "value report" in capsys.readouterr().out

    # a draft that invents a file is rejected
    bad = tmp_path / "bad.md"
    bad.write_text(SYNTHESIS.replace("## Gotchas",
                                     "- /home/u/acme/services/imaginary.py retries\n\n"
                                     "## Gotchas"), encoding="utf-8")
    assert cli.main(["curate", "p001", "--draft", str(bad)]) == 1
    assert "imaginary.py" in capsys.readouterr().out

    # the corrected draft passes
    good = tmp_path / "good.md"
    good.write_text(SYNTHESIS, encoding="utf-8")
    assert cli.main(["curate", "p001", "--draft", str(good)]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "--approve" in out

    # the USER approves
    assert cli.main(["curate", "p001", "--approve"]) == 0
    assert "registered" in capsys.readouterr().out.lower()

    # the clone is real, servable, and carries its lineage
    regp = str(home.registry_path())
    roster = reg.load_roster(regp)
    card = next(c for c in roster if c["provenance"])
    assert card["origin"] == "synthesis" and card["class"] == "knowledge"
    assert sorted(card["provenance"]["source_conversations"]) == sorted(source_ids)
    assert card["provenance"]["proposal"] == "p001"

    assert cli.main(["serve", card["id"], "--isolated"]) == 0
    served = capsys.readouterr().out
    assert "## Concrete facts" in served and "## Sources" in served and source_ids[0] in served
    assert cli.main(["serve", card["id"], "--full", "--isolated"]) == 0
    full = capsys.readouterr().out
    assert "## Sources" in full and source_ids[0] in full

    # the proposal is closed and nothing re-proposes it
    saved = propose.load_proposals()["records"][0]
    assert saved["state"] == "curated" and saved["clone_id"] == card["id"]
    rep = corpus.sync(full=True)
    assert rep["proposals"]["created"] == [] and rep["proposals"]["suppressed"] == 1

    # and the clone can be re-briefed from its own card later
    assert cli.main(["curate", card["id"], "--rebrief"]) == 0
    assert source_ids[0] in capsys.readouterr().out
