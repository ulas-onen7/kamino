"""T6: gating + scoring with the 20-day window — countable-in-window, evidence-forever."""
from datetime import datetime, timezone

from kamino.corpus import DEFAULTS
from kamino.detect import (build_idf, keep_cluster, peel_to_core,
                           score_and_classify)

CFG = DEFAULTS["detect"]
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
W = 20


def _fp(cid, entities=(), read_targets=(), tf=None, end="2026-07-20T10:00:00.000Z",
        project="x"):
    return {"conv_id": cid, "tier": "full", "project": project, "tool": "claude",
            "start": end, "end": end, "n_countable": 1,
            "entities": list(entities), "read_targets": list(read_targets),
            "opener": "", "headers": [], "tf": tf or {}, "minhash": None}


IDF = {"alpha": 2.0, "beta": 2.0, "gamma": 2.0, "topic": 2.0}


def test_strong_pair_kept_weak_pair_dropped():
    strong = [_fp("s1", entities=["/repo/a.py", "/repo/b.py"], tf={"alpha": 5, "beta": 3}),
              _fp("s2", entities=["/repo/a.py", "/repo/b.py"], tf={"alpha": 5, "beta": 3})]
    by_id = {f["conv_id"]: f for f in strong}
    assert keep_cluster(["s1", "s2"], by_id, CFG, IDF) is True

    weak = [_fp("w1", entities=["/repo/x.py"], tf={"alpha": 1}),
            _fp("w2", entities=["/repo/y.py"], tf={"gamma": 1})]
    by_id = {f["conv_id"]: f for f in weak}
    assert keep_cluster(["w1", "w2"], by_id, CFG, IDF) is False


def test_same_project_multi_signal_pair_kept():
    # H5: genuine two-conversation topics are not near-duplicates; same-project
    # pairs survive when cosine AND overlap agree.
    # tf chosen so cosine ~0.25: above the relaxed same-project edge (0.21),
    # below pair_keep_cosine (0.60) — only the two-signal H5 rule can keep it.
    pair = [_fp("p1", entities=["/p/core.py", "/p/api.py", "/p/z1.py", "/p/z2.py"],
                tf={"topic": 5, "alpha": 4}, project="proj"),
            _fp("p2", entities=["/p/core.py", "/p/api.py", "/p/y1.py", "/p/y2.py",
                                "/p/y3.py", "/p/y4.py"],
                tf={"topic": 1, "beta": 6}, project="proj")]
    by_id = {f["conv_id"]: f for f in pair}
    assert keep_cluster(["p1", "p2"], by_id, CFG, IDF) is True
    # identical content across projects never passes the H5 rule
    by_id["p2"] = dict(by_id["p2"], project="other")
    assert keep_cluster(["p1", "p2"], by_id, CFG, IDF) is False
    # ...unless the two convs share a recorded cwd — labels drifted apart
    # (asymmetric reassignment) but the work happened in the same repo.
    by_id["p1"] = dict(by_id["p1"], cwd="/w/repo")
    by_id["p2"] = dict(by_id["p2"], cwd="/w/repo")
    assert keep_cluster(["p1", "p2"], by_id, CFG, IDF) is True


def test_peel_to_core_recovers_dense_core():
    shared = ["/repo/mod/a.py", "/repo/mod/b.py"]
    tight = [_fp(f"t{i}", entities=shared + [f"/repo/only{i}.py", f"/repo/also{i}.py",
                                             f"/repo/more{i}.py"],
                 tf={"topic": 5}) for i in range(3)]
    noise = [_fp(f"n{i}", entities=[f"/other{i}/x.py"], tf={f"noise{i}": 3})
             for i in range(2)]
    fps = tight + noise
    by_id = {f["conv_id"]: f for f in fps}
    idf = build_idf(fps, 0.5)
    comp = [f["conv_id"] for f in fps]
    assert score_and_classify(comp, by_id, CFG, idf=idf)["species"] == "weak"
    core = peel_to_core(comp, by_id, CFG, idf)
    assert core is not None and set(core) == {"t0", "t1", "t2"}


def _trio(ends):
    shared = ["/repo/core/api.py", "/repo/core/models.py"]
    fps = [_fp(f"c{i}", entities=shared, read_targets=shared,
               tf={"topic": 5}, end=e) for i, e in enumerate(ends)]
    return {f["conv_id"]: f for f in fps}


def test_window_gate_needs_min_countable():
    # 3 members but only 1 within the 20-day window: not actionable.
    by_id = _trio(["2026-07-25T10:00:00.000Z", "2026-04-01T10:00:00.000Z",
                   "2026-03-01T10:00:00.000Z"])
    assert keep_cluster(list(by_id), by_id, CFG, IDF, now=NOW, window_days=W) is False
    # all 3 recent: actionable.
    by_id = _trio(["2026-07-25T10:00:00.000Z", "2026-07-20T10:00:00.000Z",
                   "2026-07-15T10:00:00.000Z"])
    assert keep_cluster(list(by_id), by_id, CFG, IDF, now=NOW, window_days=W) is True


def test_window_boundary_day_counts():
    by_id = _trio(["2026-07-06T12:00:00.000Z",   # exactly 20 days before NOW
                   "2026-07-20T10:00:00.000Z", "2026-07-25T10:00:00.000Z"])
    assert keep_cluster(list(by_id), by_id, CFG, IDF, now=NOW, window_days=W) is True


def test_two_countable_plus_old_evidence_uses_pair_rule():
    # A trio whose third member aged out must still pass via the H5 pair rule
    # on the two countable members (same project, two agreeing signals).
    shared = ["/p/core.py", "/p/api.py"]
    by_id = {
        "new1": _fp("new1", entities=shared + ["/p/z1.py", "/p/z2.py"],
                    tf={"topic": 5, "alpha": 4}, project="proj",
                    end="2026-07-25T10:00:00.000Z"),
        "new2": _fp("new2", entities=shared + ["/p/y1.py", "/p/y2.py", "/p/y3.py",
                                               "/p/y4.py"],
                    tf={"topic": 1, "beta": 6}, project="proj",
                    end="2026-07-20T10:00:00.000Z"),
        "old": _fp("old", entities=shared, tf={"topic": 3}, project="proj",
                   end="2026-03-01T10:00:00.000Z"),
    }
    assert keep_cluster(list(by_id), by_id, CFG, IDF, now=NOW, window_days=W) is True


def test_score_counts_in_window_reports_evidence():
    by_id = _trio(["2026-07-25T10:00:00.000Z", "2026-07-20T10:00:00.000Z",
                   "2026-03-01T10:00:00.000Z"])
    r = score_and_classify(list(by_id), by_id, CFG, now=NOW, window_days=W)
    assert r["n_in_window"] == 2 and r["n_evidence_only"] == 1
    assert r["signals"]["distinct_days"] == 2          # countable days only
    assert any("evidence" in w for w in r["why"])
    # shared content still draws on ALL members
    assert "/repo/core/api.py" in r["shared_read_targets"]


def test_no_window_is_spike_parity():
    by_id = _trio(["2026-07-25T10:00:00.000Z", "2026-07-20T10:00:00.000Z",
                   "2026-03-01T10:00:00.000Z"])
    r = score_and_classify(list(by_id), by_id, CFG)
    assert r["n_in_window"] == 3 and r["n_evidence_only"] == 0
    assert r["signals"]["distinct_days"] == 3


def test_naive_datetime_does_not_crash():
    by_id = _trio(["2026-07-01", "2026-07-02", "2026-07-03"])
    r = score_and_classify(list(by_id), by_id, CFG, now=NOW, window_days=W)
    assert r["score"] > 0
