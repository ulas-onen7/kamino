"""Fixes for the guardrails-review-report findings (C1, C2, I1, I2, I3, I4, I6, I7).

See .superpowers/sdd/2026-08-14-oss-launch/guardrails-review-report.md for the
findings and .superpowers/sdd/2026-08-14-oss-launch/guardrails-fix-report.md for
what changed. I5 and E3/E6 severities are explicitly out of scope here.
"""
import builtins
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli                 # noqa: E402
from kamino import health              # noqa: E402
from kamino import home                # noqa: E402
from kamino import registry as reg     # noqa: E402

BLURB = ("Knows the alpha service: its schema, its deploy path, and why the retry "
         "budget is set where it is.")


def _session(tmp_path, name):
    p = tmp_path / f"{name}.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": f"work {name}"}},
             {"type": "assistant", "message": {"role": "assistant", "content": f"done {name}"}}]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def _isolate(tmp_path):
    os.environ["KAMINO_HOME"] = str(tmp_path)
    os.environ.pop("KAMINO_REGISTRY", None)


# --------------------------------------------------------------------------- C2
def test_c2_a_non_utf8_card_is_a_finding_not_a_traceback(tmp_path):
    """A card written by a legacy-codepage editor must not traceback every verb --
    it degrades to a D5 finding, exactly like any other broken card."""
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    (Path(regp) / "cards" / "clone-b.md").write_bytes(
        "---\nid: clone-b\n---\n\nbroken \xfc byte\n".encode("latin-1"))

    findings = health.inspect_registry(regp)          # must not raise UnicodeDecodeError
    assert any(f["check"] == "D5" and f["name"] == "card-unreadable" for f in findings)
    roster = reg.load_roster(regp)                    # must not raise either
    assert [c["id"] for c in roster] == ["clone-a"]    # the healthy neighbour survives


def test_c2_doctor_survives_a_non_utf8_card(tmp_path, capsys):
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    (Path(regp) / "cards" / "clone-b.md").write_bytes(b"\xff\xfe garbage")

    rc = cli.main(["doctor"])                          # must not raise
    assert rc == 2
    assert "card-unreadable" in capsys.readouterr().out


def test_c2_inject_still_shows_the_healthy_neighbour(tmp_path):
    """integrate.inject() swallows exceptions -- before the fix a non-UTF-8 card made the
    whole roster disappear silently. After the fix, the healthy clone still shows."""
    from kamino import integrate
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    (Path(regp) / "cards" / "clone-b.md").write_bytes(b"\xff\xfe garbage")

    block = integrate.inject()
    assert "clone-a" in block


# --------------------------------------------------------------------------- C1 / I7
def test_c1_retire_refuses_when_a_sibling_cards_ref_is_unresolvable(tmp_path, capsys):
    """The C1 repro: clone-keep's snapshot_ref has a typo (blob/ instead of blobs/), so
    the card still parses but its ref cannot be resolved (D1). Retiring the UNRELATED
    clone-drop must be refused outright -- not just have its GC skip the blob -- because
    _gc_orphans's keep-set is built from every card, and one unresolvable ref anywhere
    means the whole keep-set cannot be trusted."""
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "keep"), regp, "clone-keep", BLURB)
    reg.recruit(_session(tmp_path, "drop"), regp, "clone-drop", BLURB)
    keep_blob = next(c["blob"] for c in reg.load_roster(regp) if c["id"] == "clone-keep")
    card = Path(regp) / "cards" / "clone-keep.md"
    card.write_text(card.read_text(encoding="utf-8").replace("snapshot_ref: blobs/",
                                                             "snapshot_ref: blob/"),
                    encoding="utf-8")

    assert cli.main(["retire", "clone-drop"]) == 2
    assert "D1" in capsys.readouterr().err
    assert os.path.exists(keep_blob)                     # the live blob survived
    assert (Path(regp) / "cards" / "clone-drop.md").exists()   # nothing was removed either


