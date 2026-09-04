"""T2: source serving — proposal-scoped, budgeted, truncation announced."""
import json
from datetime import datetime, timezone

import pytest

from kamino import cli, corpus, curate, preflight, propose

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(corpus, "maybe_sync", lambda *a, **k: None)
    # cli.main's spend-path guard calls the real preflight check; CI has no `claude` on PATH.
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    return corpus.ensure_store()


def _put(store, sid, text, link=None, tier="full"):
    d = store / "sessions" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"session_id": sid, "tool": "claude", "src": "", "chars": len(text),
            "end": "2026-07-25T10:00:00.000Z", "start": "2026-07-25T09:00:00.000Z",
            "pinned": True, "tier": tier, "user_turns": 3, "opener": "opener " + sid,
            "link": link, "flags": {}, "ingested_at": "2026-07-25T10:00:00.000Z",
            "cwd": "/home/u/acme", "project_slug": None, "pseudo_project": None}
    (d / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / f"{sid}.txt").write_text(text, encoding="utf-8")


def _seed(members=("conv1", "conv2")):
    cand = {"cluster_id": "c000", "score": 25.0, "species": "knowledge",
            "project": "/home/u/acme", "n_in_window": len(members),
            "n_evidence_only": 0, "why": ["w"], "signals": {},
            "shared_read_targets": ["/home/u/acme/services/value_report.py"],
            "shared_entities": ["/home/u/acme/services/value_report.py"],
            "members": [{"conv_id": m, "tool": "claude", "project": "/home/u/acme",
                         "start": "2026-07-20", "end": "2026-07-25", "n_sessions": 1,
                         "opener": f"op {m}", "countable": True} for m in members]}
    propose.refresh_proposals({"candidates": [cand]}, now=NOW)
    data = propose.load_proposals()
    data["records"][0]["state"] = "accepted"
    propose.save_proposals(data)
    return data["records"][0]


def test_serves_one_conversation_text(store):
    _put(store, "conv1", "USER: how does the value report work\n\nASSISTANT: like so.\n")
    _put(store, "conv2", "USER: unrelated\n")
    rec = _seed()
    out = curate.source_text(rec, "conv1")
    assert "how does the value report work" in out["text"]
    assert "unrelated" not in out["text"]
    assert out["truncated"] is False


def test_merged_conversation_concatenates_members_with_headers(store):
    _put(store, "conv1", "USER: first session\n")
    _put(store, "s2", "USER: second session of the same conversation\n",
         link={"type": "burst", "parent": "claude/conv1"})
    _put(store, "conv2", "USER: other\n")
    rec = _seed()
    out = curate.source_text(rec, "conv1")
    assert "first session" in out["text"] and "second session" in out["text"]
    assert out["text"].count("=== session ") == 2


def test_foreign_conv_id_refused(store):
    _put(store, "conv1", "USER: x\n")
    _put(store, "secret", "USER: not part of this proposal\n")
    rec = _seed()
    with pytest.raises(KeyError):
        curate.source_text(rec, "secret")


def test_huge_source_truncated_with_note(store):
    big = "USER: q\n\nASSISTANT: " + ("blah " * 30_000) + "\n"
    _put(store, "conv1", big, tier="degraded")
    _put(store, "conv2", "USER: y\n")
    rec = _seed()
    out = curate.source_text(rec, "conv1")
    assert out["truncated"] is True
    assert len(out["text"]) < len(big)
    assert "truncated" in out["note"].lower() and "--full" in out["note"]
    full = curate.source_text(rec, "conv1", full=True)
    assert full["truncated"] is False and len(full["text"]) > len(out["text"])


def test_cli_source_prints_text_and_note(store, capsys):
    big = "USER: q\n\nASSISTANT: " + ("blah " * 30_000) + "\n"
    _put(store, "conv1", big)
    _put(store, "conv2", "USER: y\n")
    rec = _seed()
    assert cli.main(["curate", rec["id"], "--source", "conv1"]) == 0
    out = capsys.readouterr()
    assert "blah" in out.out
    assert "truncated" in (out.out + out.err).lower()


def test_cli_source_unknown_id_errors(store, capsys):
    _put(store, "conv1", "USER: x\n")
    rec = _seed()
    assert cli.main(["curate", rec["id"], "--source", "nope"]) == 1
    assert "nope" in capsys.readouterr().err
