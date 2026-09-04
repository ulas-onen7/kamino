#!/usr/bin/env python3
"""Offline validation for fixes #1 (cache-write guard) and #2 (don't narrate CLI-failure sentinels).

Monkeypatches runtime._claude to simulate timeouts / unreadable responses — no `claude` login, no
spend. Run: python tests/test_failure_handling.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kamino import runtime as kr
from kamino import commander as cmd


def _blob():
    fd, p = tempfile.mkstemp(suffix=".txt")
    os.write(fd, b"USER: hi\n\nASSISTANT: prior work here.")
    os.close(fd)
    return p


def test_deploy_timeout_returns_error_not_sentinel():
    blob = _blob()
    orig = kr._claude
    kr._claude = lambda *a, **k: {"_timeout": True}
    try:
        r = kr.deploy(blob, ["what did we decide?"], max_turns=1, model="claude-sonnet-4-6")
    finally:
        kr._claude = orig
        os.unlink(blob)
    assert r["final_answer"] is None, r["final_answer"]
    assert r["error"] == "timed out", r["error"]
    assert all("_timeout" not in (a or "") for a in r["all_answers"]), r["all_answers"]
    print("  ok  deploy timeout -> final_answer=None, error='timed out', no sentinel leaked")


def test_deploy_parse_error():
    blob = _blob()
    orig = kr._claude
    kr._claude = lambda *a, **k: {"_parse_error": True, "rc": 1, "stderr": "boom"}
    try:
        r = kr.deploy(blob, ["q"], max_turns=1, model="m")
    finally:
        kr._claude = orig
        os.unlink(blob)
    assert r["final_answer"] is None and r["error"] == "returned an unreadable response", r
    print("  ok  deploy parse-error -> error='returned an unreadable response'")


# The exact shape a real over-window rejection returns (captured live 2026-08-15, claude 2.1.226):
# valid JSON, is_error=true, and the API error text inside a present 'result' key -- so a
# result-key-only check classifies it as success (#19).
_OVER_WINDOW = {"type": "result", "subtype": "success", "is_error": True,
                "api_error_status": 400, "terminal_reason": "prompt_too_long",
                "result": "Prompt is too long · the request is ~2501417 tokens (limit 1000000)...",
                "usage": {}, "total_cost_usd": 0}


def test_deploy_api_error_not_narrated_as_answer():
    blob = _blob()
    orig = kr._claude
    kr._claude = lambda *a, **k: dict(_OVER_WINDOW)
    try:
        r = kr.deploy(blob, ["what did we decide?"], max_turns=1, model="m")
    finally:
        kr._claude = orig
        os.unlink(blob)
    assert r["final_answer"] is None, f"API rejection narrated as answer: {r['final_answer']!r}"
    assert "prompt_too_long" in (r["error"] or ""), r["error"]
    assert all("Prompt is too long" not in a for a in r["all_answers"]), r["all_answers"]
    print("  ok  deploy over-window -> final_answer=None, error names prompt_too_long (#19)")


def test_commander_over_window_failure_is_permanent_not_try_again():
    blob = _blob()
    roster = [{"id": "clone-x", "blob": blob, "model": "m", "class": "c",
               "blurb": "knows about widget X", "transcript_tokens": 10}]
    respond_prompts = []

    def fake(args, prompt, cwd=None, timeout=600):
        if "--allowed-tools" in args:                       # the deploy turn -> over-window rejection
            return dict(_OVER_WINDOW)
        if "Clone Commander" in prompt:                     # the route turn -> succeed
            return {"result": json.dumps({"clone_id": "clone-x", "question": "q", "reason": "r"})}
        respond_prompts.append(prompt)
        return {"result": "That clone's transcript is too large for the reader model."}

    orig = kr._claude
    kr._claude = fake
    try:
        out = cmd.handle(roster, "tell me about widget X")
    finally:
        kr._claude = orig
        os.unlink(blob)
    assert out["clone_answer"] is None, out["clone_answer"]
    assert "prompt_too_long" in (out["error"] or ""), out["error"]
    assert respond_prompts and "try again" not in respond_prompts[0], respond_prompts
    assert "retrying will not help" in respond_prompts[0], respond_prompts
    print("  ok  commander over-window -> permanent failure narration, no 'try again' (#19)")


def test_commander_failure_path_tells_user_not_synthesizes_sentinel():
    blob = _blob()
    roster = [{"id": "clone-x", "blob": blob, "model": "m", "class": "c",
               "blurb": "knows about widget X", "transcript_tokens": 10}]

    def fake(args, prompt, cwd=None, timeout=600):
        if "--allowed-tools" in args:                       # the deploy turn -> fail
            return {"_timeout": True}
        if "Clone Commander" in prompt:                     # the route turn -> succeed
            return {"result": json.dumps({"clone_id": "clone-x", "question": "q", "reason": "r"})}
        return {"result": "That specialist was unavailable this time — please try again."}  # respond

    orig = kr._claude
    kr._claude = fake
    try:
        out = cmd.handle(roster, "tell me about widget X")
    finally:
        kr._claude = orig
        os.unlink(blob)
    assert out["routed_to"] == "clone-x", out["routed_to"]
    assert out["clone_answer"] is None, out["clone_answer"]
    assert out["error"] == "timed out", out["error"]
    assert "_timeout" not in out["final_answer"], out["final_answer"]
    assert "unavailable" in out["final_answer"].lower(), out["final_answer"]
    print("  ok  commander routes, clone fails -> user gets 'unavailable', sentinel never narrated")


def test_route_prompt_marks_over_window_clones():
    """The router must know when a pick can only work on a large-window reader (#19)."""
    from kamino import health
    roster = [{"id": "clone-big", "blurb": "big work",
               "transcript_tokens": health.CONSULT_CEILING_TOKENS + 1},
              {"id": "clone-ok", "blurb": "small work", "transcript_tokens": 10}]
    block = cmd._roster_block(roster)
    assert block.count("exceeds a default") == 1, block
    assert block.index("clone-big") < block.index("exceeds a default") < block.index("clone-ok")
    print("  ok  route prompt marks over-window clones, and only those (#19)")


def test_web_cache_guard_no_save_on_failure_but_saves_on_success():
    from kamino import registry as reg
    from kamino import web
    saved = []
    orig_save, orig_claude = web._save, kr._claude
    # Current _save signature: _save(k, key, result)
    web._save = lambda k, key, result: saved.append((k["id"], key))

    # A throwaway Kamino with one recruited clone -- decouples this test from the tracked
    # demo registry, which the public tree ships without.
    root = tempfile.mkdtemp()
    reg.recruit_body("USER: hi\n\nASSISTANT: hello there.", os.path.join(root, "registry"),
                      "clone-x", "a synthetic clone used only for this test's cache-guard check")
    k = web.load_kamino(root)

    # (a) total failure: every call times out -> decline + empty final -> MUST NOT cache
    kr._claude = lambda *a, **kw: {"_timeout": True}
    try:
        web.staged_answer(k, "zzz unique uncached question for guard test", lambda e, d: None)
    finally:
        kr._claude = orig_claude
    assert saved == [], f"cache poisoned on failure: {saved}"
    print("  ok  web: failed turn is NOT written to the committed cache (#1)")

    # (b) success path still caches (guard didn't break normal behavior)
    def ok(args, prompt, cwd=None, timeout=600):
        if "Clone Commander" in prompt:
            cid = k["roster"][0]["id"]
            return {"result": json.dumps({"clone_id": cid, "question": "q", "reason": "r"})}
        if "--allowed-tools" in args:
            return {"result": "a real clone answer"}
        return {"result": "a real synthesized final answer"}
    kr._claude = ok
    try:
        web.staged_answer(k, "zzz another unique uncached question ok path", lambda e, d: None)
    finally:
        kr._claude, web._save = orig_claude, orig_save
    assert len(saved) == 1, f"success path should cache exactly once: {saved}"
    print("  ok  web: successful turn IS cached (guard is not over-eager)")
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_deploy_timeout_returns_error_not_sentinel()
    test_deploy_parse_error()
    test_deploy_api_error_not_narrated_as_answer()
    test_commander_over_window_failure_is_permanent_not_try_again()
    test_commander_failure_path_tells_user_not_synthesizes_sentinel()
    test_web_cache_guard_no_save_on_failure_but_saves_on_success()
    print("FAILURE-HANDLING OK — #1 (cache guard) + #2 (no sentinel narration) validated, no spend.")
