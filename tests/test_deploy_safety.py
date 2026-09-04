#!/usr/bin/env python3
"""Validates the read-only deploy boundary — the design's headline safety property — at the
wiring level, offline (monkeypatched _claude, no spend). It asserts deploy hands the CLI a tool
WHITELIST (never a denylist), that permission_denials surface to the caller, and that the
deployed process's CWD is isolated rather than inheriting the commander's ambient (live project)
directory — --allowed-tools whitelists tool NAMES only, so an unisolated cwd would let Read/Grep/
Glob resolve relative paths against whatever real directory the commander happened to be run from.
Run: python tests/test_deploy_safety.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kamino import runtime as kr


def _blob():
    fd, p = tempfile.mkstemp(suffix=".txt")
    os.write(fd, b"USER: hi\n\nASSISTANT: prior work.")
    os.close(fd)
    return p


def test_deploy_uses_whitelist_never_denylist():
    captured = {}
    orig = kr._claude
    kr._claude = lambda args, prompt, **k: (captured.setdefault("args", args), {"result": "ok"})[1]
    blob = _blob()
    try:
        kr.deploy(blob, ["q"], max_turns=1, model="m")
    finally:
        kr._claude = orig
        os.unlink(blob)
    args = captured["args"]
    assert "--allowed-tools" in args, args
    for t in ("Read", "Grep", "Glob"):
        assert t in args, f"{t} missing from whitelist: {args}"
    assert "--disallowed-tools" not in args, f"denylist must never be used: {args}"
    # --allowed-tools is only pre-approval; --tools is what makes other tools NOT EXIST
    assert "--tools" in args, f"--tools availability restriction missing: {args}"
    assert args[args.index("--tools") + 1] == "Read Grep Glob", args
    # without this, a user's own settings.json allow rules (e.g. Bash) apply inside the reader
    assert "--setting-sources" in args, f"ambient-settings isolation missing: {args}"
    assert args[args.index("--setting-sources") + 1] == "", args
    # nothing write-capable is whitelisted
    for t in ("Write", "Edit", "Bash", "NotebookEdit"):
        assert t not in args, f"write-capable tool {t} leaked into the whitelist: {args}"
    print("  ok  deploy restricts tools via --tools + --allowed-tools + --setting-sources \"\"")


def test_deploy_preamble_carries_freeze_date():
    """design 4.6 promised 'a frozen snapshot from <date>' in the seed prompt; it shipped
    dateless because no date existed until frozen_at (#20). Dated cards must surface it,
    undated (pre-#20) cards must keep the old framing untouched."""
    captured = {}
    orig = kr._claude
    kr._claude = lambda args, prompt, **k: (captured.setdefault("prompt", prompt), {"result": "ok"})[1]
    blob = _blob()
    try:
        kr.deploy(blob, ["q"], max_turns=1, model="m", frozen_at="2026-08-15T17:00:00+00:00")
        dated = captured.pop("prompt")
        kr.deploy(blob, ["q"], max_turns=1, model="m")
        undated = captured.pop("prompt")
    finally:
        kr._claude = orig
        os.unlink(blob)
    assert "frozen snapshot from 2026-08-15" in dated, dated[:300]
    assert "frozen snapshot from" not in undated, undated[:300]
    print("  ok  deploy preamble names the freeze date when the card has one (#20)")


def test_deploy_never_inherits_ambient_cwd():
    """The one prior real gap: deploy() with no bundled files defaulted cwd=None, which a
    subprocess resolves to the CALLER's cwd -- in production that's wherever `kamino ask` was
    invoked, typically the user's live current project. That handed the deployed clone's
    Read/Grep/Glob a real, current, unrelated directory to search -- not a documented feature
    (the only stated reason for granting Read is re-opening the clone's OWN bundled files), so
    isolating it costs nothing. This proves deploy always gets an isolated, empty scratch dir
    when no files are bundled, regardless of what directory this test itself runs from."""
    captured = {}

    def fake(args, prompt, cwd=None, **k):
        # must inspect the scratch dir NOW -- deploy()'s own `finally` rmtree's it once this
        # call returns, before the test gets control back
        captured["cwd"] = cwd
        captured["was_dir"] = cwd is not None and os.path.isdir(cwd)
        captured["listing"] = os.listdir(cwd) if captured["was_dir"] else None
        return {"result": "ok"}

    orig = kr._claude
    kr._claude = fake
    blob = _blob()
    here = os.getcwd()
    try:
        kr.deploy(blob, ["q"], max_turns=1, model="m")
    finally:
        kr._claude = orig
        os.unlink(blob)
    cwd = captured["cwd"]
    assert cwd is not None, "deploy must never pass cwd=None through to the CLI subprocess"
    assert os.path.abspath(cwd) != os.path.abspath(here), \
        f"deploy inherited the caller's ambient cwd instead of an isolated scratch dir: {cwd}"
    assert captured["was_dir"], cwd
    assert captured["listing"] == [], f"scratch dir should start empty, found: {captured['listing']}"
    print("  ok  deploy with no bundled files gets an isolated empty scratch dir, not the caller's cwd")


def test_permission_denials_surface():
    orig = kr._claude
    kr._claude = lambda args, prompt, **k: {"result": "answer",
                                            "permission_denials": [{"tool_name": "Write"}]}
    blob = _blob()
    try:
        r = kr.deploy(blob, ["q"], max_turns=1, model="m")
    finally:
        kr._claude = orig
        os.unlink(blob)
    assert r["permission_denials"] == [[{"tool_name": "Write"}]], r["permission_denials"]
    print("  ok  permission_denials surface to the caller (read-only enforcement is observable)")


if __name__ == "__main__":
    test_deploy_uses_whitelist_never_denylist()
    test_deploy_never_inherits_ambient_cwd()
    test_permission_denials_surface()
    print("DEPLOY-SAFETY OK — whitelist boundary + cwd isolation + denial visibility validated, "
          "no spend.")
