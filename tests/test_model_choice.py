#!/usr/bin/env python3
"""The model is the USER's choice, not the clone's.

A blob is text: any model can read a frozen transcript, so nothing about a clone justifies
pinning the model that happens to have produced it. Recording it did active harm --

  - a pinned model id is a time bomb: when that id is retired, `claude --model <dead-id>` fails
    and every clone carrying it stops deploying, while Kamino's whole premise is that clones are
    durable;
  - it was not even honest provenance -- a Codex-born clone stored a *Claude* id, fabricated so
    the deploy path would not break on a GPT model string.

So cards carry no model, and `--model` is passed to the CLI only when a caller asks for it --
otherwise the user's own configured default applies. Offline: no `claude`, no spend.
"""
import os

from kamino import registry
from kamino import runtime as kr


def _seed(reg, clone_id="alpha"):
    return registry.recruit_body("USER: q\n\nASSISTANT: a", reg, clone_id,
                                 "knows alpha things.", clazz="knowledge")


def _capture(fn, *a, **kw):
    """Run a runtime entry point with _claude stubbed, returning the argv it built."""
    seen = {}
    orig = kr._claude
    kr._claude = lambda args, prompt, **k: (seen.setdefault("args", args), {"result": "ok"})[1]
    try:
        fn(*a, **kw)
    finally:
        kr._claude = orig
    return seen["args"]


def test_card_carries_no_model(tmp_path):
    reg = str(tmp_path / "registry")
    _seed(reg)
    card = open(os.path.join(reg, "cards", "alpha.md"), encoding="utf-8").read()
    assert "model:" not in card, card


def test_recruit_body_return_has_no_model(tmp_path):
    out = _seed(str(tmp_path / "registry"))
    assert "model" not in out, out


def test_roster_entry_has_no_model(tmp_path):
    reg = str(tmp_path / "registry")
    _seed(reg)
    entry = registry.load_roster(reg)[0]
    assert "model" not in entry, entry


def test_legacy_card_with_a_model_line_still_loads(tmp_path):
    """Cards frozen before this change keep their `model:` line. They must still parse and serve;
    the field is simply ignored, so no migration is needed."""
    reg = str(tmp_path / "registry")
    _seed(reg)
    p = os.path.join(reg, "cards", "alpha.md")
    text = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(
        text.replace("class: knowledge", "class: knowledge\nmodel: claude-sonnet-4-6"))
    entry = registry.load_roster(reg)[0]
    assert entry["id"] == "alpha"
    assert os.path.exists(entry["blob"])


def test_deploy_omits_model_when_none(tmp_path):
    blob = tmp_path / "b.txt"
    blob.write_text("USER: q\n\nASSISTANT: a", encoding="utf-8")
    args = _capture(kr.deploy, str(blob), ["what?"], max_turns=1)
    assert "--model" not in args, args


def test_deploy_passes_an_explicit_model(tmp_path):
    blob = tmp_path / "b.txt"
    blob.write_text("USER: q\n\nASSISTANT: a", encoding="utf-8")
    args = _capture(kr.deploy, str(blob), ["what?"], max_turns=1, model="claude-haiku-4-5-20251001")
    assert "--model" in args
    assert args[args.index("--model") + 1] == "claude-haiku-4-5-20251001"


def test_promote_omits_model_when_none(tmp_path):
    blob = tmp_path / "b.txt"
    blob.write_text("USER: q\n\nASSISTANT: a", encoding="utf-8")
    args = _capture(kr.promote, str(blob))
    assert "--model" not in args, args


def test_promote_passes_an_explicit_model(tmp_path):
    blob = tmp_path / "b.txt"
    blob.write_text("USER: q\n\nASSISTANT: a", encoding="utf-8")
    args = _capture(kr.promote, str(blob), model="claude-opus-4-8")
    assert args[args.index("--model") + 1] == "claude-opus-4-8"


def test_resume_session_omits_model_when_none():
    args = _capture(kr.resume_session, "some-sid", "carry on")
    assert "--model" not in args, args
