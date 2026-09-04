# tests/test_rollout.py
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import rollout  # noqa: E402


def _line(t, payload):
    return json.dumps({"timestamp": "2026-07-21T10:00:00.000Z", "type": t, "payload": payload})


def _msg(role, text, ctype=None):
    ctype = ctype or ("output_text" if role == "assistant" else "input_text")
    return _line("response_item",
                 {"type": "message", "role": role, "content": [{"type": ctype, "text": text}]})


def _rollout(tmp_path, sid="0199-aaaa", day="2026/07/21", extra_lines=None):
    d = tmp_path / "sessions"
    for part in day.split("/"):
        d = d / part
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rollout-2026-07-21T10-00-00-{sid}.jsonl"
    lines = [_line("session_meta", {"id": sid, "cwd": "/tmp/proj"}),
             _msg("user", "<environment_context>\n<cwd>/tmp/proj</cwd>\n</environment_context>"),
             _msg("developer", "<permissions instructions>sandbox stuff"),
             _msg("user", "how does auth work here?"),
             _line("response_item", {"type": "reasoning", "summary": [],
                                     "encrypted_content": "gAAAA"}),
             _line("event_msg", {"type": "agent_message", "message": "UI duplicate - drop me"}),
             _line("response_item", {"type": "function_call", "name": "exec_command",
                                     "arguments": "{\"cmd\":\"rg auth\"}", "call_id": "c1"}),
             _line("response_item", {"type": "function_call_output", "call_id": "c1",
                                     "output": "auth.py: uses JWT " + "x" * 3000}),
             _msg("assistant", "Auth uses JWT via auth.py.")]
    lines += extra_lines or []
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_flatten_codex_mapping(tmp_path):
    p = _rollout(tmp_path)
    body = rollout.flatten_codex_body(str(p))
    assert "USER: how does auth work here?" in body
    assert "ASSISTANT: Auth uses JWT via auth.py." in body
    assert "ASSISTANT: [tool call: exec_command" in body
    assert "USER: [tool result:" in body and "…[truncated]" in body
    assert "environment_context" not in body
    assert "permissions instructions" not in body
    assert "UI duplicate" not in body
    assert "gAAAA" not in body


def test_flatten_codex_drop_last_user_turn(tmp_path):
    p = _rollout(tmp_path, extra_lines=[_msg("user", "save this to kamino please")])
    body = rollout.flatten_codex_body(str(p), drop_last_user_turn=True)
    assert "save this to kamino" not in body
    assert body.rstrip().endswith("Auth uses JWT via auth.py.")


def test_list_and_resolve(tmp_path):
    os.environ["KAMINO_CODEX_SESSIONS"] = str(tmp_path / "sessions")
    p1 = _rollout(tmp_path, sid="0199-aaaa", day="2026/07/20")
    p2 = _rollout(tmp_path, sid="0199-bbbb", day="2026/07/21")
    os.utime(p1, (time.time() - 100, time.time() - 100))
    items = rollout.list_codex_sessions()
    assert [i["session_id"] for i in items] == ["0199-bbbb", "0199-aaaa"]
    assert rollout.resolve_codex_session() ["session_id"] == "0199-bbbb"
    assert rollout.resolve_codex_session("0199-aaaa")["path"] == str(p1)
    assert rollout.resolve_codex_session("ghost") is None


def test_list_empty_root(tmp_path):
    os.environ["KAMINO_CODEX_SESSIONS"] = str(tmp_path / "nowhere")
    assert rollout.list_codex_sessions() == []
    assert rollout.resolve_codex_session() is None
