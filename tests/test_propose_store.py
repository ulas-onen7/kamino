"""T1: proposal store roundtrip + topic-key matching across cluster drift."""
import json
import stat

import pytest

from tests.conftest import posix_perms

from kamino import corpus, propose


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return corpus.ensure_store()


def _candidate(project="/home/u/acme-ui", rts=None, ents=None, members=None):
    rts = rts if rts is not None else ["/home/u/acme-ui/src/a.py", "/home/u/acme-ui/src/b.py"]
    ents = ents if ents is not None else rts + ["PROJ-142"]
    members = members if members is not None else ["conv1", "conv2", "conv3"]
    return {"cluster_id": "c000", "score": 20.0, "species": "knowledge",
            "project": project, "n_in_window": len(members), "n_evidence_only": 0,
            "why": ["3 distinct conversations across 3 days"],
            "signals": {}, "shared_read_targets": rts, "shared_entities": ents,
            "members": [{"conv_id": m, "tool": "claude", "project": project,
                         "start": "2026-07-20", "end": "2026-07-25",
                         "n_sessions": 1, "opener": f"opener {m}", "countable": True}
                        for m in members]}


@posix_perms
def test_store_roundtrip_and_perms(store):
    data = propose.load_proposals()
    assert data == {"records": [], "next_id": 1}
    data["records"].append({"id": "p001", "state": "pending"})
    data["next_id"] = 2
    propose.save_proposals(data)
    assert propose.load_proposals()["records"][0]["id"] == "p001"
    mode = stat.S_IMODE((corpus.corpus_root() / "proposals.json").stat().st_mode)
    assert mode == 0o600


def test_corrupt_store_resets_empty(store):
    (corpus.corpus_root() / "proposals.json").write_text("{oops", encoding="utf-8")
    assert propose.load_proposals() == {"records": [], "next_id": 1}


def test_topic_key_caps_sizes(store):
    cand = _candidate(rts=[f"/r/{i}.py" for i in range(20)],
                      ents=[f"/e/{i}.py" for i in range(30)],
                      members=[f"m{i}" for i in range(9)])
    key = propose.topic_key(cand)
    assert len(key["read_targets"]) <= 6
    assert len(key["entities"]) <= 10
    assert key["project"] == "/home/u/acme-ui"
    assert len(key["members"]) == 9


def test_grown_cluster_matches_old_record(store):
    old = {"id": "p001", "state": "declined",
           "topic": propose.topic_key(_candidate())}
    grown = _candidate(rts=["/home/u/acme-ui/src/a.py", "/home/u/acme-ui/src/b.py",
                            "/home/u/acme-ui/src/c.py", "/home/u/acme-ui/src/d.py"],
                       members=["conv1", "conv2", "conv3", "conv4", "conv5"])
    assert propose.matches(grown, old) is True     # rt overlap vs record = 2/2


def test_member_overlap_alone_matches(store):
    old = {"id": "p001", "state": "declined",
           "topic": propose.topic_key(_candidate(rts=["/home/u/acme-ui/old.py"]))}
    new = _candidate(rts=["/home/u/acme-ui/totally/new.py"], ents=["X-1"],
                     members=["conv1", "conv2", "convNEW"])
    assert propose.matches(new, old) is True       # member overlap 2/3 >= 0.5


def test_different_project_never_matches(store):
    old = {"id": "p001", "state": "declined", "topic": propose.topic_key(_candidate())}
    other = _candidate(project="/home/u/other")
    assert propose.matches(other, old) is False


def test_disjoint_topic_does_not_match(store):
    old = {"id": "p001", "state": "declined", "topic": propose.topic_key(_candidate())}
    new = _candidate(rts=["/home/u/acme-ui/x.py", "/home/u/acme-ui/y.py"],
                     ents=["OTHER-9"], members=["c9", "c10"])
    assert propose.matches(new, old) is False
