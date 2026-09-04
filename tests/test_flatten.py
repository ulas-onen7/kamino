#!/usr/bin/env python3
"""Unit tests for flatten.py — the most logic-heavy, most bug-prone module (junk + tool-xml filters).
Pure/offline, no `claude`, no spend. Run: python tests/test_flatten.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kamino.flatten import approx_tokens, flatten_body


def _jsonl(*entries):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return p


def _u(content):
    return {"type": "user", "message": {"role": "user", "content": content}}


def _a(content):
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def test_plain_chat_roundtrips():
    p = _jsonl(_u("hello there"), _a([{"type": "text", "text": "hi back"}]))
    out = flatten_body(p); os.unlink(p)
    assert out == "USER: hello there\n\nASSISTANT: hi back", repr(out)
    print("  ok  plain user/assistant turns -> 'USER:'/'ASSISTANT:' prefixes")


def test_string_content_is_handled():
    # plain-chat transcripts carry content as a bare string, not a block list
    p = _jsonl(_a("just a string"))
    out = flatten_body(p); os.unlink(p)
    assert out == "ASSISTANT: just a string", repr(out)
    print("  ok  bare-string content flattens (source-agnostic)")


def test_junk_assistant_turns_dropped():
    p = _jsonl(
        _u("q"),
        _a([{"type": "text", "text": "No response requested."}]),
        _a([{"type": "text", "text": "API Error: overloaded"}]),
        _a([{"type": "text", "text": "Let me check for relevant skills before responding."}]),
        _a([{"type": "text", "text": "real answer"}]),
    )
    out = flatten_body(p); os.unlink(p)
    assert "No response requested" not in out and "API Error" not in out, out
    assert "relevant skills" not in out, out
    assert "ASSISTANT: real answer" in out, out
    print("  ok  junk turns (no-response / API-error / skill-preamble) dropped")


def test_long_turn_mentioning_marker_survives():
    """A real turn that merely *mentions* a marker must not be discarded. Regression: an
    unanchored substring match dropped whole documents (e.g. one describing this filter)."""
    body = ("Here is the reference. " * 40) + "The filter drops turns containing api error: text."
    assert len(body) > 400, len(body)
    p = _jsonl(_u("q"), _a([{"type": "text", "text": body}]))
    out = flatten_body(p); os.unlink(p)
    assert "Here is the reference." in out, out
    print("  ok  long turn mentioning a marker survives (not junk)")


def test_inert_tool_xml_stripped():
    txt = 'Let me think.\n<function_calls>\n<invoke name="mcp__x__y">\n</invoke>\n</function_calls>\nDone.'
    p = _jsonl(_a([{"type": "text", "text": txt}]))
    out = flatten_body(p); os.unlink(p)
    assert "function_calls" not in out and "invoke" not in out and "mcp__" not in out, out
    assert "Let me think." in out and "Done." in out, out
    print("  ok  inert <function_calls>/<invoke> tool-call text stripped, prose kept")


def test_tool_use_and_result_become_markers():
    p = _jsonl(
        _a([{"type": "tool_use", "name": "Read", "input": {"path": "/x"}}]),
        _u([{"type": "tool_result", "content": "file body"}]),
    )
    out = flatten_body(p); os.unlink(p)
    assert "[tool call: Read" in out and '"path": "/x"' in out, out
    assert "[tool result: file body]" in out, out
    print("  ok  tool_use/tool_result serialized as readable markers")


def test_thinking_dropped_by_default_kept_on_flag():
    p = _jsonl(_a([{"type": "thinking", "thinking": "secret reasoning"},
                   {"type": "text", "text": "visible"}]))
    assert "secret reasoning" not in flatten_body(p)
    assert "secret reasoning" in flatten_body(p, keep_thinking=True)
    os.unlink(p)
    print("  ok  thinking blocks dropped by default, kept with keep_thinking=True")


def test_non_api_entries_skipped():
    p = _jsonl({"type": "system", "message": {"role": "system", "content": "boot"}},
               _a([{"type": "text", "text": "kept"}]))
    out = flatten_body(p); os.unlink(p)
    assert "boot" not in out and out == "ASSISTANT: kept", repr(out)
    print("  ok  non user/assistant entries skipped")


def test_approx_tokens():
    assert approx_tokens("a" * 40) == 10 and approx_tokens("") == 0
    print("  ok  approx_tokens = len//4")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print("FLATTEN OK — junk/tool-xml/thinking/markers/plain-chat all covered, no spend.")
