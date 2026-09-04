"""T6: regenerability — a synthesized clone can be re-briefed from its card alone."""
import json

import pytest

from kamino import cli, corpus, curate, home, preflight, propose
from kamino import registry as reg

from tests.test_curate_verify import GOOD_DRAFT, _put, _seed


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(corpus, "maybe_sync", lambda *a, **k: None)
    # cli.main's spend-path guard calls the real preflight check; CI has no `claude` on PATH.
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    return corpus.ensure_store()


def _approved(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    return rec, curate.approve(rec)


def test_rebrief_from_provenance_lists_the_same_sources(store):
    rec, out = _approved(store)
    text = curate.rebrief(out["clone_id"])
    assert out["clone_id"] in text
    assert "knowledge" in text
    for conv in ("conv1", "conv2"):
        assert conv in text
    assert "kamino curate" in text                 # the same submit path as a fresh brief


def test_rebrief_reports_sources_lost_to_retention(store):
    rec, out = _approved(store)
    (corpus.corpus_root() / "sessions" / "claude" / "conv2.txt").unlink()
    (corpus.corpus_root() / "sessions" / "claude" / "conv2.json").unlink()
    text = curate.rebrief(out["clone_id"])
    assert "conv2" in text
    assert "gone" in text.lower() or "missing" in text.lower()
    assert "conv1" in text                         # surviving sources still usable


def test_rebrief_reports_new_sources_since_curation(store):
    rec, out = _approved(store)
    # the topic kept recurring after the clone was built
    _put(store, "conv9", "USER: the value report again\n\nASSISTANT: "
                         "/home/u/acme/services/value_report.py still aggregates.\n")
    text = curate.rebrief(out["clone_id"])
    assert "conv9" in text
    assert "new since" in text.lower() or "not yet" in text.lower()


def test_rebrief_needs_a_synthesized_clone(store):
    reg.recruit_body("USER: hi\n", str(home.registry_path()), "handmade", "blurb")
    with pytest.raises(curate.CurationError):
        curate.rebrief("handmade")
    with pytest.raises(KeyError):
        curate.rebrief("nope")


def test_cli_rebrief(store, capsys):
    rec, out = _approved(store)
    assert cli.main(["curate", out["clone_id"], "--rebrief"]) == 0
    assert "conv1" in capsys.readouterr().out


def test_cli_rebrief_unknown_clone_errors(store, capsys):
    assert cli.main(["curate", "nope", "--rebrief"]) == 1
    assert "nope" in capsys.readouterr().err
