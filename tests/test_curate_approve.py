"""T5: approve — a synthesized clone enters the registry with full provenance."""
import json
from datetime import datetime, timezone

import pytest

from kamino import cli, corpus, curate, home, preflight, propose
from kamino import registry as reg

from tests.test_curate_verify import GOOD_DRAFT, SRC1, SRC2, _put, _seed  # reuse


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(corpus, "maybe_sync", lambda *a, **k: None)
    # cli.main's spend-path guard calls the real preflight check; CI has no `claude` on PATH.
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    return corpus.ensure_store()


def _registry():
    return str(home.registry_path())


def test_approve_registers_clone_with_provenance(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    out = curate.approve(rec)
    roster = reg.load_roster(_registry())
    card = next(c for c in roster if c["id"] == out["clone_id"])
    assert card["origin"] == "synthesis"
    assert card["class"] == "knowledge"
    prov = card["provenance"]
    assert prov["kind"] == "synthesis" and prov["proposal"] == rec["id"]
    assert prov["recipe"] == "knowledge"
    assert prov["source_conversations"] == ["conv1", "conv2"]
    assert prov["source_sessions"] == ["conv1", "conv2"]
    assert prov["verification"]["ok"] is True
    # and it survives on disk, readable without the loader
    text = open(f"{_registry()}/cards/{out['clone_id']}.md", encoding="utf-8").read()
    assert rec["id"] in text and "conv1" in text


def test_blob_carries_synthesis_plus_sources_appendix(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    out = curate.approve(rec)
    blob = open(f"{_registry()}/{out['snapshot_ref']}", encoding="utf-8").read()
    assert "## What this covers" in blob            # the synthesis itself
    assert "## Sources" in blob                     # the appendix
    assert "conv1" in blob and "op conv1" in blob   # per-source provenance rows


def test_blob_contains_the_draft_text(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    out = curate.approve(rec)
    clone_id = out["clone_id"]
    blob = [c for c in reg.load_roster(_registry()) if c["id"] == clone_id][0]["blob"]
    assert "Ticket PROJ-142 tracks this work." in open(blob, encoding="utf-8").read()


def test_proposal_marked_curated_and_pins_kept(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    out = curate.approve(rec)
    saved = propose.load_proposals()["records"][0]
    assert saved["state"] == "curated"
    assert saved["clone_id"] == out["clone_id"]
    assert all(m["pinned"] for m in corpus.load_metas())     # sources still protected


def test_approve_refuses_without_a_draft(store):
    rec = _seed(store)
    with pytest.raises(curate.CurationError):
        curate.approve(rec)


def test_approve_refuses_a_failing_draft_unless_forced(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT.replace("## Gotchas", "## Notes"))
    with pytest.raises(curate.CurationError):
        curate.approve(rec)
    out = curate.approve(rec, force=True)
    assert out["clone_id"]


def test_approve_refuses_a_clone_id_collision_even_when_forced(store):
    """I4: --force forgives a failing draft, never a collision with an unrelated clone --
    that clone was never part of this proposal's evidence, and force used to let a
    thin-draft retry silently destroy it (the CLI's own error message for the unrelated
    failure told the user to re-run with --force)."""
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    regp = _registry()
    # a different clone already occupies the id this proposal would default to
    reg.recruit_body("unrelated transcript, not part of this proposal's evidence.",
                     regp, "acme-knowledge",
                     "An unrelated clone that happens to sit at the same default id.")
    with pytest.raises(curate.CurationError):
        curate.approve(rec)
    with pytest.raises(curate.CurationError):      # force does NOT bypass this one
        curate.approve(rec, force=True)
    card = next(c for c in reg.load_roster(regp) if c["id"] == "acme-knowledge")
    assert card["origin"] != "synthesis"            # the unrelated clone is untouched


def test_approve_replaces_its_own_earlier_clone_without_force(store):
    """The legitimate overwrite this proposal's own re-curation needs: no --force at all,
    because the occupying card's provenance names this same proposal."""
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    first = curate.approve(rec)
    curate.submit_draft(rec, GOOD_DRAFT)
    second = curate.approve(rec)
    assert second["clone_id"] == first["clone_id"]


def test_custom_name_used_as_clone_id(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    out = curate.approve(rec, name="acme value pipeline")
    assert out["clone_id"] == "acme-value-pipeline"


def test_cli_approve_end_to_end(store, tmp_path, capsys):
    rec = _seed(store)
    f = tmp_path / "d.md"
    f.write_text(GOOD_DRAFT, encoding="utf-8")
    assert cli.main(["curate", rec["id"], "--draft", str(f)]) == 0
    capsys.readouterr()
    assert cli.main(["curate", rec["id"], "--approve"]) == 0
    out = capsys.readouterr().out
    assert "registered" in out.lower()
    assert "kamino serve" in out and "--isolated" in out   # the tip must not point at the withheld stub
    assert cli.main(["list"]) == 0
    assert "acme" in capsys.readouterr().out.lower()


def test_cli_approve_without_draft_errors(store, capsys):
    rec = _seed(store)
    assert cli.main(["curate", rec["id"], "--approve"]) == 1
    assert "draft" in capsys.readouterr().err.lower()
