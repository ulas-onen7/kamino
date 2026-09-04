"""Discover + flatten OpenAI Codex CLI sessions ("rollouts") so `kamino recruit-from codex` can
freeze them. Codex stores sessions at ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
(override the root with KAMINO_CODEX_SESSIONS); we only read these files, never write them.

Each line is {"timestamp","type","payload"}. Only `response_item` lines carry the API turns —
`event_msg` re-emits the same content for the UI (dropped), `reasoning` is encrypted (dropped),
and developer-role / wrapper user turns are plumbing, not conversation. Verified against a real
rollout from Codex CLI 0.119.0 (2026-04).
"""
import json
import os
from pathlib import Path

from .flatten import TOOL_INPUT_TRUNC, TOOL_RESULT_TRUNC

_WRAPPER_PREFIXES = ("<environment_context>", "<user_instructions>", "<permissions",
                     "<system", "<turn_aborted")


def _sessions_root():
    return Path(os.environ.get("KAMINO_CODEX_SESSIONS", Path.home() / ".codex" / "sessions"))


def _session_id(path):
    """Prefer the authoritative id from the session_meta first line; fall back to the filename."""
    try:
        with open(path, encoding="utf-8") as f:
            e = json.loads(f.readline())
        if e.get("type") == "session_meta":
            return (e.get("payload") or {}).get("id") or Path(path).stem
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return Path(path).stem


def list_codex_sessions(limit=20):
    root = _sessions_root()
    if not root.exists():
        return []
    items = []
    for jsonl in root.glob("*/*/*/*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        items.append({"session_id": _session_id(jsonl), "path": str(jsonl), "mtime": mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def resolve_codex_session(session_id=None):
    if session_id is None:
        s = list_codex_sessions(limit=1)
        return s[0] if s else None
    for s in list_codex_sessions(limit=10000):
        if s["session_id"] == session_id:
            return s
    return None


def flatten_codex_body(path, drop_last_user_turn=False):
    """Rollout -> the same canonical 'ROLE: text' paragraph format flatten_body produces, so a
    Codex-born blob is indistinguishable from a Claude-born one downstream (serve/deploy/pack)."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") != "response_item":
                continue
            p = e.get("payload") or {}
            pt = p.get("type")
            if pt == "message":
                role = p.get("role")
                if role not in ("user", "assistant"):
                    continue
                text = "\n".join(c.get("text", "") for c in (p.get("content") or [])
                                 if isinstance(c, dict) and c.get("text")).strip()
                if not text:
                    continue
                if role == "user" and text.startswith(_WRAPPER_PREFIXES):
                    continue
                out.append(f"{role.upper()}: {text}")
            elif pt in ("function_call", "custom_tool_call"):
                args = p.get("arguments") or p.get("input") or ""
                if len(args) > TOOL_INPUT_TRUNC:
                    args = args[:TOOL_INPUT_TRUNC] + "…"
                out.append(f"ASSISTANT: [tool call: {p.get('name')} {args}]")
            elif pt in ("function_call_output", "custom_tool_call_output"):
                o = p.get("output") or ""
                if not isinstance(o, str):
                    o = json.dumps(o, ensure_ascii=False)
                if len(o) > TOOL_RESULT_TRUNC:
                    o = o[:TOOL_RESULT_TRUNC] + "…[truncated]"
                out.append(f"USER: [tool result: {o}]")
    if drop_last_user_turn and out and out[-1].startswith("USER:"):
        out.pop()
    return "\n\n".join(out)
