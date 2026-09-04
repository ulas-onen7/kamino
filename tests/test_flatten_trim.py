import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino.flatten import flatten_body  # noqa: E402


def _session(tmp_path):
    p = tmp_path / "s.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "design the API"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "here is the design"}},
        {"type": "user", "message": {"role": "user", "content": "save this as a clone"}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def test_keeps_last_user_turn_by_default(tmp_path):
    body = flatten_body(_session(tmp_path))
    assert "save this as a clone" in body


def test_drops_last_user_turn(tmp_path):
    body = flatten_body(_session(tmp_path), drop_last_user_turn=True)
    assert "save this as a clone" not in body
    assert "here is the design" in body
