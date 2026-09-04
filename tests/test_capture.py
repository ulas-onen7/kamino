import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import capture  # noqa: E402


def _write_session(root, project, sid, first_user_text, mtime):
    d = root / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": first_user_text}},
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def test_list_sessions_newest_first(tmp_path):
    os.environ["KAMINO_CLAUDE_PROJECTS"] = str(tmp_path)
    _write_session(tmp_path, "proj-a", "old", "older question", 1000)
    _write_session(tmp_path, "proj-b", "new", "newer question", 2000)
    sessions = capture.list_sessions()
    assert [s["session_id"] for s in sessions] == ["new", "old"]
    assert sessions[0]["preview"] == "newer question"
    assert sessions[0]["project"] == "proj-b"


def test_latest_and_resolve(tmp_path):
    os.environ["KAMINO_CLAUDE_PROJECTS"] = str(tmp_path)
    _write_session(tmp_path, "p", "s1", "q1", 1000)
    _write_session(tmp_path, "p", "s2", "q2", 2000)
    assert capture.latest_session()["session_id"] == "s2"
    assert capture.resolve_session("s1")["session_id"] == "s1"
    assert capture.resolve_session("nope") is None


def test_empty_root(tmp_path):
    os.environ["KAMINO_CLAUDE_PROJECTS"] = str(tmp_path / "missing")
    assert capture.list_sessions() == []
    assert capture.latest_session() is None
