#!/usr/bin/env python3
"""`kamino ask` must say WHICH clone answered.

The commander returns `routed_to` but cmd_ask never printed it, so an answer arrived
unattributable: a user (or host agent relaying the answer) could not judge its trustworthiness
or know which clone to promote/retire. The attribution goes to STDERR so stdout remains a pure
answer for pipelines; hosts read both streams. Offline: _claude monkeypatched, no spend.
"""
import argparse
import json

from kamino import cli, home
from kamino import registry as reg
from kamino import runtime as kr


def _fake_claude(clone_id):
    def fake(args, prompt, cwd=None, timeout=600):
        if "Clone Commander" in prompt and "--session-id" in args:
            return {"result": json.dumps({"clone_id": clone_id, "question": "q",
                                          "reason": "r", "mode": "deploy"})}
        return {"result": "the answer text"}
    return fake


def _run_ask(tmp_path, monkeypatch, clone_id):
    regp = str(tmp_path / "registry")
    reg.recruit_body("USER: q\n\nASSISTANT: a.", regp, "clone-x",
                     "knows the widget spin behavior and retry policy for the test suite")
    monkeypatch.setattr(home, "registry_path", lambda name=None: regp)
    monkeypatch.setattr(cli, "_guard", lambda *a, **k: 0)
    monkeypatch.setattr(kr, "_claude", _fake_claude(clone_id))
    return cli.cmd_ask(argparse.Namespace(question="how does the widget spin?", model=None))


def test_ask_prints_attribution_on_stderr(tmp_path, monkeypatch, capsys):
    rc = _run_ask(tmp_path, monkeypatch, "clone-x")
    out, err = capsys.readouterr()
    assert rc == 0
    assert "the answer text" in out
    assert "(via clone-x)" in err, f"attribution missing from stderr: {err!r}"
    assert "(via" not in out, f"attribution must not pollute the stdout answer: {out!r}"


def test_ask_declined_prints_no_attribution(tmp_path, monkeypatch, capsys):
    rc = _run_ask(tmp_path, monkeypatch, None)
    out, err = capsys.readouterr()
    assert rc == 0
    assert "(via" not in out and "(via" not in err, (out, err)
