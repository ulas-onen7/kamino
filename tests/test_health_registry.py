import builtins
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import health              # noqa: E402
from kamino import registry as reg     # noqa: E402

BLURB = ("Knows the alpha service: its schema, its deploy path, and why the retry "
         "budget is set where it is.")


def _session(tmp_path, name):
    p = tmp_path / f"{name}.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": f"work {name}"}},
             {"type": "assistant", "message": {"role": "assistant", "content": f"done {name}"}}]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def _registry(tmp_path, *ids):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    for cid in ids:
        reg.recruit(_session(tmp_path, cid), regp, cid, BLURB)
    return regp


def _checks(findings):
    return sorted(f["check"] for f in findings)


def test_healthy_registry_reports_nothing(tmp_path):
    regp = _registry(tmp_path, "clone-a", "clone-b")
    assert health.inspect_registry(regp) == []


def test_d10_transcript_over_window_is_a_warning_not_an_error(tmp_path):
    """A transcript past the consult ceiling can never be read on a default-window model --
    doctor must say so (#19) -- but the clone stays usable for larger-window readers, so it
    must not be dropped or block verbs."""
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit_body("x" * ((health.CONSULT_CEILING_TOKENS + 1000) * 4), regp, "clone-huge", BLURB)
    fs = [f for f in health.inspect_registry(regp) if f["check"] == "D10"]
    assert len(fs) == 1, fs
    assert fs[0]["severity"] == "warn" and fs[0]["subject"] == "clone-huge", fs[0]
    entries, _ = reg._scan_cards(regp)
    assert [e["id"] for e in entries] == ["clone-huge"], "over-window clone must stay in the roster"


