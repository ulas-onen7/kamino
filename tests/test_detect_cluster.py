"""T5: graph core port — edges, components, split_mega (all-four-signal tightening)."""
from kamino.detect import (build_idf, components, cosine, edges, jaccard,
                           overlap, score_and_classify, split_mega)
from kamino.corpus import DEFAULTS
from kamino.minhash import shingles, signature

CFG = DEFAULTS["detect"]


def _fp(cid, entities=(), read_targets=(), tf=None, prose="", start="2026-07-01",
        end="2026-07-02", project="x"):
    mh = signature(shingles(prose, 5), 64) if prose else None
    return {"conv_id": cid, "tier": "full", "project": project, "tool": "claude",
            "start": start, "end": end, "n_countable": 1,
            "entities": list(entities), "read_targets": list(read_targets),
            "opener": "", "headers": [], "tf": tf or {}, "minhash": mh}


def test_jaccard_and_cosine():
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    idf = {"x": 2.0, "y": 2.0, "z": 2.0}
    assert cosine({"x": 2, "y": 1}, {"x": 2, "y": 1}, idf) > 0.99
    assert cosine({"x": 1}, {"z": 1}, idf) == 0.0


def test_overlap_coefficient():
    assert overlap({"a", "b"}, {"b"}) == 1.0
    assert overlap({"a", "b"}, set()) == 0.0
    assert overlap(set(), set()) == 0.0


def test_idf_caps_ubiquitous_tokens():
    fps = [_fp(str(i), tf={"everywhere": 1, f"rare{i}": 1}) for i in range(10)]
    idf = build_idf(fps, df_cap_ratio=0.5)
    assert idf["everywhere"] == 0.0
    assert idf["rare0"] > 0.0


def test_knowledge_cluster_via_shared_read_targets():
    shared = ["/repo/core/api.py", "/repo/core/models.py"]
    fps = [
        _fp("k1", entities=shared, read_targets=shared, tf={"pagination": 5},
            start="2026-07-01", end="2026-07-01"),
        _fp("k2", entities=shared, read_targets=shared, tf={"pagination": 4},
            start="2026-07-05", end="2026-07-05"),
        _fp("k3", entities=shared + ["/repo/x.py"], read_targets=shared,
            tf={"pagination": 6}, start="2026-07-10", end="2026-07-10"),
        _fp("noise", entities=["/other/z.py"], read_targets=["/other/z.py"],
            tf={"payroll": 9}),
    ]
    e = edges(fps, CFG)
    comps = components([f["conv_id"] for f in fps], e.keys())
    big = max(comps, key=len)
    assert set(big) == {"k1", "k2", "k3"}
    result = score_and_classify(big, {f["conv_id"]: f for f in fps}, CFG)
    assert result["species"] == "knowledge"
    assert "/repo/core/api.py" in result["shared_read_targets"]
    assert result["score"] > 0


def test_framework_cluster_via_verbatim_template_disjoint_entities():
    template = ("Background analysis framework. Section one market position. "
                "Section two headcount and payroll structure. Section three "
                "regulatory exposure. Section four verdict and risks. ") * 15
    fps = [
        _fp("f1", entities=["/co/acme.md"], prose=template + " acme details"),
        _fp("f2", entities=["/co/globex.md"], prose=template + " globex details"),
        _fp("f3", entities=["/co/initech.md"], prose=template + " initech details"),
    ]
    e = edges(fps, CFG)
    comps = components([f["conv_id"] for f in fps], e.keys())
    big = max(comps, key=len)
    assert set(big) == {"f1", "f2", "f3"}
    result = score_and_classify(big, {f["conv_id"]: f for f in fps}, CFG)
    assert result["species"] == "framework"


def _overlap_glued_mega():
    """Two dense groups (within-group overlap 0.5) glued by ONE bridge pair whose
    only crossing signal is overlap ~0.29 — above edge_overlap 0.25, below the
    tightened 0.35. Distinct projects everywhere so no same-project relaxation."""
    fps = []
    a_shared = [f"/grp-a/shared{k}.py" for k in range(5)]
    b_shared = [f"/grp-b/shared{k}.py" for k in range(5)]
    bridge = [f"/bridge/common{k}.py" for k in range(4)]
    for i in range(7):
        ents = a_shared + [f"/grp-a/only-{i}-{k}.py" for k in range(5)]
        if i == 0:
            ents = ents + bridge
        fps.append(_fp(f"a{i}", entities=ents, tf={f"tok-a{i}": 3},
                       project=f"proj-a{i}"))
    for i in range(6):
        ents = b_shared + [f"/grp-b/only-{i}-{k}.py" for k in range(5)]
        if i == 0:
            ents = ents + bridge
        fps.append(_fp(f"b{i}", entities=ents, tf={f"tok-b{i}": 3},
                       project=f"proj-b{i}"))
    return fps


def test_split_mega_breaks_overlap_glued_component():
    # Regression for findings inheritance item 7: the spike tightened only
    # minhash/entity/cosine, so overlap-glued mega-components resisted splitting.
    fps = _overlap_glued_mega()
    by_id = {f["conv_id"]: f for f in fps}
    e = edges(fps, CFG)
    comps = components(list(by_id), e.keys())
    mega = max(comps, key=len)
    assert len(mega) == 13 and len(mega) > CFG["mega_cluster"]
    subs = sorted(split_mega(mega, by_id, CFG), key=len, reverse=True)
    groups = [set(s) for s in subs if len(s) > 1]
    assert {f"a{i}" for i in range(7)} in groups
    assert {f"b{i}" for i in range(6)} in groups
