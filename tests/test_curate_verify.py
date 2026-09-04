"""T3+T4: draft intake/storage and the mechanical species-aware verifier."""
import json
import stat
from datetime import datetime, timezone

import pytest

from tests.conftest import posix_perms

from kamino import cli, corpus, curate, preflight, propose

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

SRC1 = ("USER: how does the value report pipeline work\n\n"
        "ASSISTANT: reading it now.\n"
        '[tool call: Read {"file_path": "/home/u/acme/services/value_report.py"}]\n\n'
        "ASSISTANT: value_report.py aggregates declarations by workplace registry "
        "number, then renders the monthly document. See also PROJ-142.\n")
SRC2 = ("USER: remind me where corrections land\n\n"
        "ASSISTANT: reading the client.\n"
        '[tool call: Read {"file_path": "/home/u/acme/services/client.py"}]\n\n'
        "ASSISTANT: client.py fetches declarations; corrections re-open the month and "
        "mark the rendered report stale so the scheduler rebuilds it.\n")

GOOD_DRAFT = """# AcmeCo value report pipeline

## What this covers
How the monthly value report is produced and how corrections flow through it.

## Concrete facts
- /home/u/acme/services/value_report.py aggregates declarations by workplace
  registry number and renders the monthly document.
- /home/u/acme/services/client.py fetches the declarations.
- Ticket PROJ-142 tracks this work.

## Decisions and why
Corrections re-open the affected month rather than patching totals in place, so the
rendered report is marked stale and rebuilt by the scheduler.

## Gotchas
A stale report is only rebuilt on the scheduler pass; nothing rebuilds it inline.

## Open ends
Backfill of archived periods was never verified end to end.
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(corpus, "maybe_sync", lambda *a, **k: None)
    # cli.main's spend-path guard calls the real preflight check; CI has no `claude` on PATH.
    monkeypatch.setattr(preflight, "check_claude", lambda: (True, "claude"))
    return corpus.ensure_store()


def _put(store, sid, text):
    d = store / "sessions" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"session_id": sid, "tool": "claude", "src": "", "chars": len(text),
            "end": "2026-07-25T10:00:00.000Z", "start": "2026-07-25T09:00:00.000Z",
            "pinned": True, "tier": "full", "user_turns": 3, "opener": "op " + sid,
            "link": None, "flags": {}, "ingested_at": "2026-07-25T10:00:00.000Z",
            "cwd": "/home/u/acme", "project_slug": None, "pseudo_project": None}
    (d / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / f"{sid}.txt").write_text(text, encoding="utf-8")


def _seed(store, species="knowledge"):
    _put(store, "conv1", SRC1)
    _put(store, "conv2", SRC2)
    ents = ["/home/u/acme/services/value_report.py",
            "/home/u/acme/services/client.py", "PROJ-142"]
    cand = {"cluster_id": "c000", "score": 25.0, "species": species,
            "project": "/home/u/acme", "n_in_window": 2, "n_evidence_only": 0,
            "why": ["2 distinct conversations"], "signals": {},
            "shared_read_targets": ents[:2], "shared_entities": ents,
            "members": [{"conv_id": c, "tool": "claude", "project": "/home/u/acme",
                         "start": "2026-07-20", "end": "2026-07-25", "n_sessions": 1,
                         "opener": f"op {c}", "countable": True}
                        for c in ("conv1", "conv2")]}
    propose.refresh_proposals({"candidates": [cand]}, now=NOW)
    data = propose.load_proposals()
    data["records"][0]["state"] = "accepted"
    propose.save_proposals(data)
    return data["records"][0]


def _check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def test_clean_knowledge_draft_passes(store):
    rec = _seed(store)
    report = curate.verify(GOOD_DRAFT, rec)
    assert report["ok"] is True
    assert _check(report, "coverage")["value"] == 1.0


def test_invented_path_fails(store):
    rec = _seed(store)
    draft = GOOD_DRAFT.replace("## Gotchas",
                               "- /home/u/acme/services/invented.py does the retry\n\n"
                               "## Gotchas")
    report = curate.verify(draft, rec)
    assert report["ok"] is False
    c = _check(report, "unsupported-entities")
    assert c["ok"] is False
    assert "/home/u/acme/services/invented.py" in c["detail"]


def test_missing_section_fails(store):
    rec = _seed(store)
    draft = GOOD_DRAFT.replace("## Gotchas", "## Notes")
    report = curate.verify(draft, rec)
    assert report["ok"] is False
    assert "## Gotchas" in _check(report, "required-sections")["detail"]


def test_coverage_ratio_reported_and_gated(store):
    rec = _seed(store)
    # a draft that only ever mentions conv1's file: half the sources contributed
    draft = GOOD_DRAFT.replace("- /home/u/acme/services/client.py fetches the "
                               "declarations.\n", "")
    draft = draft.replace("client.py fetches the declarations", "")
    report = curate.verify(draft, rec)
    assert _check(report, "coverage")["value"] == 0.5
    assert _check(report, "coverage")["ok"] is True         # 0.5 is the floor, not a fail


def test_framework_coverage_uses_vocabulary_not_entities(store):
    # Entity-mention coverage would be structurally impossible for a recipe whose
    # whole job is removing the entities; framework drafts are covered by shared
    # content vocabulary instead.
    rec = _seed(store, species="framework")
    clean = ("## What this method produces\nA monthly aggregate report for one subject.\n\n"
             "## Inputs\nsubject id, period, declaration source\n\n"
             "## Steps\n1. Fetch the declarations for the period.\n"
             "2. Aggregate by registry key.\n3. Render, then mark stale on correction.\n\n"
             "## Judgment calls\nPrefer re-opening a period over patching totals.\n\n"
             "## Failure modes\nStale output is only rebuilt on the scheduler pass.\n")
    cov = _check(curate.verify(clean, rec), "coverage")
    assert cov["value"] == 1.0 and "vocabulary" in cov["detail"]
    # a draft about something else entirely shares no vocabulary
    unrelated = clean.replace("declaration", "sourdough").replace("registry", "oven")
    unrelated = ("## What this method produces\nBread.\n\n## Inputs\nflour\n\n"
                 "## Steps\n1. Knead.\n\n## Judgment calls\nnone\n\n"
                 "## Failure modes\nBurnt crust.\n")
    assert _check(curate.verify(unrelated, rec), "coverage")["ok"] is False


def test_framework_draft_must_strip_entities(store):
    rec = _seed(store, species="framework")
    leaky = ("## What this method produces\nA report.\n\n## Inputs\nA subject.\n\n"
             "## Steps\n1. Read /home/u/acme/services/value_report.py\n\n"
             "## Judgment calls\nnone\n\n## Failure modes\nnone\n")
    report = curate.verify(leaky, rec)
    assert report["ok"] is False
    c = _check(report, "entities-stripped")
    assert "/home/u/acme/services/value_report.py" in c["detail"]

    clean = ("## What this method produces\nA monthly aggregate report for one subject.\n\n"
             "## Inputs\nsubject id, period, declaration source\n\n"
             "## Steps\n1. Fetch the declarations for the period.\n"
             "2. Aggregate by registry key.\n3. Render, then mark stale on correction.\n\n"
             "## Judgment calls\nPrefer re-opening a period over patching totals.\n\n"
             "## Failure modes\nStale output is only rebuilt on the scheduler pass.\n")
    assert curate.verify(clean, rec)["ok"] is True


@posix_perms
def test_draft_stored_with_report_and_perms(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT)
    root = corpus.corpus_root()
    md = root / "drafts" / f"{rec['id']}.md"
    js = root / "drafts" / f"{rec['id']}.json"
    assert md.read_text(encoding="utf-8") == GOOD_DRAFT
    assert json.loads(js.read_text(encoding="utf-8"))["ok"] is True
    assert stat.S_IMODE(md.stat().st_mode) == 0o600
    assert propose.load_proposals()["records"][0]["state"] == "accepted"  # no auto-register


def test_redraft_overwrites(store):
    rec = _seed(store)
    curate.submit_draft(rec, GOOD_DRAFT.replace("## Gotchas", "## Notes"))
    assert curate.load_draft(rec)["report"]["ok"] is False
    curate.submit_draft(rec, GOOD_DRAFT)
    assert curate.load_draft(rec)["report"]["ok"] is True


def test_cli_draft_prints_report(store, tmp_path, capsys):
    rec = _seed(store)
    f = tmp_path / "draft.md"
    f.write_text(GOOD_DRAFT, encoding="utf-8")
    assert cli.main(["curate", rec["id"], "--draft", str(f)]) == 0
    out = capsys.readouterr().out
    assert "unsupported-entities" in out and "coverage" in out
    assert "kamino curate" in out                       # tells the user how to approve


def test_cli_draft_failing_exits_nonzero(store, tmp_path, capsys):
    rec = _seed(store)
    f = tmp_path / "bad.md"
    f.write_text(GOOD_DRAFT.replace("## Open ends", "## Loose ends"), encoding="utf-8")
    assert cli.main(["curate", rec["id"], "--draft", str(f)]) == 1
    assert "## Open ends" in capsys.readouterr().out
