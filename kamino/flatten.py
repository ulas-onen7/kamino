#!/usr/bin/env python3
"""Session JSONL -> flattened transcript text (a clone's portable 'memory').

Keeps only the real API turns (user/assistant), serializes tool exchanges as readable markers,
drops native thinking blocks (their conclusions live in the text turns; dropping them keeps the
blob lean and dodges signature issues), and strips junk / inert tool-call litter. Source-agnostic:
a plain-chat transcript (no tool blocks) flattens identically.
"""
import base64
import json
import re
import sys

TOOL_INPUT_TRUNC = 800
TOOL_RESULT_TRUNC = 1500
CHARS_PER_TOKEN = 4   # rough chars->tokens heuristic for the demo's "~N tok" displays


def approx_tokens(text):
    return len(text) // CHARS_PER_TOKEN

# Assistant turns that carry no real content — session-log artifacts, API/policy errors, or
# tool/skill-invocation preambles (e.g. the global `superpowers` plugin makes the model emit
# "let me check for relevant skills"). Dropped so they never enter a clone's "memory".
_JUNK_MARKERS = ("no response requested", "api error:", "violate our usage policy",
                 "let me check for relevant skills", "let me check for any relevant skills",
                 "checking for relevant skills")

# Inert tool-call syntax the model sometimes writes as TEXT when a tool isn't actually available
# (under --tools "" / --strict-mcp-config). Stripped from text blocks before they're kept.
_TOOL_XML = re.compile(r"<function_calls>.*?</function_calls>", re.DOTALL)
_INVOKE_XML = re.compile(r"<invoke\b.*?</invoke>", re.DOTALL)


def _strip_tool_xml(text):
    text = _TOOL_XML.sub("", text)
    text = _INVOKE_XML.sub("", text)
    return text.strip()


# A marker appearing mid-turn only implies junk in a turn short enough to be nothing BUT the
# artifact. Matching `m in t` unanchored at any length silently discarded whole real turns that
# merely *mention* a marker — a debugging session quoting an API error, or any document describing
# this filter (several markers are ordinary English). Prefixes still match at any length.
_JUNK_SUBSTR_MAX = 400


def _is_junk_assistant(text):
    t = text.strip().lower()
    if not t:
        return True
    if any(t.startswith(m) for m in _JUNK_MARKERS):
        return True
    return len(t) <= _JUNK_SUBSTR_MAX and any(m in t for m in _JUNK_MARKERS)


def _content_to_str(c):
    if isinstance(c, list):
        return " ".join(
            x.get("text", "") if isinstance(x, dict) else str(x) for x in c
        )
    return str(c)


def block_to_text(b, keep_thinking=False):
    t = b.get("type")
    if t == "text":
        return _strip_tool_xml(b.get("text", ""))
    if t == "thinking":
        if not keep_thinking:
            return None
        return f"[reasoning: {b.get('thinking', '')}]"
    if t == "tool_use":
        inp = json.dumps(b.get("input", {}), ensure_ascii=False)
        if len(inp) > TOOL_INPUT_TRUNC:
            inp = inp[:TOOL_INPUT_TRUNC] + "…"
        return f"[tool call: {b.get('name')} {inp}]"
    if t == "tool_result":
        c = _content_to_str(b.get("content"))
        if len(c) > TOOL_RESULT_TRUNC:
            c = c[:TOOL_RESULT_TRUNC] + "…[truncated]"
        err = " ERROR" if b.get("is_error") else ""
        return f"[tool result{err}: {c}]"
    return None


def flatten_body(path, keep_thinking=False, drop_last_user_turn=False):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") not in ("user", "assistant"):
                continue
            msg = e.get("message") or {}
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, list):
                blocks = content
            elif isinstance(content, str):
                blocks = [{"type": "text", "text": content}]
            else:
                blocks = []
            parts = []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                txt = block_to_text(b, keep_thinking)
                if txt:
                    parts.append(txt)
            if parts and role:
                joined = "\n".join(parts)
                if role == "assistant" and _is_junk_assistant(joined):
                    continue  # skip "No response requested." / API-error / empty turns
                out.append(f"{role.upper()}: " + joined)
    if drop_last_user_turn and out and out[-1].startswith("USER:"):
        out.pop()
    return "\n\n".join(out)


# Genuine user uploads live as base64 `image`/`document` content blocks inside message turns (an
# inspected real session: 12 image blocks). Top-level `attachment` entries are hook/tool output, not
# uploads, and `file-history-snapshot` is edit-tracking — neither is extracted.
_UPLOAD_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/gif": "gif",
               "image/webp": "webp", "image/svg+xml": "svg", "application/pdf": "pdf",
               "text/plain": "txt", "text/csv": "csv", "text/markdown": "md"}


def extract_uploads(path):
    """Pull genuine uploaded files out of a session: base64 `image`/`document` blocks → [{name, data}].
    These are exactly what the flattened transcript loses, so recruit can bundle their real bytes."""
    ups, n = [], 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (e.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or b.get("type") not in ("image", "document"):
                    continue
                src = b.get("source") or {}
                if src.get("type") != "base64" or not src.get("data"):
                    continue
                n += 1
                mt = src.get("media_type", "application/octet-stream")
                ext = _UPLOAD_EXT.get(mt, mt.split("/")[-1].split("+")[0] if "/" in mt else "bin")
                try:
                    data = base64.b64decode(src["data"])
                except Exception:
                    continue
                ups.append({"name": b.get("title") or f"upload-{n}.{ext}", "data": data})
    return ups


if __name__ == "__main__":
    keep = "--keep-thinking" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    print(flatten_body(args[0], keep_thinking=keep))