def test_c1_gc_orphans_refuses_on_an_unresolvable_ref_not_just_d5(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "keep"), regp, "clone-keep", BLURB)
    card = Path(regp) / "cards" / "clone-keep.md"
    card.write_text(card.read_text(encoding="utf-8").replace("snapshot_ref: blobs/",
                                                             "snapshot_ref: blob/"),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="clone-keep"):
        reg._gc_orphans(regp)


def test_c1_d1_fix_string_no_longer_points_at_retire(tmp_path):
    """D1's fix used to be `kamino retire {cid}`, which is exactly the command that
    destroys a recoverable transcript when the blob still exists under a slightly
    different path. It must never suggest the destructive command."""
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    blob = next((Path(regp) / "blobs").iterdir())
    blob.unlink()
    findings = health.inspect_registry(regp)
    d1 = next(f for f in findings if f["check"] == "D1")
    assert "kamino retire" not in d1["fix"]


def test_c1_d7_fix_string_does_not_say_delete_by_hand_next_to_an_unresolved_ref(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "keep"), regp, "clone-keep", BLURB)
    card = Path(regp) / "cards" / "clone-keep.md"
    card.write_text(card.read_text(encoding="utf-8").replace("snapshot_ref: blobs/",
                                                             "snapshot_ref: blob/"),
                    encoding="utf-8")
    # clone-keep's real blob is now a D7 orphan (nothing points at it under the typo'd ref)
    findings = health.inspect_registry(regp)
    d7 = next(f for f in findings if f["check"] == "D7")
    assert "delete" not in d7["fix"] or "by hand" not in d7["fix"]


def test_c1_gc_refuses_on_a_card_that_lost_its_md_suffix(tmp_path):
    """A live card renamed by an editor backup (clone-keep.md~) or a merge-conflict
    .orig is invisible to _scan_cards entirely -- no finding is ever emitted for it --
    yet it may be the only thing naming a live blob. Retiring an UNRELATED clone must
    refuse rather than silently GC that blob away as an orphan."""
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "keep"), regp, "clone-keep", BLURB)
    reg.recruit(_session(tmp_path, "drop"), regp, "clone-drop", BLURB)
    keep_blob = next(c["blob"] for c in reg.load_roster(regp) if c["id"] == "clone-keep")
    card = Path(regp) / "cards" / "clone-keep.md"
    card.rename(Path(regp) / "cards" / "clone-keep.md~")

    with pytest.raises(ValueError, match="clone-keep.md~"):
        reg.retire(regp, "clone-drop")
    assert os.path.exists(keep_blob)                            # the live blob survived
    assert (Path(regp) / "cards" / "clone-drop.md").exists()     # nothing was removed

    assert cli.main(["retire", "clone-drop"]) == 2
    assert os.path.exists(keep_blob)
    assert (Path(regp) / "cards" / "clone-drop.md").exists()


def test_c1_gc_ignores_hidden_dotfiles_in_cards_dir(tmp_path):
    """The false-positive trap: .DS_Store / .gitkeep are common, harmless directory
    litter. A registry that has one must not be permanently unable to GC."""
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    (Path(regp) / "cards" / ".DS_Store").write_text("", encoding="utf-8")
    (Path(regp) / "blobs" / "clone-deadbeefdeadbeef.txt").write_text("stray", encoding="utf-8")

    removed = reg._gc_orphans(regp)
    assert removed == ["blobs/clone-deadbeefdeadbeef.txt"]


def test_i7_a_refused_retire_leaves_the_registry_untouched(tmp_path):
    """Direct library callers (bypassing the CLI's own D5/D1 pre-guard) used to delete
    the card, THEN raise from _gc_orphans -- a half-mutated registry. The scan must run
    before any mutation, so a refused retire changes nothing at all."""
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "keep"), regp, "clone-keep", BLURB)
    reg.recruit(_session(tmp_path, "drop"), regp, "clone-drop", BLURB)
    card = Path(regp) / "cards" / "clone-keep.md"
    card.write_text("corrupted\n", encoding="utf-8")     # D5 unparseable

    with pytest.raises(ValueError):
        reg.retire(regp, "clone-drop")
    assert (Path(regp) / "cards" / "clone-drop.md").exists()   # never removed
    assert (Path(regp) / "cards" / "clone-keep.md").exists()


