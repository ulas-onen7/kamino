"""T1: corpus store skeleton — root resolution, 0700 perms, config defaults + overrides."""
import json
import stat

import pytest

from tests.conftest import posix_perms

from kamino import corpus


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return tmp_path / "corpus"


def test_root_honors_env(store):
    assert corpus.corpus_root() == store


@posix_perms
def test_ensure_creates_dirs_with_0700(store):
    root = corpus.ensure_store()
    assert (root / "sessions").is_dir()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


@posix_perms
def test_config_file_is_0600(store):
    corpus.ensure_store()
    assert stat.S_IMODE((store / "config.json").stat().st_mode) == 0o600


def test_defaults_materialize_and_user_edits_override(store):
    cfg = corpus.load_config()
    assert cfg["grace_days"] == 45
    assert cfg["window_days"] == 20
    assert cfg["degrade_chars"] == 120_000
    assert cfg["misfiled_home_ratio"] == 0.10
    path = store / "config.json"
    user = json.loads(path.read_text(encoding="utf-8"))
    user["grace_days"] = 7
    path.write_text(json.dumps(user), encoding="utf-8")
    cfg2 = corpus.load_config()
    assert cfg2["grace_days"] == 7          # user override wins
    assert cfg2["skip_min_chars"] == 1500   # untouched defaults survive
