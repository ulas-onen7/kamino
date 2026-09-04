"""T9: sync orchestration — incremental, throttled, purge-integrated; observe CLI."""
import json

import pytest

from kamino import cli, corpus


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    cc = tmp_path / "cc"
    cx = tmp_path / "cx"
    monkeypatch.setenv("KAMINO_CLAUDE_PROJECTS", str(cc))
    monkeypatch.setenv("KAMINO_CODEX_SESSIONS", str(cx))
    proj = cc / "-home-u-myrepo"
    proj.mkdir(parents=True)
    body = "\n".join(
        json.dumps({"type": "user", "uuid": f"u{i}", "parentUuid": None,
                    "timestamp": f"2026-07-25T08:0{i}:00.000Z",
                    "message": {"role": "user", "content": f"question {i} " * 80}})
        for i in range(3))
    (proj / "aaaa1111-2222-3333-4444-555566667777.jsonl").write_text(body, encoding="utf-8")
    return proj


def test_sync_ingests_then_noops(world):
    r1 = corpus.sync()
    assert r1["ingested"] == 1
    r2 = corpus.sync()
    assert r2["ingested"] == 0          # cursor: nothing changed
    (world / "aaaa1111-2222-3333-4444-555566667777.jsonl").write_text(
        (world / "aaaa1111-2222-3333-4444-555566667777.jsonl").read_text(encoding="utf-8")
        + "\n" + json.dumps({"type": "user", "uuid": "u9", "parentUuid": None,
                             "timestamp": "2026-07-25T09:00:00.000Z",
                             "message": {"role": "user", "content": "more " * 100}}),
        encoding="utf-8")
    r3 = corpus.sync()
    assert r3["ingested"] == 1          # appended source re-ingests


def test_maybe_sync_throttles(world):
    assert corpus.maybe_sync() is not None      # first: runs
    assert corpus.maybe_sync() is None          # within throttle window: no-op
    cfg = corpus.load_config()
    cfg["sync_throttle_minutes"] = 0
    assert corpus.maybe_sync(cfg) is not None   # throttle disabled: runs


def test_status_counts(world):
    corpus.sync()
    st = corpus.status()
    assert st["sessions"] == 1 and st["conversations"] == 1
    assert st["tiers"] == {"full": 1}
    assert st["last_sync"]
    assert st["store_bytes"] > 0


def test_cli_observe_status_and_sync(world, capsys):
    assert cli.main(["observe", "sync"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["ingested"] == 1
    assert cli.main(["observe", "status"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["sessions"] == 1