# --------------------------------------------------------------------------- I1
def test_i1_a_thin_description_does_not_trigger_the_unusable_notice(tmp_path):
    """D4 (thin description) never removes a clone from the roster, so the session-start
    notice must not call it unusable."""
    from kamino import integrate
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", "alpha specialist")  # 17 chars, D4

    block = integrate.roster_context()
    assert "unusable" not in block


def test_i1_counts_clones_not_findings(tmp_path):
    """One card with BOTH a broken files: manifest (a finding that does not drop it from
    the roster) and a missing id (a finding that does) must be counted as ONE unusable
    clone, not two."""
    from kamino import integrate
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    card = Path(regp) / "cards" / "clone-a.md"
    text = card.read_text(encoding="utf-8")
    text = text.replace("class: coding", "class: coding\nfiles: {not json")
    text = text.replace("id: clone-a\n", "")
    card.write_text(text, encoding="utf-8")

    block = integrate.roster_context()
    assert "1 clone(s)" in block
    assert "2 clone(s)" not in block


def test_i1_names_only_the_checks_that_actually_drop_a_clone(tmp_path):
    from kamino import integrate
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    next((Path(regp) / "blobs").iterdir()).unlink()      # D1, drops clone-a

    block = integrate.roster_context()
    assert "1 clone(s)" in block
    assert "D1" in block


# --------------------------------------------------------------------------- I2 / I3
def test_i2_a_broken_clone_elsewhere_does_not_block_ask(tmp_path, monkeypatch, capsys):
    """The I2 repro: clone-a's blob moved away, clone-b is healthy and still routable.
    `ask` must not brick the whole registry over a card it never routed to."""
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    reg.recruit(_session(tmp_path, "b"), regp, "clone-b", BLURB)
    next((Path(regp) / "blobs").iterdir()).unlink()      # breaks whichever recruited first

    from kamino import commander, preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    monkeypatch.setattr(commander, "handle",
                        lambda roster, q, emit=None, model=None, **kw: {
                            "routed_to": roster[0]["id"], "final_answer": "the answer",
                            "recommend_promote": False})
    assert cli.main(["ask", "how does it work?"]) == 0
    assert "the answer" in capsys.readouterr().out


def test_i2_ask_still_blocks_when_the_routed_clone_itself_is_broken(tmp_path, monkeypatch, capsys):
    """Defence in depth: if the commander ever claims it routed to a clone this same
    shallow scan flags as broken, `ask` must not relay that answer as trustworthy."""
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    reg.recruit(_session(tmp_path, "b"), regp, "clone-b", BLURB)
    next((Path(regp) / "blobs").iterdir()).unlink()

    from kamino import commander, preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    monkeypatch.setattr(commander, "handle",
                        lambda roster, q, emit=None, model=None, **kw: {
                            "routed_to": "clone-a", "final_answer": "do not trust me",
                            "recommend_promote": False})
    assert cli.main(["ask", "how does it work?"]) == 2
    captured = capsys.readouterr()
    assert "do not trust me" not in captured.out
    assert "D1" in captured.err


def test_i2_round2_d4_alone_never_blocks_the_routed_clone(tmp_path, monkeypatch, capsys):
    """Regression from the first I2 fix: `own` had no check-id restriction, so a fully
    servable clone with a thin (<40 char) description -- D4, error severity, but never
    unusable per health.description_routable's own docstring and I1's classification --
    got its answer withheld with rc 2. D4 must ride along as a warning at most, never a
    block, for the clone actually routed to."""
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", "alpha specialist")  # 17 chars, D4

    from kamino import commander, preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    monkeypatch.setattr(commander, "handle",
                        lambda roster, q, emit=None, model=None, **kw: {
                            "routed_to": "clone-a", "final_answer": "the answer",
                            "recommend_promote": False})
    assert cli.main(["ask", "how does it work?"]) == 0
    assert "the answer" in capsys.readouterr().out


