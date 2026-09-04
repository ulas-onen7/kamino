"""Discover Claude Code sessions on disk so `kamino recruit` can pick one without a JSONL path.

Claude Code stores sessions at ~/.claude/projects/<project>/<session-id>.jsonl (override the root
with KAMINO_CLAUDE_PROJECTS). We only read these files; we never write them.
"""
import json
import os
from pathlib import Path


def _projects_root():
    return Path(os.environ.get("KAMINO_CLAUDE_PROJECTS", Path.home() / ".claude" / "projects"))


def first_user_preview(path, limit=80):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "user":
                    continue
                content = (e.get("message") or {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text")
                else:
                    text = ""
                text = text.strip().replace("\n", " ")
                if text:
                    return text[:limit]
    except OSError:
        pass
    return ""


def list_sessions(limit=20):
    root = _projects_root()
    if not root.exists():
        return []
    items = []
    for jsonl in root.glob("*/*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        items.append({"session_id": jsonl.stem, "path": str(jsonl),
                      "project": jsonl.parent.name, "mtime": mtime,
                      "preview": first_user_preview(jsonl)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def latest_session():
    s = list_sessions(limit=1)
    return s[0] if s else None


def resolve_session(session_id=None):
    if session_id is None:
        return latest_session()
    for s in list_sessions(limit=10000):
        if s["session_id"] == session_id:
            return s
    return None
