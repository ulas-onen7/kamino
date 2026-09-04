"""T4: conversation-level fingerprint assembly — merge along lineage, mirrors out,
misfiled reassigned to pseudo_project, minhash merged by elementwise min."""
import json

import pytest

from kamino import corpus, detect

TEXT_A = ("USER: investigate the acme-ui pagination bug PROJ-142\n\n"
          "ASSISTANT: reading.\n"
          '[tool call: Read {"file_path": "/home/u/acme-ui/src/api/pagination.ts"}]\n\n'
          "ASSISTANT: the offset is computed twice in computeOffset.\n")
TEXT_B = ("USER: continue the pagination fix\n\n"
          "ASSISTANT: checking helpers.\n"
          '[tool call: Read {"file_path": "/home/u/acme-ui/src/api/helpers.ts"}]\n\n'
          "ASSISTANT: helpers look clean, patching pagination now.\n")
TEXT_MIRROR = ("USER: The following is the Codex agent history for reference\n"
               "quoted content mentions /home/u/elsewhere/quoted.py and MIRROR-99\n\n"
               "ASSISTANT: noted.\n")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    return corpus.ensure_store()


def _put(store, sid, text, tier="full", link=None, flags=None, cwd="/home/u/acme-ui",
         pseudo=None, start="2026-07-25T09:00:00.000Z", end="2026-07-25T10:00:00.000Z"):
    d = store / "sessions" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"session_id": sid, "tool": "claude", "src": "", "end": end,
            "pinned": False, "tier": tier, "chars": len(text), "user_turns": 3,
            "start": start, "opener": text.split("\n")[0][6:], "link": link,
            "flags": flags or {}, "ingested_at": end, "cwd": cwd,
            "project_slug": None, "pseudo_project": pseudo}
    (d / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
    if tier != "skip":
        (d / f"{sid}.txt").write_text(text, encoding="utf-8")
    return meta


def test_linked_sessions_merge_into_one_fingerprint(store):
    metas = [_put(store, "root", TEXT_A),
             _put(store, "cont", TEXT_B, link={"type": "continuation",
                                               "parent": "claude/root"},
                  start="2026-07-25T11:00:00.000Z", end="2026-07-25T12:00:00.000Z")]
    fps = detect.conv_fingerprints(metas, corpus.load_config())
    assert len(fps) == 1
    fp = fps[0]
    assert fp["conv_id"] == "root"
    assert "/home/u/acme-ui/src/api/pagination.ts" in fp["read_targets"]
    assert "/home/u/acme-ui/src/api/helpers.ts" in fp["read_targets"]
    assert fp["tf"]["pagination"] >= 3                     # summed across members
    assert fp["opener"].startswith("investigate the acme-ui pagination")
    assert fp["n_countable"] == 2
    assert fp["minhash"] is not None


def test_mirror_member_content_excluded(store):
    metas = [_put(store, "root", TEXT_A),
             _put(store, "mir", TEXT_MIRROR, link={"type": "burst",
                                                   "parent": "claude/root"},
                  flags={"mirror": True})]
    fps = detect.conv_fingerprints(metas, corpus.load_config())
    assert len(fps) == 1
    fp = fps[0]
    assert fp["n_countable"] == 1
    assert "MIRROR-99" not in fp["entities"]               # quoted content contributes nothing
    assert not any("quoted.py" in e for e in fp["entities"])


def test_degraded_member_disables_minhash_keeps_entities(store):
    big = TEXT_B + "x" * 200
    metas = [_put(store, "root", TEXT_A),
             _put(store, "deg", big, tier="degraded",
                  link={"type": "burst", "parent": "claude/root"})]
    fps = detect.conv_fingerprints(metas, corpus.load_config())
    fp = fps[0]
    assert fp["minhash"] is None                           # conv tier degraded
    assert "/home/u/acme-ui/src/api/helpers.ts" in fp["read_targets"]


def test_misfiled_conversation_reassigned_to_pseudo_project(store):
    metas = [_put(store, "mis", TEXT_A, flags={"misfiled": True},
                  cwd="/home/u/wrong-window", pseudo="/home/u/acme-ui")]
    fps = detect.conv_fingerprints(metas, corpus.load_config())
    assert fps[0]["project"] == "/home/u/acme-ui"


def test_unflagged_pseudo_disjoint_also_reassigned(store):
    # T8 finding: dominant paths in a genuinely different tree are reassignment
    # evidence even below the misfiled flag's path-count bar.
    metas = [_put(store, "quiet", TEXT_A,
                  cwd="/home/u/wrong-window", pseudo="/home/u/acme-ui")]
    fps = detect.conv_fingerprints(metas, corpus.load_config())
    assert fps[0]["project"] == "/home/u/acme-ui"


def test_related_pseudo_never_reassigns(store):
    # cwd inside (or containing) the pseudo prefix is the studying-own-monorepo
    # case: not a reassignment.
    metas = [_put(store, "sub", TEXT_A,
                  cwd="/home/u/acme-ui/packages/web", pseudo="/home/u/acme-ui")]
    fps = detect.conv_fingerprints(metas, corpus.load_config())
    assert fps[0]["project"] == "/home/u/acme-ui/packages/web"


def test_minhash_merge_equals_union_signature(store):
    from kamino.minhash import shingles, signature
    metas = [_put(store, "a", TEXT_A),
             _put(store, "b", TEXT_B, link={"type": "burst", "parent": "claude/a"})]
    cfg = corpus.load_config()
    fp = detect.conv_fingerprints(metas, cfg)[0]
    merged = fp["minhash"]
    session_fps = detect.fingerprints(metas, cfg)
    sig_a, sig_b = session_fps["claude/a"]["minhash"], session_fps["claude/b"]["minhash"]
    assert merged == [min(x, y) for x, y in zip(sig_a, sig_b)]
