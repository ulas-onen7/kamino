"""T7: the push experience end to end — detect, surface, decide, remember.

One test walking the whole Phase 3 loop on a synthetic corpus: work happens ->
sync detects repetition -> roster surfaces one proposal -> the user declines ->
the topic regrows and stays silent forever -> a second topic appears -> the user
accepts -> its evidence is pinned and handed off as a pack.
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from kamino import cli, corpus, propose

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

ACME_BODIES = [
    "the value report service pulls declarations from the client and aggregates them "
    "by workplace registry number before rendering the monthly output. corrections "
    "re-open the affected month and mark the rendered report stale. ",
    "declarations arrive through the client, get grouped on the registry number of "
    "each workplace, and the monthly output is built from those totals. premium basis "
    "folds in day counts and gross earnings per insured person. ",
    "monthly rendering sits on an aggregation keyed by workplace registry, fed by "
    "whatever the client fetched. backfill replays archived periods through the "
    "ordinary ingestion path so no special-case math exists. ",
]

# A genuinely different topic needs genuinely different prose — reusing one body set
# across two projects makes the fixtures literally identical content, and the detector
# is right to glue them.
WEBAPP_BODIES = [
    "pagination offsets get computed twice in the list endpoint: once in the route "
    "handler and again inside the cursor helper, so the second page skips a row. ",
    "the cursor helper owns offset math; the route handler must pass the raw page "
    "token through untouched or rows vanish between pages. ",
    "list responses carry a next-page token derived from the last row id, which is "
    "why sorting changes silently break the cursor contract. ",
]


def _iso_day(n):
    """Fixture dates are relative to today so corpus retention (grace_days=45) can never age
    members out as wall-clock time passes (#22). The offsets keep every session older than the
    7-day recency bonus, matching how the original fixed July dates scored."""
    return (date.today() - timedelta(days=44 - n)).isoformat()


def _jsonl(project, files, body, day, tag):
    recs = [{"type": "user", "uuid": f"{tag}0", "parentUuid": None, "cwd": project,
             "timestamp": f"{day}T08:00:00.000Z",
             "message": {"role": "user",
                         "content": f"walk me through {project} once more ({tag})"}}]
    for i, f in enumerate(files):
        recs.append({"type": "assistant", "uuid": f"{tag}r{i}",
                     "parentUuid": f"{tag}0", "cwd": project,
                     "timestamp": f"{day}T08:0{i + 1}:00.000Z",
                     "message": {"role": "assistant", "content": [
                         {"type": "tool_use", "name": "Read",
                          "input": {"file_path": f}}]}})
    recs.append({"type": "assistant", "uuid": f"{tag}b", "parentUuid": f"{tag}0",
                 "cwd": project, "timestamp": f"{day}T08:05:00.000Z",
                 # x10 keeps every body set clear of the real 1500-char skip floor
                 "message": {"role": "assistant", "content": body * 10}})
    recs.append({"type": "user", "uuid": f"{tag}q", "parentUuid": f"{tag}b",
                 "cwd": project, "timestamp": f"{day}T08:06:00.000Z",
                 "message": {"role": "user", "content": f"thanks, and about {tag}"}})
    return "\n".join(json.dumps(r) for r in recs)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAMINO_CLAUDE_PROJECTS", str(tmp_path / "cc"))
    monkeypatch.setenv("KAMINO_CODEX_SESSIONS", str(tmp_path / "cx"))
    # window off: the fixture offsets (20-32 days ago) sit outside the 20-day detection window
    monkeypatch.setattr(corpus, "load_config",
                        lambda: {**corpus.DEFAULTS, "window_days": 0})
    return tmp_path / "cc"


def _plant(cc, slug, project, files, bodies, n=3, start=0):
    d = cc / slug
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        tag = f"{slug[-4:]}{start + i}"
        (d / f"{tag}1111-2222-3333-4444-55556666777{i}.jsonl").write_text(
            _jsonl(project, files, bodies[i % 3], _iso_day(12 + start + i * 3), tag),
            encoding="utf-8")


ACME = ["/home/u/acme/services/value_report.py", "/home/u/acme/services/client.py"]
WEBAPP = ["/home/u/webapp/api/pagination.ts", "/home/u/webapp/api/helpers.ts"]


def test_the_push_experience(world, capsys):
    # 1. the user works: three conversations re-derive the same acme knowledge
    _plant(world, "-home-u-acme", "/home/u/acme", ACME, ACME_BODIES)
    rep = corpus.sync()
    assert rep["ingested"] == 3
    assert rep["proposals"]["created"] == ["p001"]

    # 2. Kamino speaks first: the next roster call carries exactly one proposal
    assert cli.main(["roster"]) == 0
    roster = json.loads(capsys.readouterr().out)
    assert "kamino_proposal" in roster[-1]
    pitch = roster[-1]["kamino_proposal"]
    assert pitch["id"] == "p001" and "acme" in pitch["summary"]
    assert "kamino accept p001" in pitch["how_to_answer"]

    # ...and does not speak again today
    assert cli.main(["roster"]) == 0
    assert not any("kamino_proposal" in e
                   for e in json.loads(capsys.readouterr().out))

    # 3. the user declines
    assert cli.main(["decline", "p001"]) == 0
    capsys.readouterr()
    assert cli.main(["proposals"]) == 0
    assert "p001" not in capsys.readouterr().out

    # 4. the topic keeps recurring — and stays silent forever
    _plant(world, "-home-u-acme", "/home/u/acme", ACME, ACME_BODIES,
           n=2, start=3)
    rep = corpus.sync()
    assert rep["ingested"] == 2
    assert rep["proposals"]["created"] == [] and rep["proposals"]["suppressed"] >= 1
    later = NOW.replace(day=28)
    assert propose.surfaced(now=later) is None

    # 5. a DIFFERENT topic appears and is proposed on its own merits
    _plant(world, "-home-u-webapp", "/home/u/webapp", WEBAPP, WEBAPP_BODIES)
    rep = corpus.sync()
    assert rep["proposals"]["created"] == ["p002"]

    # 6. the user accepts: evidence pinned, pack handed to curation
    assert cli.main(["accept", "p002"]) == 0
    out = capsys.readouterr().out
    pack = json.loads(out[out.index("{"):])
    assert pack["proposal_id"] == "p002"
    assert pack["project"] == "/home/u/webapp"
    assert len(pack["members"]) >= 3
    pinned = [m for m in corpus.load_metas() if m.get("pinned")]
    assert len(pinned) >= 3
    assert all(m["cwd"] == "/home/u/webapp" for m in pinned)

    # 7. accepted topics do not come back either
    rep = corpus.sync(full=True)
    assert rep["proposals"]["created"] == []
    states = {r["id"]: r["state"] for r in propose.load_proposals()["records"]}
    assert states == {"p001": "declined", "p002": "accepted"}
