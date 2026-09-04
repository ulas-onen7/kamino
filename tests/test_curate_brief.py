"""T1: species-matched recipes + the curation brief handed to the host agent."""
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


def _candidate(species="knowledge", project="/home/u/acme",
               members=("conv1", "conv2", "conv3")):
    rts = [f"{project}/services/value_report.py", f"{project}/services/client.py"]
    return {"cluster_id": "c000", "score": 25.0, "species": species,
            "project": project, "n_in_window": len(members), "n_evidence_only": 0,
            "why": ["3 distinct conversations across 3 days",
                    "2 read targets shared by >= 2 convs"],
            "signals": {"avg_entity_jaccard": 0.31}, "shared_read_targets": rts,
            "shared_entities": rts + ["PROJ-142"],
            "members": [{"conv_id": m, "tool": "claude", "project": project,
                         "start": "2026-07-2%d" % (i + 1), "end": "2026-07-2%d" % (i + 1),
                         "n_sessions": 1, "opener": f"how does {project} work ({m})",
                         "countable": True} for i, m in enumerate(members)]}


def _seed(species="knowledge", state="accepted"):
    propose.refresh_proposals({"candidates": [_candidate(species)]}, now=NOW)
    data = propose.load_proposals()
    data["records"][0]["state"] = state
    propose.save_proposals(data)
    return data["records"][0]


def test_recipes_cover_every_surfaced_species():
    for species in ("knowledge", "framework", "runbook"):
        r = curate.RECIPES[species]
        assert r["instructions"] and r["sections"]
        assert r["verify"] in ("entities-kept", "entities-stripped")


def test_knowledge_brief_has_recipe_evidence_and_read_commands(store):
    rec = _seed("knowledge")
    text = curate.brief(rec)
    assert "knowledge" in text
    assert "merge" in text.lower()                      # the knowledge recipe's verb
    assert "3 distinct conversations across 3 days" in text
    for m in ("conv1", "conv2", "conv3"):
        assert f"kamino curate {rec['id']} --source {m}" in text
    assert "/home/u/acme/services/value_report.py" in text
    assert f"kamino curate {rec['id']} --draft" in text  # how to submit


def test_framework_brief_demands_stripping_entities(store):
    rec = _seed("framework")
    text = curate.brief(rec)
    assert "strip" in text.lower()
    assert "PROJ-142" in text                           # named as an entity to remove


def test_brief_lists_required_sections(store):
    rec = _seed("knowledge")
    text = curate.brief(rec)
    for section in curate.RECIPES["knowledge"]["sections"]:
        assert section in text


def test_cli_curate_prints_brief(store, capsys):
    rec = _seed("knowledge")
    assert cli.main(["curate", rec["id"]]) == 0
    out = capsys.readouterr().out
    assert "--source conv1" in out


def test_cli_curate_unknown_id_errors(store, capsys):
    assert cli.main(["curate", "p999"]) == 1
    assert "p999" in capsys.readouterr().err


def test_curate_requires_an_accepted_proposal(store, capsys):
    rec = _seed("knowledge", state="pending")
    assert cli.main(["curate", rec["id"]]) == 1
    err = capsys.readouterr().err
    assert "accept" in err.lower()
