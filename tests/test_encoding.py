#!/usr/bin/env python3
"""Regression: the `claude` subprocess must use UTF-8 explicitly, not the OS locale codec.

On Windows, subprocess text mode defaults to the ANSI code page (e.g. cp1254 on Turkish Windows),
so a Turkish/Unicode prompt or claude's UTF-8 JSON output raised UnicodeEncode/DecodeError and
crashed `kamino ask`. `_claude` must pin encoding='utf-8' (and decode defensively). Offline, no spend.
Run: python tests/test_encoding.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kamino import runtime as kr


class _FakeCompleted:
    def __init__(self, stdout):
        self.stdout, self.stderr, self.returncode = stdout, "", 0


# a prompt that is UNencodable in cp1254 (em-dash is, but CJK + emoji are not) — the real failure shape
_UNICODE_PROMPT = "İŞKUR başvurusu ışğ — 你好 😀"


def _run_capturing(monkeypatch_setattr):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted(json.dumps({"result": "ok"}))

    monkeypatch_setattr(kr.subprocess, "run", fake_run)
    kr._claude(["-p"], _UNICODE_PROMPT)
    return captured


def test_claude_subprocess_pins_utf8(monkeypatch):
    captured = _run_capturing(monkeypatch.setattr)
    assert captured.get("encoding") == "utf-8", \
        f"_claude must pin encoding='utf-8' (Windows locale codec crashes on Unicode); got {captured.get('encoding')!r}"
    assert captured.get("errors") == "replace", \
        f"_claude should decode defensively (errors='replace'); got {captured.get('errors')!r}"


if __name__ == "__main__":
    orig = kr.subprocess.run
    try:
        captured = _run_capturing(lambda obj, name, val: setattr(obj, name, val))
        assert captured.get("encoding") == "utf-8", captured.get("encoding")
        assert captured.get("errors") == "replace", captured.get("errors")
        print("ENCODING OK — _claude pins UTF-8 for the claude subprocess.")
    finally:
        kr.subprocess.run = orig
