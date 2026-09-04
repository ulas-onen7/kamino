"""T5: lineage links — fork (same-file-first parentUuid), burst, continuation."""
import json

import pytest

from kamino import corpus

CONT = corpus.DEFAULTS["continuation_marker"]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return tmp_path / "corpus"


def _meta(sid, tool="claude", cwd="/home/u/repo", start="2026-07-20T08:00:00.000Z",
          opener="do the thing", src=""):
    return {"session_id": sid, "tool": tool, "src": src, "project_slug": None,
            "cwd": cwd, "pseudo_project": None, "start": start, "end": start,
            "chars": 5000, "user_turns": 3, "tier": "full", "opener": opener,
            "link": None, "flags": {}, "pinned": False, "ingested_at": ""}


def _session_file(tmp_path, name, records):
    f = tmp_path / f"{name}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(f)


def test_fork_same_file_parent_is_not_a_fork(tmp_path):
    src = _session_file(tmp_path, "self", [
        {"type": "attachment", "uuid": "hook1"},
        {"type": "user", "uuid": "u1", "parentUuid": "hook1",
         "message": {"role": "user", "content": "hi"}},
    ])
    assert corpus.first_parent_uuid(src) == "hook1"
    assert "hook1" in corpus.file_uuids(src)
    links = corpus.link_sessions([_meta("self", src=src)], corpus.DEFAULTS)
    assert links == {}


def test_fork_cross_file_parent_links(tmp_path):
    parent_src = _session_file(tmp_path, "par", [
        {"type": "user", "uuid": "p1", "parentUuid": None,
         "message": {"role": "user", "content": "origin"}},
    ])
    child_src = _session_file(tmp_path, "chi", [
        {"type": "assistant", "uuid": "c1", "parentUuid": "p1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "cont"}]}},
    ])
    metas = [_meta("par", src=parent_src, start="2026-07-19T08:00:00.000Z"),
             _meta("chi", src=child_src, start="2026-07-20T08:00:00.000Z")]
    links = corpus.link_sessions(metas, corpus.DEFAULTS)
    assert links["claude/chi"] == {"type": "fork", "parent": "claude/par"}


def test_burst_links_to_earliest():
    metas = [_meta("b1", start="2026-07-20T08:00:00.000Z", opener="learn @x.py run it"),
             _meta("b2", start="2026-07-20T09:00:00.000Z", opener="learn @x.py run it"),
             _meta("b3", start="2026-07-20T10:00:00.000Z", opener="learn @x.py run it"),
             _meta("other", start="2026-07-20T10:00:00.000Z", opener="different task")]
    links = corpus.link_sessions(metas, corpus.DEFAULTS)
    assert links["claude/b2"] == {"type": "burst", "parent": "claude/b1"}
    assert links["claude/b3"] == {"type": "burst", "parent": "claude/b1"}
    assert "claude/other" not in links


def test_continuation_links_to_nearest_preceding_within_gap():
    metas = [_meta("root", start="2026-07-20T08:00:00.000Z", opener="build the feature"),
             _meta("cont", start="2026-07-21T08:00:00.000Z", opener=CONT + " that ran out"),
             _meta("late", start="2026-07-27T08:00:00.000Z", opener=CONT + " that ran out")]
    links = corpus.link_sessions(metas, corpus.DEFAULTS)
    assert links["claude/cont"] == {"type": "continuation", "parent": "claude/root"}
    assert "claude/late" not in links          # 6 days > 3-day gap from cont


def test_burst_wins_over_continuation():
    op = CONT + " summary follows"
    metas = [_meta("r", start="2026-07-20T07:00:00.000Z", opener="origin work"),
             _meta("c1", start="2026-07-20T08:00:00.000Z", opener=op),
             _meta("c2", start="2026-07-20T09:00:00.000Z", opener=op)]
    links = corpus.link_sessions(metas, corpus.DEFAULTS)
    assert links["claude/c2"] == {"type": "burst", "parent": "claude/c1"}   # burst first
    assert links["claude/c1"] == {"type": "continuation", "parent": "claude/r"}
