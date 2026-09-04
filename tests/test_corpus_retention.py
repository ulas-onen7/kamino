"""T8: retention purge — grace cutoff, pin exemption, cursor semantics."""
import json
from datetime import datetime, timezone

import pytest

from kamino import corpus

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return corpus.ensure_store()


def _put(store, sid, end, pinned=False, src=""):
    d = store / "sessions" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"session_id": sid, "tool": "claude", "src": src, "end": end,
            "pinned": pinned, "tier": "full", "chars": 5000, "user_turns": 3,
            "start": end, "opener": "x", "link": None, "flags": {},
            "ingested_at": end, "cwd": None, "project_slug": None,
            "pseudo_project": None}
    (d / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / f"{sid}.txt").write_text("USER: hi", encoding="utf-8")
    return meta


def test_aged_unpinned_purged_fresh_kept(store):
    _put(store, "old", "2026-05-01T10:00:00.000Z")            # 86 days old
    _put(store, "fresh", "2026-07-20T10:00:00.000Z")          # 6 days old
    report = corpus.purge(corpus.DEFAULTS, now=NOW)
    assert report["purged"] == ["old"]
    assert not (store / "sessions" / "claude" / "old.json").exists()
    assert not (store / "sessions" / "claude" / "old.txt").exists()
    assert (store / "sessions" / "claude" / "fresh.txt").exists()


def test_pinned_survives_forever(store):
    _put(store, "cited", "2026-01-01T10:00:00.000Z", pinned=True)
    report = corpus.purge(corpus.DEFAULTS, now=NOW)
    assert report["purged"] == [] and report["kept_pinned"] == 1
    assert (store / "sessions" / "claude" / "cited.json").exists()


def test_cursor_entry_dropped_only_when_source_gone(store, tmp_path):
    alive_src = tmp_path / "alive.jsonl"
    alive_src.write_text("{}", encoding="utf-8")
    _put(store, "src-alive", "2026-05-01T10:00:00.000Z", src=str(alive_src))
    _put(store, "src-gone", "2026-05-01T10:00:00.000Z", src=str(tmp_path / "gone.jsonl"))
    cursor = corpus.load_cursor()
    cursor["sources"][str(alive_src)] = {"mtime": 1.0, "size": 2, "session_id": "src-alive"}
    cursor["sources"][str(tmp_path / "gone.jsonl")] = {"mtime": 1.0, "size": 2,
                                                       "session_id": "src-gone"}
    corpus.save_cursor(cursor)
    report = corpus.purge(corpus.DEFAULTS, now=NOW)
    assert sorted(report["purged"]) == ["src-alive", "src-gone"]
    cursor2 = corpus.load_cursor()
    # living source stays in the cursor: seen-and-purged, no re-ingest churn;
    # if the file ever changes (resumed session), re-ingest is CORRECT
    assert str(alive_src) in cursor2["sources"]
    assert str(tmp_path / "gone.jsonl") not in cursor2["sources"]