def test_i2_round2_elsewhere_counts_distinct_clones_not_findings(tmp_path, monkeypatch, capsys):
    """Same miscount class as I1: one OTHER card with two findings (a broken files:
    manifest and a thin description) must be reported as one registry issue, not two."""
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)
    reg.recruit(_session(tmp_path, "b"), regp, "clone-b", BLURB)
    card = Path(regp) / "cards" / "clone-b.md"
    text = card.read_text(encoding="utf-8")
    text = text.replace("class: coding", "class: coding\nfiles: {not json")
    card.write_text(text, encoding="utf-8")            # D5 files-json-invalid on clone-b

    from kamino import commander, preflight
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    monkeypatch.setattr(commander, "handle",
                        lambda roster, q, emit=None, model=None, **kw: {
                            "routed_to": "clone-a", "final_answer": "the answer",
                            "recommend_promote": False})
    assert cli.main(["ask", "how does it work?"]) == 0
    err = capsys.readouterr().err
    assert "1 other registry issue" in err
    assert "2 other registry issue" not in err


def test_i3_ask_does_not_hash_any_blob(tmp_path, monkeypatch):
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)

    opened, real_open = [], builtins.open

    def recording_open(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    from kamino import commander
    monkeypatch.setattr(commander, "handle",
                        lambda roster, q, emit=None, model=None, **kw: {
                            "routed_to": None, "final_answer": "no clone fits",
                            "recommend_promote": False})
    monkeypatch.setattr(builtins, "open", recording_open)
    cli.main(["ask", "anything?"])
    assert not any("blobs" in p for p in opened), opened


def test_i3_roster_does_not_hash_any_blob(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path)
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, "a"), regp, "clone-a", BLURB)

    opened, real_open = [], builtins.open

    def recording_open(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", recording_open)
    assert cli.main(["roster"]) == 0
    assert not any("blobs" in p for p in opened), opened


# --------------------------------------------------------------------------- I4
def test_i4_force_does_not_overwrite_an_unrelated_clone(tmp_path, monkeypatch):
    from kamino import corpus, curate, propose
    from tests.test_curate_verify import GOOD_DRAFT, _seed

    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(corpus, "maybe_sync", lambda *a, **k: None)
    store = corpus.ensure_store()

    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    regp = str(home.registry_path())
    reg.recruit_body("unrelated transcript, not part of this proposal's evidence.",
                     regp, "acme-knowledge",
                     "An unrelated clone that happens to sit at the same default id.")

    with pytest.raises(curate.CurationError):
        curate.approve(rec)
    with pytest.raises(curate.CurationError):          # force must NOT bypass this
        curate.approve(rec, force=True)
    card = next(c for c in reg.load_roster(regp) if c["id"] == "acme-knowledge")
    assert card["origin"] != "synthesis"                 # the unrelated clone survives untouched


def test_i4_force_still_lets_the_same_proposal_replace_its_own_clone(tmp_path, monkeypatch):
    from kamino import corpus, curate
    from tests.test_curate_verify import GOOD_DRAFT, _seed

    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(corpus, "maybe_sync", lambda *a, **k: None)
    store = corpus.ensure_store()

    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    out = curate.approve(rec)                            # first curation

    curate.submit_draft(rec, GOOD_DRAFT)                  # re-curate the same proposal
    out2 = curate.approve(rec)                            # no --force needed at all
    assert out2["clone_id"] == out["clone_id"]


# --------------------------------------------------------------------------- I6
def test_i6_doctor_does_not_request_the_demo_scope(tmp_path, monkeypatch):
    _isolate(tmp_path)
    captured = {}

    def fake_collect(*scopes, **kw):
        captured["scopes"] = scopes
        return []

    monkeypatch.setattr(health, "collect", fake_collect)
    cli.main(["doctor"])
    assert "demo" not in captured["scopes"]


def test_i6_doctor_does_not_report_a_fabricated_demo_error(tmp_path, monkeypatch, capsys):
    """A non-editable pip install ships no demo data root -- E4 is real, but `doctor` is
    not a demo entry point, so it must not turn that into an error on an otherwise
    healthy install."""
    _isolate(tmp_path)
    home.ensure_registry("personal")
    from kamino import paths, preflight
    monkeypatch.setattr(paths, "demo_roots", lambda: [])
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "1.0.0"))

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "E4" not in out
    assert "demo-root-missing" not in out
