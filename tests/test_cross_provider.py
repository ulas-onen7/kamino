#!/usr/bin/env python3
"""Cross-provider consent (launch review P0-4): a codex-origin clone deployed on the claude
CLI sends its transcript to a provider that never saw the original conversation. The old
behavior was a stderr note AFTER deployment; these tests pin the new contract -- refused
BEFORE anything is sent, per-call flag or standing policy to consent.
Run: python -m pytest tests/test_cross_provider.py
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kamino import commander, home
from kamino import runtime as kr


CODEX_CARD = {"id": "codex-work", "class": "backend", "blurb": "b" * 60, "blob": "/nowhere",
              "origin": "codex", "files": [], "transcript_tokens": 100}


def _route_then_respond(monkeypatch, clone_id="codex-work"):
    """_claude answers the route call with a decision picking `clone_id`, and every later
    call (the respond stage) with a canned relay."""
    calls = {"n": 0}

    def fake(args, prompt, cwd=None, timeout=600):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"result": json.dumps({"clone_id": clone_id, "question": "q",
                                          "reason": "match", "mode": "deploy"}),
                    "usage": {}}
        return {"result": "relayed", "usage": {}}
    monkeypatch.setattr(commander.kr, "_claude", fake)
    return calls


def test_blocked_before_any_send_by_default(monkeypatch):
    _route_then_respond(monkeypatch)

    def never(*a, **k):
        raise AssertionError("deploy ran: transcript was sent without consent")
    monkeypatch.setattr(commander.kr, "deploy", never)
    events = []
    r = commander.handle([CODEX_CARD], "how does auth work?",
                         emit=lambda ev, d: events.append(ev))
    assert r["error"] == commander.CROSS_PROVIDER_BLOCKED
    assert r["routed_to"] == "codex-work"      # the miss is visible, not silent
    assert "blocked" in events and "deploying" not in events


def test_flag_allows_the_deploy(monkeypatch):
    _route_then_respond(monkeypatch)
    deployed = {}

    def fake_deploy(blob, reqs, **kw):
        deployed["blob"] = blob
        return {"final_answer": "from the clone", "error": None,
                "turn1_subagent_input_total": 1, "deploy_cost_usd": 0.0}
    monkeypatch.setattr(commander.kr, "deploy", fake_deploy)
    r = commander.handle([CODEX_CARD], "q", allow_cross_provider=True)
    assert deployed and r["error"] is None and r["clone_answer"] == "from the clone"


def test_claude_origin_clones_are_untouched(monkeypatch):
    _route_then_respond(monkeypatch, clone_id="native")
    card = {**CODEX_CARD, "id": "native", "origin": None}
    deployed = {}

    def fake_deploy(blob, reqs, **kw):
        deployed["ok"] = True
        return {"final_answer": "fine", "error": None,
                "turn1_subagent_input_total": 1, "deploy_cost_usd": 0.0}
    monkeypatch.setattr(commander.kr, "deploy", fake_deploy)
    r = commander.handle([card], "q")           # no flag, no policy
    assert deployed and r["error"] is None


def test_standing_policy_env_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path))
    monkeypatch.delenv("KAMINO_ALLOW_CROSS_PROVIDER", raising=False)
    assert home.cross_provider_allowed() is False
    monkeypatch.setenv("KAMINO_ALLOW_CROSS_PROVIDER", "1")
    assert home.cross_provider_allowed() is True
    monkeypatch.delenv("KAMINO_ALLOW_CROSS_PROVIDER")
    (tmp_path / "policy.json").write_text(json.dumps({"cross_provider_reads": True}),
                                          encoding="utf-8")
    assert home.cross_provider_allowed() is True
    (tmp_path / "policy.json").write_text(json.dumps({"cross_provider_reads": False}),
                                          encoding="utf-8")
    assert home.cross_provider_allowed() is False
    (tmp_path / "policy.json").write_text("not json", encoding="utf-8")
    assert home.cross_provider_allowed() is False   # malformed policy never grants consent


def test_cli_passes_flag_and_policy_through(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from kamino import cli
    from kamino import registry as reg
    monkeypatch.setenv("KAMINO_HOME", str(tmp_path))
    monkeypatch.delenv("KAMINO_ALLOW_CROSS_PROVIDER", raising=False)
    monkeypatch.setattr(cli, "_guard", lambda *a, **k: 0)
    monkeypatch.setattr(cli.corpus, "maybe_sync", lambda: None)
    monkeypatch.setattr(reg, "_scan_cards", lambda p: ([CODEX_CARD], []))
    seen = {}

    def fake_handle(roster, q, emit=None, model=None, allow_cross_provider=False):
        seen["allow"] = allow_cross_provider
        return {"routed_to": None, "final_answer": "x", "recommend_promote": False}
    monkeypatch.setattr(cli.commander, "handle", fake_handle)
    args = SimpleNamespace(question="q", model=None, allow_cross_provider=False)
    cli.cmd_ask(args)
    assert seen["allow"] is False
    args.allow_cross_provider = True
    cli.cmd_ask(args)
    assert seen["allow"] is True
    args.allow_cross_provider = False
    monkeypatch.setenv("KAMINO_ALLOW_CROSS_PROVIDER", "1")
    cli.cmd_ask(args)
    assert seen["allow"] is True
