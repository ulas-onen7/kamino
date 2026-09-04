import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import draft  # noqa: E402


def test_draft_parses_model_json(monkeypatch):
    monkeypatch.setattr(draft, "_claude", lambda args, prompt: {
        "result": '{"name": "Nginx TLS Hardening", "description": "Hardens nginx for prod TLS.", "class": "sre"}'
    })
    out = draft.draft_card("USER: fix nginx\nASSISTANT: done")
    assert out == {"name": "nginx-tls-hardening", "description": "Hardens nginx for prod TLS.", "class": "sre"}


def test_draft_falls_back_on_unparseable(monkeypatch):
    monkeypatch.setattr(draft, "_claude", lambda args, prompt: {"result": "sorry no json here"})
    out = draft.draft_card("USER: hi")
    assert out == {"name": "clone", "description": "", "class": "coding"}


def test_draft_falls_back_on_error(monkeypatch):
    monkeypatch.setattr(draft, "_claude", lambda args, prompt: {"_timeout": True})
    out = draft.draft_card("USER: hi")
    assert out["name"] == "clone" and out["class"] == "coding"


def test_prompt_asks_for_scope_boundaries():
    """The POSITION is load-bearing, not just the phrase: integrate._blurb_line splits the
    blurb on "Does not cover:" to keep that sentence through truncation, so the prompt must
    pin the sentence to START with exactly that, at the end of the blurb."""
    p = draft._PROMPT
    assert "router" in p
    assert 'starting exactly \\"Does not cover:\\"' in p, \
        "the boundary sentence must be instructed to START with exactly this marker"
    assert "End with one sentence" in p, "a router must be able to rule a clone OUT, not only in"
    from kamino import integrate
    assert integrate.BOUNDARY_MARKER == "Does not cover:", \
        "the prompt and the injection must agree on the marker verbatim"


def test_draft_small_blob_kept_whole(monkeypatch):
    seen = {}

    def fake_claude(args, prompt):
        seen["prompt"] = prompt
        return {"result": '{"name": "x", "description": "y", "class": "coding"}'}

    monkeypatch.setattr(draft, "_claude", fake_claude)
    blob = "USER: fix nginx\nASSISTANT: done"
    draft.draft_card(blob)
    assert blob in seen["prompt"]
    assert draft._OMITTED_MARKER not in seen["prompt"]


def test_draft_large_blob_sees_head_and_tail(monkeypatch):
    """A long session's knowledge lives in its final synthesis turn (design 4.3), so the
    drafter must see the tail -- otherwise the blurb and its "Does not cover:" exclusions
    are drafted blind to the session's conclusions (issue #18)."""
    seen = {}

    def fake_claude(args, prompt):
        seen["prompt"] = prompt
        return {"result": '{"name": "x", "description": "y", "class": "coding"}'}

    monkeypatch.setattr(draft, "_claude", fake_claude)
    head = "HEAD-SENTINEL problem statement"
    tail = "TAIL-SENTINEL final synthesis"
    blob = head + ("\nfiller line of transcript middle" * 5000) + tail
    draft.draft_card(blob)
    p = seen["prompt"]
    assert "HEAD-SENTINEL" in p
    assert "TAIL-SENTINEL" in p
    assert draft._OMITTED_MARKER in p
    body = p.split("<session_transcript>\n", 1)[1].rsplit("\n</session_transcript>", 1)[0]
    assert len(body) <= draft._HEAD_CHARS + draft._TAIL_CHARS + len(draft._OMITTED_MARKER)


def test_slug():
    assert draft.slug("Nginx TLS Hardening!") == "nginx-tls-hardening"