def test_d10_silent_for_a_normal_transcript(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    assert [f for f in health.inspect_registry(regp) if f["check"] == "D10"] == []


def test_d1_blob_missing_is_an_error_and_drops_the_entry(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    blob = next((tmp_path / "registry" / "blobs").iterdir())
    blob.unlink()
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D1"]
    assert findings[0]["severity"] == "error"
    assert findings[0]["subject"] == "clone-a"
    assert findings[0]["fix"]
    assert reg.load_roster(regp) == []


def test_d3_id_missing_is_an_error(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    card = Path(regp) / "cards" / "clone-a.md"
    card.write_text(card.read_text(encoding="utf-8").replace("id: clone-a\n", ""),
                    encoding="utf-8")
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D3"]
    assert findings[0]["name"] == "id-missing"
    assert findings[0]["severity"] == "error"
    assert reg.load_roster(regp) == []


def test_d3_id_mismatch_is_an_error(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    card = Path(regp) / "cards" / "clone-a.md"
    card.write_text(card.read_text(encoding="utf-8").replace("id: clone-a", "id: clone-zzz"),
                    encoding="utf-8")
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D3"]
    assert findings[0]["name"] == "id-mismatch"
    assert findings[0]["severity"] == "error"
    assert findings[0]["fix"] == "set `id: clone-a` in cards/clone-a.md"
    assert reg.load_roster(regp) == []


def test_a_broken_clone_does_not_hide_its_healthy_neighbours(tmp_path):
    regp = _registry(tmp_path, "clone-a", "clone-b")
    next((tmp_path / "registry" / "blobs").iterdir()).unlink()
    assert len(reg.load_roster(regp)) == 1
    assert len(health.inspect_registry(regp)) == 1


def test_d2_digest_mismatch_when_a_blob_is_edited_in_place(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    blob = next((tmp_path / "registry" / "blobs").iterdir())
    blob.write_text(blob.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D2"]
    assert findings[0]["severity"] == "error"
    assert reg.load_roster(regp)[0]["id"] == "clone-a"   # still usable, still reported


def test_d4_thin_description_is_an_error_but_keeps_the_clone_visible(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "clone-a"), regp, "clone-a", "alpha specialist")
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D4"]
    assert findings[0]["severity"] == "error"
    assert reg.load_roster(regp)[0]["id"] == "clone-a"


def test_d4_accepts_a_description_that_can_actually_route(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    assert health.inspect_registry(regp) == []


def test_description_routable_judges_only_the_card_being_written():
    assert health.description_routable("clone-new", BLURB) == []
    problems = health.description_routable("clone-new", "alpha specialist")
    assert problems[0]["check"] == "D4" and problems[0]["subject"] == "clone-new"


def test_d2_is_not_paid_for_on_the_hot_roster_path(tmp_path, monkeypatch):
    """Pins "no content read", not merely "no finding emitted" -- the two differ. A
    regression that hoists the read out of the `deep` guard while leaving the finding
    gated would satisfy the weaker assertion while reading every blob on every
    load_roster call, which is the corpus-scaling failure the docstring warns against."""
    regp = _registry(tmp_path, "clone-a")
    blob = next((tmp_path / "registry" / "blobs").iterdir())
    blob.write_text("tampered", encoding="utf-8")

    opened, real_open = [], builtins.open

    def recording_open(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", recording_open)
    reg._scan_cards(regp)                                   # shallow
    assert not any("blobs" in p for p in opened), opened
    opened.clear()
    assert reg._scan_cards(regp, deep=True)[1] != []        # deep reads, and reports
    assert any("blobs" in p for p in opened)


def test_d2_tolerates_a_blob_recruited_from_crlf_content(tmp_path):
    """A session whose real turn content holds literal \\r\\n (e.g. output pasted from a
    Windows tool) must recruit clean. Reading the blob back in text mode applies universal-
    newline translation (\\r\\n -> \\n) before the digest check hashes it, so the re-encoded
    string does not reproduce the bytes actually on disk and D2 false-positives on an intact
    clone -- which blocks serve/ask/promote since D2 is an error."""
    p = tmp_path / "crlf.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": "line1\r\nline2\r\nline3"}},
             {"type": "assistant", "message": {"role": "assistant", "content": "done"}}]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(str(p), regp, "clone-a", BLURB)
    findings = health.inspect_registry(regp)
    assert _checks(findings) == []


def test_d5_unparseable_frontmatter_drops_the_entry(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    (Path(regp) / "cards" / "clone-a.md").write_text("no frontmatter here\n", encoding="utf-8")
    findings = health.inspect_registry(regp)
    # D5 drops the card before it reaches D7's keep-set, so its blob is now
    # genuinely unreferenced -- D7 reporting it alongside D5 is correct, not noise.
    assert _checks(findings) == ["D5", "D7"]
    assert findings[0]["name"] == "unparseable-frontmatter"
    assert findings[0]["severity"] == "error"
    assert reg.load_roster(regp) == []


def test_d5_invalid_files_json_keeps_the_clone_but_reports(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    card = Path(regp) / "cards" / "clone-a.md"
    text = card.read_text(encoding="utf-8").replace("class: coding",
                                                    "class: coding\nfiles: {not json")
    card.write_text(text, encoding="utf-8")
    findings = health.inspect_registry(regp)
    assert [f["name"] for f in findings] == ["files-json-invalid"]
    assert findings[0]["check"] == "D5"
    assert findings[0]["severity"] == "error"
    assert reg.load_roster(regp)[0]["id"] == "clone-a"


def test_d5_invalid_provenance_json_keeps_the_clone_but_reports(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    card = Path(regp) / "cards" / "clone-a.md"
    text = card.read_text(encoding="utf-8").replace("class: coding",
                                                    "class: coding\nprovenance: {not json")
    card.write_text(text, encoding="utf-8")
    findings = health.inspect_registry(regp)
    assert [f["name"] for f in findings] == ["provenance-json-invalid"]
    assert findings[0]["check"] == "D5"
    assert findings[0]["severity"] == "error"
    assert reg.load_roster(regp)[0]["id"] == "clone-a"


@pytest.mark.parametrize("bad_files_json", ["5", "null", "[1,2,3]", '{"a":1}'])
def test_d5_malformed_files_shape_is_reported_not_a_crash(tmp_path, bad_files_json):
    """`files:` that parses as JSON but is not a list of objects (int, null, a list of
    non-dicts, a bare object) used to be silently tolerated by the old bare `except
    Exception: pass`. Rejecting only bad syntax and not bad shape turns that silent
    tolerance into a crash that kills the whole roster -- worse than the silence it
    replaced. Each of these four must report and survive, not raise."""
    regp = _registry(tmp_path, "clone-a")
    card = Path(regp) / "cards" / "clone-a.md"
    text = card.read_text(encoding="utf-8").replace(
        "class: coding", f"class: coding\nfiles: {bad_files_json}")
    card.write_text(text, encoding="utf-8")
    findings = health.inspect_registry(regp)
    assert [f["name"] for f in findings] == ["files-json-invalid"]
    assert findings[0]["check"] == "D5"
    assert findings[0]["severity"] == "error"
    roster = reg.load_roster(regp)          # must not raise
    assert roster[0]["id"] == "clone-a"


def test_d5_files_manifest_does_not_leak_into_the_next_card_without_one(tmp_path):
    """`manifest` has no per-iteration scope of its own -- Python reuses the same loop
    body's locals across iterations. A card with no `files:` key must not inherit the
    previous card's parsed manifest just because that variable was never reassigned
    this time round."""
    regp = str(tmp_path / "registry")
    reg.init(regp)
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("bundled artifact content", encoding="utf-8")
    reg.recruit(_session(tmp_path, "clone-a"), regp, "clone-a", BLURB, files=[str(artifact)])
    reg.recruit(_session(tmp_path, "clone-b"), regp, "clone-b", BLURB)
    roster = {c["id"]: c for c in reg.load_roster(regp)}
    assert roster["clone-a"]["files"]                       # its own manifest resolved
    assert roster["clone-b"]["files"] == []                 # not clone-a's leaking in


def test_d6_missing_bundled_file_is_a_warning(tmp_path):
    art = tmp_path / "spec.md"
    art.write_text("the real bytes\n", encoding="utf-8")
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "clone-a"), regp, "clone-a", BLURB, files=[str(art)])
    next((Path(regp) / "files").iterdir()).unlink()
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D6"]
    assert findings[0]["severity"] == "warn"
    assert reg.load_roster(regp)[0]["files"] == []


def test_d7_orphan_blob_is_a_warning(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    (Path(regp) / "blobs" / "clone-deadbeefdeadbeef.txt").write_text("stray", encoding="utf-8")
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D7"]
    assert "clone-deadbeefdeadbeef.txt" in findings[0]["detail"]


def test_d9_empty_registry_is_info_not_a_problem(tmp_path):
    regp = str(tmp_path / "registry")
    reg.init(regp)
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D9"]
    assert findings[0]["severity"] == "info"


def test_registry_level_checks_are_skipped_for_a_single_clone(tmp_path):
    regp = _registry(tmp_path, "clone-a")
    (Path(regp) / "blobs" / "clone-deadbeefdeadbeef.txt").write_text("stray", encoding="utf-8")
    assert health.inspect_registry(regp, clone_id="clone-a") == []


def test_d7_keep_set_is_not_leaked_from_an_earlier_cards_manifest(tmp_path):
    """`used` must reflect each card's OWN manifest, read fresh every iteration. Card ids are
    chosen so the no-files card ("clone-a") sorts and is processed BEFORE the card with a
    bundled file ("clone-b") -- the ordering that pins the exact hazard: if `manifest` were
    only bound inside `if meta.get("files"):` instead of unconditionally at the top of the
    loop body, this first, files-less iteration would read `manifest` before it was ever
    assigned. A registry-level scan must not crash on that, and clone-b's genuinely bundled
    file must count as used, not orphaned."""
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "clone-a"), regp, "clone-a", BLURB)
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("bundled artifact content", encoding="utf-8")
    reg.recruit(_session(tmp_path, "clone-b"), regp, "clone-b", BLURB, files=[str(artifact)])
    assert health.inspect_registry(regp) == []


def test_d7_agrees_with_the_collector_about_a_recoverable_card(tmp_path):
    """The invariant D7 exists to protect: it must never call something an orphan that
    `_gc_orphans` would actually keep. A card with a missing `id:` is D3-broken, but
    `_gc_orphans` reads `snapshot_ref` straight off the card regardless of id -- it keeps
    this blob. If D7's keep-set were harvested only after the D3 `continue`, it would call
    this same, still-recoverable blob an orphan, and D7's fix says to delete orphans by
    hand: a user following that advice for a one-line `id:` fix would destroy a live
    transcript. Assert the two actually agree, not a hardcoded expectation of either one."""
    regp = _registry(tmp_path, "clone-a")
    card = Path(regp) / "cards" / "clone-a.md"
    card.write_text(card.read_text(encoding="utf-8").replace("id: clone-a\n", ""),
                    encoding="utf-8")
    findings = health.inspect_registry(regp)
    assert _checks(findings) == ["D3"]
    blob = next((Path(regp) / "blobs").iterdir())
    removed = reg._gc_orphans(regp)
    assert removed == []
    assert blob.exists()


def test_d7_does_not_call_a_mixed_shape_manifests_ref_an_orphan(tmp_path):
    """A `files:` manifest that fails shape validation (JSON-valid, but not a uniform
    list of objects) still gets partially processed by `_gc_orphans`'s bare
    `except Exception: pass`: it adds each dict entry's ref to the keep-set as it goes,
    and only the first non-dict entry aborts the loop. D5's own harvest used to stop at
    the same shape check and keep nothing, so a file this card actually references would
    read as orphaned -- and D7's fix string says to delete orphans by hand. Assert D7
    never names a ref `_gc_orphans` would keep, rather than hardcoding either side's
    exact behaviour.

    `_gc_orphans` itself now refuses outright on ANY D5 finding (this card's
    files-json-invalid included), so it never gets far enough to run its own
    per-manifest salvage here -- it keeps everything by refusing wholesale. That
    still agrees with D7: both must consider this ref safe, never orphaned."""
    art = tmp_path / "spec.md"
    art.write_text("the real bytes\n", encoding="utf-8")
    regp = str(tmp_path / "registry")
    reg.init(regp)
    reg.recruit(_session(tmp_path, "clone-a"), regp, "clone-a", BLURB, files=[str(art)])
    card = Path(regp) / "cards" / "clone-a.md"
    text = card.read_text(encoding="utf-8")
    files_line = next(ln for ln in text.splitlines() if ln.startswith("files:"))
    manifest = json.loads(files_line[len("files:"):].strip())
    mixed = json.dumps(manifest + [2])           # [{"ref": ..., ...}, 2] -- dict, then junk
    text = text.replace(files_line, f"files: {mixed}")
    card.write_text(text, encoding="utf-8")

    findings = health.inspect_registry(regp)
    assert [f["name"] for f in findings] == ["files-json-invalid"]
    orphan_findings = [f for f in findings if f["check"] == "D7"]
    assert orphan_findings == []                 # D5 reports; D7 must not ALSO call it orphaned

    ref = manifest[0]["ref"]
    bundled = Path(regp) / ref
    assert bundled.exists()
    with pytest.raises(ValueError):              # the D5 gate refuses wholesale
        reg._gc_orphans(regp)
    assert bundled.exists()                       # nothing was deleted either way
