"""T3: fingerprint cache — lazy, content-keyed (chars + fp_version), purge-cleaned."""
import json
from datetime import datetime, timezone

import pytest

from kamino import corpus, detect

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)

TEXT = ("USER: please fix the pagination bug in acme-ui, see PROJ-142\n\n"
        "ASSISTANT: reading the module.\n"
        '[tool call: Read {"file_path": "/home/u/acme-ui/src/api/pagination.ts"}]\n\n'
        "USER: thanks, also the offset math\n\n"
        "ASSISTANT: the offset is computed twice, fixing.\n")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return corpus.ensure_store()


def _put(store, sid, tier="full", text=TEXT, tool="claude"):
    d = store / "sessions" / tool
    d.mkdir(parents=True, exist_ok=True)
    meta = {"session_id": sid, "tool": tool, "src": "", "end": "2026-07-25T10:00:00.000Z",
            "pinned": False, "tier": tier, "chars": len(text), "user_turns": 3,
            "start": "2026-07-25T09:00:00.000Z", "opener": "x", "link": None,
            "flags": {}, "ingested_at": "2026-07-25T10:00:00.000Z", "cwd": None,
            "project_slug": None, "pseudo_project": None}
    (d / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
    if tier != "skip":
        (d / f"{sid}.txt").write_text(text, encoding="utf-8")
    return meta


def test_first_run_computes_and_caches(store):
    metas = [_put(store, "s1")]
    fps = detect.fingerprints(metas, corpus.load_config())
    assert "/home/u/acme-ui/src/api/pagination.ts" in fps["claude/s1"]["entities"]
    assert fps["claude/s1"]["minhash"] is not None          # full tier gets a signature
    assert (store / "sessions" / "claude" / "s1.fp").exists()


def test_second_run_uses_cache(store):
    metas = [_put(store, "s1")]
    cfg = corpus.load_config()
    detect.fingerprints(metas, cfg)
    fp_path = store / "sessions" / "claude" / "s1.fp"
    cached = json.loads(fp_path.read_text(encoding="utf-8"))
    cached["entities"].append("SENTINEL-1")
    fp_path.write_text(json.dumps(cached), encoding="utf-8")
    fps = detect.fingerprints(metas, cfg)
    assert "SENTINEL-1" in fps["claude/s1"]["entities"]     # served from cache


def test_chars_mismatch_recomputes(store):
    metas = [_put(store, "s1")]
    cfg = corpus.load_config()
    detect.fingerprints(metas, cfg)
    fp_path = store / "sessions" / "claude" / "s1.fp"
    cached = json.loads(fp_path.read_text(encoding="utf-8"))
    cached["entities"].append("SENTINEL-1")
    fp_path.write_text(json.dumps(cached), encoding="utf-8")
    metas[0]["chars"] += 100                                # session grew since caching
    fps = detect.fingerprints(metas, cfg)
    assert "SENTINEL-1" not in fps["claude/s1"]["entities"]


def test_version_bump_recomputes(store, monkeypatch):
    metas = [_put(store, "s1")]
    cfg = corpus.load_config()
    detect.fingerprints(metas, cfg)
    fp_path = store / "sessions" / "claude" / "s1.fp"
    cached = json.loads(fp_path.read_text(encoding="utf-8"))
    cached["entities"].append("SENTINEL-1")
    fp_path.write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr(detect, "FP_VERSION", detect.FP_VERSION + 1)
    fps = detect.fingerprints(metas, cfg)
    assert "SENTINEL-1" not in fps["claude/s1"]["entities"]


def test_skip_tier_excluded_degraded_has_no_minhash(store):
    metas = [_put(store, "s1", tier="skip"),
             _put(store, "s2", tier="degraded")]
    fps = detect.fingerprints(metas, corpus.load_config())
    assert "claude/s1" not in fps
    assert fps["claude/s2"]["minhash"] is None
    assert fps["claude/s2"]["entities"]                     # cheap signals still extracted


def test_purge_removes_fp_cache(store):
    meta = _put(store, "old")
    meta["end"] = "2026-01-01T10:00:00.000Z"
    (store / "sessions" / "claude" / "old.json").write_text(json.dumps(meta),
                                                            encoding="utf-8")
    detect.fingerprints([meta], corpus.load_config())
    assert (store / "sessions" / "claude" / "old.fp").exists()
    corpus.purge(corpus.DEFAULTS, now=NOW)
    assert not (store / "sessions" / "claude" / "old.fp").exists()
