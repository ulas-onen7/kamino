"""Staleness signals (kamino/freshness.py): G3 source drift scoped to the clone's own
file mentions, G5 corpus recurrence, and the opt-in shelf life -- all mechanical, all
threshold-gated so a fresh clone carries no marker at all."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import freshness  # noqa: E402
from kamino import registry as reg  # noqa: E402

BLURB = ("Knows the alpha service: its schema, its deploy path, and why the retry "
         "budget is set where it is.")


def _repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    for rel in ("src/schema.py", "src/util_helpers.py", "docs/README.md", "src/README.md"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n")
    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    git("init", "-q")
    git("add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    return root, sha, git


def test_extract_mentions_full_path_and_unique_basename_only(tmp_path):
    root, _, _ = _repo(tmp_path)
    body = ("USER: fix src/schema.py please\n"
            "ASSISTANT: touched util_helpers.py and mentioned README.md somewhere\n")
    m = freshness.extract_mentions(body, str(root))
    assert "src/schema.py" in m                      # full repo-relative path
    assert "src/util_helpers.py" in m                # unique basename = unambiguous
    assert not any(f.endswith("README.md") for f in m), \
        "a basename shared by two files is noise, not a mention"


def test_recruit_stores_pins_mentions_and_shelf_life(tmp_path):
    root, sha, _ = _repo(tmp_path)
    regp = str(tmp_path / "registry")
    reg.recruit_body("USER: about src/schema.py\n\nASSISTANT: done\n", regp, "clone-x",
                     BLURB, source=[{"repo": "proj", "sha": sha, "root": str(root)}],
                     shelf_life_days=90)
    c = reg._scan_cards(regp)[0][0]
    assert c["source"] == [{"repo": "proj", "sha": sha, "root": str(root)}]
    assert "src/schema.py" in c["mentions"]
    assert int(c["shelf_life_days"]) == 90


def test_d11_drift_warns_only_when_mentioned_files_moved(tmp_path):
    root, sha, git = _repo(tmp_path)
    regp = str(tmp_path / "registry")
    reg.recruit_body("USER: about src/schema.py\n\nASSISTANT: done\n", regp, "clone-x",
                     BLURB, source=[{"repo": "proj", "sha": sha, "root": str(root)}])

    (root / "docs" / "README.md").write_text("changed\n")     # churn OFF the mention set
    git("add", "."); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "docs")
    findings, ledger = freshness.assess(regp)
    assert not [f for f in findings if f["check"] == "D11"], \
        "churn away from the mention set is not evidence at all -- silence, not info"

    (root / "src" / "schema.py").write_text("x = 2\n")        # churn ON the mention set
    git("add", "."); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "schema")
    findings, ledger = freshness.assess(regp)
    d11 = [f for f in findings if f["check"] == "D11" and f["severity"] == "warn"]
    assert len(d11) == 1 and "touched files this clone discusses" in d11[0]["detail"]
    assert ledger["clone-x"]["drift"]["scoped"] == 1


def test_d11_without_mentions_unscoped_churn_is_info_grade(tmp_path):
    """A card with a pin but no mention set (nothing path-like in the transcript) falls
    back to raw commit distance -- weak evidence, so info, never warn."""
    root, sha, git = _repo(tmp_path)
    regp = str(tmp_path / "registry")
    reg.recruit_body("USER: nothing path-like here\n\nASSISTANT: done\n", regp, "clone-x",
                     BLURB, source=[{"repo": "proj", "sha": sha, "root": str(root)}])
    (root / "docs" / "README.md").write_text("changed\n")
    git("add", "."); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "r")
    findings, _ = freshness.assess(regp)
    d11 = [f for f in findings if f["check"] == "D11"]
    assert d11 and d11[0]["severity"] == "info" and "weak evidence" in d11[0]["detail"]


def test_d11_wait_a_stable_topic_scores_zero_forever(tmp_path):
    """The self-calibration property: heavy churn away from the mention set never warns."""
    root, sha, git = _repo(tmp_path)
    regp = str(tmp_path / "registry")
    reg.recruit_body("USER: about src/schema.py\n\nASSISTANT: done\n", regp, "clone-x",
                     BLURB, source=[{"repo": "proj", "sha": sha, "root": str(root)}])
    for i in range(5):
        (root / "docs" / "README.md").write_text(f"rev {i}\n")
        git("add", "."); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", f"r{i}")
    findings, _ = freshness.assess(regp)
    assert not [f for f in findings if f["check"] == "D11" and f["severity"] == "warn"]


def test_d12_shelf_life_is_opt_in_and_pure_date_math(tmp_path):
    regp = str(tmp_path / "registry")
    reg.recruit_body("USER: hi\n\nASSISTANT: done\n", regp, "clone-law", BLURB,
                     shelf_life_days=30)
    reg.recruit_body("USER: hi\n\nASSISTANT: done\n", regp, "clone-log", BLURB)
    future = datetime.now(timezone.utc) + timedelta(days=31)
    findings, ledger = freshness.assess(regp, now=future)
    d12 = [f for f in findings if f["check"] == "D12"]
    assert [f["subject"] for f in d12] == ["clone-law"], "no shelf life declared, no verdict"
    assert ledger["clone-law"]["expired"] is True

    c = reg.load_roster(regp)[0]
    assert freshness.hot_marker(dict(c, freshness=None), now=future) in (
        ", past shelf life", ""), "hot marker must not throw on either card"
    law = next(x for x in reg.load_roster(regp) if x["id"] == "clone-law")
    assert freshness.hot_marker(dict(law, freshness=None), now=future) == ", past shelf life"


def test_d13_recurrence_counts_only_post_freeze_overlap():
    frozen = datetime(2026, 8, 1, tzinfo=timezone.utc)
    card = {"id": "clone-x", "mentions": ["src/schema.py"], "source": []}
    metas = [
        {"end": "2026-08-10T00:00:00+00:00", "cwd": "/p"},   # post-freeze, overlapping
        {"end": "2026-08-11T00:00:00+00:00", "cwd": "/p"},
        {"end": "2026-08-12T00:00:00+00:00", "cwd": "/p"},
        {"end": "2026-07-01T00:00:00+00:00", "cwd": "/p"},   # pre-freeze: never counts
    ]
    reader = lambda m: {"read_targets": ["/p/src/schema.py"]}
    assert freshness._recurrence(card, frozen, metas, reader) == 3
    assert freshness._recurrence(card, frozen, metas[:2], reader) == 2


def test_ledger_marker_invalidated_by_re_recruit(tmp_path):
    entry = {"frozen_at": "2026-08-01T00:00:00+00:00",
             "drift": {"scoped": 4, "unscoped": 9}}
    assert "drifted: 4" in freshness.marker(entry, "2026-08-01T00:00:00+00:00")
    assert freshness.marker(entry, "2026-08-20T00:00:00+00:00") == "", \
        "a re-recruited clone must not inherit its predecessor's verdict"
    assert freshness.marker(None, "2026-08-01T00:00:00+00:00") == ""


def test_roster_injection_marks_only_flagged_clones(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    from kamino import home, integrate
    home.ensure_registry()
    regp = str(home.registry_path())
    reg.recruit_body("USER: hi\n\nASSISTANT: done\n", regp, "clone-fresh", BLURB)
    reg.recruit_body("USER: hi\n\nASSISTANT: done\n", regp, "clone-old", BLURB)
    old = reg._scan_cards(regp)[0]
    fa = next(c["frozen_at"] for c in old if c["id"] == "clone-old")
    freshness.write_ledger(regp, {"clone-old": {"frozen_at": fa,
                                                "drift": {"scoped": 7, "unscoped": 20}}})
    block = integrate.roster_context()
    line_old = next(ln for ln in block.splitlines() if ln.startswith("- clone-old"))
    line_fresh = next(ln for ln in block.splitlines() if ln.startswith("- clone-fresh"))
    assert "drifted: 7 commit(s)" in line_old
    assert "drifted" not in line_fresh, "a marker on everything is a marker on nothing"
