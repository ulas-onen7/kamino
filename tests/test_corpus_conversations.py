"""T7: derived conversation view — merge-links union, mirrors counted out, skips excluded."""
from kamino import corpus


def _meta(sid, tool="claude", start="2026-07-20T08:00:00.000Z", tier="full",
          link=None, flags=None, chars=5000, turns=3, cwd="/home/u/repo"):
    return {"session_id": sid, "tool": tool, "src": "", "project_slug": None,
            "cwd": cwd, "pseudo_project": None, "start": start,
            "end": start.replace("T08:", "T09:"), "chars": chars, "user_turns": turns,
            "tier": tier, "opener": "x", "link": link, "flags": flags or {},
            "pinned": False, "ingested_at": ""}


def test_burst_chain_collapses_to_one_conversation():
    metas = [
        _meta("a", start="2026-07-20T08:00:00.000Z"),
        _meta("b", start="2026-07-20T09:00:00.000Z",
              link={"type": "burst", "parent": "claude/a"}),
        _meta("c", start="2026-07-21T08:00:00.000Z",
              link={"type": "continuation", "parent": "claude/b"}),
        _meta("solo", start="2026-07-22T08:00:00.000Z"),
    ]
    convs = {c["conv_id"]: c for c in corpus.conversations(metas)}
    assert len(convs) == 2
    chain = convs["a"]
    assert chain["sessions"] == ["a", "b", "c"]
    assert chain["chars"] == 15000 and chain["user_turns"] == 9
    assert chain["start"].startswith("2026-07-20T08") and chain["end"].startswith("2026-07-21T09")
    assert chain["n_countable"] == 3
    assert convs["solo"]["sessions"] == ["solo"]


def test_mirrors_kept_for_provenance_but_not_counted():
    metas = [
        _meta("real1"),
        _meta("mir", start="2026-07-20T10:00:00.000Z", flags={"mirror": True},
              link={"type": "burst", "parent": "claude/real1"}),
    ]
    (conv,) = corpus.conversations(metas)
    assert conv["sessions"] == ["real1", "mir"]
    assert conv["n_countable"] == 1
    assert conv["mirrors"] == ["mir"]


def test_skip_tier_sessions_are_excluded_entirely():
    metas = [_meta("keep"), _meta("tiny", tier="skip")]
    convs = corpus.conversations(metas)
    assert [c["conv_id"] for c in convs] == ["keep"]


def test_degraded_member_degrades_the_conversation():
    metas = [
        _meta("a"),
        _meta("big", tier="degraded", link={"type": "burst", "parent": "claude/a"}),
    ]
    (conv,) = corpus.conversations(metas)
    assert conv["tier"] == "degraded"
