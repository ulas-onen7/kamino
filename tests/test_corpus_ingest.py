"""T3: ingest write path — flatten, tier, scrub, atomic 0600 writes, full ids."""
import json
import stat

import pytest

from tests.conftest import posix_perms

from kamino import corpus


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return tmp_path / "corpus"


def _claude_session(tmp_path, name="aaaa1111-2222-3333-4444-555566667777", turns=3):
    f = tmp_path / f"{name}.jsonl"
    lines = [json.dumps({"type": "queue-operation",
                         "timestamp": "2026-07-20T08:00:00.000Z"})]
    for i in range(turns):
        lines.append(json.dumps({"type": "user", "uuid": f"u{i}", "parentUuid": None,
                                 "timestamp": f"2026-07-20T08:0{i+1}:00.000Z",
                                 "message": {"role": "user",
                                             "content": f"please fix bug number {i} SECRET"}}))
        lines.append(json.dumps({"type": "assistant", "uuid": f"a{i}", "parentUuid": f"u{i}",
                                 "timestamp": f"2026-07-20T08:0{i+1}:30.000Z",
                                 "message": {"role": "assistant",
                                             "content": [{"type": "text",
                                                          "text": "done, fixed it properly " * 30}]}}))
    f.write_text("\n".join(lines), encoding="utf-8")
    return {"tool": "claude", "src": str(f), "project_slug": "-home-u-myrepo",
            "mtime": f.stat().st_mtime, "size": f.stat().st_size}


def _codex_session(tmp_path):
    f = tmp_path / "rollout-2026-07-20-0199aaaa-bbbb-cccc-dddd-eeeeffff0000.jsonl"
    lines = [
        json.dumps({"timestamp": "2026-07-20T09:00:00.000Z", "type": "session_meta",
                    "payload": {"id": "0199aaaa-bbbb-cccc-dddd-eeeeffff0000",
                                "cwd": "/home/u/myrepo"}}),
        json.dumps({"timestamp": "2026-07-20T09:01:00.000Z", "type": "response_item",
                    "payload": {"type": "message", "role": "user",
                                "content": [{"type": "input_text",
                                             "text": "review the webhook " * 40}]}}),
        json.dumps({"timestamp": "2026-07-20T09:02:00.000Z", "type": "response_item",
                    "payload": {"type": "message", "role": "user",
                                "content": [{"type": "input_text",
                                             "text": "second question " * 40}]}}),
    ]
    f.write_text("\n".join(lines), encoding="utf-8")
    return {"tool": "codex", "src": str(f), "project_slug": None,
            "mtime": f.stat().st_mtime, "size": f.stat().st_size}


@posix_perms
def test_ingest_claude_writes_text_and_meta(store, tmp_path):
    cfg = corpus.load_config()
    cfg["skip_min_chars"] = 100
    meta = corpus.ingest(_claude_session(tmp_path), cfg)
    assert meta["session_id"] == "aaaa1111-2222-3333-4444-555566667777"
    assert meta["tool"] == "claude" and meta["tier"] == "full"
    assert meta["user_turns"] == 3
    assert meta["start"] == "2026-07-20T08:00:00.000Z"
    assert meta["end"].startswith("2026-07-20T08:03")
    assert meta["pinned"] is False and meta["link"] is None
    txt = store / "sessions" / "claude" / f"{meta['session_id']}.txt"
    js = store / "sessions" / "claude" / f"{meta['session_id']}.json"
    assert txt.read_text(encoding="utf-8").startswith("USER: please fix bug")
    assert stat.S_IMODE(txt.stat().st_mode) == 0o600
    assert stat.S_IMODE(js.stat().st_mode) == 0o600
    assert json.loads(js.read_text(encoding="utf-8")) == meta


def test_ingest_applies_scrub_hook_before_write(store, tmp_path):
    cfg = corpus.load_config()
    cfg["skip_min_chars"] = 100
    meta = corpus.ingest(_claude_session(tmp_path), cfg,
                         scrub=lambda t: t.replace("SECRET", "[redacted]"))
    txt = (store / "sessions" / "claude" / f"{meta['session_id']}.txt").read_text(encoding="utf-8")
    assert "SECRET" not in txt and "[redacted]" in txt


def test_ingest_codex_full_id_and_cwd(store, tmp_path):
    cfg = corpus.load_config()
    cfg["skip_min_chars"] = 100
    meta = corpus.ingest(_codex_session(tmp_path), cfg)
    assert meta["session_id"] == "0199aaaa-bbbb-cccc-dddd-eeeeffff0000"
    assert meta["cwd"] == "/home/u/myrepo"
    assert meta["tier"] == "full"
    assert (store / "sessions" / "codex" / f"{meta['session_id']}.txt").exists()


def test_skip_tier_writes_meta_only(store, tmp_path):
    cfg = corpus.load_config()   # real skip thresholds: 2 turns / 1500 chars
    src = _claude_session(tmp_path, name="bbbb1111-2222-3333-4444-555566667777", turns=1)
    meta = corpus.ingest(src, cfg)
    assert meta["tier"] == "skip"
    assert not (store / "sessions" / "claude" / f"{meta['session_id']}.txt").exists()
    assert (store / "sessions" / "claude" / f"{meta['session_id']}.json").exists()


def test_reingest_preserves_pin_and_manual_state(store, tmp_path):
    """Pins are the ONLY thing standing between accepted-proposal evidence and the
    grace purge. An active session is re-ingested every time it grows, so a fresh
    meta must inherit the pin instead of resetting it (found live: the pinned count
    drifted 33 -> 31 while sessions kept growing)."""
    src = _claude_session(tmp_path)
    meta = corpus.ingest(src, corpus.DEFAULTS)
    p = store / "sessions" / "claude" / f"{meta['session_id']}.json"
    saved = json.loads(p.read_text(encoding="utf-8"))
    saved["pinned"] = True
    p.write_text(json.dumps(saved), encoding="utf-8")

    # the session grows and is re-ingested
    with open(src["src"], "a", encoding="utf-8") as f:
        f.write("\n" + json.dumps({"type": "user", "uuid": "u9", "parentUuid": None,
                                   "timestamp": "2026-07-20T09:00:00.000Z",
                                   "message": {"role": "user",
                                               "content": "one more thing " * 40}}))
    again = corpus.ingest(src, corpus.DEFAULTS)
    assert again["pinned"] is True
    assert json.loads(p.read_text(encoding="utf-8"))["pinned"] is True
    assert again["chars"] > meta["chars"]          # it really did re-ingest


def test_reingest_defaults_to_unpinned_for_new_sessions(store, tmp_path):
    meta = corpus.ingest(_claude_session(tmp_path), corpus.DEFAULTS)
    assert meta["pinned"] is False
