# tests/test_roster_serve.py
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kamino import cli              # noqa: E402
from kamino import registry as reg  # noqa: E402


def _isolate_home(tmp_path):
    os.environ["KAMINO_HOME"] = str(tmp_path)
    os.environ.pop("KAMINO_REGISTRY", None)


def _session(tmp_path, name):
    p = tmp_path / f"{name}.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": f"work {name}"}},
             {"type": "assistant", "message": {"role": "assistant", "content": f"done {name}"}}]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def _recruit(tmp_path, clone_id="clone-a",
             blurb="Knows the alpha service: its schema, its deploy path, and the retry budget."):
    from kamino import home
    regp = str(home.ensure_registry("personal"))
    reg.recruit(_session(tmp_path, clone_id), regp, clone_id, blurb)
    return regp


def test_roster_json_empty(tmp_path, capsys):
    _isolate_home(tmp_path)
    from kamino import home
    home.ensure_registry("personal")
    assert cli.main(["roster"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_roster_json_fields(tmp_path, capsys):
    _isolate_home(tmp_path)
    _recruit(tmp_path)
    assert cli.main(["roster"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["id"] == "clone-a"
    assert data[0]["description"] == (
        "Knows the alpha service: its schema, its deploy path, and the retry budget.")
    assert isinstance(data[0]["tokens"], int)
    assert set(data[0]) == {"id", "class", "description", "tokens"}, data[0]


def test_serve_prints_the_transcript(tmp_path, capsys):
    _isolate_home(tmp_path)
    _recruit(tmp_path)
    assert cli.main(["serve", "clone-a", "--isolated"]) == 0
    out = capsys.readouterr().out
    assert "work clone-a" in out and "done clone-a" in out


def test_serve_emits_a_guard_header(tmp_path, capsys):
    _isolate_home(tmp_path)
    _recruit(tmp_path)
    assert cli.main(["serve", "clone-a", "--isolated"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[kamino:"), out[:80]
    assert "subagent" in out.split("\n")[0]


def test_serve_bare_withholds_and_points_at_isolated(tmp_path, capsys):
    """Bare `serve` is the payload gate: an uninstructed agent must not get the transcript."""
    _isolate_home(tmp_path)
    _recruit(tmp_path)
    assert cli.main(["serve", "clone-a"]) == 0
    out = capsys.readouterr().out
    assert "work clone-a" not in out and "done clone-a" not in out
    assert "kamino serve clone-a --isolated" in out


def test_serve_full_is_accepted_alias(tmp_path, capsys):
    """--full is a no-op kept only because installed instruction files still pass it. Identical
    output, asserted whole, so the alias cannot silently start meaning something again. It does
    not imply --isolated: both stay withheld."""
    _isolate_home(tmp_path)
    _recruit(tmp_path)
    assert cli.main(["serve", "clone-a"]) == 0
    plain = capsys.readouterr().out
    assert cli.main(["serve", "clone-a", "--full"]) == 0
    assert capsys.readouterr().out == plain
    assert "--isolated" in plain


def test_serve_unknown_clone(tmp_path, capsys):
    _isolate_home(tmp_path)
    from kamino import home
    home.ensure_registry("personal")
    assert cli.main(["serve", "ghost"]) == 1
    assert "no such clone" in capsys.readouterr().err


def test_serve_announces_bundled_files(tmp_path, capsys):
    _isolate_home(tmp_path)
    from kamino import home
    regp = str(home.ensure_registry("personal"))
    art = tmp_path / "notes.txt"
    art.write_text("artifact bytes", encoding="utf-8")
    reg.recruit(_session(tmp_path, "b"), regp, "clone-b",
               "Knows the beta service: its queue consumer and its dead-letter handling.",
               files=[str(art)])
    assert cli.main(["serve", "clone-b", "--isolated"]) == 0
    assert "[bundled file: notes.txt ->" in capsys.readouterr().out
