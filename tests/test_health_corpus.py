import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import health   # noqa: E402


def _by_check(findings):
    return {f["check"]: f for f in findings}


def test_corpus_checks_are_silent_when_observation_is_off(monkeypatch):
    monkeypatch.setenv("KAMINO_OBSERVE", "0")
    assert health.check_env("corpus") == []


def test_c1_warns_when_the_corpus_was_never_synced(monkeypatch):
    monkeypatch.setenv("KAMINO_OBSERVE", "1")
    from kamino import corpus
    monkeypatch.setattr(corpus, "load_cursor", lambda: {"last_sync": ""})
    f = _by_check(health.check_env("corpus"))["C1"]
    assert f["name"] == "never-synced" and f["severity"] == "warn"


def test_c1_warns_when_the_last_sync_is_stale(monkeypatch):
    monkeypatch.setenv("KAMINO_OBSERVE", "1")
    from kamino import corpus
    old = (datetime.now(timezone.utc) - timedelta(days=health.STALE_SYNC_DAYS + 1)).isoformat()
    monkeypatch.setattr(corpus, "load_cursor", lambda: {"last_sync": old})
    assert _by_check(health.check_env("corpus"))["C1"]["name"] == "sync-stale"


def test_c1_is_silent_on_a_fresh_sync(monkeypatch):
    monkeypatch.setenv("KAMINO_OBSERVE", "1")
    from kamino import corpus
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(corpus, "load_cursor", lambda: {"last_sync": now})
    assert "C1" not in _by_check(health.check_env("corpus"))


def test_c3_warns_when_proposal_surfacing_raises(monkeypatch):
    monkeypatch.setenv("KAMINO_OBSERVE", "1")
    from kamino import propose

    def boom():
        raise RuntimeError("proposals file is a directory")
    monkeypatch.setattr(propose, "surfaced", boom)
    f = _by_check(health.check_env("corpus"))["C3"]
    assert f["severity"] == "warn" and "directory" in f["detail"]


def test_c2_errors_on_an_unreadable_corpus_store(monkeypatch, tmp_path):
    monkeypatch.setenv("KAMINO_OBSERVE", "1")
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    from kamino import corpus
    corpus.ensure_store()
    (corpus.corpus_root() / "cursor.json").write_text("{not json", encoding="utf-8")
    f = _by_check(health.check_env("corpus"))["C2"]
    assert f["severity"] == "error" and f["fix"]


def test_c2_is_silent_on_a_healthy_store(monkeypatch, tmp_path):
    monkeypatch.setenv("KAMINO_OBSERVE", "1")
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    from kamino import corpus
    corpus.ensure_store()
    assert "C2" not in _by_check(health.check_env("corpus"))
